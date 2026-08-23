# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""Orchestrates ``electrumx-update apply``: the only code path allowed to
change what is running.

A higher ``artifact_revision`` under the *same* ElectrumX version is metadata
only: applying it advances verified release state without stopping services,
rebuilding images, reindexing Core, or touching the running node. A version
change retains the existing transactional switch and health-gate behavior.

Version-changing transactions also coordinate with the known host-side
Ravencoin bandwidth controller.  That controller can legitimately recreate the
ElectrumX container while reconciling ``MAX_SESSIONS``; doing so concurrently
with the updater's Compose transaction races container identity and can make
both promotion and rollback fail.  The controller is therefore suspended before
runtime mutation and restored only after successful promotion or exact rollback.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Optional

import external_mutation_guard
from update_decision import (
    ApplyVerdict, EligibilityVerdict, HealthGateResult, HealthVerdict,
    VerificationVerdict, evaluate_apply, evaluate_health,
)
from update_state import (
    UpdateState, record_promotion, record_rollback, record_stuck,
)


@dataclasses.dataclass
class ApplyHooks:
    stop_services: Callable[[], None]
    switch_atomically: Callable[[dict], None]
    start_services: Callable[[], None]
    run_health_checks: Callable[[dict], HealthGateResult]
    rollback_to: Callable[[Optional[dict]], None]
    finalize_success: Optional[Callable[[], None]] = None


@dataclasses.dataclass
class ApplyResult:
    verdict: object
    detail: str = ""


def _resume_external_mutator(suspended: bool) -> str:
    """Best-effort restore after the transaction has reached a safe state."""
    if not suspended:
        return ""
    try:
        external_mutation_guard.resume_if_suspended(True)
    except external_mutation_guard.ExternalMutationGuardError as exc:
        return f"; warning: external controller restart failed: {exc}"
    return ""


def _rollback_after_failure(state: UpdateState, hooks: ApplyHooks, *,
                            previous: Optional[dict], reason: str,
                            external_mutator_suspended: bool = False) -> ApplyResult:
    try:
        hooks.rollback_to(previous)
    except Exception as rollback_exc:  # noqa: BLE001 - operational boundary
        detail = (
            f"{reason}; automatic rollback also failed: "
            f"{type(rollback_exc).__name__}: {rollback_exc}; operator intervention required")
        if external_mutator_suspended:
            detail += "; external container reconciler intentionally remains suspended"
        record_stuck(state, reason=detail)
        return ApplyResult(HealthVerdict.STUCK_NO_BLIND_ROLLBACK, detail)

    detail = f"{reason}; exact previous release restored"
    detail += _resume_external_mutator(external_mutator_suspended)
    record_rollback(state, reason=detail, restored_release=previous)
    return ApplyResult(HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD, detail)


def _revision_only(previous: Optional[dict], manifest: dict) -> bool:
    if not previous:
        return False
    if previous.get("electrumxVersion") != manifest.get("electrumxVersion"):
        return False
    current_revision = previous.get("artifact_revision")
    candidate_revision = manifest.get("artifact_revision")
    return isinstance(current_revision, int) and not isinstance(current_revision, bool) and \
        isinstance(candidate_revision, int) and not isinstance(candidate_revision, bool) and \
        candidate_revision > current_revision


def apply_pending_candidate(state: UpdateState, hooks: ApplyHooks, *,
                            approve_consensus_change: bool) -> ApplyResult:
    """Run an explicit apply after immediate trust revalidation."""
    candidate = state.pending_candidate
    eligibility = candidate.get("_eligibilityVerdict") if candidate else None
    verification = candidate.get("_verificationVerdict") if candidate else None

    try:
        eligibility_enum = EligibilityVerdict(eligibility) if eligibility else None
        verification_enum = VerificationVerdict(verification) if verification else None
    except ValueError:
        return ApplyResult(
            ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE,
            "pending candidate contains an unknown persisted verdict")

    gate = evaluate_apply(
        pending_candidate=candidate,
        pending_verdict=eligibility_enum,
        pending_verification=verification_enum,
        approve_consensus_change=approve_consensus_change,
    )
    if gate.verdict != ApplyVerdict.ALLOWED:
        return ApplyResult(gate.verdict, gate.reason)

    manifest = candidate["manifest"]
    previous = state.current_release

    # Artifact revision is deliberately informational for a running node.
    # The signed release identity/high-water advances, but no runtime hooks run.
    if _revision_only(previous, manifest):
        record_promotion(state, applied_release=manifest)
        return ApplyResult(
            HealthVerdict.PROMOTE_TO_CURRENT,
            "revision-only promotion: running services, images and databases unchanged")

    try:
        external_mutator_suspended = external_mutation_guard.suspend_if_active()
    except external_mutation_guard.ExternalMutationGuardError as exc:
        reason = f"external container reconciler could not be suspended before mutation: {exc}"
        record_stuck(state, reason=reason)
        return ApplyResult(HealthVerdict.STUCK_NO_BLIND_ROLLBACK, reason)

    rollback_safe = manifest.get("rollbackSafe", False)
    try:
        hooks.stop_services()
        hooks.switch_atomically(manifest)
        hooks.start_services()
        health = hooks.run_health_checks(manifest)
    except Exception as exc:  # noqa: BLE001 - subprocess/docker/filesystem boundary
        reason = f"update runtime failed: {type(exc).__name__}: {exc}"
        if rollback_safe:
            return _rollback_after_failure(
                state, hooks, previous=previous, reason=reason,
                external_mutator_suspended=external_mutator_suspended)
        if external_mutator_suspended:
            reason += "; external container reconciler intentionally remains suspended"
        record_stuck(
            state, reason=reason +
            "; rollbackSafe=false, automatic rollback intentionally suppressed")
        return ApplyResult(
            HealthVerdict.STUCK_NO_BLIND_ROLLBACK,
            state.failure_reason or reason)

    health_decision = evaluate_health(health, rollback_safe=rollback_safe)
    if health_decision.verdict == HealthVerdict.PROMOTE_TO_CURRENT:
        detail = health_decision.reason or "post-update health gates passed"
        detail += _resume_external_mutator(external_mutator_suspended)
        record_promotion(state, applied_release=manifest)
        return ApplyResult(health_decision.verdict, detail)

    if health_decision.verdict == HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD:
        return _rollback_after_failure(
            state, hooks, previous=previous,
            reason=health_decision.reason or "post-update health gates failed",
            external_mutator_suspended=external_mutator_suspended)

    detail = health_decision.reason
    if external_mutator_suspended:
        detail = (detail or "post-update health state is indeterminate") + \
            "; external container reconciler intentionally remains suspended"
    record_stuck(state, reason=detail)
    return ApplyResult(health_decision.verdict, detail)
