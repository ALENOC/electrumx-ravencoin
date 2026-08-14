import pytest

from electrumx.lib.hash import hex_str_to_hash
from electrumx.server.ravencoin_backend import (
    INCIDENT_CHECKPOINT_HASH,
    MINIMUM_SAFE_CORE,
    RavencoinDatabaseMismatchError,
    UnsafeRavencoinCoreError,
    enforce_backend_policy,
    evaluate_backend,
    parse_core_version,
    verify_database_chain,
)


def network_info(version=4_080_000, subversion="/Ravencoin:4.8.0/"):
    return {"version": version, "subversion": subversion}


def blockchain_info(chain="main", blocks=4_494_000, headers=4_494_000, ibd=False):
    return {
        "chain": chain,
        "blocks": blocks,
        "headers": headers,
        "initialblockdownload": ibd,
    }


@pytest.mark.parametrize("version", [4_060_000, 4_060_100, 4_060_101, 4_070_000])
def test_known_unsafe_core_versions_are_rejected(version):
    status = evaluate_backend(
        network_info(version), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=100,
    )
    assert not status.version_safe
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(status)


@pytest.mark.parametrize("version, parsed", [
    (4_080_000, (4, 8, 0, 0)),
    (4_080_100, (4, 8, 1, 0)),
    (4_090_000, (4, 9, 0, 0)),
    (5_000_000, (5, 0, 0, 0)),
])
def test_safe_and_future_structural_versions_are_accepted(version, parsed):
    assert parse_core_version(version) == parsed
    assert parsed >= MINIMUM_SAFE_CORE
    status = evaluate_backend(
        network_info(version), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=100,
    )
    assert status.core_safe
    assert enforce_backend_policy(status) is None


@pytest.mark.parametrize("version", [None, "4.8.0", True, -1])
def test_malformed_versions_fail_closed(version):
    with pytest.raises(ValueError):
        evaluate_backend(
            network_info(version), blockchain_info(), "mainnet",
            INCIDENT_CHECKPOINT_HASH,
        )


def test_wrong_network_and_checkpoint_are_rejected():
    wrong_network = evaluate_backend(
        network_info(), blockchain_info(chain="test"), "mainnet", None,
    )
    assert not wrong_network.network_matches
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(wrong_network)

    wrong_checkpoint = evaluate_backend(
        network_info(), blockchain_info(), "mainnet", "00" * 32,
    )
    assert not wrong_checkpoint.checkpoint_known
    with pytest.raises(UnsafeRavencoinCoreError):
        enforce_backend_policy(wrong_checkpoint)


def test_null_ibd_is_unknown_but_equal_heights_are_synchronized():
    status = evaluate_backend(
        network_info(), blockchain_info(ibd=None), "mainnet",
        INCIDENT_CHECKPOINT_HASH, observed_at=123,
    )
    assert status.initial_block_download is None
    assert status.synchronized
    assert status.public_dict("ElectrumX-RVN 1.13.0.dev1")["observedAt"] == 123


def test_explicit_unsafe_override_is_prominent_not_silent():
    status = evaluate_backend(
        network_info(4_070_000), blockchain_info(), "mainnet",
        INCIDENT_CHECKPOINT_HASH,
    )
    warning = enforce_backend_policy(status, allow_unsafe=True)
    assert "unsafe Ravencoin backend" in warning
    assert "below 4.8.0" in warning


class FakeDaemon:
    def __init__(self, core_hash):
        self.core_hash = core_hash

    async def block_hex_hashes(self, height, count):
        assert count == 1
        return [self.core_hash]


class FakeDB:
    class State:
        height = 10
        tip = hex_str_to_hash("11" * 32)

    state = State()


@pytest.mark.asyncio
async def test_database_tip_mismatch_refuses_to_serve():
    with pytest.raises(RavencoinDatabaseMismatchError, match="rewind or rebuild"):
        await verify_database_chain(FakeDB(), FakeDaemon("22" * 32))
