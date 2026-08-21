import logging
from array import array
from types import SimpleNamespace

import pytest

from electrumx.lib.hash import hash_to_hex_str
from electrumx.server import block_processor as bp_module
from electrumx.server.db import DB


class RecordingFile:
    def __init__(self, reads=None, extent=0):
        self.reads = reads or {}
        self.writes = []
        self._extent = extent

    def read(self, start, size=-1):
        return self.reads.get((start, size), b"")

    def write(self, start, data, *, sync=False):
        self.writes.append((start, data, sync))
        self._extent = max(self._extent, start + len(data))

    def logical_size(self):
        return self._extent


@pytest.mark.asyncio
async def test_txcounts_torn_tail_is_detected_without_assert_crash():
    db = DB.__new__(DB)
    db.state = SimpleNamespace(height=1, tx_count=2)
    db.tx_counts = None
    db.fs_metadata_issue = None
    db.tx_counts_file = RecordingFile({(0, 16): array("Q", [1, 0]).tobytes()})
    db.logger = logging.getLogger("test")

    await DB._read_tx_counts(db)
    assert db.fs_metadata_issue is not None
    assert "non-monotonic" in db.fs_metadata_issue

    with pytest.raises(DB.DBError):
        await DB._read_tx_counts(db, force=True, strict=True)


def test_flush_fs_uses_durable_writes_for_all_three_flat_metadata_files():
    db = DB.__new__(DB)
    db.fs_height = -1
    db.fs_tx_count = 0
    db.fs_asset_count = 0
    db.fs_h160_count = 0
    db.tx_counts = array("Q", [1])
    db.header_offset = lambda height: height * 80
    db.headers_file = RecordingFile()
    db.tx_counts_file = RecordingFile()
    db.hashes_file = RecordingFile()

    state = SimpleNamespace(height=0, tx_count=1, asset_count=0, h160_count=0)
    flush = SimpleNamespace(
        state=state, headers=[b"h" * 80], block_tx_hashes=[b"x" * 32])

    DB.flush_fs(db, flush)

    assert db.headers_file.writes[0][2] is True
    assert db.tx_counts_file.writes[0][2] is True
    assert db.hashes_file.writes[0][2] is True


@pytest.mark.asyncio
async def test_bounded_startup_repair_rebuilds_torn_tail_from_daemon(monkeypatch):
    tip = b"t" * 32
    tx_hash = b"x" * 32
    header = b"h" * 80

    class Coin:
        @staticmethod
        def static_header_len(height):
            return 80

        @staticmethod
        def validate_header(raw, height):
            assert raw == header
            assert height == 0

        @staticmethod
        def header_prevhash(raw):
            return bytes(32)

        @staticmethod
        def header_hash(raw):
            return tip

    class FakeBlock:
        height = 0
        size = 100
        def __enter__(self):
            self.header = header
            return self
        def __exit__(self, *args):
            return False
        def iter_txs(self):
            yield object(), tx_hash

    class Daemon:
        async def block_hex_hashes(self, start, count):
            assert (start, count) == (0, 1)
            return [hash_to_hex_str(tip)]

    async def prefetch_many(*args, **kwargs):
        return None
    async def streamed_block(*args, **kwargs):
        return FakeBlock()
    async def delete_stale(*args, **kwargs):
        return None

    monkeypatch.setattr(bp_module.OnDiskBlock, "prefetch_many", prefetch_many)
    monkeypatch.setattr(bp_module.OnDiskBlock, "streamed_block", streamed_block)
    monkeypatch.setattr(bp_module.OnDiskBlock, "delete_stale", delete_stale)

    db = SimpleNamespace(
        state=SimpleNamespace(height=0, tx_count=1, asset_count=0, h160_count=0, tip=tip),
        fs_metadata_issue="torn txcounts/hash tail",
        logger=logging.getLogger("test"),
        header_offset=lambda height: height * 80,
        headers_file=RecordingFile(),
        tx_counts_file=RecordingFile(),
        hashes_file=RecordingFile(),
        fs_height=-1, fs_tx_count=0, fs_asset_count=0, fs_h160_count=0,
        fs_zero_slot_offset=None,
        DBError=DB.DBError,
    )
    db.fs_metadata_needs_recovery = lambda: True
    async def reread(*, force=False, strict=False):
        assert force and strict
        db.fs_metadata_issue = None
    db._read_tx_counts = reread

    processor = bp_module.BlockProcessor.__new__(bp_module.BlockProcessor)
    processor.db = db
    processor.daemon = Daemon()
    processor.coin = Coin()

    assert await processor._repair_trailing_fs_metadata() is True
    assert db.headers_file.writes == [(0, header, True)]
    assert db.tx_counts_file.writes == [(0, array("Q", [1]).tobytes(), True)]
    assert db.hashes_file.writes == [(0, tx_hash, True)]
    assert db.fs_height == 0
    assert db.fs_tx_count == 1
