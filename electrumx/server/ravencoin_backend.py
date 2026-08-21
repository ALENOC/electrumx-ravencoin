# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Ravencoin Core compatibility and incident-safety checks.

This module intentionally keeps ElectrumX's own software version separate from the
identity and health of the Ravencoin Core daemon behind it.
"""

from dataclasses import dataclass
import re
import time

from electrumx.lib.hash import hash_to_hex_str


MINIMUM_SAFE_CORE = (4, 8, 0, 0)
MINIMUM_SAFE_CORE_STRING = "4.8.0"
INCIDENT_CHECKPOINT_HEIGHT = 4_487_775
INCIDENT_CHECKPOINT_HASH = (
    "000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd"
)

#: The safety profile this deployment claims its backend was certified against.
#: A profile describes required behaviour; a version number does not.
SAFETY_PROFILE = "rvn-consensus-2026-08-v1"

#: Only the official RavenProject repository is an eligible Core source. Being
#: listed here makes a release a candidate for certification, never trusted by
#: repository name or version alone.
KNOWN_SOURCE_REPOSITORIES = ("RavenProject/Ravencoin",)


class IdentityEvidence:
    """How much is really known about which Core binary is running.

    These labels exist so a wallet is never shown a self-reported commit that
    looks cryptographically proven.  Only the first means this deployment pinned
    and checked the artifact itself.
    """

    #: This deployment built or pinned the Core artifact and verified its digest
    #: at image build time, so the identity comes from trusted build config.
    BUILD_VERIFIED = "BUILD_IDENTITY_VERIFIED"
    #: An operator configured the identity by hand.  Plausible, unproven.
    ATTESTED = "BUILD_IDENTITY_ATTESTED"
    #: Only the daemon's own version and subversion strings are known.
    VERSION_ONLY = "VERSION_ONLY"
    #: Nothing usable was reported.
    UNKNOWN = "UNKNOWN"

    ALL = (BUILD_VERIFIED, ATTESTED, VERSION_ONLY, UNKNOWN)


@dataclass(frozen=True)
class BackendIdentity:
    """Where the running Ravencoin Core is claimed to have come from.

    Built from deployment configuration, never from a value the daemon echoes
    back at runtime: a compromised daemon must not be able to choose its own
    identity.
    """

    repository: str = None
    tag: str = None
    commit: str = None
    artifact_sha256: str = None
    evidence: str = IdentityEvidence.VERSION_ONLY

    @classmethod
    def from_config(cls, repository=None, tag=None, commit=None,
                    artifact_sha256=None, evidence=None):
        repository = (repository or "").strip() or None
        tag = (tag or "").strip() or None
        commit = (commit or "").strip().lower() or None
        artifact_sha256 = (artifact_sha256 or "").strip().lower() or None
        declared = (evidence or "").strip().upper() or None

        if commit is not None and not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ValueError("configured Ravencoin source commit is malformed")
        if artifact_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}",
                                                            artifact_sha256):
            raise ValueError("configured Ravencoin artifact digest is malformed")
        if repository is not None and repository not in KNOWN_SOURCE_REPOSITORIES:
            raise ValueError(
                f"configured Ravencoin source repository {repository!r} is not one of "
                f"{', '.join(KNOWN_SOURCE_REPOSITORIES)}"
            )
        if declared is not None and declared not in IdentityEvidence.ALL:
            raise ValueError(f"unknown identity evidence level {declared!r}")

        if repository is None or commit is None:
            # Without an identity there is nothing to attest to, whatever the
            # operator configured.
            return cls(evidence=IdentityEvidence.VERSION_ONLY)
        if declared == IdentityEvidence.BUILD_VERIFIED and artifact_sha256 is None:
            raise ValueError(
                "BUILD_IDENTITY_VERIFIED requires the pinned artifact digest, "
                "otherwise the claim cannot be checked at image build time"
            )
        evidence = declared or IdentityEvidence.ATTESTED
        return cls(repository=repository, tag=tag, commit=commit,
                   artifact_sha256=artifact_sha256, evidence=evidence)

    def public_dict(self):
        if self.repository is None or self.commit is None:
            return {"evidence": self.evidence}
        return {
            "evidence": self.evidence,
            "sourceRepository": self.repository,
            "sourceTag": self.tag,
            "sourceCommit": self.commit,
            "artifactSha256": self.artifact_sha256,
        }


class RavencoinBackendError(RuntimeError):
    """Base class for a backend that cannot safely serve Ravencoin data."""


class UnsafeRavencoinCoreError(RavencoinBackendError):
    """The daemon is the wrong network, vulnerable, or violates the checkpoint."""


class RavencoinDatabaseMismatchError(RavencoinBackendError):
    """The ElectrumX database does not belong to the daemon's canonical chain."""


