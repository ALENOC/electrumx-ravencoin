# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Ravencoin Core compatibility and incident-safety checks.

This module intentionally keeps ElectrumX's own software version separate from the
identity and health of the Ravencoin Core daemon behind it.
"""

from dataclasses import dataclass
import time

from electrumx.lib.hash import hash_to_hex_str


MINIMUM_SAFE_CORE = (4, 8, 0, 0)
MINIMUM_SAFE_CORE_STRING = "4.8.0"
INCIDENT_CHECKPOINT_HEIGHT = 4_487_775
INCIDENT_CHECKPOINT_HASH = (
    "000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd"
)


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
    observed_at: int

    @property
    def core_safe(self):
        return self.version_safe and self.network_matches and self.checkpoint_known

    def public_dict(self, server_version):
        """Return only non-secret backend evidence suitable for public RPC clients."""
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
            },
            "compatibility": {
                "minimumSafeCore": MINIMUM_SAFE_CORE_STRING,
                "coreSafe": self.core_safe,
                "networkMatches": self.network_matches,
                "backendSynchronized": self.synchronized,
                "kawpowHeightValidation": True,
                "checkpoint4487775": self.checkpoint_known,
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
    checkpoint_known = (not checkpoint_required or
                        checkpoint_hash == INCIDENT_CHECKPOINT_HASH)
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
    """Refuse a stale/forked ElectrumX database instead of serving it silently."""
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
