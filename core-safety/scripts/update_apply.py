# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Orchestrates ``electrumx-update apply``: the only code path that is
allowed to change what is running.

check/status/show never call anything in this module. This module is only
ever entered from an explicit operator command, and it refuses immediately
if the pending candidate is not both ELIGIBLE and VERIFIED, or if it carries
consensusImpact without --approve-consensus-change.

The five steps below (pre-pull is assumed already done by ``check``; stop,
switch, start, health-check, confirm-or-rollback) are each an injected hook
so this module can be exercised in tests without docker, without a real
Core/ElectrumX process, and without touching real blockchain or database
files. Production wiring supplies hooks that shell out to docker/systemd;
tests supply fakes.
"""

from __future__ import annotations

import dataclasses
from typing import Callable, Optional

from update_decision import (
    ApplyVerdict, Decision, EligibilityVerdict, HealthGateResult, HealthVerdict,
    VerificationVerdict, evaluate_apply, evaluate_health,
)
from update_state import UpdateState, record_promotion, record_rollback, record_stuck


@dataclasses.dataclass
class ApplyHooks:
    stop_services: Callable[[], None]
    switch_atomically: Callable[[dict], None]
    start_services: Callable[[], None]
    run_health_checks: Callable[[dict], HealthGateResult]
    rollback_to: Callable[[Optional[dict]], None]


@dataclasses.dataclass
class ApplyResult:
    verdict: object
    detail: str = ""


def apply_pending_candidate(state: UpdateState, hooks: ApplyHooks, *,
                            approve_consensus_change: bool) -> ApplyResult:
    """Runs steps 14-19 of the update algorithm. Steps 1-13 (discovery,
    eligibility, verification, pre-pull) already happened during ``check``
    and are represented here only by what ``check`` recorded in
    ``state.pending_candidate``.
    """
    candidate = state.pending_candidate
    eligibility = candidate.get("_eligibilityVerdict") if candidate else None
    verification = candidate.get("_verificationVerdict") if candidate else None

    gate = evaluate_apply(
        pending_candidate=candidate,
        pending_verdict=EligibilityVerdict(eligibility) if eligibility else None,
        pending_verification=VerificationVerdict(verification) if verification else None,
        approve_consensus_change=approve_consensus_change,
    )
    if gate.verdict != ApplyVerdict.ALLOWED:
        return ApplyResult(gate.verdict, gate.reason)

    manifest = candidate["manifest"]
    previous = state.current_release

    hooks.stop_services()
    hooks.switch_atomically(manifest)
    hooks.start_services()
    health = hooks.run_health_checks(manifest)

    health_decision = evaluate_health(health, rollback_safe=manifest.get("rollbackSafe", False))

    if health_decision.verdict == HealthVerdict.PROMOTE_TO_CURRENT:
        record_promotion(state, applied_release=manifest)
        return ApplyResult(health_decision.verdict, health_decision.reason)

    if health_decision.verdict == HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD:
        hooks.rollback_to(previous)
        record_rollback(state, reason=health_decision.reason)
        return ApplyResult(health_decision.verdict, health_decision.reason)

    # STUCK_NO_BLIND_ROLLBACK: leave the switched, unhealthy state exactly as
    # it is and record that an operator must intervene. Never guess.
    record_stuck(state, reason=health_decision.reason)
    return ApplyResult(health_decision.verdict, health_decision.reason)
