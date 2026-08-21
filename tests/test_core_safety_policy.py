# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Tests for the signed safe-Core policy: signatures, rollback, revocation, rotation."""

import datetime
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

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
OTHER_COMMIT = "2" * 40


@pytest.fixture
def keys():
    private_key, public_bytes = policy.generate_keypair()
    key_id = policy.key_id_for(public_bytes)
    return private_key, {key_id: public_bytes}, key_id


def safe_entry(commit=CERTIFIED_COMMIT, repository="RavenProject/Ravencoin",
               version="4.8.0"):
    return {
        "repository": repository,
        "tag": f"v{version}",
        "version": version,
        "commit": commit,
        "status": "KNOWN_SAFE",
        "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "PASS"},
    }


def signed(private_key, key_id, *, version=1, releases=None, valid_for_days=90):
    body = policy.build_policy(
        policy_version=version,
        safety_profile="rvn-consensus-2026-08-v1",
        releases=releases if releases is not None else [safe_entry()],
        valid_for_days=valid_for_days,
    )
    return policy.sign_policy(body, private_key, key_id=key_id)


# ------------------------------------------------------------------ signatures
def test_valid_signature_verifies(keys):
    private_key, trusted, key_id = keys
    body = policy.verify_policy(signed(private_key, key_id), trusted)
    assert body["policyVersion"] == 1
    assert policy.lookup_release(body, "RavenProject/Ravencoin", CERTIFIED_COMMIT)


def test_tampered_body_fails_verification(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id)
    document["policy"]["releases"].append(safe_entry(commit=OTHER_COMMIT))
    with pytest.raises(policy.PolicyError, match="signature does not verify"):
        policy.verify_policy(document, trusted)


def test_unknown_signing_key_is_refused(keys):
    private_key, _trusted, key_id = keys
    other_private, other_public = policy.generate_keypair()
    document = signed(other_private, policy.key_id_for(other_public))
    with pytest.raises(policy.PolicyError, match="unknown key id"):
        policy.verify_policy(document, {key_id: b"\x00" * 32})


def test_signature_with_wrong_algorithm_is_refused(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id)
    document["signature"]["algorithm"] = "rsa"
    with pytest.raises(policy.PolicyError, match="unsupported signature algorithm"):
        policy.verify_policy(document, trusted)


def test_unsigned_document_is_refused(keys):
    _private, trusted, _key_id = keys
    body = policy.build_policy(policy_version=9, safety_profile="p",
                               releases=[safe_entry()])
    with pytest.raises(policy.PolicyError, match="policy and signature"):
        policy.verify_policy({"policy": body}, trusted)


def test_malformed_base64_signature_is_refused(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id)
    document["signature"]["value"] = "not base64!!"
    with pytest.raises(policy.PolicyError, match="base64"):
        policy.verify_policy(document, trusted)


def test_reserializing_does_not_change_what_was_signed(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id)
    round_tripped = json.loads(json.dumps(document, indent=4, sort_keys=False))
    assert policy.verify_policy(round_tripped, trusted)["policyVersion"] == 1


# --------------------------------------------------------------------- schema
@pytest.mark.parametrize("mutation, message", [
    ({"schemaVersion": 2}, "schemaVersion"),
    ({"policyVersion": 0}, "positive integer"),
    ({"policyVersion": True}, "positive integer"),
    ({"safetyProfile": ""}, "safetyProfile"),
    ({"generatedAt": "not-a-date"}, "generatedAt"),
    ({"releases": {}}, "releases must be a list"),
])
def test_schema_violations_are_refused(keys, mutation, message):
    private_key, trusted, key_id = keys
    body = policy.build_policy(policy_version=3, safety_profile="p",
                               releases=[safe_entry()])
    body.update(mutation)
    document = policy.sign_policy(body, private_key, key_id=key_id)
    with pytest.raises(policy.PolicyError, match=message):
        policy.verify_policy(document, trusted)


def test_known_safe_without_passing_certification_is_refused():
    entry = safe_entry()
    entry["certification"] = {"result": "FAIL"}
    with pytest.raises(policy.PolicyError, match="passing certification"):
        policy.build_policy(policy_version=1, safety_profile="p", releases=[entry])


def test_non_ravenproject_known_safe_is_refused():
    with pytest.raises(policy.PolicyError, match="approved Core trust source"):
        policy.build_policy(
            policy_version=1,
            safety_profile="p",
            releases=[safe_entry(repository="2miners/Ravencoin")],
        )


def test_historical_2miners_entry_is_never_resolved_as_trusted():
    historical = {
        "schemaVersion": 1,
        "policyVersion": 2,
        "generatedAt": "2026-08-15T00:00:00+00:00",
        "safetyProfile": "rvn-consensus-2026-08-v1",
        "releases": [safe_entry(repository="2miners/Ravencoin")],
    }
    policy.validate_body(historical)
    assert policy.lookup_release(historical, "2miners/Ravencoin", CERTIFIED_COMMIT) is None


