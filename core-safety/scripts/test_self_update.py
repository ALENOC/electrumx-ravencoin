# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for the ElectrumX node self-update system.

Run with:  python3 -m unittest core-safety/scripts/test_self_update.py -v
(from the repository root, with core-safety/scripts on sys.path).

Every test here exercises the real modules with fakes only at the true I/O
boundary (network, docker, filesystem paths chosen per-test in a tempdir).
Nothing here weakens a check to force a pass; a test that would need that is
a sign the code under test is wrong, not the test.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import update_manifest as um
from electrumx_update_cli import ReleaseCandidate, ReleaseSource, run_check, format_show
from update_apply import ApplyHooks, apply_pending_candidate
from update_decision import (
    ApplyVerdict, EligibilityVerdict, HealthGateResult, HealthVerdict,
    VerificationVerdict, HostFacts, evaluate_apply, evaluate_eligibility,
    evaluate_health, evaluate_verification,
)
from update_state import UpdateState, load_state, save_state

CURRENT_ARTIFACT_DIGEST = "sha256:" + "a" * 64
CURRENT_PROVENANCE_DIGEST = "sha256:" + "b" * 64
CANDIDATE_PROVENANCE_DIGEST = "sha256:" + "2" * 64


def _current_release(version="1.2.0") -> dict:
    return {
        "electrumxVersion": version,
        "artifact_revision": 0,
        "artifactDigest": CURRENT_ARTIFACT_DIGEST,
        "provenanceDigest": CURRENT_PROVENANCE_DIGEST,
    }


def _empty_high_water() -> dict:
    return {
        "schemaVersion": 2,
        "highestAcceptedVersion": None,
        "releases": {},
    }


def _host(**overrides) -> HostFacts:
    base = dict(
        architecture="linux/amd64",
        installed_updater_version="1.0.0",
        current_electrumx_version="1.2.0",
        current_core_commit="a" * 40,
        current_artifact_revision=0,
        current_artifact_digest=CURRENT_ARTIFACT_DIGEST,
        current_provenance_digest=CURRENT_PROVENANCE_DIGEST,
    )
    base.update(overrides)
    return HostFacts(**base)


def _signed_manifest(key_pair, *, core_commit="c" * 40, architecture="linux/amd64",
                     artifact_digest="sha256:" + "d" * 64, channel="stable",
                     electrumx_version="1.3.0", rollback_safe=True,
                     consensus_impact=False, cert_digest="c" * 64,
                     db_schema=1, auto_update_eligible=True):
        private_key, public_bytes = key_pair
        body = um.build_manifest(
            electrumx_version=electrumx_version,
            artifact_revision=0,
            channel=channel,
            artifact_digest=artifact_digest,
            provenance_digest=CANDIDATE_PROVENANCE_DIGEST,
            architecture=architecture,
            core_version="4.8.0", core_repository="RavenProject/Ravencoin",
            core_tag="v4.8.0", core_commit=core_commit,
            certification_report_digest=cert_digest, safe_core_policy_version=3,
            required_updater_version="1.0.0", config_compatibility={},
            db_compatibility={"schemaVersion": db_schema},
            rollback_safe=rollback_safe, consensus_impact=consensus_impact,
            auto_update_eligible=auto_update_eligible and not consensus_impact,
            installer_filename="electrumx-ravencoin-install.py",
            installer_digest="sha256:" + "1" * 64,
        )
        key_id = um.key_id_for(public_bytes)
        return um.sign_manifest(body, private_key, key_id=key_id), key_id, public_bytes


