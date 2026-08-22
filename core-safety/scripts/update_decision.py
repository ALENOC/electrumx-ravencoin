# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Pure decision logic for the ElectrumX node self-update system."""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Optional

from packaging.version import InvalidVersion, Version

AUTO_UPDATE_MODES = ("off", "notify", "stable", "security")


class EligibilityVerdict(enum.Enum):
    ELIGIBLE = "ELIGIBLE"
    IGNORED_SAME_VERSION = "IGNORED_SAME_VERSION"
    IGNORED_SAME_ARTIFACT = "IGNORED_SAME_ARTIFACT"
    REFUSED_OLDER_VERSION = "REFUSED_OLDER_VERSION"
    REFUSED_OLDER_REVISION = "REFUSED_OLDER_REVISION"
    REFUSED_ARTIFACT_EQUIVOCATION = "REFUSED_ARTIFACT_EQUIVOCATION"
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
    REFUSED_CORE_POLICY_VERSION_MISMATCH = "REFUSED_CORE_POLICY_VERSION_MISMATCH"
    REFUSED_UPDATER_TOO_OLD = "REFUSED_UPDATER_TOO_OLD"
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
    current_db_schema: int = 1
    current_artifact_revision: int = 0
    current_artifact_digest: Optional[str] = None


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


def _revision_value(value, label: str) -> Optional[int]:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def evaluate_eligibility(*, auto_update_mode: str, channel: str,
                         current_version: str, candidate_version: str,
                         candidate_is_prerelease: bool,
                         current_revision: Optional[int] = None,
                         candidate_revision: Optional[int] = None,
                         current_artifact_digest: Optional[str] = None,
                         candidate_artifact_digest: Optional[str] = None) -> Decision:
    """Classify version+revision ordering; legacy callers retain version-only semantics."""
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
                        f"AUTO_UPDATE=security only considers the security channel, got {channel!r}")
    if channel == "stable" and candidate_is_prerelease:
        return Decision(EligibilityVerdict.REFUSED_PRERELEASE_ON_STABLE_CHANNEL)

    try:
        current = Version(current_version)
        candidate = Version(candidate_version)
    except InvalidVersion as exc:
        return Decision(EligibilityVerdict.REFUSED_OLDER_VERSION,
                        f"unparseable version: {exc}")

    if candidate < current:
        return Decision(EligibilityVerdict.REFUSED_OLDER_VERSION)
    if candidate > current:
        return Decision(EligibilityVerdict.ELIGIBLE)

    # Preserve the version-1 API for tests/callers that have not supplied
    # authenticated manifest revision data yet.
    if current_revision is None or candidate_revision is None:
        return Decision(EligibilityVerdict.IGNORED_SAME_VERSION)
    try:
        old_revision = _revision_value(current_revision, "current_revision")
        new_revision = _revision_value(candidate_revision, "candidate_revision")
    except ValueError as exc:
        return Decision(EligibilityVerdict.REFUSED_OLDER_REVISION, str(exc))

    if new_revision < old_revision:
        return Decision(EligibilityVerdict.REFUSED_OLDER_REVISION)
    if new_revision > old_revision:
        return Decision(EligibilityVerdict.ELIGIBLE)
    if current_artifact_digest and candidate_artifact_digest and \
            current_artifact_digest != candidate_artifact_digest:
        return Decision(
            EligibilityVerdict.REFUSED_ARTIFACT_EQUIVOCATION,
            "same version/revision is bound to a different artifact digest")
    return Decision(EligibilityVerdict.IGNORED_SAME_ARTIFACT)


def _updater_version_compatible(host: HostFacts, manifest: dict) -> bool:
    required = manifest.get("requiredUpdaterVersion")
    if not isinstance(required, str) or not required:
        return False
    try:
        return Version(host.installed_updater_version) >= Version(required)
    except InvalidVersion:
        return False


def _db_compatibility_known(host: HostFacts, manifest: dict) -> bool:
    db_compat = manifest.get("dbCompatibility")
    if not isinstance(db_compat, dict):
        return False
    candidate_schema = db_compat.get("schemaVersion")
    if not isinstance(candidate_schema, int) or isinstance(candidate_schema, bool) or \
            candidate_schema < 1:
        return False
    current_schema = host.current_db_schema
    if not isinstance(current_schema, int) or isinstance(current_schema, bool) or \
            current_schema < 1:
        return False
    if candidate_schema == current_schema:
        return True
    migration = db_compat.get("migration")
    if not isinstance(migration, dict):
        return False
    from_schema = migration.get("fromSchema")
    to_schema = migration.get("toSchema")
    if from_schema != current_schema or to_schema != candidate_schema:
        return False
    return isinstance(migration.get("reversible"), bool)


