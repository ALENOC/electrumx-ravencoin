from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiorpcx import RPCError

import electrumx
from electrumx.lib.coins import Ravencoin
from electrumx.server.ravencoin_backend import (
    INCIDENT_CHECKPOINT_HASH,
    BackendIdentity,
    evaluate_backend,
)
from electrumx.server.session import ElectrumX


def backend_status(version=4_080_000):
    return evaluate_backend(
        {'version': version, 'subversion': f'/Ravencoin:{version}/'},
        {
            'chain': 'main',
            'blocks': 4_494_000,
            'headers': 4_494_000,
            'initialblockdownload': False,
        },
        'mainnet',
        INCIDENT_CHECKPOINT_HASH,
        observed_at=123,
    )


def fake_session(status):
    daemon = SimpleNamespace(
        refresh_ravencoin_backend_status=AsyncMock(return_value=status)
    )
    return SimpleNamespace(
        env=SimpleNamespace(
            coin=Ravencoin,
            ravencoin_backend_info_max_age=5,
            allow_unsafe_ravencoin_core=False,
            ravencoin_backend_identity=BackendIdentity.from_config(
                repository="2miners/Ravencoin",
                tag="v4.8.0",
                commit="b60f50e04f1fba425b28804e61be2694faaf3469",
                artifact_sha256=(
                    "966cf8978af1f2e3f36e9733d011eb92f4116750af6f8e77c5a5ced525577c4c"
                ),
                evidence="BUILD_IDENTITY_VERIFIED",
            ),
        ),
        session_mgr=SimpleNamespace(
            daemon=daemon,
            broadcast_transaction=AsyncMock(return_value='txid'),
        ),
        bump_cost=Mock(),
        logger=Mock(),
        txs_sent=0,
    )


def test_server_features_advertise_optional_ravencoin_capability():
    env = SimpleNamespace(report_services=[], coin=Ravencoin)
    features = ElectrumX.server_features(env)
    assert features['server_version'] == electrumx.version
    assert features['ravencoin']['backend_info'] is True
    assert features['ravencoin']['kawpow_height_validation'] is True
    assert features['ravencoin']['checkpoint']['height'] == 4_487_775


@pytest.mark.asyncio
async def test_backend_rpc_returns_sanitized_fresh_evidence():
    session = fake_session(backend_status())
    result = await ElectrumX.ravencoin_backend(session)
    assert result['serverVersion'] == electrumx.version
    assert result['backend']['version'] == '4.8.0'
    assert result['compatibility']['coreSafe'] is True
    assert result['observedAt'] == 123
    assert set(result['backend']) == {
        'name', 'version', 'versionNumber', 'subversion', 'network',
        'blocks', 'headers', 'initialBlockDownload', 'identity',
    }
    identity = result['backend']['identity']
    assert identity == {
        'evidence': 'BUILD_IDENTITY_VERIFIED',
        'sourceRepository': '2miners/Ravencoin',
        'sourceTag': 'v4.8.0',
        'sourceCommit': 'b60f50e04f1fba425b28804e61be2694faaf3469',
        'artifactSha256':
            '966cf8978af1f2e3f36e9733d011eb92f4116750af6f8e77c5a5ced525577c4c',
    }
    assert result['compatibility']['safetyProfile'] == 'rvn-consensus-2026-08-v1'
    assert result['compatibility']['identityEvidence'] == 'BUILD_IDENTITY_VERIFIED'
    # Nothing secret may ride along with the identity.
    serialized = repr(result).lower()
    for forbidden in ('rpcuser', 'rpcpassword', 'secret', 'token', '/run/',
                      'privkey'):
        assert forbidden not in serialized


@pytest.mark.asyncio
async def test_broadcast_rechecks_backend_and_blocks_downgrade():
    session = fake_session(backend_status(4_070_000))
    with pytest.raises(RPCError, match='unsafe Ravencoin backend'):
        await ElectrumX.transaction_broadcast(session, 'deadbeef')
    session.session_mgr.daemon.refresh_ravencoin_backend_status.assert_awaited_once_with(
        'mainnet', max_age=0
    )
    session.session_mgr.broadcast_transaction.assert_not_awaited()
