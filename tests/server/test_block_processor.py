from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from electrumx.server.block_processor import BlockProcessor, ChainError, OnDiskBlock


PREFETCH_LIMIT = 100


def _fake_processor(*, caught_up, first_sync, reopened_for_serving, height=100):
    '''A minimal stand-in for BlockProcessor with just what on_caught_up()
    touches, so the real method can be exercised without a database, a
    daemon or a network connection.'''
    touched_attrs = ('touched', 'asset_touched', 'qualifier_touched', 'h160_touched',
                     'broadcast_touched', 'frozen_touched', 'validator_touched',
                     'qualifier_association_touched')
    fake = SimpleNamespace(
        state=SimpleNamespace(first_sync=first_sync, height=height),
        caught_up=caught_up,
        reopened_for_serving=reopened_for_serving,
        flush=AsyncMock(),
        notifications=SimpleNamespace(on_block=AsyncMock()),
        db=SimpleNamespace(open_for_serving=AsyncMock()),
        **{attr: {'x'} for attr in touched_attrs},
    )
    return fake


@pytest.mark.asyncio
async def test_on_caught_up_reopens_for_serving_exactly_once_per_process():
    '''The very first time a process becomes caught up (whether or not it is
    the historical first sync ever - a plain restart of an already-synced
    node hits this too), the DB must be reopened in serving mode exactly
    once.'''
    fake = _fake_processor(caught_up=False, first_sync=False, reopened_for_serving=False)
    await BlockProcessor.on_caught_up(fake)
    assert fake.reopened_for_serving is True
    fake.db.open_for_serving.assert_awaited_once()


@pytest.mark.asyncio
async def test_on_caught_up_does_not_reopen_db_when_regaining_after_a_revoke():
    '''Once the DB has already been reopened for serving in this process, a
    later regain (after _catch_up_state revoked caught_up mid-life, while
    client sessions may be connected) must not reopen it again - see the
    caution in on_caught_up() about closing DB handles out from under live
    queries.'''
    fake = _fake_processor(caught_up=False, first_sync=False, reopened_for_serving=True)
    await BlockProcessor.on_caught_up(fake)
    assert fake.reopened_for_serving is True
    fake.db.open_for_serving.assert_not_awaited()


def test_normal_sync_and_tip_lag_do_not_revoke():
    # Freshly caught up, backend at the same height: stays caught up.
    assert BlockProcessor._catch_up_state(True, 100, 100, PREFETCH_LIMIT) is True

    # Backend one block ahead (the normal steady-state gap while a new block
    # is being fetched and indexed): still caught up, no flapping.
    assert BlockProcessor._catch_up_state(True, 100, 101, PREFETCH_LIMIT) is True

    # Right at the tolerance boundary: still within one prefetch batch.
    assert BlockProcessor._catch_up_state(True, 100, 200, PREFETCH_LIMIT) is True

    # Not caught up in the first place: nothing to revoke, stays False. This
    # is the ordinary first-sync/initial-IBD case, unaffected by this check.
    assert BlockProcessor._catch_up_state(False, 100, 100, PREFETCH_LIMIT) is False
    assert BlockProcessor._catch_up_state(False, 0, 4_000_000, PREFETCH_LIMIT) is False


def test_backend_height_jump_revokes_caught_up():
    # This is the actual bug: ElectrumX matched a temporarily low backend
    # height (e.g. mid Core-reindex) and was marked caught up, then the
    # backend went on to advance far past that.  The old code left
    # self.caught_up latched True forever once set; against that
    # implementation this assertion fails.
    assert BlockProcessor._catch_up_state(True, 100, 1000, PREFETCH_LIMIT) is False

    # Just past the one-batch tolerance: also revoked.
    assert BlockProcessor._catch_up_state(True, 100, 201, PREFETCH_LIMIT) is False


