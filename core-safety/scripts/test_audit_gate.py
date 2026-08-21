# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for the GLM5.3 mandatory pre-release audit gate."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit_gate import (
    AuditRecord, AuditStatus, GateDecision, PublicationGates, ReleaseState,
    advance_state, changed_since_pass, publication_allowed,
    verify_audited_candidate_binding,
)


def _all_true_gates(**overrides) -> PublicationGates:
    base = dict(core_certification_passed=True, ci_passed=True,
                release_artifacts_verified=True, security_audit_passed=True,
                commit_matches_audited=True, tree_matches_audited=True,
                signed_manifest_verified=True, no_blocking_findings=True,
                no_post_audit_security_relevant_changes=True,
                human_approval_explicit=True)
    base.update(overrides)
    return PublicationGates(**base)


class AuditGateTests(unittest.TestCase):

    def test_not_run_blocks_release(self):
        d = advance_state(current_state=ReleaseState.DEVELOPMENT,
                          audit_status=AuditStatus.NOT_RUN)
        self.assertFalse(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.READY_FOR_AUDIT)

    def test_failed_blocks_release(self):
        d = advance_state(current_state=ReleaseState.AUDIT_IN_PROGRESS,
                          audit_status=AuditStatus.FAILED)
        self.assertFalse(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.REMEDIATION_REQUIRED)

    def test_remediation_required_blocks_release(self):
        d = advance_state(current_state=ReleaseState.REMEDIATION_REQUIRED,
                          audit_status=AuditStatus.REMEDIATION_REQUIRED)
        self.assertFalse(d.publication_allowed)

    def test_passed_without_human_approval_stays_blocked(self):
        d = advance_state(current_state=ReleaseState.AUDIT_PASSED,
                          audit_status=AuditStatus.PASSED, human_approval=False)
        self.assertFalse(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.READY_FOR_HUMAN_APPROVAL)

    def test_passed_with_approval_reaches_approved_not_published(self):
        d = advance_state(current_state=ReleaseState.READY_FOR_HUMAN_APPROVAL,
                          audit_status=AuditStatus.PASSED, human_approval=True)
        self.assertEqual(d.release_state, ReleaseState.APPROVED)
        self.assertFalse(d.publication_allowed)  # publication is a separate gate

    def test_ready_for_audit_cannot_jump_to_published(self):
        for status in AuditStatus:
            d = advance_state(current_state=ReleaseState.READY_FOR_AUDIT,
                              audit_status=status, human_approval=True)
            self.assertNotEqual(d.release_state, ReleaseState.PUBLISHED)

    def test_post_audit_source_change_requires_re_audit(self):
        self.assertTrue(changed_since_pass(changed_categories=frozenset({"source"})))

    def test_post_audit_dependency_change_requires_re_audit(self):
        self.assertTrue(changed_since_pass(changed_categories=frozenset({"dependencies"})))

    def test_purely_editorial_change_does_not_require_re_audit(self):
        self.assertFalse(changed_since_pass(changed_categories=frozenset({"editorial"})))

    def test_no_changes_does_not_require_re_audit(self):
        self.assertFalse(changed_since_pass(changed_categories=frozenset()))

    def test_unrecognized_category_fails_closed_to_re_audit(self):
        self.assertTrue(changed_since_pass(changed_categories=frozenset({"something_new"})))

    def test_commit_mismatch_blocks_and_requires_re_audit(self):
        record = AuditRecord(
            audited_commit_sha="a" * 40, audited_tree_sha="b" * 40,
            audited_release_version="1.3.0", audited_manifest_digest="sha256:x",
            audited_artifact_digests=("sha256:y",), audit_report_reference="ref",
            audit_completed_at="2026-08-20T00:00:00Z")
        d = verify_audited_candidate_binding(
            audit_record=record, current_commit_sha="c" * 40,
            current_tree_sha="b" * 40)
        self.assertFalse(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.RE_AUDIT_REQUIRED)

    def test_tree_mismatch_blocks_and_requires_re_audit(self):
        record = AuditRecord(
            audited_commit_sha="a" * 40, audited_tree_sha="b" * 40,
            audited_release_version="1.3.0", audited_manifest_digest="sha256:x",
            audited_artifact_digests=("sha256:y",), audit_report_reference="ref",
            audit_completed_at="2026-08-20T00:00:00Z")
        d = verify_audited_candidate_binding(
            audit_record=record, current_commit_sha="a" * 40,
            current_tree_sha="d" * 40)
        self.assertFalse(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.RE_AUDIT_REQUIRED)

    def test_exact_audited_candidate_matches(self):
        record = AuditRecord(
            audited_commit_sha="a" * 40, audited_tree_sha="b" * 40,
            audited_release_version="1.3.0", audited_manifest_digest="sha256:x",
            audited_artifact_digests=("sha256:y",), audit_report_reference="ref",
            audit_completed_at="2026-08-20T00:00:00Z")
        d = verify_audited_candidate_binding(
            audit_record=record, current_commit_sha="a" * 40,
            current_tree_sha="b" * 40)
        self.assertEqual(d.release_state, ReleaseState.APPROVED)

    def test_publication_allowed_only_when_all_gates_true(self):
        d = publication_allowed(_all_true_gates())
        self.assertTrue(d.publication_allowed)
        self.assertEqual(d.release_state, ReleaseState.PUBLISHED)

    def test_single_false_gate_blocks_publication(self):
        for field in ("core_certification_passed", "ci_passed",
                      "release_artifacts_verified", "security_audit_passed",
                      "commit_matches_audited", "tree_matches_audited",
                      "signed_manifest_verified", "no_blocking_findings",
                      "no_post_audit_security_relevant_changes",
                      "human_approval_explicit"):
            d = publication_allowed(_all_true_gates(**{field: False}))
            self.assertFalse(d.publication_allowed, f"{field} should block publication")

    def test_artifact_mismatch_is_a_blocking_gate(self):
        d = publication_allowed(_all_true_gates(release_artifacts_verified=False))
        self.assertFalse(d.publication_allowed)

    def test_no_bypass_via_human_confirmation_alone(self):
        # human_approval_explicit=True cannot compensate for a commit/tree
        # mismatch or a missing audit; publication_allowed checks every gate
        # independently, so approval alone never flips the result.
        d = publication_allowed(_all_true_gates(
            commit_matches_audited=False, human_approval_explicit=True))
        self.assertFalse(d.publication_allowed)


if __name__ == "__main__":
    unittest.main()
