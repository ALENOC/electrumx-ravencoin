import logging
import os
from types import SimpleNamespace

import pytest

from electrumx.lib.util import DataParser, pack_le_uint32, pack_le_uint64, subclasses
from electrumx.server.block_processor import BlockProcessor
from electrumx.server.db import DB, PREFIX_ASSET_TO_ID, PREFIX_BROADCAST, PREFIX_ID_TO_ASSET
from electrumx.server.storage import Storage, db_class

# Same engine discovery/skip pattern as tests/server/test_storage.py: engines
# that are not installed in this environment are skipped, not silently passed.
_db_engines = []
for _klass in subclasses(Storage):
    try:
        _klass.import_module()
    except ImportError:
        _db_engines.append('skip')
    else:
        _db_engines.append(_klass.__name__)


@pytest.fixture(params=_db_engines)
def asset_and_suid_db(tmpdir, request):
    if request.param == 'skip':
        pytest.skip('no storage engine available')
    cwd = os.getcwd()
    os.chdir(str(tmpdir))
    asset_db = db_class(request.param)('asset', False)
    suid_db = db_class(request.param)('suid', False)
    yield asset_db, suid_db
    asset_db.close()
    suid_db.close()
    os.chdir(cwd)


def _empty_processor_state():
    '''Every attribute BlockProcessor.flush_data() / undo_asset_db() touch,
    with the same defaults BlockProcessor.__init__ uses. Using real
    accumulator containers lets the real (unbound) methods run unmodified,
    so the test exercises the actual flush/rollback code rather than a
    re-implementation of it.'''
    return SimpleNamespace(
        state=None,
        headers=[], tx_hashes=[],
        utxo_cache={}, utxo_deletes=[], utxo_undos=[],
        new_asset_ids={}, new_asset_ids_undos=[], asset_ids_deletes=[],
        new_h160_ids={}, new_h160_ids_undos=[], h160_ids_deletes=[],
        asset_metadata={}, asset_metadata_undos=[], asset_metadata_deletes=[],
        asset_metadata_history={}, asset_metadata_history_undos=[], asset_metadata_history_deletes=[],
        asset_broadcasts={}, asset_broadcasts_undos=[], asset_broadcasts_deletes=[],
        tags={}, tags_undos=[], tags_deletes=[],
        tag_history={}, tag_history_undos=[], tag_history_deletes=[],
        freezes={}, freezes_undos=[], freezes_deletes=[],
        freeze_history={}, freeze_history_undos=[], freeze_history_deletes=[],
        verifiers={}, verifiers_undos=[], verifiers_deletes=[],
        verifier_history={}, verifier_history_undos=[], verifier_history_deletes=[],
        associations={}, associations_undos=[], associations_deletes=[],
        association_history={}, association_history_undos=[], association_history_deletes=[],
        asset_touched=set(), qualifier_touched=set(), h160_touched=set(),
        broadcast_touched=set(), frozen_touched=set(), validator_touched=set(),
        qualifier_association_touched=set(),
    )


def test_broadcast_survives_flush_and_is_removed_on_reorg_rollback(asset_and_suid_db):
    '''Round-trip regression for SRV-01.

    A broadcast recorded while processing block A must be persisted such
    that, on reorg, undo_asset_db() can find and remove it. This exercises
    the real BlockProcessor.flush_data() / undo_asset_db() against the real
    DB.flush_asset_db() / read_broadcast_undo_info() on an on-disk store -
    an in-memory-list check would catch neither the original
    argument-substitution bug (the undo list silently dropped at flush)
    nor the key-material bug in the delete path (asset name used instead
    of asset id, which does not match how the entry was actually stored).
    '''
    asset_db, suid_db = asset_and_suid_db

    db = object.__new__(DB)
    db.asset_db = asset_db
    db.suid_db = suid_db
    db.logger = logging.getLogger('test_asset_broadcast_reorg')

    asset_id = pack_le_uint32(7)
    asset_name = b'MYASSET'
    idx_b = pack_le_uint32(0)
    tx_numb = pack_le_uint64(123)[:5]
    broadcast_key = asset_id + idx_b + tx_numb
    broadcast_data = b'\x01' * 34

    # id<->name mapping, as flush_suid_db would have written it forward.
    suid_db.put(PREFIX_ID_TO_ASSET + asset_id, asset_name)
    suid_db.put(PREFIX_ASSET_TO_ID + asset_name, asset_id)

    height = 4_500_000

    # --- Forward: block at `height` broadcasts a message ---
    fwd = _empty_processor_state()
    fwd.asset_broadcasts[broadcast_key] = broadcast_data
    # Matches advance_block(): internal_broadcast_undo_info is a list of
    # per-broadcast byte chunks accumulated over the block, joined at flush.
    fwd.asset_broadcasts_undos.append(([broadcast_key], height))

    flush_data = BlockProcessor.flush_data(fwd)
    db.flush_asset_db(flush_data)

    # The broadcast is visible, served as confirmed data.
    assert asset_db.get(PREFIX_BROADCAST + broadcast_key) == broadcast_data

    # --- The undo record must actually have been persisted ---
    undo_raw = db.read_broadcast_undo_info(height)
    assert undo_raw is not None, (
        'broadcast undo info was not flushed to the DB; a reorg at this '
        'height would find nothing to roll back (SRV-01)'
    )
    data_parser = DataParser(undo_raw)
    assert not data_parser.is_finished()

    # --- Reorg: roll the block back ---
    bak = _empty_processor_state()
    bak.db = db
    BlockProcessor.undo_asset_db(bak, height)

    assert bak.asset_broadcasts_deletes == [PREFIX_BROADCAST + broadcast_key], (
        'rollback computed the wrong delete key; it must match the key the '
        'broadcast was actually stored under (asset id + suffix), not the '
        'resolved asset name'
    )

    rollback_flush = BlockProcessor.flush_data(bak)
    db.flush_asset_db(rollback_flush)

    # --- No orphaned broadcast remains ---
    assert asset_db.get(PREFIX_BROADCAST + broadcast_key) is None


def test_no_broadcast_no_undo_record_and_rollback_is_a_no_op(asset_and_suid_db):
    '''Control: a block with no broadcasts must not create a phantom undo
    record, and rolling back an untouched height must be a no-op.'''
    asset_db, suid_db = asset_and_suid_db
    db = object.__new__(DB)
    db.asset_db = asset_db
    db.suid_db = suid_db
    db.logger = logging.getLogger('test_asset_broadcast_reorg')

    height = 4_500_001

    fwd = _empty_processor_state()
    db.flush_asset_db(BlockProcessor.flush_data(fwd))
    assert db.read_broadcast_undo_info(height) is None

    bak = _empty_processor_state()
    bak.db = db
    BlockProcessor.undo_asset_db(bak, height)
    assert bak.asset_broadcasts_deletes == []
