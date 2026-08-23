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

from electrumx_core_safety import artifact_revision

AUTO_UPDATE_MODES = ("off", "notify", "stable", "security")
EligibilityVerdict = artifact_revision.EligibilityVerdict


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
    # Current production ElectrumX DB schema. Existing pre-updater installs of
    # this project are schema 1; new installers persist the schema explicitly.
    current_db_schema: int = 1
    # Release identity comes from the same authenticated operational state as
    # current_electrumx_version. Missing or malformed values are preserved and
    # refused by the canonical artifact ordering function; they are never
    # silently coerced to revision 0 or an empty digest.
    current_artifact_revision: object = None
    current_artifact_digest: object = None
    current_provenance_digest: object = None


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
                         host: HostFacts, candidate_version: str,
                         candidate_is_prerelease: bool,
                         candidate_revision: object,
                         candidate_artifact_digest: object,
                         candidate_provenance_digest: object) -> Decision:
    """Step 1-5 of the update algorithm: is this release worth even looking at.

    Never treats a mutable ``latest`` tag as trust; the caller is expected to
    have already enumerated concrete tagged GitHub Releases and to call this
    once per candidate. Version/revision/digest ordering is delegated to the
    single canonical implementation in ``artifact_revision``.
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

    ordering = artifact_revision.compare_revision(
        {
            "electrumxVersion": host.current_electrumx_version,
            "artifact_revision": host.current_artifact_revision,
            "artifactDigest": host.current_artifact_digest,
            "provenanceDigest": host.current_provenance_digest,
        },
        {
            "electrumxVersion": candidate_version,
            "artifact_revision": candidate_revision,
            "artifactDigest": candidate_artifact_digest,
            "provenanceDigest": candidate_provenance_digest,
        },
    )
    return Decision(ordering.verdict, ordering.reason)


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
    if not isinstance(migration.get("reversible"), bool):
        return False
    return True


def evaluate_verification(*, manifest: Optional[dict], signature_valid: bool,
                          downloaded_artifact_digest: Optional[str],
                          host: HostFacts, safe_core_certified_commits: frozenset,
                          github_reachable: bool = True,
                          safe_core_certification_digests: Optional[dict] = None,
                          verified_core_policy_version: Optional[int] = None) -> Decision:
    """Steps 6-12: verify everything about a candidate before it may ever be
    pre-pulled or presented to an operator as a real candidate.

    ``safe_core_certified_commits`` is the set of Core commits this
    installation's verified safe-Core policy currently recognizes as certified.
    When ``safe_core_certification_digests`` is supplied, the signed ElectrumX
    manifest must also name the exact certification report digest recorded by
    that policy; a matching commit alone is not enough.

    ``safeCorePolicyVersion`` in the release manifest is the policy version
    that existed when the release was prepared. A node may have a newer policy,
    but it may never accept a release that requires a policy newer than the one
    it has actually verified.
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
    architecture = manifest.get("architecture")
    # Signed manifests may declare a multi-architecture target set such as
    # "linux/amd64,linux/arm64" (the same canonical form the manifest
    # builder validates).  Accept it only when this host's platform is one
    # of the declared targets; anything else remains a mismatch refusal.
    if isinstance(architecture, str):
        targets = tuple(item.strip() for item in architecture.split(",")
                        if item.strip())
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
                "signed update manifest certification digest does not match "
                "the verified safe-Core policy",
            )

    if not _db_compatibility_known(host, manifest):
        return Decision(
            VerificationVerdict.REFUSED_UNKNOWN_DB_COMPATIBILITY,
            "candidate DB schema/migration is not compatible with the installed schema")

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

    manifest = pending_candidate.get("manifest") or {}
    if manifest.get("consensusImpact") and not approve_consensus_change:
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
