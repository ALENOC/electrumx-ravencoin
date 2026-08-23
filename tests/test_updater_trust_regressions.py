# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

import json
import os
import pathlib
import sys

import pytest


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import electrumx_update_cli as cli  # noqa: E402
import update_policy  # noqa: E402
from update_decision import (  # noqa: E402
    HostFacts, VerificationVerdict, evaluate_verification,
)
from update_state import (  # noqa: E402
    UpdateState, effective_core_policy_floor, load_state,
    record_verified_core_policy, save_state,
)

ARTIFACT_DIGEST = "sha256:" + "d" * 64
CURRENT_ARTIFACT_DIGEST = "sha256:" + "a" * 64
CURRENT_PROVENANCE_DIGEST = "sha256:" + "b" * 64
PROVENANCE_DIGEST = "sha256:" + "c" * 64


def _host():
    return HostFacts(
        architecture="linux/amd64",
        installed_updater_version="1.0.0",
        current_electrumx_version="1.0.0",
        current_core_commit="a" * 40,
        current_artifact_revision=0,
        current_artifact_digest=CURRENT_ARTIFACT_DIGEST,
        current_provenance_digest=CURRENT_PROVENANCE_DIGEST,
    )


def _manifest(commit="c" * 40, cert_digest="e" * 64):
    return {
        "schemaVersion": 2,
        "electrumxVersion": "1.1.0",
        "artifact_revision": 0,
        "artifactDigest": ARTIFACT_DIGEST,
        "provenanceDigest": PROVENANCE_DIGEST,
        "architecture": "linux/amd64",
        "coreCommit": commit,
        "certificationReportDigest": cert_digest,
        "dbCompatibility": {"schemaVersion": 1},
        "requiredUpdaterVersion": "1.0.0",
        "safeCorePolicyVersion": 1,
    }


def test_certification_digest_must_match_verified_policy():
    commit = "c" * 40
    decision = evaluate_verification(
        manifest=_manifest(commit, "f" * 64),
        signature_valid=True,
        downloaded_artifact_digest=ARTIFACT_DIGEST,
        host=_host(),
        safe_core_certified_commits=frozenset({commit}),
        safe_core_certification_digests={commit: "e" * 64},
    )
    assert decision.verdict is VerificationVerdict.REFUSED_CERTIFICATION_DIGEST_MISMATCH


def test_matching_certification_digest_can_verify():
    commit = "c" * 40
    decision = evaluate_verification(
        manifest=_manifest(commit, "e" * 64),
        signature_valid=True,
        downloaded_artifact_digest=ARTIFACT_DIGEST,
        host=_host(),
        safe_core_certified_commits=frozenset({commit}),
        safe_core_certification_digests={commit: "e" * 64},
    )
    assert decision.verdict is VerificationVerdict.VERIFIED


def test_production_apply_is_wired_to_reverified_transactional_switch():
    assert cli.PRODUCTION_APPLY_READY is True


def test_legacy_state_migrates_with_zero_policy_floor(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schemaVersion": 1,
        "currentRelease": {"electrumxVersion": "1.13.0"},
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="manual 1.13.1 -> 1.13.3 trust transition"):
        load_state(str(path))


def test_schema_v2_missing_policy_floor_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({"schemaVersion": 2}), encoding="utf-8")
    with pytest.raises(ValueError, match="minimumCorePolicyVersion"):
        load_state(str(path))


@pytest.mark.parametrize("value", [-1, True, "3", None])
def test_schema_v2_invalid_policy_floor_fails_closed(tmp_path, value):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schemaVersion": 2,
        "minimumCorePolicyVersion": value,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="minimumCorePolicyVersion"):
        load_state(str(path))