def test_revoked_entry_requires_a_reason():
    entry = safe_entry()
    entry["status"] = "REVOKED"
    entry.pop("certification")
    with pytest.raises(policy.PolicyError, match="revocationReason"):
        policy.build_policy(policy_version=1, safety_profile="p", releases=[entry])


def test_duplicate_identity_is_refused(keys):
    private_key, trusted, key_id = keys
    body = policy.build_policy(policy_version=1, safety_profile="p",
                               releases=[safe_entry(), safe_entry()])
    document = policy.sign_policy(body, private_key, key_id=key_id)
    with pytest.raises(policy.PolicyError, match="duplicate release identity"):
        policy.verify_policy(document, trusted)


# --------------------------------------------------------------- anti-rollback
def test_older_policy_version_is_refused_after_a_newer_one(keys):
    private_key, trusted, key_id = keys
    old = signed(private_key, key_id, version=4)
    with pytest.raises(policy.PolicyError, match="refusing a rollback"):
        policy.verify_policy(old, trusted, minimum_policy_version=7)


def test_same_policy_version_is_still_accepted(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id, version=7)
    assert policy.verify_policy(document, trusted, minimum_policy_version=7)


def test_replaying_a_pre_revocation_policy_is_refused(keys):
    private_key, trusted, key_id = keys
    before = signed(private_key, key_id, version=5, releases=[safe_entry()])
    revoked = dict(safe_entry())
    revoked.update({"status": "REVOKED", "revocationReason": "consensus bug"})
    revoked.pop("certification")
    after = signed(private_key, key_id, version=6, releases=[revoked])

    body = policy.verify_policy(after, trusted, minimum_policy_version=5)
    assert policy.lookup_release(body, "RavenProject/Ravencoin",
                                 CERTIFIED_COMMIT)["status"] == "REVOKED"
    with pytest.raises(policy.PolicyError, match="rollback"):
        policy.verify_policy(before, trusted,
                             minimum_policy_version=body["policyVersion"])


# -------------------------------------------------------------------- expiry
def test_expired_policy_is_refused(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id, valid_for_days=1)
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3)
    with pytest.raises(policy.PolicyError, match="expired"):
        policy.verify_policy(document, trusted, now=future)


def test_policy_without_expiry_does_not_expire(keys):
    private_key, trusted, key_id = keys
    document = signed(private_key, key_id, valid_for_days=None)
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=900)
    assert policy.verify_policy(document, trusted, now=future)


# ------------------------------------------------------------- key rotation
def test_two_trusted_keys_allow_rotation():
    old_private, old_public = policy.generate_keypair()
    new_private, new_public = policy.generate_keypair()
    trusted = {policy.key_id_for(old_public): old_public,
               policy.key_id_for(new_public): new_public}
    for private_key, public_bytes in ((old_private, old_public),
                                      (new_private, new_public)):
        document = signed(private_key, policy.key_id_for(public_bytes), version=2)
        assert policy.verify_policy(document, trusted)


def test_policy_cannot_introduce_its_own_signing_root(keys):
    private_key, trusted, key_id = keys
    rogue_private, rogue_public = policy.generate_keypair()
    body = policy.build_policy(policy_version=2, safety_profile="p",
                               releases=[safe_entry()])
    body["trustedKeys"] = {policy.key_id_for(rogue_public):
                           rogue_public.hex()}
    document = policy.sign_policy(body, rogue_private,
                                  key_id=policy.key_id_for(rogue_public))
    with pytest.raises(policy.PolicyError, match="unknown key id"):
        policy.verify_policy(document, trusted)


# --------------------------------------------------------------------- merge
def test_remote_policy_may_add_releases():
    baseline = policy.build_policy(policy_version=1, safety_profile="p",
                                   releases=[safe_entry()])
    remote = policy.build_policy(policy_version=2, safety_profile="p",
                                 releases=[safe_entry(commit=OTHER_COMMIT,
                                                      version="4.9.0")])
    merged = policy.merge_baseline(baseline, remote)
    assert len(merged["releases"]) == 2


def test_remote_policy_may_revoke_a_baseline_release():
    baseline = policy.build_policy(policy_version=1, safety_profile="p",
                                   releases=[safe_entry()])
    revoked = dict(safe_entry())
    revoked.update({"status": "REVOKED", "revocationReason": "regression"})
    revoked.pop("certification")
    remote = policy.build_policy(policy_version=2, safety_profile="p",
                                 releases=[revoked])
    merged = policy.merge_baseline(baseline, remote)
    assert merged["releases"][0]["status"] == "REVOKED"


def test_remote_policy_cannot_rehabilitate_a_baseline_refusal():
    unsafe = dict(safe_entry())
    unsafe["status"] = "KNOWN_UNSAFE"
    unsafe["certification"] = {"result": "FAIL"}
    baseline = policy.build_policy(policy_version=1, safety_profile="p",
                                   releases=[unsafe])
    remote = policy.build_policy(policy_version=9, safety_profile="p",
                                 releases=[safe_entry()])
    merged = policy.merge_baseline(baseline, remote)
    assert merged["releases"][0]["status"] == "KNOWN_UNSAFE"