def parse_core_version(version):
    """Decode Core's integer client version without lexicographic comparisons."""
    if isinstance(version, bool) or not isinstance(version, int) or version < 0:
        raise ValueError(f"invalid Ravencoin Core version: {version!r}")
    major, remainder = divmod(version, 1_000_000)
    minor, remainder = divmod(remainder, 10_000)
    patch, build = divmod(remainder, 100)
    return major, minor, patch, build


def core_version_string(version_tuple):
    major, minor, patch, build = version_tuple
    base = f"{major}.{minor}.{patch}"
    return f"{base}.{build}" if build else base


def expected_daemon_chain(electrum_network):
    mapping = {"mainnet": "main", "testnet": "test", "regtest": "regtest"}
    try:
        return mapping[electrum_network]
    except KeyError as exc:
        raise ValueError(f"unsupported Ravencoin network: {electrum_network!r}") from exc


@dataclass(frozen=True)
class RavencoinBackendStatus:
    version_number: int
    version_tuple: tuple
    subversion: str
    network: str
    blocks: int
    headers: int
    initial_block_download: object
    version_safe: bool
    network_matches: bool
    synchronized: bool
    checkpoint_known: bool
    checkpoint_verified: bool
    observed_at: int

    @property
    def core_safe(self):
        return self.version_safe and self.network_matches and self.checkpoint_known

    def public_dict(self, server_version, identity=None):
        """Return only non-secret backend evidence suitable for public RPC clients.

        ``identity`` describes where the running Core is claimed to come from.  It
        is deployment configuration, and its evidence level says plainly how much
        that claim is worth, so a wallet can weigh it against its signed policy.
        """
        identity = identity or BackendIdentity()
        return {
            "server": "ElectrumX-RVN",
            "serverVersion": server_version,
            "backend": {
                "name": "Ravencoin Core",
                "version": core_version_string(self.version_tuple),
                "versionNumber": self.version_number,
                "subversion": self.subversion,
                "network": self.network,
                "blocks": self.blocks,
                "headers": self.headers,
                "initialBlockDownload": self.initial_block_download,
                "identity": identity.public_dict(),
            },
            "compatibility": {
                # Kept for older clients: it is the floor below which nothing is
                # ever safe.  It is not a statement that anything above it is.
                "minimumSafeCore": MINIMUM_SAFE_CORE_STRING,
                # The profile this deployment claims its backend satisfies.  A
                # wallet decides trust from its own signed policy, not from here.
                "safetyProfile": SAFETY_PROFILE,
                "identityEvidence": identity.evidence,
                "coreSafe": self.core_safe,
                "networkMatches": self.network_matches,
                "backendSynchronized": self.synchronized,
                "kawpowHeightValidation": True,
                # Only a real comparison against a backend that already holds the
                # checkpoint height counts as verified.  A backend still below it
                # must not publish evidence it cannot have.
                "checkpoint4487775": self.checkpoint_verified,
            },
            "observedAt": self.observed_at,
        }


