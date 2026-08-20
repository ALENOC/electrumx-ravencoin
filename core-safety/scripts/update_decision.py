# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Pure decision logic for the ElectrumX node self-update system.

Every function here is a pure function from (current state, candidate
release/manifest, host facts) to a verdict enum. No network, no filesystem,
no subprocess, no docker. Detect / verify / pre-pull are things a node may do
unattended; install is not. That split is enforced by keeping "is this
candidate eligible and trustworthy" (this module) completely separate from
"perform the switch" (update_apply.py), so an automated ``electrumx-update
check`` can only ever populate a pending candidate, never install it.

The default branch of every classifier here is a refusal. A field this module
does not recognize, or a check it cannot complete, must not silently pass.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

AUTO_UPDATE_MODES = ("off", "notify", "stable", "security")


class EligibilityVerdict(enum.Enum):
    ELIGIBLE = "ELIGIBLE"
    IGNORED_SAME_VERSION = "IGNORED_SAME_VERSION"
    REFUSED_OLDER_VERSION = "REFUSED_OLDER_VERSION"
    REFUSED_PRERELEASE_ON_STABLE_CHANNEL = "REFUSED_PRERELEASE_ON_STABLE_CHANNEL"
    REFUSED_WRONG_CHANNEL = "REFUSED_WRONG_CHANNEL"
    REFUSED_AUTO_UPDATE_OFF = "REFUSED_AUTO_UPDATE_OFF"


class VerificationVerdict(enum.Enum):
    VERIFIED = "VERIFIED"
    REFUSED_NO_MANIFEST = "REFUSED_NO_MANIFEST"
    REFUSED_INVALID_SIGNATURE = "REFUSED_INVALID_SIGNATURE"
    REFUSED_ARTIFACT_DIGEST_MISMATCH = "REFUSED_ARTIFACT_DIGEST_MISMATCH"
    REFUSED_ARCHITECTURE_MISMATCH = "REFUSED_ARCHITECTURE_MISMATCH"
    REFUSED_CORE_IDENTITY_MISMATCH = "REFUSED_CORE_IDENTITY_MISMATCH"
    REFUSED_MISSING_CERTIFICATION_DIGEST = "REFUSED_MISSING_CERTIFICATION_DIGEST"
    REFUSED_CERTIFICATION_DIGEST_MISMATCH = "REFUSED_CERTIFICATION_DIGEST_MISMATCH"
    REFUSED_UNKNOWN_DB_COMPATIBILITY = "REFUSED_UNKNOWN_DB_COMPATIBILITY"
    REFUSED_GITHUB_UNREACHABLE = "REFUSED_GITHUB_UNREACHABLE"


class ApplyVerdict(enum.Enum):
    ALLOWED = "ALLOWED"
    REFUSED_NO_VERIFIED_CANDIDATE = "REFUSED_NO_VERIFIED_CANDIDATE"
    REFUSED_CONSENSUS_CHANGE_NOT_APPROVED = "REFUSED_CONSENSUS_CHANGE_NOT_APPROVED"


class HealthVerdict(enum.Enum):
    PROMOTE_TO_CURRENT = "PROMOTE_TO_CURRENT"
    ROLLBACK_TO_LAST_KNOWN_GOOD = "ROLLBACK_TO_LAST_KNOWN_GOOD"
    STUCK_NO_BLIND_ROLLBACK = "STUCK_NO_BLIND_ROLLBACK"


@dataclass(frozen=True)
class HostFacts:
    architecture: str
    installed_updater_version: str
    current_electrumx_version: str
    current_core_commit: Optional[str] = None


@dataclass(frozen=True)
class HealthGateResult:
    expected_electrumx_version_ok: bool
    expected_core_version_ok: bool
    core_source_identity_ok: bool
    expected_core_commit_ok: bool
    core_rpc_healthy: bool
    correct_mainnet: bool
    checkpoint_verified: bool
    core_not_crash_looping: bool
    electrumx_db_opens: bool
    electrumx_db_tip_matches_core: bool
    electrumx_service_responds: bool
    no_startup_safety_policy_rejection: bool

    def all_pass(self) -> bool:
        return all((
            self.expected_electrumx_version_ok,
            self.expected_core_version_ok,
            self.core_source_identity_ok,
            self.expected_core_commit_ok,
            self.core_rpc_healthy,
            self.correct_mainnet,
            self.checkpoint_verified,
            self.core_not_crash_looping,
            self.electrumx_db_opens,
            self.electrumx_db_tip_matches_core,
            self.electrumx_service_responds,
            self.no_startup_safety_policy_rejection,
        ))

    def failures(self):
        return [name for name, value in self.__dict__.items() if value is False]


@dataclass(frozen=True)
class Decision:
    verdict: enum.Enum
    reason: str = ""


