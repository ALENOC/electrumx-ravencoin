# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Mandatory pre-release security audit gate (GLM5.3).

Pure decision logic, no I/O: the release automation records facts here
(commit/tree identity, audit result, whether a change since the last PASS was
security-relevant) and this module tells it what state that leaves the
candidate in and whether publication is allowed. Nothing in this module runs
an audit, calls GLM5.3, or publishes anything; it only encodes the state
machine and gating rules so those decisions cannot be taken ad hoc elsewhere.
"""

from __future__ import annotations

import dataclasses
import enum
from typing import Optional


class AuditStatus(enum.Enum):
    NOT_RUN = "NOT_RUN"
    IN_PROGRESS = "IN_PROGRESS"
    FAILED = "FAILED"
    REMEDIATION_REQUIRED = "REMEDIATION_REQUIRED"
    RE_AUDIT_REQUIRED = "RE_AUDIT_REQUIRED"
    PASSED = "PASSED"


class ReleaseState(enum.Enum):
    DEVELOPMENT = "DEVELOPMENT"
    READY_FOR_AUDIT = "READY_FOR_AUDIT"
    AUDIT_IN_PROGRESS = "AUDIT_IN_PROGRESS"
    REMEDIATION_REQUIRED = "REMEDIATION_REQUIRED"
    RE_AUDIT_REQUIRED = "RE_AUDIT_REQUIRED"
    AUDIT_PASSED = "AUDIT_PASSED"
    READY_FOR_HUMAN_APPROVAL = "READY_FOR_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    PUBLISHED = "PUBLISHED"

    # Categories whose presence in a change since the last PASS always
    # invalidates that PASS. "Editorial" changes (docs wording, comments)
    # are the only kind that may, in principle, preserve a PASS -- and only
    # when explicitly proven not to touch any of these.


SECURITY_RELEVANT_CATEGORIES = frozenset({
    "source", "electrumx_runtime", "core_integration", "trust_logic",
    "policy_logic", "certification_pipeline", "signing_workflow",
    "installer", "updater", "rollback_logic", "db_migrations",
    "dockerfile", "base_images", "compose", "network_security_boundary",
    "chainstrap_validation", "node_monitor_integration", "dependencies",
    "dependency_hashes", "artifact_build_process", "trusted_manifest_metadata",
    "consensus_classification", "ci_security_gates",
})


@dataclasses.dataclass(frozen=True)
class AuditRecord:
    """Immutable facts about the last completed audit, once PASSED."""
    audited_commit_sha: str
    audited_tree_sha: str
    audited_release_version: str
    audited_manifest_digest: str
    audited_artifact_digests: tuple
    audit_report_reference: str
    audit_completed_at: str


@dataclasses.dataclass(frozen=True)
class GateDecision:
    release_state: ReleaseState
    publication_allowed: bool
    reason: str


def advance_state(*, current_state: ReleaseState, audit_status: AuditStatus,
                  human_approval: bool = False) -> GateDecision:
    """Steps DEVELOPMENT -> ... -> PUBLISHED strictly through the audit gate.
    READY_FOR_AUDIT can never jump straight to PUBLISHED; AUDIT_PASSED can
    never be treated as still valid once a security-relevant change has
    occurred (callers must call ``changed_since_pass`` first and, if it
    returns True, treat the state as RE_AUDIT_REQUIRED before calling this).
    """
    if audit_status == AuditStatus.NOT_RUN:
        return GateDecision(ReleaseState.READY_FOR_AUDIT, False,
                            "audit has not run; publication blocked")
    if audit_status == AuditStatus.IN_PROGRESS:
        return GateDecision(ReleaseState.AUDIT_IN_PROGRESS, False,
                            "audit in progress; publication blocked")
    if audit_status == AuditStatus.FAILED:
        return GateDecision(ReleaseState.REMEDIATION_REQUIRED, False,
                            "audit failed; remediation required before re-audit")
    if audit_status == AuditStatus.REMEDIATION_REQUIRED:
        return GateDecision(ReleaseState.REMEDIATION_REQUIRED, False,
                            "remediation required; publication blocked")
    if audit_status == AuditStatus.RE_AUDIT_REQUIRED:
        return GateDecision(ReleaseState.RE_AUDIT_REQUIRED, False,
                            "re-audit required; previous PASS no longer valid")

    # PASSED
    if not human_approval:
        return GateDecision(ReleaseState.READY_FOR_HUMAN_APPROVAL, False,
                            "audit passed; awaiting explicit human approval")
    return GateDecision(ReleaseState.APPROVED, False,
                        "approved; publication step is a separate explicit action")


def changed_since_pass(*, changed_categories: frozenset) -> bool:
    """True if anything in ``changed_categories`` intersects the categories
    that always invalidate a prior PASS. An empty or purely-editorial set of
    categories (i.e. none of the security-relevant ones) leaves PASS intact;
    everything else, including an unrecognized category, is treated as
    security-relevant by default (fail closed, per "nel dubbio: RE_AUDIT_REQUIRED").
    """
    if not changed_categories:
        return False
    known_editorial_only = changed_categories <= frozenset({"editorial"})
    if known_editorial_only:
        return False
    return True


def verify_audited_candidate_binding(*, audit_record: AuditRecord,
                                     current_commit_sha: str,
                                     current_tree_sha: str) -> GateDecision:
    """Steps 24-25 of the spec: publication requires the exact audited
    commit/tree, with no bypass via human confirmation alone.
    """
    if current_commit_sha != audit_record.audited_commit_sha:
        return GateDecision(ReleaseState.RE_AUDIT_REQUIRED, False,
                            "current commit does not match AUDITED_COMMIT_SHA")
    if current_tree_sha != audit_record.audited_tree_sha:
        return GateDecision(ReleaseState.RE_AUDIT_REQUIRED, False,
                            "current tree does not match AUDITED_TREE_SHA")
    return GateDecision(ReleaseState.APPROVED, False,
                        "commit/tree match the audited candidate")


@dataclasses.dataclass(frozen=True)
class PublicationGates:
    core_certification_passed: bool
    ci_passed: bool
    release_artifacts_verified: bool
    security_audit_passed: bool
    commit_matches_audited: bool
    tree_matches_audited: bool
    signed_manifest_verified: bool
    no_blocking_findings: bool
    no_post_audit_security_relevant_changes: bool
    human_approval_explicit: bool


def publication_allowed(gates: PublicationGates) -> GateDecision:
    failing = [name for name, value in dataclasses.asdict(gates).items() if not value]
    if failing:
        return GateDecision(ReleaseState.READY_FOR_HUMAN_APPROVAL, False,
                            "publication blocked: " + ", ".join(sorted(failing)))
    return GateDecision(ReleaseState.PUBLISHED, True,
                        "all publication gates satisfied")