def evaluate_backend(network_info, blockchain_info, electrum_network,
                     checkpoint_hash=None, observed_at=None):
    """Build structured compatibility evidence from sanitized Core RPC results."""
    version_number = network_info.get("version")
    version_tuple = parse_core_version(version_number)
    subversion = network_info.get("subversion")
    if not isinstance(subversion, str):
        raise ValueError("Ravencoin Core subversion is missing or malformed")

    network = blockchain_info.get("chain")
    blocks = blockchain_info.get("blocks")
    headers = blockchain_info.get("headers")
    ibd = blockchain_info.get("initialblockdownload")
    if not isinstance(network, str):
        raise ValueError("Ravencoin Core network is missing or malformed")
    if isinstance(blocks, bool) or not isinstance(blocks, int) or blocks < 0:
        raise ValueError("Ravencoin Core block height is missing or malformed")
    if isinstance(headers, bool) or not isinstance(headers, int) or headers < blocks:
        raise ValueError("Ravencoin Core header height is missing or malformed")
    if ibd not in (True, False, None):
        raise ValueError("Ravencoin Core IBD state is malformed")

    network_matches = network == expected_daemon_chain(electrum_network)
    version_safe = version_tuple >= MINIMUM_SAFE_CORE
    checkpoint_required = network == "main" and blocks >= INCIDENT_CHECKPOINT_HEIGHT
    checkpoint_matches = checkpoint_hash == INCIDENT_CHECKPOINT_HASH
    # A backend below the checkpoint height cannot violate it yet, so startup is
    # not blocked; it also cannot prove anything, so it is not verified either.
    checkpoint_known = not checkpoint_required or checkpoint_matches
    checkpoint_verified = checkpoint_required and checkpoint_matches
    synchronized = ibd is not True and blocks == headers

    return RavencoinBackendStatus(
        version_number=version_number,
        version_tuple=version_tuple,
        subversion=subversion,
        network=network,
        blocks=blocks,
        headers=headers,
        initial_block_download=ibd,
        version_safe=version_safe,
        network_matches=network_matches,
        synchronized=synchronized,
        checkpoint_known=checkpoint_known,
        checkpoint_verified=checkpoint_verified,
        observed_at=int(time.time() if observed_at is None else observed_at),
    )


def enforce_backend_policy(status, allow_unsafe=False):
    """Fail closed unless an explicit development override was configured."""
    if status.core_safe:
        return
    problems = []
    if not status.version_safe:
        problems.append(
            f"Core {core_version_string(status.version_tuple)} is below "
            f"{MINIMUM_SAFE_CORE_STRING}"
        )
    if not status.network_matches:
        problems.append(f"daemon network {status.network!r} does not match server")
    if not status.checkpoint_known:
        problems.append(f"checkpoint {INCIDENT_CHECKPOINT_HEIGHT} does not match")
    message = "unsafe Ravencoin backend: " + "; ".join(problems)
    if not allow_unsafe:
        raise UnsafeRavencoinCoreError(message)
    return message


async def verify_database_chain(db, daemon):
    """Refuse a stale/forked ElectrumX database instead of serving it silently.

    The database must already be open: its on-disk state is the evidence being
    checked.  Verifying an unopened database would silently pass, so this fails
    closed with a diagnosable error instead.
    """
    if getattr(db, "state", None) is None:
        raise RavencoinDatabaseMismatchError(
            "ElectrumX database state is unavailable; open the database before "
            "verifying it against Ravencoin Core"
        )

    height = db.state.height
    if height < 0:
        return

    core_tip_at_height = (await daemon.block_hex_hashes(height, 1))[0]
    database_tip = hash_to_hex_str(db.state.tip)
    if database_tip != core_tip_at_height:
        raise RavencoinDatabaseMismatchError(
            f"ElectrumX DB tip {database_tip} at {height} is not Core's "
            f"canonical hash {core_tip_at_height}; rewind or rebuild before serving"
        )

    if height >= INCIDENT_CHECKPOINT_HEIGHT:
        stored_hash = hash_to_hex_str(
            (await db.fs_block_hashes(INCIDENT_CHECKPOINT_HEIGHT, 1))[0]
        )
        if stored_hash != INCIDENT_CHECKPOINT_HASH:
            raise RavencoinDatabaseMismatchError(
                f"ElectrumX DB checkpoint {INCIDENT_CHECKPOINT_HEIGHT} is {stored_hash}, "
                f"expected {INCIDENT_CHECKPOINT_HASH}; rewind or rebuild before serving"
            )