def evaluate_eligibility(*, auto_update_mode: str, channel: str,
                         current_version: str, candidate_version: str,
                         candidate_is_prerelease: bool) -> Decision:
    """Step 1-5 of the update algorithm: is this release worth even looking at.

    Never treats a mutable ``latest`` tag as trust; the caller is expected to
    have already enumerated concrete tagged GitHub Releases and to call this
    once per candidate.
    """
    if auto_update_mode not in AUTO_UPDATE_MODES:
        return Decision(EligibilityVerdict.REFUSED_AUTO_UPDATE_OFF,
                        f"unknown AUTO_UPDATE mode {auto_update_mode!r}")
    if auto_update_mode == "off":
        return Decision(EligibilityVerdict.REFUSED_AUTO_UPDATE_OFF)

    if auto_update_mode in ("stable", "notify") and channel not in ("stable", "security"):
        return Decision(EligibilityVerdict.REFUSED_WRONG_CHANNEL,
                        f"channel {channel!r} not enabled by AUTO_UPDATE={auto_update_mode}")
    if auto_update_mode == "security" and channel != "security":
        return Decision(EligibilityVerdict.REFUSED_WRONG_CHANNEL,
                        f"AUTO_UPDATE=security only considers the security channel, "
                        f"got {channel!r}")

    if channel == "stable" and candidate_is_prerelease:
        return Decision(EligibilityVerdict.REFUSED_PRERELEASE_ON_STABLE_CHANNEL)

    try:
        current = Version(current_version)
        candidate = Version(candidate_version)
    except InvalidVersion as exc:
        return Decision(EligibilityVerdict.REFUSED_OLDER_VERSION,
                        f"unparseable version: {exc}")

    if candidate == current:
        return Decision(EligibilityVerdict.IGNORED_SAME_VERSION)
    if candidate < current:
        return Decision(EligibilityVerdict.REFUSED_OLDER_VERSION)
    return Decision(EligibilityVerdict.ELIGIBLE)


def evaluate_verification(*, manifest: Optional[dict], signature_valid: bool,
                          downloaded_artifact_digest: Optional[str],
                          host: HostFacts, safe_core_certified_commits: frozenset,
                          github_reachable: bool = True,
                          safe_core_certification_digests: Optional[dict] = None) -> Decision:
    """Steps 6-12: verify everything about a candidate before it may ever be
    pre-pulled or presented to an operator as a real candidate.

    ``safe_core_certified_commits`` is the set of Core commits this
    installation's verified safe-Core policy currently recognizes as certified.
    When ``safe_core_certification_digests`` is supplied, the signed ElectrumX
    manifest must also name the exact certification report digest recorded by
    that policy; a matching commit alone is not enough.
    """
    if not github_reachable:
        return Decision(VerificationVerdict.REFUSED_GITHUB_UNREACHABLE)
    if manifest is None:
        return Decision(VerificationVerdict.REFUSED_NO_MANIFEST)
    if not signature_valid:
        return Decision(VerificationVerdict.REFUSED_INVALID_SIGNATURE)
    if not downloaded_artifact_digest or \
            downloaded_artifact_digest != manifest.get("artifactDigest"):
        return Decision(VerificationVerdict.REFUSED_ARTIFACT_DIGEST_MISMATCH)
    if manifest.get("architecture") != host.architecture:
        return Decision(VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH)

    commit = manifest.get("coreCommit")
    if not commit or commit not in safe_core_certified_commits:
        return Decision(VerificationVerdict.REFUSED_CORE_IDENTITY_MISMATCH)

    certification_digest = manifest.get("certificationReportDigest")
    if not certification_digest:
        return Decision(VerificationVerdict.REFUSED_MISSING_CERTIFICATION_DIGEST)
    if safe_core_certification_digests is not None:
        expected_digest = safe_core_certification_digests.get(commit)
        if not expected_digest or certification_digest != expected_digest:
            return Decision(
                VerificationVerdict.REFUSED_CERTIFICATION_DIGEST_MISMATCH,
                "signed update manifest certification digest does not match "
                "the verified safe-Core policy",
            )

    db_compat = manifest.get("dbCompatibility")
    if not isinstance(db_compat, dict) or "schemaVersion" not in db_compat:
        return Decision(VerificationVerdict.REFUSED_UNKNOWN_DB_COMPATIBILITY)

    return Decision(VerificationVerdict.VERIFIED)


def evaluate_apply(*, pending_candidate: Optional[dict],
                   pending_verdict: Optional[EligibilityVerdict],
                   pending_verification: Optional[VerificationVerdict],
                   approve_consensus_change: bool) -> Decision:
    """Gate for ``electrumx-update apply``: install is always an explicit,
    separate operator action, never a consequence of check/status/show, of a
    timer, of a restart, or of a reboot.
    """
    if pending_candidate is None or \
            pending_verdict != EligibilityVerdict.ELIGIBLE or \
            pending_verification != VerificationVerdict.VERIFIED:
        return Decision(ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE)

    if pending_candidate.get("consensusImpact") and not approve_consensus_change:
        return Decision(ApplyVerdict.REFUSED_CONSENSUS_CHANGE_NOT_APPROVED)

    return Decision(ApplyVerdict.ALLOWED)


def evaluate_health(result: HealthGateResult, *, rollback_safe: bool) -> Decision:
    """Steps 17-19: confirm, or refuse to blindly roll back across an
    irreversible migration.
    """
    if result.all_pass():
        return Decision(HealthVerdict.PROMOTE_TO_CURRENT)
    if rollback_safe:
        return Decision(HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD,
                        f"failed gates: {', '.join(result.failures())}")
    return Decision(HealthVerdict.STUCK_NO_BLIND_ROLLBACK,
                    f"failed gates: {', '.join(result.failures())}; "
                    f"rollbackSafe=false, operator intervention required")
