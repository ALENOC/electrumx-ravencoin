# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""GLM53-RVN-003 regression tests: the transaction-hash metadata extent is
validated globally at startup, and the bounded crash-recovery repair refuses
damage it cannot provably fix instead of writing sparse zero holes."""

import logging
import os
from types import SimpleNamespace

import pytest

from electrumx.lib import util
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

    def find_zero_slot(self, slot_size):
        return None


class NeverFile:
    """A hashes file whose zero-slot scan must never be reached."""

    def logical_size(self):
        raise AssertionError("extent check must run before the zero scan")

    def find_zero_slot(self, slot_size):
        raise AssertionError("extent check must run before the zero scan")


def _db(height, tx_count, tx_counts, *, hashes_extent=None, hashes_file=None):
    tip = b"t" * 32

    class Coin:
        @staticmethod
        def header_hash(raw):
            return tip

    return SimpleNamespace(
        state=SimpleNamespace(height=height, tx_count=tx_count, tip=tip),
        tx_counts=tx_counts,
        fs_metadata_issue=None,
        fs_zero_slot_offset=None,
        coin=Coin(),
        header_len=lambda h: 80,
        header_offset=lambda h: h * 80,
        headers_file=RecordingFile({(height * 80, 80): b"h" * 80}),
        hashes_file=hashes_file if hashes_file is not None
        else RecordingFile(extent=hashes_extent),
    )


def test_extent_truncation_below_committed_tx_count_is_detected():
    # Tip block is intact but the file is 32 bytes short overall: the old
    # tip-only checks passed, the global extent check must not.
    db = _db(0, 2, [2], hashes_file=NeverFile())
    db.hashes_file = RecordingFile(
        reads={(0, 64): b"x" * 32 + b"y" * 32}, extent=32)
    assert DB.fs_metadata_needs_recovery(db) is True
    assert "truncated" in db.fs_metadata_issue


def test_extent_extra_bytes_beyond_tx_count_are_detected():
    db = _db(0, 1, [1],
             hashes_file=RecordingFile(reads={(0, 32): b"x" * 32}, extent=64))
    assert DB.fs_metadata_needs_recovery(db) is True
    assert "unexpected bytes" in db.fs_metadata_issue


def test_zero_slot_mid_history_is_detected():
    # Height 1: tx 0 is a sparse zero hole, the tip block (tx 1, tx 2) is
    # intact, so only the global scan can find the hole.
    hashes = bytes(32) + b"y" * 32 + b"z" * 32

    class ScanningFile:

        def read(self, start, size=-1):
            return hashes[start:start + size if size != -1 else None]

        def logical_size(self):
            return len(hashes)

        def find_zero_slot(self, slot_size):
            return hashes.find(bytes(slot_size))

    db = _db(1, 3, [1, 3], hashes_file=ScanningFile())
    assert DB.fs_metadata_needs_recovery(db) is True
    assert db.fs_metadata_issue is not None
    assert db.fs_zero_slot_offset == 0


def _real_scanning_file(data, tmp=None):
    """A RecordingFile-like object whose find_zero_slot actually scans."""
    import tempfile
    tmp = tmp or tempfile.mkdtemp(prefix="hashes-extent-")
    prefix = os.path.join(tmp, "hashes")
    logical = util.LogicalFile(prefix, 4, 1_000_000)
    logical.write(0, data)
    return logical


def test_healthy_metadata_has_no_issue():
    db = _db(0, 2, [2],
             hashes_file=RecordingFile(reads={(0, 64): b"x" * 64}, extent=64))
    assert DB.fs_metadata_needs_recovery(db) is False
    assert db.fs_metadata_issue is None
    assert db.fs_zero_slot_offset is None


def test_logical_file_size_and_zero_slot_scan(tmp_path):
    prefix = str(tmp_path / "hashes")
    logical = util.LogicalFile(prefix, 4, 64)
    assert logical.logical_size() == 0
    data = b"x" * 64 + b"y" * 64
    logical.write(0, data)
    assert logical.logical_size() == 128
    assert logical.find_zero_slot(32) is None
    holed = data[:32] + bytes(32) + data[64:]
    logical2 = util.LogicalFile(prefix + "2-", 4, 64)
    logical2.write(0, holed)
    assert logical2.find_zero_slot(32) == 32


def test_zero_slot_scan_catches_runs_straddling_chunk_boundaries(tmp_path,
                                                                 monkeypatch):
    prefix = str(tmp_path / "hashes")
    logical = util.LogicalFile(prefix, 4, 1_000_000)
    # One zero slot crossing the internal 4 MiB chunk boundary.
    filler = b"a" * ((1 << 22) - 16) + bytes(32) + b"b" * 64
    logical.write(0, filler)
    assert logical.find_zero_slot(32) == (1 << 22) - 16


@pytest.mark.asyncio
async def test_repair_refuses_truncation_below_recovery_window(monkeypatch):
    """Damage older than the bounded window must not be silently re-paired
    with a past-EOF write that creates sparse zero holes."""
    from electrumx.lib.hash import hash_to_hex_str  # noqa: F811

    tip = b"t" * 32
    tx_hash = b"x" * 32
    header = b"h" * 80

    class Coin:
        @staticmethod
        def static_header_len(height):
            return 80

        @staticmethod
        def validate_header(raw, height):
            return None

        @staticmethod
        def header_prevhash(raw):
            return tip

        @staticmethod
        def header_hash(raw):
            return tip

    class FakeBlock:
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
            assert (start, count) == (37, 64)
            return [hash_to_hex_str(tip)] * 64

    async def prefetch_many(*args, **kwargs):
        return None

    async def streamed_block(*args, **kwargs):
        return FakeBlock()

    async def delete_stale(*args, **kwargs):
        return None

    monkeypatch.setattr(bp_module.OnDiskBlock, "prefetch_many", prefetch_many)
    monkeypatch.setattr(bp_module.OnDiskBlock, "streamed_block", streamed_block)
    monkeypatch.setattr(bp_module.OnDiskBlock, "delete_stale", delete_stale)

    # Height 100, one tx per block: the 64-block window starts at height 37
    # with a prior cumulative count of 37.  The hashes file only holds 36
    # slots, so data is missing below the window the repair would rewrite.
    state = SimpleNamespace(height=100, tx_count=101, asset_count=0,
                            h160_count=0, tip=tip)

    class CountsFile:

        def read(self, start, size):
            assert (start, size) == (36 * 8, 8)
            return (37).to_bytes(8, "little")

    hashes = RecordingFile(extent=36 * 32)

    db = SimpleNamespace(
        state=state, fs_metadata_issue="truncated",
        fs_zero_slot_offset=None,
        logger=logging.getLogger("test"),
        header_offset=lambda height: height * 80,
        headers_file=RecordingFile({(36 * 80, 80): header}),
        tx_counts_file=CountsFile(),
        hashes_file=hashes,
        fs_height=-1, fs_tx_count=0, fs_asset_count=0, fs_h160_count=0,
        DBError=DB.DBError,
    )
    db.fs_metadata_needs_recovery = lambda: True

    processor = bp_module.BlockProcessor.__new__(bp_module.BlockProcessor)
    processor.db = db
    processor.daemon = Daemon()
    processor.coin = Coin()

    with pytest.raises(DB.DBError) as excinfo:
        await processor._repair_trailing_fs_metadata()
    assert "below the bounded recovery window" in str(excinfo.value)
    assert hashes.writes == [], "no past-EOF write may be performed"


@pytest.mark.asyncio
async def test_repair_refuses_extent_beyond_tx_count(monkeypatch):
    """Extra unexpected hashes bytes must not be silently accepted."""
    from electrumx.lib.hash import hash_to_hex_str  # noqa: F811

    tip = b"t" * 32
    tx_hash = b"x" * 32
    header = b"h" * 80

    class Coin:
        @staticmethod
        def static_header_len(height):
            return 80

        @staticmethod
        def validate_header(raw, height):
            return None

        @staticmethod
        def header_prevhash(raw):
            return bytes(32)

        @staticmethod
        def header_hash(raw):
            return tip

    class FakeBlock:
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
            return [hash_to_hex_str(tip)]
    from electrumx.lib.hash import hash_to_hex_str  # noqa: F811

    async def prefetch_many(*args, **kwargs):
        return None

    async def streamed_block(*args, **kwargs):
        return FakeBlock()

    async def delete_stale(*args, **kwargs):
        return None

    monkeypatch.setattr(bp_module.OnDiskBlock, "prefetch_many", prefetch_many)
    monkeypatch.setattr(bp_module.OnDiskBlock, "streamed_block", streamed_block)
    monkeypatch.setattr(bp_module.OnDiskBlock, "delete_stale", delete_stale)

    state = SimpleNamespace(height=0, tx_count=1, asset_count=0,
                            h160_count=0, tip=tip)
    hashes = RecordingFile(extent=512)  # far beyond one tx

    db = SimpleNamespace(
        state=state, fs_metadata_issue="extra bytes",
        fs_zero_slot_offset=None,
        logger=logging.getLogger("test"),
        header_offset=lambda height: height * 80,
        headers_file=RecordingFile(),
        tx_counts_file=RecordingFile(),
        hashes_file=hashes,
        fs_height=-1, fs_tx_count=0, fs_asset_count=0, fs_h160_count=0,
        DBError=DB.DBError,
    )
    db.fs_metadata_needs_recovery = lambda: True

    processor = bp_module.BlockProcessor.__new__(bp_module.BlockProcessor)
    processor.db = db
    processor.daemon = Daemon()
    processor.coin = Coin()

    with pytest.raises(DB.DBError) as excinfo:
        await processor._repair_trailing_fs_metadata()
    assert "beyond the committed tx_count" in str(excinfo.value)
    assert hashes.writes == []