def evaluate_verification(*, manifest: Optional[dict], signature_valid: bool,
                          downloaded_artifact_digest: Optional[str],
                          host: HostFacts, safe_core_certified_commits: frozenset,
                          github_reachable: bool = True,
                          safe_core_certification_digests: Optional[dict] = None,
                          verified_core_policy_version: Optional[int] = None) -> Decision:
    if not github_reachable:
        return Decision(VerificationVerdict.REFUSED_GITHUB_UNREACHABLE)
    if manifest is None:
        return Decision(VerificationVerdict.REFUSED_NO_MANIFEST)
    if not signature_valid:
        return Decision(VerificationVerdict.REFUSED_INVALID_SIGNATURE)
    if not downloaded_artifact_digest or \
            downloaded_artifact_digest != manifest.get("artifactDigest"):
        return Decision(VerificationVerdict.REFUSED_ARTIFACT_DIGEST_MISMATCH)

    architecture = manifest.get("architecture")
    if isinstance(architecture, str):
        targets = tuple(item.strip() for item in architecture.split(",") if item.strip())
    else:
        targets = ()
    if host.architecture not in targets:
        return Decision(VerificationVerdict.REFUSED_ARCHITECTURE_MISMATCH)
    if not _updater_version_compatible(host, manifest):
        return Decision(
            VerificationVerdict.REFUSED_UPDATER_TOO_OLD,
            "installed updater does not satisfy requiredUpdaterVersion")

    manifest_policy_version = manifest.get("safeCorePolicyVersion")
    if not isinstance(manifest_policy_version, int) or \
            isinstance(manifest_policy_version, bool) or manifest_policy_version < 1:
        return Decision(VerificationVerdict.REFUSED_CORE_POLICY_VERSION_MISMATCH,
                        "manifest safeCorePolicyVersion is malformed")
    if verified_core_policy_version is not None:
        if not isinstance(verified_core_policy_version, int) or \
                isinstance(verified_core_policy_version, bool) or \
                verified_core_policy_version < manifest_policy_version:
            return Decision(
                VerificationVerdict.REFUSED_CORE_POLICY_VERSION_MISMATCH,
                "release requires a newer safe-Core policy than this node has verified")

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
                "signed update manifest certification digest does not match the verified safe-Core policy")
    if not _db_compatibility_known(host, manifest):
        return Decision(
            VerificationVerdict.REFUSED_UNKNOWN_DB_COMPATIBILITY,
            "candidate DB schema/migration is not compatible with the installed schema")
    return Decision(VerificationVerdict.VERIFIED)


def evaluate_apply(*, pending_candidate: Optional[dict],
                   pending_verdict: Optional[EligibilityVerdict],
                   pending_verification: Optional[VerificationVerdict],
                   approve_consensus_change: bool) -> Decision:
    if pending_candidate is None or \
            pending_verdict != EligibilityVerdict.ELIGIBLE or \
            pending_verification != VerificationVerdict.VERIFIED:
        return Decision(ApplyVerdict.REFUSED_NO_VERIFIED_CANDIDATE)
    manifest = pending_candidate.get("manifest") or {}
    if manifest.get("consensusImpact") and not approve_consensus_change:
        return Decision(ApplyVerdict.REFUSED_CONSENSUS_CHANGE_NOT_APPROVED)
    return Decision(ApplyVerdict.ALLOWED)


def evaluate_health(result: HealthGateResult, *, rollback_safe: bool) -> Decision:
    if result.all_pass():
        return Decision(HealthVerdict.PROMOTE_TO_CURRENT)
    if rollback_safe:
        return Decision(HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD,
                        f"failed gates: {', '.join(result.failures())}")
    return Decision(HealthVerdict.STUCK_NO_BLIND_ROLLBACK,
                    f"failed gates: {', '.join(result.failures())}; "
                    f"rollbackSafe=false, operator intervention required")
