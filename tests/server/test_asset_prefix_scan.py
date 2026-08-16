import asyncio
import os
import threading

import pytest

from electrumx.lib.util import subclasses
from electrumx.server.db import DB, MAX_ASSETS_WITH_PREFIX, PREFIX_ASSET_TO_ID
from electrumx.server.storage import Storage, db_class

# Same engine discovery/skip pattern as tests/server/test_storage.py.
_db_engines = []
for _klass in subclasses(Storage):
    try:
        _klass.import_module()
    except ImportError:
        _db_engines.append('skip')
    else:
        _db_engines.append(_klass.__name__)


@pytest.fixture(params=_db_engines)
def suid_db(tmpdir, request):
    if request.param == 'skip':
        pytest.skip('no storage engine available')
    cwd = os.getcwd()
    os.chdir(str(tmpdir))
    db = db_class(request.param)('suid', False)
    yield db
    db.close()
    os.chdir(cwd)


def test_a_one_character_prefix_scan_is_bounded(suid_db):
    '''SRV-08: a short prefix matching a large share of the asset namespace
    must not force a full-namespace scan. Seed well past the bound and
    confirm the result is capped, not merely "eventually correct".'''
    over_the_bound = MAX_ASSETS_WITH_PREFIX + 500
    with suid_db.write_batch() as batch:
        for i in range(over_the_bound):
            name = f'A{i:06d}'.encode('ascii')
            batch.put(PREFIX_ASSET_TO_ID + name, i.to_bytes(4, 'little'))

    db = object.__new__(DB)
    db.suid_db = suid_db

    result = asyncio.run(db.get_assets_with_prefix(b'A'))

    assert len(result) == MAX_ASSETS_WITH_PREFIX


def test_explicit_limit_is_honored(suid_db):
    with suid_db.write_batch() as batch:
        for i in range(50):
            name = f'B{i:06d}'.encode('ascii')
            batch.put(PREFIX_ASSET_TO_ID + name, i.to_bytes(4, 'little'))

    db = object.__new__(DB)
    db.suid_db = suid_db

    result = asyncio.run(db.get_assets_with_prefix(b'B', limit=5))

    assert len(result) == 5


def test_no_matches_returns_empty(suid_db):
    db = object.__new__(DB)
    db.suid_db = suid_db

    result = asyncio.run(db.get_assets_with_prefix(b'NOPE'))

    assert result == []


def test_all_utxos_resolves_asset_list_off_the_event_loop(suid_db):
    '''RVN-01: resolving a multi-name asset filter used to run a
    synchronous per-name LevelDB lookup directly on the event loop,
    before ever reaching run_in_thread -- blocking every other session
    for however long the filter took to resolve. It must run in the
    thread pool instead, like read_utxos() below it already does.'''
    db = object.__new__(DB)
    db.suid_db = suid_db

    calling_threads = []
    real_get_id_for_asset = DB.get_id_for_asset

    def tracking_get_id_for_asset(self, asset):
        calling_threads.append(threading.current_thread())
        return real_get_id_for_asset(self, asset)

    db.get_id_for_asset = tracking_get_id_for_asset.__get__(db, DB)

    event_loop_thread = threading.current_thread()
    result = asyncio.run(db.all_utxos(b'\x00' * 11, ['A', 'B', 'C']))

    assert result == []
    assert calling_threads, 'get_id_for_asset was never called'
    assert all(t is not event_loop_thread for t in calling_threads)
