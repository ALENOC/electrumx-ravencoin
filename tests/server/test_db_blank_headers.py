import logging
from types import SimpleNamespace

import pytest

from electrumx.lib.hash import double_sha256
from electrumx.server.db import DB

HEADER_SIZE = 80
CHAIN_LEN = 6


class Coin:
    '''Just enough coin for the header record paths.'''

    @classmethod
    def header_hash(cls, header):
        return double_sha256(header)

    @classmethod
    def header_prevhash(cls, header):
        return header[4:36]

    @classmethod
    def static_header_offset(cls, height):
        return height * HEADER_SIZE

    @classmethod
    def static_header_len(cls, height):
        return HEADER_SIZE


class MemoryHeadersFile:
    def __init__(self, data):
        self.data = bytearray(data)
        self.synced_writes = 0

    def read(self, start, size=-1):
        if size < 0:
            return bytes(self.data[start:])
        return bytes(self.data[start:start + size])

    def write(self, start, data, *, sync=False):
        if sync:
            self.synced_writes += 1
        self.data[start:start + len(data)] = data


def build_chain(length=CHAIN_LEN):
    headers, prev = [], bytes(32)
    for i in range(length):
        header = (i + 1).to_bytes(4, 'little') + prev + bytes(HEADER_SIZE - 36)
        headers.append(header)
        prev = double_sha256(header)
    return headers


def make_db(headers, blank_heights=(), repairer=None):
    raw = bytearray(b''.join(headers))
    for height in blank_heights:
        raw[height * HEADER_SIZE:(height + 1) * HEADER_SIZE] = bytes(HEADER_SIZE)
    db = DB.__new__(DB)
    db.coin = Coin
    db.header_offset = Coin.static_header_offset
    db.header_len = Coin.static_header_len
    db.headers_file = MemoryHeadersFile(raw)
    db.state = SimpleNamespace(height=len(headers) - 1,
                               tip=double_sha256(headers[-1]))
    db.logger = logging.getLogger('test')
    db.header_repairer = repairer
    db.env = SimpleNamespace(header_scan_on_startup=True)
    return db


def repairer_for(headers):
    async def repair(heights):
        return {height: headers[height] for height in heights}
    return repair


@pytest.mark.asyncio
async def test_blank_record_is_detected():
    headers = build_chain()
    db = make_db(headers, blank_heights=(3,))
    raw, count = db._read_headers_from_disk(0, CHAIN_LEN)
    assert db.blank_header_heights(raw, 0, count) == [3]


@pytest.mark.asyncio
async def test_read_headers_repairs_instead_of_serving_zeros():
    headers = build_chain()
    db = make_db(headers, blank_heights=(3,), repairer=repairer_for(headers))

    raw, count = await db.read_headers(0, CHAIN_LEN)

    assert count == CHAIN_LEN
    assert raw == b''.join(headers)
    assert db.blank_header_heights(raw, 0, count) == []
    # the repair must be durable, not just in the page cache
    assert db.headers_file.synced_writes == 1


@pytest.mark.asyncio
async def test_tip_record_is_repaired_against_chain_state():
    headers = build_chain()
    tip = CHAIN_LEN - 1
    db = make_db(headers, blank_heights=(tip,), repairer=repairer_for(headers))

    raw, count = await db.read_headers(tip, 1)

    assert count == 1
    assert raw == headers[tip]


@pytest.mark.asyncio
async def test_read_headers_fails_when_it_cannot_repair():
    headers = build_chain()
    db = make_db(headers, blank_heights=(3,))

    with pytest.raises(DB.DBError) as exc:
        await db.read_headers(0, CHAIN_LEN)
    assert 'height 3' in str(exc.value)


@pytest.mark.asyncio
async def test_unlinked_replacement_is_refused():
    headers = build_chain()
    wrong = build_chain()[2][:4] + bytes(32) + bytes(HEADER_SIZE - 36)

    async def bad_repairer(heights):
        return {height: wrong for height in heights}

    db = make_db(headers, blank_heights=(3,), repairer=bad_repairer)

    with pytest.raises(DB.DBError) as exc:
        await db.read_headers(0, CHAIN_LEN)
    assert 'height 3' in str(exc.value)
    # nothing may be written when the replacement does not link
    assert db.headers_file.synced_writes == 0


@pytest.mark.asyncio
async def test_wrong_length_replacement_is_refused():
    headers = build_chain()

    async def short_repairer(heights):
        return {height: b'\x00' * (HEADER_SIZE - 1) for height in heights}

    db = make_db(headers, blank_heights=(3,), repairer=short_repairer)

    with pytest.raises(DB.DBError) as exc:
        await db.read_headers(0, CHAIN_LEN)
    assert 'expected' in str(exc.value)
    assert db.headers_file.synced_writes == 0


@pytest.mark.asyncio
async def test_scan_reports_damage_without_repairing():
    headers = build_chain()
    db = make_db(headers, blank_heights=(1, 4))

    assert await db.scan_header_records() == [1, 4]
    assert db.headers_file.synced_writes == 0


@pytest.mark.asyncio
async def test_scan_repairs_every_damaged_record():
    headers = build_chain()
    db = make_db(headers, blank_heights=(1, 4), repairer=repairer_for(headers))

    assert await db.scan_header_records(repair=True) == [1, 4]
    assert db.headers_file.data == bytearray(b''.join(headers))
    assert await db.scan_header_records() == []


@pytest.mark.asyncio
async def test_clean_store_scans_clean():
    headers = build_chain()
    db = make_db(headers)
    assert await db.scan_header_records() == []