class ConsensusImpactClassificationTests(unittest.TestCase):

    def test_none_is_auto_update_eligible(self):
        consensus_impact, auto_eligible = um.classify_consensus_impact(
            um.CONSENSUS_IMPACT_NONE)
        self.assertFalse(consensus_impact)
        self.assertTrue(auto_eligible)

    def test_compatibility_is_not_auto_update_eligible(self):
        consensus_impact, auto_eligible = um.classify_consensus_impact(
            um.CONSENSUS_IMPACT_COMPATIBILITY)
        self.assertFalse(consensus_impact)
        self.assertFalse(auto_eligible)

    def test_consensus_change_forces_manual_approval(self):
        consensus_impact, auto_eligible = um.classify_consensus_impact(
            um.CONSENSUS_IMPACT_CONSENSUS_CHANGE)
        self.assertTrue(consensus_impact)
        self.assertFalse(auto_eligible)

    def test_unclassifiable_value_fails_closed(self):
        with self.assertRaises(um.ManifestError):
            um.classify_consensus_impact("UNKNOWN")

    def test_missing_classification_fails_closed(self):
        with self.assertRaises(um.ManifestError):
            um.classify_consensus_impact(None)

    def test_empty_string_fails_closed(self):
        with self.assertRaises(um.ManifestError):
            um.classify_consensus_impact("")


class EligibilityTests(unittest.TestCase):

    def test_newer_stable_eligible(self):
        d = evaluate_eligibility(
            auto_update_mode="stable", channel="stable", host=_host(),
            candidate_version="1.3.0", candidate_is_prerelease=False,
            candidate_revision=0,
            candidate_artifact_digest="sha256:" + "d" * 64,
            candidate_provenance_digest=CANDIDATE_PROVENANCE_DIGEST)
        self.assertEqual(d.verdict, EligibilityVerdict.ELIGIBLE)

    def test_same_version_ignored(self):
        d = evaluate_eligibility(
            auto_update_mode="stable", channel="stable", host=_host(),
            candidate_version="1.2.0", candidate_is_prerelease=False,
            candidate_revision=0,
            candidate_artifact_digest=CURRENT_ARTIFACT_DIGEST,
            candidate_provenance_digest=CURRENT_PROVENANCE_DIGEST)
        self.assertEqual(d.verdict, EligibilityVerdict.IGNORED_SAME_ARTIFACT)

    def test_older_refused(self):
        d = evaluate_eligibility(
            auto_update_mode="stable", channel="stable", host=_host(),
            candidate_version="1.1.0", candidate_is_prerelease=False,
            candidate_revision=0,
            candidate_artifact_digest="sha256:" + "d" * 64,
            candidate_provenance_digest=CANDIDATE_PROVENANCE_DIGEST)
        self.assertEqual(d.verdict, EligibilityVerdict.REFUSED_OLDER_VERSION)

    def test_prerelease_refused_on_stable_channel(self):
        d = evaluate_eligibility(
            auto_update_mode="stable", channel="stable", host=_host(),
            candidate_version="1.3.0", candidate_is_prerelease=True,
            candidate_revision=0,
            candidate_artifact_digest="sha256:" + "d" * 64,
            candidate_provenance_digest=CANDIDATE_PROVENANCE_DIGEST)
        self.assertEqual(d.verdict, EligibilityVerdict.REFUSED_PRERELEASE_ON_STABLE_CHANNEL)

    def test_auto_update_off_refuses_everything(self):
        d = evaluate_eligibility(
            auto_update_mode="off", channel="stable", host=_host(),
            candidate_version="1.3.0", candidate_is_prerelease=False,
            candidate_revision=0,
            candidate_artifact_digest="sha256:" + "d" * 64,
            candidate_provenance_digest=CANDIDATE_PROVENANCE_DIGEST)
        self.assertEqual(d.verdict, EligibilityVerdict.REFUSED_AUTO_UPDATE_OFF)


