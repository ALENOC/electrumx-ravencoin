import asyncio
import logging
import os
from types import SimpleNamespace

import pytest

from electrumx.lib.util import pack_le_uint32, pack_le_uint64, subclasses
from electrumx.server.db import (
    DB, PREFIX_ASSET_TO_ID, PREFIX_H160_TAG_CURRENT, PREFIX_H160_TAG_HISTORY,
    PREFIX_H160_TO_ID,
)
from electrumx.server.storage import Storage, db_class

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
    asset_db = db_class(request.param)(str(tmpdir.join('asset')), False)
    suid_db = db_class(request.param)(str(tmpdir.join('suid')), False)
    yield asset_db, suid_db
    asset_db.close()
    suid_db.close()


class _FakeHashesFile:
    '''Records the tx_num it was asked for and returns a deterministic,
    distinguishable 32-byte hash for it.'''

    def __init__(self):
        self.requested_tx_nums = []

    def read(self, offset, size):
        assert size == 32
        assert offset % 32 == 0
        tx_num = offset // 32
        self.requested_tx_nums.append(tx_num)
        return tx_num.to_bytes(4, 'little') + b'\0' * 28


def _seeded_db(asset_db, suid_db, *, h160, qualifier_name, h160_id, qualifier_id,
                tx_pos, tx_num, flag):
    db = object.__new__(DB)
    db.asset_db = asset_db
    db.suid_db = suid_db
    db.logger = logging.getLogger('test_h160_qualified')
    db.hashes_file = _FakeHashesFile()
    db.tx_counts = [10_000]
    db.state = SimpleNamespace(height=0, tx_count=10_000)

    suid_db.put(PREFIX_H160_TO_ID + h160, h160_id)
    suid_db.put(PREFIX_ASSET_TO_ID + qualifier_name, qualifier_id)

    latest_tag_id = pack_le_uint32(tx_pos) + pack_le_uint64(tx_num)[:5]
    current_lookup_key = PREFIX_H160_TAG_CURRENT + h160_id + qualifier_id
    asset_db.put(current_lookup_key, latest_tag_id)

    current_entry_key = PREFIX_H160_TAG_HISTORY + h160_id + latest_tag_id
    db_ret = qualifier_id + bytes([flag])
    asset_db.put(current_entry_key, db_ret)

    return db


def test_is_h160_qualified_reads_tx_pos_and_tx_num_from_the_value(asset_and_suid_db):
    '''RVN-04: is_h160_qualified unpacked tx_pos/tx_num from the lookup KEY
    (h160_id + qualifier_id) instead of the VALUE it stored them in. With
    h160_id=1, qualifier_id=2 that key-based unpack silently produces
    tx_pos=328, tx_num=512 -- both wrong, and both far from the tx_pos=7,
    tx_num=3 actually stored.'''
    asset_db, suid_db = asset_and_suid_db
    h160 = os.urandom(20)
    qualifier_name = b'QUALIFIER'
    db = _seeded_db(
        asset_db, suid_db, h160=h160, qualifier_name=qualifier_name,
        h160_id=pack_le_uint32(1), qualifier_id=pack_le_uint32(2),
        tx_pos=7, tx_num=3, flag=1,
    )

    result = asyncio.run(db.is_h160_qualified(h160, qualifier_name))

    assert result['tx_pos'] == 7
    assert db.hashes_file.requested_tx_nums == [3]
    assert result['flag'] is True


def test_is_h160_qualified_rejects_out_of_range_tx_num(asset_and_suid_db):
    '''An internally-inconsistent entry (tx_num beyond the indexed tx
    count) must raise a clear, deterministic error, not crash with a bare
    TypeError from hash_to_hex_str(None) deep inside fs_tx_hash.'''
    asset_db, suid_db = asset_and_suid_db
    h160 = os.urandom(20)
    qualifier_name = b'QUALIFIER'
    db = _seeded_db(
        asset_db, suid_db, h160=h160, qualifier_name=qualifier_name,
        h160_id=pack_le_uint32(1), qualifier_id=pack_le_uint32(2),
        tx_pos=7, tx_num=20_000, flag=1,
    )

    with pytest.raises(DB.DBError, match='internally inconsistent'):
        asyncio.run(db.is_h160_qualified(h160, qualifier_name))
