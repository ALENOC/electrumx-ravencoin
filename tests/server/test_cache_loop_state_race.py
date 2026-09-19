import asyncio
from types import SimpleNamespace

import pytest

from electrumx.server.block_processor import BlockProcessor


def _cache_loop_processor():
    '''Enough of a BlockProcessor to run check_cache_size_loop().

    state is None, exactly as it is between spawning the loop and the moment
    fetch_and_process_blocks() has opened the databases.
    '''
    bp = BlockProcessor.__new__(BlockProcessor)
    bp.state = None
    bp.state_ready = asyncio.Event()
    bp.env = SimpleNamespace(cache_MB=1200)
    bp.daemon = SimpleNamespace()
    bp.utxo_cache = {}
    bp.utxo_deletes = []
    bp.db = SimpleNamespace(
        history=SimpleNamespace(unflushed_memsize=lambda: 0),
        fs_tx_count=0,
        fs_height=0,
    )
    # the accumulators the loop measures; empty is the startup state
    for attr in ('new_asset_ids', 'new_h160_ids', 'asset_metadata',
                 'asset_metadata_history', 'asset_broadcasts', 'tags',
                 'tag_history', 'freezes', 'freeze_history', 'verifiers',
                 'verifier_history', 'associations', 'association_history'):
        setattr(bp, attr, {})
    return bp


@pytest.mark.asyncio
async def test_cache_loop_waits_for_chain_state():
    '''Reading chain state before it is published took the server down with
    AttributeError: 'NoneType' object has no attribute 'tx_count'.'''
    bp = _cache_loop_processor()

    task = asyncio.create_task(bp.check_cache_size_loop())
    await asyncio.sleep(0.05)

    assert not task.done(), "the loop must wait instead of reading missing state"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cache_loop_proceeds_once_state_is_published():
    bp = _cache_loop_processor()
    task = asyncio.create_task(bp.check_cache_size_loop())
    await asyncio.sleep(0.05)

    # what fetch_and_process_blocks() does after opening the databases
    bp.state = SimpleNamespace(tx_count=10, height=5, chain_size=1)
    bp.state_ready.set()
    await asyncio.sleep(0.05)

    # it must now be running the loop body, not still blocked on the gate
    assert not task.done() or task.exception() is None

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