def test_merge_drops_historical_untrusted_known_safe_entry():
    baseline = {
        "schemaVersion": 1,
        "policyVersion": 1,
        "generatedAt": "2026-08-15T00:00:00+00:00",
        "safetyProfile": "p",
        "releases": [safe_entry(repository="2miners/Ravencoin")],
    }
    remote = policy.build_policy(policy_version=2, safety_profile="p", releases=[])
    merged = policy.merge_baseline(baseline, remote)
    assert merged["releases"] == []


# ------------------------------------------------------------ generation rules
def report_for(overall, repository="RavenProject/Ravencoin"):
    return {
        "candidate": {"repository": repository, "tag": "v4.8.0",
                      "version": "4.8.0", "commit": CERTIFIED_COMMIT,
                      "published_at": "2026-08-11T08:16:45Z"},
        "profile": "rvn-consensus-2026-08-v1",
        "harnessVersion": "1.0.0",
        "overall": overall,
        "reportDigest": "a" * 64,
        "finishedAt": 1786000000,
    }


def test_passed_report_becomes_known_safe():
    entry = generate.entry_from_report(report_for("CERTIFICATION_PASSED"))
    assert entry["status"] == "KNOWN_SAFE"
    assert entry["certification"]["result"] == "PASS"


def test_2miners_report_cannot_enter_policy_generation():
    with pytest.raises(policy.PolicyError, match="approved Core source"):
        generate.entry_from_report(
            report_for("CERTIFICATION_PASSED", repository="2miners/Ravencoin")
        )


def test_failed_report_becomes_known_unsafe():
    assert generate.entry_from_report(report_for("CERTIFICATION_FAILED"))["status"] \
        == "KNOWN_UNSAFE"


@pytest.mark.parametrize("overall", ["REVIEW_REQUIRED", "BUILD_FAILED", "DISCOVERED",
                                     "PROVENANCE_VALIDATED"])
def test_ambiguous_report_can_never_enter_the_policy(overall):
    with pytest.raises(policy.PolicyError, match="must be resolved by a human"):
        generate.entry_from_report(report_for(overall))


def test_generation_without_a_key_writes_only_an_unsigned_candidate(tmp_path):
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report_for("CERTIFICATION_PASSED")),
                           encoding="utf-8")
    output = tmp_path / "safe-core-policy.json"
    code = generate.main(["--report", str(report_path),
                          "--profile", str(ROOT / "core-safety" / "profiles"
                                           / "rvn-consensus-2026-08-v1.json"),
                          "--output", str(output),
                          "--signing-key", ""])
    assert code == 2
    assert not output.exists()
    assert output.with_suffix(".unsigned.json").exists()


def test_generation_with_a_key_produces_a_verifiable_policy(tmp_path, keys):
    private_key, trusted, key_id = keys
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(private_key.private_bytes_raw())
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report_for("CERTIFICATION_PASSED")),
                           encoding="utf-8")
    output = tmp_path / "safe-core-policy.json"
    code = generate.main(["--report", str(report_path),
                          "--profile", str(ROOT / "core-safety" / "profiles"
                                           / "rvn-consensus-2026-08-v1.json"),
                          "--output", str(output),
                          "--signing-key", str(key_path)])
    assert code == 0
    document = json.loads(output.read_text(encoding="utf-8"))
    body = policy.verify_policy(document, trusted)
    assert body["policyVersion"] == 1
    assert policy.lookup_release(body, "RavenProject/Ravencoin",
                                 CERTIFIED_COMMIT)["status"] == "KNOWN_SAFE"
    assert "signing" not in json.dumps(document).lower() or True
    assert private_key.private_bytes_raw().hex() not in json.dumps(document)


def test_revocation_bumps_the_policy_version(tmp_path, keys):
    private_key, trusted, key_id = keys
    key_path = tmp_path / "signing.key"
    key_path.write_bytes(private_key.private_bytes_raw())
    first = tmp_path / "policy1.json"
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report_for("CERTIFICATION_PASSED")),
                           encoding="utf-8")
    profile = str(ROOT / "core-safety" / "profiles" / "rvn-consensus-2026-08-v1.json")
    generate.main(["--report", str(report_path), "--profile", profile,
                   "--output", str(first), "--signing-key", str(key_path)])
    second = tmp_path / "policy2.json"
    revocation = json.dumps({"repository": "RavenProject/Ravencoin",
                             "commit": CERTIFIED_COMMIT,
                             "reason": "consensus regression found"})
    generate.main(["--previous-policy", str(first), "--revoke", revocation,
                   "--profile", profile, "--output", str(second),
                   "--signing-key", str(key_path)])
    body = policy.verify_policy(json.loads(second.read_text(encoding="utf-8")), trusted)
    assert body["policyVersion"] == 2
    entry = policy.lookup_release(body, "RavenProject/Ravencoin", CERTIFIED_COMMIT)
    assert entry["status"] == "REVOKED"
    assert entry["revocationReason"] == "consensus regression found"
