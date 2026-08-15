# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Tests for the Ravencoin Core safety certification pipeline.

Every GitHub interaction is mocked; no test contacts github.com.  No test builds
Ravencoin Core: the harness is exercised through its registry and its
fail-closed aggregation, and a candidate with no evidence must never come out
safe.
"""

import copy
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
PROFILE_PATH = ROOT / "core-safety" / "profiles" / "rvn-consensus-2026-08-v1.json"
FIXTURES_PATH = ROOT / "core-safety" / "fixtures" / "mainnet-incident-headers.json"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


candidate_module = _load("candidate")
policy_module = _load("policy")
discover = _load("discover_releases")
certify = _load("certify_core")

Candidate = candidate_module.Candidate
CandidateError = candidate_module.CandidateError
CandidateState = candidate_module.CandidateState
TestResult = candidate_module.TestResult

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
CERTIFIED_TAG_OBJECT = "9f553fcbcd6929acf24c0dfea456398dc6455dae"
OTHER_COMMIT = "1" * 40


def make_candidate(**overrides):
    values = dict(repository="2miners/Ravencoin", tag="v4.8.0", commit=CERTIFIED_COMMIT,
                  version="4.8.0", tag_object=CERTIFIED_TAG_OBJECT, tag_verified=False,
                  commit_verified=True, published_at="2026-08-11T08:16:45Z")
    values.update(overrides)
    return Candidate(**values)


# ------------------------------------------------------------------- identity
def test_identity_is_repository_and_commit_not_version():
    first = make_candidate()
    same_version_other_commit = make_candidate(commit=OTHER_COMMIT)
    same_version_other_repository = make_candidate(repository="RavenProject/Ravencoin")
    assert first.identity != same_version_other_commit.identity
    assert first.identity != same_version_other_repository.identity
    assert first.version == same_version_other_commit.version == "4.8.0"


def test_repository_outside_allowlist_is_refused():
    with pytest.raises(CandidateError, match="allowlist"):
        make_candidate(repository="attacker/Ravencoin")


@pytest.mark.parametrize("field, value", [
    ("commit", "short"),
    ("commit", "g" * 40),
    ("version", "4.8.0-rc1"),
    ("version", "latest"),
    ("tag", "v4.8.0 && rm -rf /"),
    ("artifact_sha256", "nothex"),
])
def test_malformed_candidate_fields_are_refused(field, value):
    with pytest.raises(CandidateError):
        make_candidate(**{field: value})


def test_known_unsafe_generations_are_flagged():
    for version in ("4.6.1", "4.6.1.1", "4.7.0"):
        assert make_candidate(version=version, tag=f"v{version}").is_known_unsafe_version
    assert not make_candidate().is_known_unsafe_version


# ---------------------------------------------------------------- aggregation
def test_all_passing_yields_certification_passed():
    results = {"a": TestResult.PASS, "b": TestResult.PASS}
    assert candidate_module.aggregate_state(results, required_ids=("a", "b")) \
        is CandidateState.CERTIFICATION_PASSED


@pytest.mark.parametrize("result, expected", [
    (TestResult.FAIL, CandidateState.CERTIFICATION_FAILED),
    (TestResult.UNAVAILABLE, CandidateState.REVIEW_REQUIRED),
    (TestResult.ERROR, CandidateState.REVIEW_REQUIRED),
    (TestResult.SKIPPED, CandidateState.REVIEW_REQUIRED),
])
def test_no_non_passing_result_ever_becomes_safe(result, expected):
    results = {"a": TestResult.PASS, "b": result}
    assert candidate_module.aggregate_state(results, required_ids=("a", "b")) is expected


def test_missing_required_result_is_review_required():
    assert candidate_module.aggregate_state({"a": TestResult.PASS},
                                            required_ids=("a", "b")) \
        is CandidateState.REVIEW_REQUIRED


def test_flagged_review_trigger_outranks_all_passes():
    results = {"a": TestResult.PASS}
    assert candidate_module.aggregate_state(results, required_ids=("a",),
                                            triggers_flagged=True) \
        is CandidateState.REVIEW_REQUIRED


def test_profile_allowlist_must_match_code_allowlist():
    profile = candidate_module.load_profile(PROFILE_PATH)
    assert profile["profileId"] == "rvn-consensus-2026-08-v1"
    assert tuple(profile["candidateSources"]["allowed"]) \
        == candidate_module.ALLOWED_SOURCE_REPOSITORIES


def test_chain_data_tests_stay_required_when_data_is_absent():
    profile = candidate_module.load_profile(PROFILE_PATH)
    required = candidate_module.required_test_ids(profile, have_chain_data=False,
                                                  artifact_pinned=False)
    assert "incident-checkpoint-hash" in required
    assert "transfer-overflow-deployment" in required


# -------------------------------------------------------------------- harness
def test_harness_run_without_any_evidence_is_never_safe():
    profile = candidate_module.load_profile(PROFILE_PATH)
    environment = certify.Environment(candidate=make_candidate())
    report = certify.certify(make_candidate(), profile, environment)
    assert report["overall"] == CandidateState.REVIEW_REQUIRED.value
    assert report["reportDigest"]
    assert all(entry["result"] != TestResult.PASS.value
               for test_id, entry in report["results"].items()
               if entry["scope"] == "core")


def test_known_unsafe_version_short_circuits_to_known_unsafe():
    profile = candidate_module.load_profile(PROFILE_PATH)
    unsafe = make_candidate(version="4.7.0", tag="v4.7.0")
    report = certify.certify(unsafe, profile, certify.Environment(candidate=unsafe))
    assert report["overall"] == CandidateState.KNOWN_UNSAFE.value


def test_fixture_tests_pass_against_the_real_incident_vectors():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    environment = certify.Environment(candidate=make_candidate(), fixtures=fixtures)
    for test_id in ("kawpow-header-shape", "nheight-binding-rejects-forged",
                    "post-boundary-valid-accepted"):
        function, scope = certify.REGISTRY[test_id]
        outcome = function(environment)
        assert outcome.result is TestResult.PASS, (test_id, outcome.detail)
        assert scope == "harness"


def test_forged_fixture_that_is_accepted_would_fail_the_suite():
    fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
    # Replace a forged vector with a genuine header: the rule accepts it, and the
    # test must then report FAIL rather than quietly passing.
    genuine = fixtures["validHeaders"][2]
    fixtures = copy.deepcopy(fixtures)
    fixtures["invalidHeaders"] = [{
        "name": "not-actually-forged",
        "chainHeight": genuine["height"],
        "declaredHeight": genuine["height"],
        "headerHex": genuine["headerHex"],
        "derivation": "test only",
        "expected": "reject",
    }]
    function, _ = certify.REGISTRY["nheight-binding-rejects-forged"]
    outcome = function(certify.Environment(candidate=make_candidate(), fixtures=fixtures))
    assert outcome.result is TestResult.FAIL


def test_missing_binaries_make_core_tests_unavailable_not_passing():
    environment = certify.Environment(candidate=make_candidate())
    for test_id in ("build-candidate-commit", "mainnet-genesis",
                    "regtest-consensus-smoke", "regtest-asset-consensus",
                    "required-indexes-usable", "incident-checkpoint-hash",
                    "transfer-overflow-deployment"):
        function, _ = certify.REGISTRY[test_id]
        assert function(environment).result is TestResult.UNAVAILABLE, test_id


def test_artifact_digest_mismatch_is_a_failure(tmp_path):
    artifact = tmp_path / "core.tar.gz"
    artifact.write_bytes(b"not the pinned artifact")
    pinned = make_candidate(artifact_sha256="0" * 64, artifact_name="core.tar.gz")
    function, _ = certify.REGISTRY["provenance-artifact-digest"]
    outcome = function(certify.Environment(candidate=pinned, artifact=artifact))
    assert outcome.result is TestResult.FAIL


def test_unconfirmed_tag_resolution_is_unavailable():
    function, _ = certify.REGISTRY["provenance-tag-resolves-to-commit"]
    unconfirmed = make_candidate(commit_verified=False)
    outcome = function(certify.Environment(candidate=unconfirmed))
    assert outcome.result is TestResult.UNAVAILABLE


# ------------------------------------------------------------------ discovery
def fake_github(pages):
    calls = []

    def fetch(url, token=None, timeout=30):
        calls.append(url)
        for suffix, payload in pages.items():
            if url.endswith(suffix):
                if isinstance(payload, Exception):
                    raise payload
                return payload
        raise discover.DiscoveryError(f"unexpected url {url}")

    fetch.calls = calls
    return fetch


def release_payload(tag="v4.8.0", **overrides):
    payload = {
        "tag_name": tag,
        "draft": False,
        "prerelease": False,
        "published_at": "2026-08-11T08:16:45Z",
        "html_url": f"https://github.com/2miners/Ravencoin/releases/tag/{tag}",
        "assets": [{"name": "ravencoin-4.8.0-x86_64-linux-gnu.tar.gz"},
                   {"name": "SHA256SUMS"}],
    }
    payload.update(overrides)
    return payload


def annotated_tag_pages(repository="2miners/Ravencoin", tag="v4.8.0",
                        commit=CERTIFIED_COMMIT, verified=False):
    return {
        f"/repos/{repository}/releases?per_page=10": [release_payload(tag)],
        f"/repos/{repository}/git/ref/tags/{tag}":
            {"object": {"type": "tag", "sha": CERTIFIED_TAG_OBJECT}},
        f"/repos/{repository}/git/tags/{CERTIFIED_TAG_OBJECT}":
            {"object": {"type": "commit", "sha": commit},
             "verification": {"verified": verified}},
    }


def test_discovery_resolves_annotated_tag_to_commit():
    fetch = fake_github(annotated_tag_pages())
    found = discover.discover_repository("2miners/Ravencoin", fetch)
    assert len(found) == 1
    entry = found[0]
    assert entry["commit"] == CERTIFIED_COMMIT
    assert entry["tag_object"] == CERTIFIED_TAG_OBJECT
    assert entry["tag_verified"] is False
    assert entry["state"] == CandidateState.PROVENANCE_VALIDATED.value


def test_discovery_refuses_repository_outside_allowlist():
    with pytest.raises(discover.DiscoveryError, match="allowlist"):
        discover.discover_repository("attacker/Ravencoin", fake_github({}))


def test_same_version_from_both_repositories_are_two_candidates():
    pages = {}
    pages.update(annotated_tag_pages("2miners/Ravencoin", commit=CERTIFIED_COMMIT))
    pages.update({
        "/repos/RavenProject/Ravencoin/releases?per_page=10": [release_payload("v4.8.0")],
        "/repos/RavenProject/Ravencoin/git/ref/tags/v4.8.0":
            {"object": {"type": "commit", "sha": OTHER_COMMIT}},
    })
    found = discover.discover_all(fake_github(pages))
    identities = {f"{entry['repository']}@{entry['commit']}" for entry in found}
    assert len(identities) == 2


def test_tag_pointing_at_unexpected_object_is_provenance_failure():
    pages = {
        "/repos/2miners/Ravencoin/releases?per_page=10": [release_payload()],
        "/repos/2miners/Ravencoin/git/ref/tags/v4.8.0":
            {"object": {"type": "tree", "sha": CERTIFIED_TAG_OBJECT}},
    }
    found = discover.discover_repository("2miners/Ravencoin", fake_github(pages))
    assert found[0]["state"] == CandidateState.PROVENANCE_FAILED.value


def test_release_target_disagreeing_with_tag_is_review_required():
    pages = annotated_tag_pages()
    pages["/repos/2miners/Ravencoin/releases?per_page=10"] = [
        release_payload(target_commitish=OTHER_COMMIT)]
    found = discover.discover_repository("2miners/Ravencoin", fake_github(pages))
    assert found[0]["state"] == CandidateState.REVIEW_REQUIRED.value


def test_non_version_tag_is_review_required_not_guessed():
    pages = {"/repos/2miners/Ravencoin/releases?per_page=10":
             [release_payload("nightly-build")]}
    found = discover.discover_repository("2miners/Ravencoin", fake_github(pages))
    assert found[0]["state"] == CandidateState.REVIEW_REQUIRED.value


def test_draft_releases_are_ignored():
    pages = {"/repos/2miners/Ravencoin/releases?per_page=10":
             [release_payload(draft=True)]}
    assert discover.discover_repository("2miners/Ravencoin", fake_github(pages)) == []


def test_no_new_release_produces_nothing():
    pages = {"/repos/2miners/Ravencoin/releases?per_page=10": []}
    assert discover.discover_repository("2miners/Ravencoin", fake_github(pages)) == []


def test_rate_limit_becomes_review_required_not_silence():
    error = discover.DiscoveryError("GitHub rate limited or refused the request (403)")
    pages = {"/repos/2miners/Ravencoin/releases?per_page=10": error,
             "/repos/RavenProject/Ravencoin/releases?per_page=10": error}
    found = discover.discover_all(fake_github(pages))
    assert len(found) == 2
    assert all(entry["state"] == CandidateState.REVIEW_REQUIRED.value for entry in found)


def test_malformed_api_response_is_an_error():
    pages = {"/repos/2miners/Ravencoin/releases?per_page=10": {"unexpected": "object"}}
    with pytest.raises(discover.DiscoveryError, match="not a list"):
        discover.discover_repository("2miners/Ravencoin", fake_github(pages))


def test_already_processed_candidate_is_not_reprocessed():
    fetch = fake_github(annotated_tag_pages())
    found = discover.discover_repository("2miners/Ravencoin", fetch)
    processed = {f"2miners/Ravencoin@{CERTIFIED_COMMIT}": {"tag": "v4.8.0"}}
    assert discover.new_candidates(found, processed) == []


def test_retagged_commit_is_flagged_for_review():
    pages = annotated_tag_pages(tag="v4.8.0")
    fetch = fake_github(pages)
    found = discover.discover_repository("2miners/Ravencoin", fetch)
    processed = {f"2miners/Ravencoin@{CERTIFIED_COMMIT}": {"tag": "v4.8.0-old"}}
    fresh = discover.new_candidates(found, processed)
    assert len(fresh) == 1
    assert fresh[0]["state"] == CandidateState.REVIEW_REQUIRED.value


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "state" / "processed.json"
    discover.save_state(path, {"2miners/Ravencoin@" + CERTIFIED_COMMIT: {"tag": "v4.8.0"}})
    assert discover.load_state(path)["2miners/Ravencoin@" + CERTIFIED_COMMIT]["tag"] \
        == "v4.8.0"


def test_corrupt_state_file_falls_back_to_empty(tmp_path):
    path = tmp_path / "processed.json"
    path.write_text("{not json", encoding="utf-8")
    assert discover.load_state(path) == {}