def test_regression_reindex_incident_scenario():
    '''Models the actual incident without a real multi-million-block reindex:
    backend reports a temporary low height, ElectrumX matches it and is
    marked caught up, the backend jumps far ahead (as Core does after
    finishing a reindex's raw block scan and starting chain activation),
    caught-up must be revoked, and it must be restored once ElectrumX has
    genuinely indexed up to the backend's new height.

    The "become caught up" half of the state machine is the caller's
    existing, unchanged behaviour (on_caught_up() is invoked once
    next_block_hashes() finds nothing left to fetch, i.e. indexed height
    has reached the daemon height); this test drives that same rule
    directly since it isn't the part under test, while every "should this
    still count as caught up" decision goes through the real
    _catch_up_state() being fixed here.
    '''
    caught_up = False
    indexed_height = 100

    # Core, mid own reindex, temporarily reports a low height and ElectrumX
    # reaches it.
    daemon_height = 100
    caught_up = BlockProcessor._catch_up_state(caught_up, indexed_height, daemon_height, PREFETCH_LIMIT)
    assert indexed_height >= daemon_height
    caught_up = True  # what on_caught_up() does when there is nothing left to fetch
    assert caught_up is True

    # Core's reindex continues; its real height turns out to be far ahead.
    daemon_height = 1000
    caught_up = BlockProcessor._catch_up_state(caught_up, indexed_height, daemon_height, PREFETCH_LIMIT)
    assert caught_up is False, 'must not remain latched caught up after a material backend jump'

    # ElectrumX works through the backlog in prefetch-sized batches; still
    # behind, so still correctly not caught up.
    indexed_height = 500
    caught_up = BlockProcessor._catch_up_state(caught_up, indexed_height, daemon_height, PREFETCH_LIMIT)
    assert caught_up is False

    indexed_height = 950
    caught_up = BlockProcessor._catch_up_state(caught_up, indexed_height, daemon_height, PREFETCH_LIMIT)
    assert caught_up is False

    # ElectrumX reaches the backend's height: genuinely caught up again.
    indexed_height = 1000
    caught_up = BlockProcessor._catch_up_state(caught_up, indexed_height, daemon_height, PREFETCH_LIMIT)
    assert indexed_height >= daemon_height
    caught_up = True  # on_caught_up() fires again, nothing left to fetch
    assert caught_up is True


@pytest.mark.asyncio
async def test_confirmed_block_chain_error_is_diagnosed_distinctly_and_not_flushed(caplog):
    '''RVN-05 (confirmed-block side): daemon-provided block data that
    violates an internal consensus/index invariant (ChainError) must be
    reported with a distinct, operator-visible "daemon-integrity failure"
    diagnosis rather than the generic crash-trace message used for
    unrelated bugs, and it must propagate rather than being swallowed.
    self.ok stays False (set before any tx of a block is processed, only
    True again once a block completes), so this must not attempt to
    flush partial state.
    '''
    state = SimpleNamespace(height=100)
    fake = SimpleNamespace(
        env=SimpleNamespace(write_bad_vouts_to_file=False),
        db=SimpleNamespace(
            state=SimpleNamespace(copy=lambda: state),
            open_for_sync=AsyncMock(return_value=SimpleNamespace(copy=lambda: state))),
        daemon=SimpleNamespace(),
        state=state,
        ok=False,
        caught_up=True,
        reorg_count=None,
        _repair_trailing_fs_metadata=AsyncMock(),
        next_block_hashes=AsyncMock(side_effect=ChainError('bad asset reference')),
        flush=AsyncMock(),
    )

    with patch('electrumx.server.block_processor.verify_database_chain', AsyncMock()), \
            patch.object(OnDiskBlock, 'scan_files', AsyncMock()):
        with caplog.at_level('ERROR'):
            with pytest.raises(ChainError):
                await BlockProcessor.fetch_and_process_blocks(fake, AsyncMock(), AsyncMock())

    assert any('daemon-integrity failure' in record.message for record in caplog.records)
    assert any('height 100' in record.message for record in caplog.records)
    fake.flush.assert_not_awaited()
