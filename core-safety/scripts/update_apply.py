# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""Orchestrates ``electrumx-update apply``: the only code path allowed to
change what is running.

``check`` / ``status`` / ``show`` never call anything in this module. This
module is entered only from an explicit operator command and refuses unless the
pending candidate is both ELIGIBLE and VERIFIED. A consensus-changing manifest
also requires ``--approve-consensus-change``.

Production hooks stage/build before stopping the old node, atomically switch a
same-filesystem release directory, start the new stack, run real health gates,
and either restore the exact previous release or return a promotion decision.
The caller durably saves the promoted UpdateState *before* invoking the optional
``finalize_success`` hook that deletes the last-known-good directory/journal.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Optional

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
    # Deliberately not invoked inside apply_pending_candidate. The production
    # CLI invokes it only after save_state() has durably recorded promotion.
    finalize_success: Optional[Callable[[], None]] = None


@dataclasses.dataclass
class ApplyResult:
    verdict: object
    detail: str = ""


def _rollback_after_failure(state: UpdateState, hooks: ApplyHooks, *,
                            previous: Optional[dict], reason: str) -> ApplyResult:
    try:
        hooks.rollback_to(previous)
    except Exception as rollback_exc:  # noqa: BLE001 - operational boundary
        detail = (
            f"{reason}; automatic rollback also failed: "
            f"{type(rollback_exc).__name__}: {rollback_exc}; operator intervention required")
        record_stuck(state, reason=detail)
        return ApplyResult(HealthVerdict.STUCK_NO_BLIND_ROLLBACK, detail)

    detail = f"{reason}; exact previous release restored"
    record_rollback(state, reason=detail, restored_release=previous)
    return ApplyResult(HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD, detail)


def apply_pending_candidate(state: UpdateState, hooks: ApplyHooks, *,
                            approve_consensus_change: bool) -> ApplyResult:
    """Run the explicit apply transaction after discovery/trust revalidation.

    The caller is responsible for re-fetching and re-verifying the signed
    manifest and artifact immediately before entering this function. The
    persisted verdict is still checked here as defence in depth.
    """
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
    rollback_safe = manifest.get("rollbackSafe", False)

    try:
        # Production ``stop_services`` performs/statically validates staging and
        # the new image build before stopping the old node. This preserves the
        # small, testable hook API while minimizing downtime.
        hooks.stop_services()
        hooks.switch_atomically(manifest)
        hooks.start_services()
        health = hooks.run_health_checks(manifest)
    except Exception as exc:  # noqa: BLE001 - subprocess/docker/filesystem boundary
        reason = f"update runtime failed: {type(exc).__name__}: {exc}"
        if rollback_safe:
            return _rollback_after_failure(
                state, hooks, previous=previous, reason=reason)
        record_stuck(
            state, reason=reason +
            "; rollbackSafe=false, automatic rollback intentionally suppressed")
        return ApplyResult(
            HealthVerdict.STUCK_NO_BLIND_ROLLBACK,
            state.failure_reason or reason)

    health_decision = evaluate_health(health, rollback_safe=rollback_safe)

    if health_decision.verdict == HealthVerdict.PROMOTE_TO_CURRENT:
        # Only in-memory state changes here. The CLI must fsync this state before
        # calling hooks.finalize_success / TransactionalComposeSwitch.finalize_success.
        record_promotion(state, applied_release=manifest)
        return ApplyResult(health_decision.verdict, health_decision.reason)

    if health_decision.verdict == HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD:
        return _rollback_after_failure(
            state, hooks, previous=previous,
            reason=health_decision.reason or "post-update health gates failed")

    # STUCK_NO_BLIND_ROLLBACK: leave the switched unhealthy state and exact
    # backup/journal in place so an operator can choose a migration-safe action.
    record_stuck(state, reason=health_decision.reason)
    return ApplyResult(health_decision.verdict, health_decision.reason)