def test_future_state_schema_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    path.write_text(json.dumps({
        "schemaVersion": 999,
        "minimumCorePolicyVersion": 3,
    }), encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported update state schemaVersion"):
        load_state(str(path))


def test_persisted_policy_floor_cannot_be_lowered_by_configuration():
    state = UpdateState(minimum_core_policy_version=5)
    assert effective_core_policy_floor(state, 0) == 5
    assert effective_core_policy_floor(state, 3) == 5
    assert effective_core_policy_floor(state, 7) == 7


def test_record_verified_policy_advances_monotonically():
    state = UpdateState(minimum_core_policy_version=2)
    record_verified_core_policy(state, 3)
    assert state.minimum_core_policy_version == 3
    record_verified_core_policy(state, 3)
    assert state.minimum_core_policy_version == 3
    with pytest.raises(ValueError, match="below persisted anti-rollback floor"):
        record_verified_core_policy(state, 2)
    assert state.minimum_core_policy_version == 3


def test_invalid_configured_policy_floor_fails_closed():
    state = UpdateState(minimum_core_policy_version=2)
    for value in (-1, True, "3", None):
        with pytest.raises(ValueError, match="configured Core policy floor"):
            effective_core_policy_floor(state, value)


def test_saved_state_is_private(tmp_path):
    path = tmp_path / "state.json"
    save_state(str(path), UpdateState(minimum_core_policy_version=3))
    if os.name == "posix":
        assert path.stat().st_mode & 0o777 == 0o600


def test_environment_policy_floor_can_only_raise_persisted_floor(monkeypatch):
    state = UpdateState(minimum_core_policy_version=4)
    monkeypatch.setenv("ELECTRUMX_MIN_CORE_POLICY_VERSION", "2")
    assert effective_core_policy_floor(state, cli._configured_policy_floor()) == 4
    monkeypatch.setenv("ELECTRUMX_MIN_CORE_POLICY_VERSION", "6")
    assert effective_core_policy_floor(state, cli._configured_policy_floor()) == 6


def test_invalid_environment_policy_floor_is_rejected(monkeypatch):
    monkeypatch.setenv("ELECTRUMX_MIN_CORE_POLICY_VERSION", "not-an-integer")
    with pytest.raises(ValueError, match="must be an integer"):
        cli._configured_policy_floor()
    monkeypatch.setenv("ELECTRUMX_MIN_CORE_POLICY_VERSION", "-1")
    with pytest.raises(ValueError, match="cannot be negative"):
        cli._configured_policy_floor()


def _resolved_policy(version):
    return update_policy.ResolvedPolicy(
        body={"policyVersion": version},
        source="test",
        commits=frozenset({"c" * 40}),
        certification_digests={"c" * 40: "d" * 64},
    )


def test_production_resolver_uses_persisted_floor_and_advances_it():
    state = UpdateState(minimum_core_policy_version=4)
    observed = {}

    def resolver(**kwargs):
        observed.update(kwargs)
        return _resolved_policy(6)

    resolved = cli.resolve_production_core_policy(
        state, configured_floor=2, resolver=resolver)
    assert resolved.version == 6
    assert observed["minimum_policy_version"] == 4
    assert observed["bundled_path"] == cli.DEFAULT_CORE_POLICY_PATH
    assert observed["cache_path"] == cli.DEFAULT_CORE_POLICY_CACHE_PATH
    assert observed["key_path"] == cli.DEFAULT_CORE_POLICY_KEY_PATH
    assert observed["remote_url"] == cli.DEFAULT_CORE_POLICY_URL
    assert state.minimum_core_policy_version == 6


def test_production_resolver_configuration_can_only_raise_floor():
    state = UpdateState(minimum_core_policy_version=4)
    observed = {}

    def resolver(**kwargs):
        observed.update(kwargs)
        return _resolved_policy(7)

    cli.resolve_production_core_policy(
        state, configured_floor=7, resolver=resolver)
    assert observed["minimum_policy_version"] == 7
    assert state.minimum_core_policy_version == 7


def test_failed_production_policy_resolution_does_not_advance_floor():
    state = UpdateState(minimum_core_policy_version=5)

    def resolver(**kwargs):
        raise update_policy.PolicyResolutionError("no verified policy")

    with pytest.raises(update_policy.PolicyResolutionError, match="no verified policy"):
        cli.resolve_production_core_policy(
            state, configured_floor=0, resolver=resolver)
    assert state.minimum_core_policy_version == 5


def test_resolver_cannot_return_policy_below_persisted_floor():
    state = UpdateState(minimum_core_policy_version=5)

    def malicious_or_buggy_resolver(**kwargs):
        # Even if a future resolver regression ignores the floor argument,
        # record_verified_core_policy is a second monotonic fail-closed gate.
        return _resolved_policy(4)

    with pytest.raises(ValueError, match="below persisted anti-rollback floor"):
        cli.resolve_production_core_policy(
            state, configured_floor=0, resolver=malicious_or_buggy_resolver)
    assert state.minimum_core_policy_version == 5