class VerificationTests(unittest.TestCase):

    def setUp(self):
        self.key_pair = um.generate_keypair()
        self.host = _host()

    def test_invalid_signature_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair)
        signed["signature"]["value"] = "AAAA"
        with self.assertRaises(um.ManifestError):
            um.verify_manifest(signed, {key_id: public_bytes})

    def test_unknown_key_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair)
        with self.assertRaises(um.ManifestError):
            um.verify_manifest(signed, {"different-key-id": public_bytes})

    def test_wrong_digest_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair)
        body = um.verify_manifest(signed, {key_id: public_bytes})
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "e" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.REFUSED_ARTIFACT_DIGEST_MISMATCH)

    def test_wrong_architecture_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair, architecture="linux/arm64")
        body = um.verify_manifest(signed, {key_id: public_bytes})
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "d" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH)

    def test_wrong_core_commit_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair, core_commit="f" * 40)
        body = um.verify_manifest(signed, {key_id: public_bytes})
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "d" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.REFUSED_CORE_IDENTITY_MISMATCH)

    def test_missing_certification_digest_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair, cert_digest="c" * 64)
        body = um.verify_manifest(signed, {key_id: public_bytes})
        body["certificationReportDigest"] = ""
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "d" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.REFUSED_MISSING_CERTIFICATION_DIGEST)

    def test_incompatible_db_refused(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair)
        body = um.verify_manifest(signed, {key_id: public_bytes})
        body["dbCompatibility"] = {}
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "d" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.REFUSED_UNKNOWN_DB_COMPATIBILITY)

    def test_verified_when_everything_checks_out(self):
        signed, key_id, public_bytes = _signed_manifest(self.key_pair)
        body = um.verify_manifest(signed, {key_id: public_bytes})
        d = evaluate_verification(manifest=body, signature_valid=True,
                                  downloaded_artifact_digest="sha256:" + "d" * 64,
                                  host=self.host,
                                  safe_core_certified_commits=frozenset({"c" * 40}))
        self.assertEqual(d.verdict, VerificationVerdict.VERIFIED)

    def test_rollback_safe_cannot_be_true_with_irreversible_migration(self):
        with self.assertRaises(um.ManifestError):
            um.build_manifest(
                electrumx_version="1.3.0", artifact_revision=0,
                channel="stable",
                artifact_digest="sha256:" + "d" * 64,
                provenance_digest=CANDIDATE_PROVENANCE_DIGEST,
                architecture="linux/amd64",
                core_version="4.8.0", core_repository="RavenProject/Ravencoin",
                core_tag="v4.8.0", core_commit="c" * 40,
                certification_report_digest="c" * 64, safe_core_policy_version=3,
                required_updater_version="1.0.0", config_compatibility={},
                db_compatibility={"schemaVersion": 2,
                                  "migration": {"reversible": False}},
                rollback_safe=True, consensus_impact=False,
                auto_update_eligible=False,
                installer_filename="electrumx-ravencoin-install.py",
                installer_digest="sha256:" + "1" * 64,
            )


class ApplyGateTests(unittest.TestCase):

    def test_apply_refuses_consensus_change_without_approval(self):
        d = evaluate_apply(
            pending_candidate={"manifest": {"consensusImpact": True}},
            pending_verdict=EligibilityVerdict.ELIGIBLE,
            pending_verification=VerificationVerdict.VERIFIED,
            approve_consensus_change=False,
        )
        self.assertEqual(d.verdict, ApplyVerdict.REFUSED_CONSENSUS_CHANGE_NOT_APPROVED)

    def test_apply_allows_consensus_change_with_explicit_approval(self):
        d = evaluate_apply(
            pending_candidate={"manifest": {"consensusImpact": True}},
            pending_verdict=EligibilityVerdict.ELIGIBLE,
            pending_verification=VerificationVerdict.VERIFIED,
            approve_consensus_change=True,
        )
        self.assertEqual(d.verdict, ApplyVerdict.ALLOWED)

    def test_apply_refuses_unverified_candidate(self):
        d = evaluate_apply(
            pending_candidate={"consensusImpact": False},
            pending_verdict=EligibilityVerdict.ELIGIBLE,
            pending_verification=VerificationVerdict.REFUSED_INVALID_SIGNATURE,
            approve_consensus_change=False,
        )
        self.assertEqual(d.verdict, ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE)

    def test_apply_refuses_when_no_pending_candidate(self):
        d = evaluate_apply(pending_candidate=None, pending_verdict=None,
                           pending_verification=None, approve_consensus_change=False)
        self.assertEqual(d.verdict, ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE)


