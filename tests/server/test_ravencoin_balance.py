from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiorpcx import RPCError

from electrumx.server.db import UTXO
from electrumx.server.session import (
    ElectrumX, MAX_ASSET_FILTER_LENGTH, check_asset, check_h160,
)


def fake_balance_session(all_utxos, balance_delta):
    return SimpleNamespace(
        db=SimpleNamespace(all_utxos=AsyncMock(return_value=all_utxos)),
        mempool=SimpleNamespace(
            balance_delta=AsyncMock(return_value=balance_delta),
            unordered_UTXOs=AsyncMock(return_value=[]),
            potential_spends=AsyncMock(return_value=set()),
        ),
        bump_cost=Mock(),
    )


@pytest.mark.asyncio
async def test_get_balance_native_rvn_uses_exact_key_not_first_key():
    # RVN-02 session-layer surface: even if the balance_delta dict ever
    # ends up holding more than one entry, get_balance must select the
    # exact key that was requested rather than "whichever key is first".
    utxos = [UTXO(1, 0, b'\x00' * 32, 10, None, 1000)]
    # None (native RVN) is deliberately NOT first in insertion order.
    poisoned_unconfirmed = {'SOMEOTHERASSET': 999, None: 1234}
    session = fake_balance_session(utxos, poisoned_unconfirmed)

    result = await ElectrumX.get_balance(session, b'\x00' * 11, False)

    assert result == {'confirmed': 1000, 'unconfirmed': 1234}


@pytest.mark.asyncio
async def test_get_balance_single_asset_uses_exact_key_not_first_key():
    utxos = [UTXO(1, 0, b'\x00' * 32, 10, 'FOO', 500)]
    poisoned_unconfirmed = {None: 999, 'FOO': 321}
    session = fake_balance_session(utxos, poisoned_unconfirmed)

    result = await ElectrumX.get_balance(session, b'\x00' * 11, 'FOO')

    assert result == {'confirmed': 500, 'unconfirmed': 321}


@pytest.mark.asyncio
async def test_hashX_listunspent_rejects_oversized_asset_filter_before_any_work():
    # RVN-01: an oversized asset-list parameter must be rejected before any
    # DB or mempool work is attempted, not after paying for it.
    session = SimpleNamespace(
        db=SimpleNamespace(all_utxos=AsyncMock()),
        mempool=SimpleNamespace(
            unordered_UTXOs=AsyncMock(), potential_spends=AsyncMock(),
        ),
        bump_cost=Mock(),
    )
    oversized = [f'ASSET{i}' for i in range(MAX_ASSET_FILTER_LENGTH + 1)]

    with pytest.raises(RPCError, match='must not exceed'):
        await ElectrumX.hashX_listunspent(session, b'\x00' * 11, oversized)

    session.db.all_utxos.assert_not_awaited()
    session.mempool.unordered_UTXOs.assert_not_awaited()
    session.mempool.potential_spends.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_balance_rejects_oversized_asset_filter_before_any_work():
    session = SimpleNamespace(
        db=SimpleNamespace(all_utxos=AsyncMock()),
        mempool=SimpleNamespace(balance_delta=AsyncMock()),
        bump_cost=Mock(),
    )
    oversized = [f'ASSET{i}' for i in range(MAX_ASSET_FILTER_LENGTH + 1)]

    with pytest.raises(RPCError, match='must not exceed'):
        await ElectrumX.get_balance(session, b'\x00' * 11, oversized)

    session.db.all_utxos.assert_not_awaited()
    session.mempool.balance_delta.assert_not_awaited()


def test_check_asset_rejects_non_ascii_cleanly():
    # RVN-07: check_asset's callers uniformly do name.encode('ascii')
    # afterward. A non-ASCII name must be rejected here with a clean
    # RPCError, not left to surface as an unhandled UnicodeEncodeError.
    with pytest.raises(RPCError, match='ASCII'):
        check_asset('ASéET')  # contains e-acute


def test_check_h160_rejects_non_hex_cleanly():
    # RVN-07: check_h160's callers uniformly do bytes.fromhex(h160)
    # afterward. A non-hex value must be rejected here with a clean
    # RPCError, not left to surface as an unhandled ValueError.
    not_hex = 'z' * 40
    assert len(not_hex) == 40
    with pytest.raises(RPCError, match='hexadecimal'):
        check_h160(not_hex)


@pytest.mark.asyncio
async def test_is_qualified_rejects_non_hex_h160_without_crashing():
    # End-to-end through the actual RPC handler for
    # blockchain.asset.check_tag / blockchain.tag.check (RVN-04's own
    # endpoint): a malformed h160 must come back as a clean BAD_REQUEST,
    # not an unhandled ValueError from bytes.fromhex deep inside it.
    session = SimpleNamespace(bump_cost=Mock(), db=SimpleNamespace(
        is_h160_qualified=AsyncMock()))
    with pytest.raises(RPCError, match='hexadecimal'):
        await ElectrumX.is_qualified(session, 'z' * 40, 'FOO')
    session.db.is_h160_qualified.assert_not_awaited()


@pytest.mark.asyncio
async def test_hashX_listunspent_accepts_filter_with_none_member():
    # A [None, 'ASSET'] filter (native RVN plus one asset) is a legitimate
    # client request and must survive the frozenset conversion.
    session = fake_balance_session(all_utxos=[], balance_delta={})
    result = await ElectrumX.hashX_listunspent(session, b'\x00' * 11, [None, 'FOO'])
    assert result == []
    session.db.all_utxos.assert_awaited_once()
    filter_arg = session.db.all_utxos.await_args.args[1]
    assert isinstance(filter_arg, frozenset)
    assert filter_arg == frozenset({None, 'FOO'})
