from types import SimpleNamespace

from electrumx.server.db import DB


class FakeHashesFile:
    def __init__(self, data):
        self.data = data

    def read(self, start, size):
        return self.data[start:start + size]


def make_db(hashes_data, tx_counts, height):
    db = DB.__new__(DB)
    db.tx_counts = tx_counts
    db.state = SimpleNamespace(height=height)
    db.hashes_file = FakeHashesFile(hashes_data)
    return db


def test_fs_tx_hash_returns_hash_for_full_read():
    tx_hash = b'\xab' * 32
    db = make_db(tx_hash, tx_counts=[1], height=0)
    assert db.fs_tx_hash(0) == (tx_hash, 0)


def test_fs_tx_hash_returns_none_when_height_not_on_disk():
    db = make_db(b'', tx_counts=[1], height=-1)
    tx_hash, height = db.fs_tx_hash(0)
    assert tx_hash is None
    assert height == 0


def test_fs_tx_hash_returns_none_on_short_read():
    # RA-2: a truncated read must not be returned as if it were a real hash.
    db = make_db(b'\xab' * 16, tx_counts=[1], height=0)
    tx_hash, height = db.fs_tx_hash(0)
    assert tx_hash is None
    assert height == 0


def test_fs_tx_hash_returns_none_on_empty_read():
    db = make_db(b'', tx_counts=[1], height=0)
    tx_hash, height = db.fs_tx_hash(0)
    assert tx_hash is None
    assert height == 0
