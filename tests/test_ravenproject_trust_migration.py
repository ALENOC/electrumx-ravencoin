# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Tests for the atomic v2 -> v3 RavenProject Core trust migration gate.

The migration revokes the 2miners/Ravencoin baseline and promotes the
certified RavenProject/Ravencoin release in a single signed transition. These
tests exercise core-safety/scripts/verify_migration_v3.py, the pre-signing
check the CI workflow runs before it ever touches POLICY_SIGNING_KEY.
"""

import hashlib
import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"


def _load(name):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("policy")
generate = _load("generate_policy")
migration = _load("verify_migration_v3")

BASELINE_REPOSITORY = "2miners/Ravencoin"
BASELINE_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
CANDIDATE_REPOSITORY = "RavenProject/Ravencoin"
CANDIDATE_COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"

REQUIRED_TESTS = ("consensus-vectors", "signature-verification")


def _report(*, repository=CANDIDATE_REPOSITORY, tag="v4.8.0", commit=CANDIDATE_COMMIT,
           overall="CERTIFICATION_PASSED", required_tests=REQUIRED_TESTS,
           results=None):
    report = {
        "candidate": {"repository": repository, "tag": tag, "version": "4.8.0",
                      "commit": commit, "published_at": "2026-08-19T00:16:44Z"},
        "profile": "rvn-consensus-2026-08-v1",
        "profileRevision": 2,
        "profileSha256": "8606d330e917414d75bfd0225804faa1ca3a3593f6886e0ac5347fc7444ebd40",
        "harnessVersion": "1.0.0",
        "overall": overall,
        "requiredTests": list(required_tests),
        "results": results if results is not None else {
            test_id: {"result": "PASS"} for test_id in required_tests
        },
        "finishedAt": 1787217509,
    }
    report["reportDigest"] = hashlib.sha256(json.dumps(
        report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def _revocation(*, repository=BASELINE_REPOSITORY, commit=BASELINE_COMMIT,
                reason="2miners is not a trust source; superseded by the "
                       "RavenProject-only Core trust root"):
    return {"repository": repository, "commit": commit, "reason": reason,
            "revokedAt": "2026-08-20T00:00:00+00:00"}


def _v2_entry():
    return {
        "repository": BASELINE_REPOSITORY, "tag": "v4.8.0", "version": "4.8.0",
        "commit": BASELINE_COMMIT, "status": "KNOWN_SAFE",
        "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "PASS"},
    }


def _v2_document(*, extra_releases=None):
    # v2 predates the RavenProject-only trust restriction and legitimately
    # carries a 2miners/Ravencoin KNOWN_SAFE entry; build_policy() enforces
    # that restriction on *new* policies, so the historical v2 body is
    # assembled directly, exactly as the real signed v2 policy on disk is.
    private_key, public_bytes = policy.generate_keypair()
    key_id = policy.key_id_for(public_bytes)
    releases = [_v2_entry()] + list(extra_releases or [])
    body = {
        "schemaVersion": policy.SCHEMA_VERSION,
        "policyVersion": 2,
        "generatedAt": "2026-08-11T08:20:00+00:00",
        "safetyProfile": "rvn-consensus-2026-08-v1",
        "releases": sorted(releases, key=lambda item: (item["repository"], item["commit"])),
    }
    document = policy.sign_policy(body, private_key, key_id=key_id)
    return document, {key_id: public_bytes}


def _expected_candidate():
    """A v3 body built independently of verify_atomic_migration, to prove the
    module reproduces it rather than trivially agreeing with itself."""
    releases = generate.merge_previous(
        {"releases": [_v2_entry()]}, [generate.entry_from_report(_report())],
        [_revocation()])
    return policy.build_policy(policy_version=3, safety_profile="rvn-consensus-2026-08-v1",
                               releases=releases)


def test_migration_is_monotonic():
    document, trusted = _v2_document()
    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=_report(),
        revocation=_revocation(), reviewed_candidate=_expected_candidate())
    assert body["policyVersion"] == 3


def test_v3_contains_ravenproject_known_safe():
    document, trusted = _v2_document()
    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=_report(),
        revocation=_revocation(), reviewed_candidate=_expected_candidate())
    entry = policy.lookup_release(body, CANDIDATE_REPOSITORY, CANDIDATE_COMMIT)
    assert entry is not None and entry["status"] == "KNOWN_SAFE"


def test_v3_contains_2miners_revoked():
    document, trusted = _v2_document()
    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=_report(),
        revocation=_revocation(), reviewed_candidate=_expected_candidate())
    entry = next(e for e in body["releases"] if e["repository"] == BASELINE_REPOSITORY)
    assert entry["status"] == "REVOKED"


def test_no_2miners_known_safe_remains():
    document, trusted = _v2_document()
    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=_report(),
        revocation=_revocation(), reviewed_candidate=_expected_candidate())
    assert policy.lookup_release(body, BASELINE_REPOSITORY, BASELINE_COMMIT) is None
    assert all(e["status"] != "KNOWN_SAFE" for e in body["releases"]
              if e["repository"] == BASELINE_REPOSITORY)


def test_no_untrusted_repository_becomes_known_safe():
    with pytest.raises(policy.PolicyError, match="approved Core source"):
        generate.entry_from_report(_report(repository="evil/Ravencoin"))


def test_certification_identity_must_match_repository_and_commit():
    document, trusted = _v2_document()
    mismatched = _report(commit="9" * 40)
    with pytest.raises(policy.PolicyError, match="commit"):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=mismatched,
            revocation=_revocation(), reviewed_candidate=_expected_candidate())


def test_old_2miners_certification_cannot_promote_ravenproject():
    document, trusted = _v2_document()
    wrong_source = _report(repository=BASELINE_REPOSITORY, commit=BASELINE_COMMIT)
    with pytest.raises(policy.PolicyError, match=CANDIDATE_REPOSITORY):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=wrong_source,
            revocation=_revocation(), reviewed_candidate=_expected_candidate())


def test_wrong_report_digest_fails():
    document, trusted = _v2_document()
    tampered = _report()
    tampered["reportDigest"] = "0" * 64
    with pytest.raises(policy.PolicyError, match="reportDigest"):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=tampered,
            revocation=_revocation(), reviewed_candidate=_expected_candidate())


def test_missing_mandatory_test_pass_fails():
    document, trusted = _v2_document()
    failing = _report(results={"consensus-vectors": {"result": "PASS"},
                               "signature-verification": {"result": "FAIL"}})
    with pytest.raises(policy.PolicyError, match="signature-verification"):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=failing,
            revocation=_revocation(), reviewed_candidate=_expected_candidate())


def test_revocation_only_v3_is_rejected_as_incomplete_migration():
    """A candidate that only revokes 2miners, without promoting RavenProject,
    must never be treated as the reviewed target: the regenerated (complete)
    body will not match it, so the mismatch is caught before signing."""
    document, trusted = _v2_document()
    revocation_only_candidate = policy.build_policy(
        policy_version=3, safety_profile="rvn-consensus-2026-08-v1",
        releases=generate.merge_previous(
            {"releases": [_v2_entry()]}, [], [_revocation()]))
    with pytest.raises(policy.PolicyError, match="does not match"):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=_report(),
            revocation=_revocation(), reviewed_candidate=revocation_only_candidate)


def test_unexpected_third_release_fails():
    third_party = {
        "repository": CANDIDATE_REPOSITORY, "tag": "v4.7.0", "version": "4.7.0",
        "commit": "3" * 40, "status": "KNOWN_SAFE",
        "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "PASS"},
    }
    document, trusted = _v2_document(extra_releases=[third_party])
    with pytest.raises(policy.PolicyError, match="unexpected"):
        migration.verify_atomic_migration(
            previous_document=document, trusted_keys=trusted, report=_report(),
            revocation=_revocation(), reviewed_candidate=_expected_candidate())


def test_generated_candidate_matches_reviewed_v3_deterministically():
    document, trusted = _v2_document()
    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=_report(),
        revocation=_revocation(), reviewed_candidate=_expected_candidate())
    expected = _expected_candidate()
    for field in ("schemaVersion", "policyVersion", "safetyProfile", "releases"):
        assert body[field] == expected[field]


def test_reproduces_the_real_committed_unsigned_v3_candidate():
    """Cross-check against the actual on-disk artifacts this migration ships:
    the real v2 baseline, the real certification report, and the real
    reviewed candidate committed at
    core-safety/production/safe-core-policy-v3.unsigned.json."""
    real_v2_body = json.loads(
        (ROOT / "core-safety/production/safe-core-policy-v2.json")
        .read_text(encoding="utf-8"))["policy"]
    private_key, public_bytes = policy.generate_keypair()
    key_id = policy.key_id_for(public_bytes)
    document = policy.sign_policy(real_v2_body, private_key, key_id=key_id)
    trusted = {key_id: public_bytes}

    real_report = json.loads((
        ROOT / "core-safety/production/certifications/"
        "ravenproject-ravencoin-v4.8.0-22549129-profile-r1.json"
    ).read_text(encoding="utf-8"))
    real_candidate = json.loads((
        ROOT / "core-safety/production/safe-core-policy-v3.unsigned.json"
    ).read_text(encoding="utf-8"))

    body = migration.verify_atomic_migration(
        previous_document=document, trusted_keys=trusted, report=real_report,
        revocation=_revocation(), reviewed_candidate=real_candidate)
    assert body["releases"] == real_candidate["releases"]