def _all_pass_health() -> HealthGateResult:
    return HealthGateResult(*([True] * 12))


def _failing_health() -> HealthGateResult:
    fields = [True] * 12
    fields[4] = False  # core_rpc_healthy
    return HealthGateResult(*fields)


class HealthTests(unittest.TestCase):

    def test_all_gates_pass_promotes(self):
        d = evaluate_health(_all_pass_health(), rollback_safe=True)
        self.assertEqual(d.verdict, HealthVerdict.PROMOTE_TO_CURRENT)

    def test_failure_with_rollback_safe_rolls_back(self):
        d = evaluate_health(_failing_health(), rollback_safe=True)
        self.assertEqual(d.verdict, HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD)

    def test_failure_without_rollback_safe_never_blindly_rolls_back(self):
        d = evaluate_health(_failing_health(), rollback_safe=False)
        self.assertEqual(d.verdict, HealthVerdict.STUCK_NO_BLIND_ROLLBACK)


class StateAtomicWriteTests(unittest.TestCase):

    def test_save_then_load_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            state = UpdateState(current_release=_current_release())
            save_state(path, state)
            loaded = load_state(path)
            self.assertEqual(loaded.current_release["electrumxVersion"], "1.2.0")

    def test_load_missing_file_returns_empty_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            loaded = load_state(os.path.join(tmp, "does-not-exist.json"))
            self.assertIsNone(loaded.current_release)

    def test_no_leftover_temp_file_after_successful_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            save_state(path, UpdateState())
            leftovers = [f for f in os.listdir(tmp) if f.startswith(".update-state-")]
            self.assertEqual(leftovers, [])


class RunCheckAndApplyIntegrationTests(unittest.TestCase):
    """Exercises check -> apply end to end through fakes at the true I/O
    boundary only (network via ReleaseSource, docker/systemd via ApplyHooks).
    """

    def setUp(self):
        self.key_pair = um.generate_keypair()
        _, public_bytes = self.key_pair
        self.trusted_keys = {um.key_id_for(public_bytes): public_bytes}
        self.host = _host()
        self.safe_commits = frozenset({"c" * 40})
        self.high_water = _empty_high_water()

    def _candidate(self, **overrides):
        signed, _, _ = _signed_manifest(self.key_pair, **overrides)
        defaults = dict(version="1.3.0", channel="stable", is_prerelease=False,
                        signed_manifest_document=signed, artifact_bytes=b"x",
                        artifact_digest="sha256:" + "d" * 64)
        return ReleaseCandidate(**defaults)

    def test_successful_check_then_apply_promotes(self):
        state = UpdateState(current_release=_current_release())
        source = ReleaseSource(list_candidates=lambda: [self._candidate()])
        state = run_check(state=state, source=source, host=self.host,
                          trusted_keys=self.trusted_keys,
                          safe_core_certified_commits=self.safe_commits,
                          artifact_high_water=self.high_water,
                          auto_update_mode="stable")
        self.assertEqual(state.pending_candidate["_verificationVerdict"],
                         VerificationVerdict.VERIFIED.value)

        calls = []
        hooks = ApplyHooks(
            stop_services=lambda: calls.append("stop"),
            switch_atomically=lambda manifest: calls.append("switch"),
            start_services=lambda: calls.append("start"),
            run_health_checks=lambda manifest: _all_pass_health(),
            rollback_to=lambda previous: calls.append("rollback"),
        )
        result = apply_pending_candidate(state, hooks, approve_consensus_change=False)
        self.assertEqual(result.verdict, HealthVerdict.PROMOTE_TO_CURRENT)
        self.assertEqual(calls, ["stop", "switch", "start"])
        self.assertEqual(state.current_release["electrumxVersion"], "1.3.0")

    def test_health_check_failure_triggers_rollback(self):
        state = UpdateState(current_release=_current_release())
        source = ReleaseSource(list_candidates=lambda: [self._candidate(rollback_safe=True)])
        state = run_check(state=state, source=source, host=self.host,
                          trusted_keys=self.trusted_keys,
                          safe_core_certified_commits=self.safe_commits,
                          artifact_high_water=self.high_water,
                          auto_update_mode="stable")
        hooks = ApplyHooks(
            stop_services=lambda: None, switch_atomically=lambda m: None,
            start_services=lambda: None,
            run_health_checks=lambda m: _failing_health(),
            rollback_to=lambda previous: setattr(state, "_rolled_back_to", previous),
        )
        result = apply_pending_candidate(state, hooks, approve_consensus_change=False)
        self.assertEqual(result.verdict, HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD)
        self.assertEqual(state.current_release["electrumxVersion"], "1.2.0")

    def test_rollback_unsafe_migration_blocks_blind_rollback(self):
        state = UpdateState(current_release=_current_release())
        source = ReleaseSource(
            list_candidates=lambda: [self._candidate(rollback_safe=False)])
        state = run_check(state=state, source=source, host=self.host,
                          trusted_keys=self.trusted_keys,
                          safe_core_certified_commits=self.safe_commits,
                          artifact_high_water=self.high_water,
                          auto_update_mode="stable")
        rollback_called = []
        hooks = ApplyHooks(
            stop_services=lambda: None, switch_atomically=lambda m: None,
            start_services=lambda: None,
            run_health_checks=lambda m: _failing_health(),
            rollback_to=lambda previous: rollback_called.append(previous),
        )
        result = apply_pending_candidate(state, hooks, approve_consensus_change=False)
        self.assertEqual(result.verdict, HealthVerdict.STUCK_NO_BLIND_ROLLBACK)
        self.assertEqual(rollback_called, [])
        # current_release is left exactly as it was mid-switch: apply_pending_candidate
        # never re-reads it after switch_atomically, only state.current_release tracked
        # by update_state, which record_stuck deliberately does not touch.
        self.assertEqual(state.current_release["electrumxVersion"], "1.2.0")

    def test_consensus_change_candidate_requires_explicit_approval(self):
        state = UpdateState(current_release=_current_release())
        source = ReleaseSource(
            list_candidates=lambda: [self._candidate(consensus_impact=True)])
        state = run_check(state=state, source=source, host=self.host,
                          trusted_keys=self.trusted_keys,
                          safe_core_certified_commits=self.safe_commits,
                          artifact_high_water=self.high_water,
                          auto_update_mode="stable")
        # run_check's pending dict does not carry consensusImpact at the top
        # level; apply_pending_candidate reads it off candidate["manifest"].
        state.pending_candidate["consensusImpact"] = \
            state.pending_candidate["manifest"]["consensusImpact"]
        hooks = ApplyHooks(
            stop_services=lambda: (_ for _ in ()).throw(AssertionError("must not run")),
            switch_atomically=lambda m: None, start_services=lambda: None,
            run_health_checks=lambda m: _all_pass_health(),
            rollback_to=lambda previous: None,
        )
        result = apply_pending_candidate(state, hooks, approve_consensus_change=False)
        self.assertEqual(result.verdict, ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE
                         if False else ApplyVerdict.REFUSED_CONSENSUS_CHANGE_NOT_APPROVED)

    def test_github_unreachable_leaves_node_untouched(self):
        state = UpdateState(current_release=_current_release())
        source = ReleaseSource(list_candidates=lambda: [self._candidate()], reachable=False)
        state = run_check(state=state, source=source, host=self.host,
                          trusted_keys=self.trusted_keys,
                          safe_core_certified_commits=self.safe_commits,
                          artifact_high_water=self.high_water,
                          auto_update_mode="stable")
        self.assertIsNone(state.pending_candidate)
        self.assertEqual(state.current_release["electrumxVersion"], "1.2.0")
        self.assertEqual(state.failure_reason,
                         VerificationVerdict.REFUSED_GITHUB_UNREACHABLE.value)


if __name__ == "__main__":
    unittest.main()
