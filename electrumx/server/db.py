# Copyright (c) 2016-2020, Neil Booth
# Copyright (c) 2017, the ElectrumX authors
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Interface to the blockchain database.'''


import ast
import copy
import os
import time
from array import array
from bisect import bisect_right
from collections import namedtuple

import attr
from aiorpcx import run_in_thread, sleep

from electrumx.lib import util
from electrumx.lib.hash import hash_to_hex_str, HASHX_LEN
from electrumx.lib.merkle import Merkle, MerkleCache
from electrumx.lib.util import (
    formatted_time, pack_be_uint32, pack_le_uint32,
    unpack_le_uint32, unpack_be_uint32, unpack_le_uint64, base_encode,
)
from electrumx.server.history import History
from electrumx.server.storage import db_class

ASSET = namedtuple("ASSET", "tx_num tx_pos tx_hash height name value")
UTXO = namedtuple("UTXO", "tx_num tx_pos tx_hash height value")


@attr.s(slots=True)
class FlushData(object):
    state = attr.ib()
    headers = attr.ib()
    block_tx_hashes = attr.ib()
    # The following are flushed to the UTXO DB if undo_infos is not None
    undo_infos = attr.ib()
    adds = attr.ib()
    deletes = attr.ib()
    # Assets
    asset_adds = attr.ib()
    asset_deletes = attr.ib()
    asset_meta_adds = attr.ib()
    asset_meta_reissues = attr.ib()
    asset_undo_infos = attr.ib()
    asset_meta_undos = attr.ib()
    asset_meta_deletes = attr.ib()

    # Asset Qualifiers
    h160_qualifier = attr.ib()
    h160_qualifier_undos = attr.ib()
    h160_qualifier_deletes = attr.ib()

    restricted_freezes = attr.ib()
    restricted_freezes_undos = attr.ib()
    restricted_freezes_deletes = attr.ib()

    restricted_strings = attr.ib()
    restricted_strings_undos = attr.ib()
    restricted_strings_deletes = attr.ib()

    qualifier_associations = attr.ib()
    qualifier_associations_undos = attr.ib()
    qualifier_associations_deletes = attr.ib()

    # Broadcasts
    asset_broadcasts = attr.ib()
    asset_broadcasts_undo = attr.ib()
    asset_broadcasts_del = attr.ib()


@attr.s(slots=True)
class ChainState:
    height = attr.ib()
    tx_count = attr.ib()
    asset_count = attr.ib()
    chain_size = attr.ib()
    tip = attr.ib()
    flush_count = attr.ib()   # of UTXOs
    sync_time = attr.ib()    # Cumulative
    flush_time = attr.ib()    # Time of flush
    first_sync = attr.ib()
    db_version = attr.ib()

    def copy(self):
        return copy.copy(self)


class DB:
    '''Simple wrapper of the backend database for querying.

    Performs no DB update, though the DB will be cleaned on opening if
    it was shutdown uncleanly.
    '''

    DB_VERSIONS = [8]

    class DBError(Exception):
        '''Raised on general DB errors generally indicating corruption.'''

    def __init__(self, env):
        self.logger = util.class_logger(__name__, self.__class__.__name__)
        self.env = env
        self.coin = env.coin

        self.header_offset = self.coin.static_header_offset
        self.header_len = self.coin.static_header_len

        self.logger.info(f'switching current directory to {env.db_dir}')
        os.chdir(env.db_dir)

        self.db_class = db_class(self.env.db_engine)
        self.history = History()
        self.utxo_db = None
        self.state = None
        self.last_flush_state = None

        self.fs_height = -1
        self.fs_tx_count = 0
        self.fs_asset_count = 0
        
        self.tx_counts = None
        
        self.asset_db = None
        self.asset_info_db = None

        self.logger.info(f'using {self.env.db_engine} for DB backend')

        # Header merkle cache
        self.merkle = Merkle()
        self.header_mc = MerkleCache(self.merkle, self.fs_block_hashes)

        self.headers_file = util.LogicalFile('meta/headers', 2, 16000000)
        self.tx_counts_file = util.LogicalFile('meta/txcounts', 2, 2000000)
        self.hashes_file = util.LogicalFile('meta/hashes', 4, 16000000)

    async def _read_tx_counts(self):
        if self.tx_counts is not None:
            return
        # tx_counts[N] has the cumulative number of txs at the end of
        # height N.  So tx_counts[0] is 1 - the genesis coinbase
        size = (self.state.height + 1) * 8
        tx_counts = self.tx_counts_file.read(0, size)
        assert len(tx_counts) == size
        self.tx_counts = array('Q', tx_counts)
        if self.tx_counts:
            assert self.state.tx_count == self.tx_counts[-1]
        else:
            assert self.state.tx_count == 0

    async def _open_dbs(self, for_sync, compacting):
        assert self.utxo_db is None
        assert self.asset_db is None
        assert self.asset_info_db is None

        # First UTXO DB
        self.utxo_db = self.db_class('utxo', for_sync)
        if self.utxo_db.is_new:
            self.logger.info('created new database')
            self.logger.info('creating metadata directory')
            os.mkdir('meta')
            with util.open_file('COIN', create=True) as f:
                f.write(f'ElectrumX databases and metadata for '
                        f'{self.coin.NAME} {self.coin.NET}'.encode())
        else:
            self.logger.info(f'opened UTXO DB (for sync: {for_sync})')
        self.read_utxo_state()

        # Asset DB
        self.asset_db = self.db_class('asset', for_sync)
        self.asset_info_db = self.db_class('asset_info', for_sync)

        # Then history DB
        self.state.flush_count = self.history.open_db(self.db_class, for_sync,
                                                      self.state.flush_count,
                                                      compacting)
        self.clear_excess_undo_info()

        # Read TX counts (requires meta directory)
        await self._read_tx_counts()
        return self.state

    async def open_for_compacting(self):
        return await self._open_dbs(True, True)

    async def open_for_sync(self):
        '''Open the databases to sync to the daemon.

        When syncing we want to reserve a lot of open files for the
        synchronization.  When serving clients we want the open files for
        serving network connections.
        '''
        return await self._open_dbs(True, False)

    async def open_for_serving(self):
        '''Open the databases for serving.  If they are already open they are
        closed first.
        '''
        if self.utxo_db:
            self.logger.info('closing DBs to re-open for serving')
            self.utxo_db.close()
            self.asset_db.close()
            self.asset_info_db.close()
            self.history.close_db()
            self.utxo_db = None
            self.asset_db = None
            self.asset_info_db = None
        return await self._open_dbs(False, False)

    # Header merkle cache
    async def populate_header_merkle_cache(self):
        self.logger.info('populating header merkle cache...')
        length = max(1, self.state.height - self.env.reorg_limit)
        start = time.monotonic()
        await self.header_mc.initialize(length)
        elapsed = time.monotonic() - start
        self.logger.info(f'header merkle cache populated in {elapsed:.1f}s')

    async def header_branch_and_root(self, length, height):
        return await self.header_mc.branch_and_root(length, height)

    # Flushing
    def assert_flushed(self, flush_data):
        '''Asserts state is fully flushed.'''
        assert flush_data.state.tx_count == self.fs_tx_count == self.state.tx_count
        assert flush_data.state.asset_count == self.fs_asset_count == self.state.asset_count
        assert flush_data.state.height == self.fs_height == self.state.height
        assert flush_data.state.tip == self.state.tip
        assert not flush_data.headers
        assert not flush_data.block_tx_hashes
        assert not flush_data.adds
        assert not flush_data.deletes
        assert not flush_data.undo_infos

        assert not flush_data.asset_adds
        assert not flush_data.asset_deletes
        assert not flush_data.asset_undo_infos

        assert not flush_data.asset_meta_adds
        assert not flush_data.asset_meta_reissues
        assert not flush_data.asset_meta_undos
        assert not flush_data.asset_meta_deletes

        assert not flush_data.h160_qualifier
        assert not flush_data.h160_qualifier_undos
        assert not flush_data.h160_qualifier_deletes

        assert not flush_data.restricted_freezes
        assert not flush_data.restricted_freezes_undos
        assert not flush_data.restricted_freezes_deletes

        assert not flush_data.restricted_strings
        assert not flush_data.restricted_strings_undos
        assert not flush_data.restricted_strings_deletes

        assert not flush_data.qualifier_associations
        assert not flush_data.qualifier_associations_undos
        assert not flush_data.qualifier_associations_deletes

        assert not flush_data.asset_broadcasts
        assert not flush_data.asset_broadcasts_undo
        assert not flush_data.asset_broadcasts_del

        self.history.assert_flushed()

    def flush_dbs(self, flush_data, flush_utxos, size_remaining):
        '''Flush out cached state.  History is always flushed; UTXOs are
        flushed if flush_utxos.'''
        if flush_data.state.height == self.state.height:
            self.assert_flushed(flush_data)
            return

        start_time = time.time()

        # Flush to file system
        self.flush_fs(flush_data)

        # Then history
        self.flush_history()
        flush_data.state.flush_count = self.history.flush_count

        # Flush state last as it reads the wall time.
        if flush_utxos:
            self.flush_asset_db(flush_data)
            self.flush_asset_info_db(flush_data)
            self.flush_utxo_db(flush_data)

        end_time = time.time()
        elapsed = end_time - start_time
        flush_interval = end_time - self.last_flush_state.flush_time
        flush_data.state.flush_time = end_time
        flush_data.state.sync_time += flush_interval

        # Update and flush state again so as not to drop the batch commit time
        if flush_utxos:
            self.state = flush_data.state.copy()
            self.write_utxo_state(self.utxo_db)

        tx_delta = flush_data.state.tx_count - self.last_flush_state.tx_count
        asset_delta = flush_data.state.asset_count - self.last_flush_state.asset_count
        size_delta = flush_data.state.chain_size - self.last_flush_state.chain_size

        self.logger.info(f'flush #{self.history.flush_count:,d} took {elapsed:.1f}s.  '
                         f'Height {flush_data.state.height:,d} '
                         f'txs: {flush_data.state.tx_count:,d} ({tx_delta:+,d}) '
                         f'assets: {flush_data.state.asset_count:,d} ({asset_delta:+,d}) '
                         f'size: {flush_data.state.chain_size:,d} ({size_delta:+,d})')

        # Catch-up stats
        if self.utxo_db.for_sync:
            size_per_sec_gen = flush_data.state.chain_size / (flush_data.state.sync_time + 0.01)
            size_per_sec_last = size_delta / (flush_interval + 0.01)
            eta = size_remaining / (size_per_sec_last + 0.01) * 1.1
            self.logger.info(f'MB/sec since genesis: {size_per_sec_gen / 1_000_000:.2f}, '
                             f'since last flush: {size_per_sec_last / 1_000_000:.2f}')
            self.logger.info(f'sync time: {formatted_time(flush_data.state.sync_time)}  '
                             f'ETA: {formatted_time(eta)}')

        self.last_flush_state = flush_data.state.copy()

    def flush_fs(self, flush_data):
        '''Write headers, tx counts and block tx hashes to the filesystem.

        The first height to write is self.fs_height + 1.  The FS
        metadata is all append-only, so in a crash we just pick up
        again from the height stored in the DB.
        '''
        prior_tx_count = (self.tx_counts[self.fs_height]
                          if self.fs_height >= 0 else 0)
        assert len(flush_data.block_tx_hashes) == len(flush_data.headers)
        assert flush_data.state.height == self.fs_height + len(flush_data.headers)
        assert flush_data.state.tx_count == (self.tx_counts[-1] if self.tx_counts else 0)
        assert len(self.tx_counts) == flush_data.state.height + 1
        hashes = b''.join(flush_data.block_tx_hashes)
        flush_data.block_tx_hashes.clear()
        assert len(hashes) % 32 == 0
        assert len(hashes) // 32 == flush_data.state.tx_count - prior_tx_count

        # Write the headers, tx counts, and tx hashes
        height_start = self.fs_height + 1
        offset = self.header_offset(height_start)
        self.headers_file.write(offset, b''.join(flush_data.headers))
        flush_data.headers.clear()

        offset = height_start * self.tx_counts.itemsize
        self.tx_counts_file.write(offset,
                                  self.tx_counts[height_start:].tobytes())
        offset = prior_tx_count * 32
        self.hashes_file.write(offset, hashes)

        self.fs_height = flush_data.state.height
        self.fs_tx_count = flush_data.state.tx_count
        self.fs_asset_count = flush_data.state.asset_count

    def flush_history(self):
        self.history.flush()

    def flush_asset_info_db(self, flush_data: FlushData):
        start_time = time.monotonic()
        adds = len(flush_data.asset_meta_adds)
        reissues = len(flush_data.asset_meta_reissues)

        with self.asset_info_db.write_batch() as batch:
            batch_delete = batch.delete
            for key in flush_data.asset_meta_deletes:
                batch_delete(key)
            flush_data.asset_meta_deletes.clear()

            batch_put = batch.put
            for key, value in flush_data.asset_meta_reissues.items():
                batch_put(key, value)
            flush_data.asset_meta_reissues.clear()

            for key, value in flush_data.asset_meta_adds.items():
                batch_put(key, value)
            flush_data.asset_meta_adds.clear()

            self.flush_asset_meta_undos(batch_put, flush_data.asset_meta_undos)
            flush_data.asset_meta_undos.clear()

        if self.asset_info_db.for_sync:
            elapsed = time.monotonic() - start_time
            self.logger.info(f'{adds:,d} assets\' metadata created, '
                             f'{reissues:,d} assets\' metadata reissued, '
                             f'{elapsed:.1f}s, committing...')

    def flush_asset_db(self, flush_data: FlushData):
        start_time = time.monotonic()
        add_count = len(flush_data.asset_adds)
        spend_count = len(flush_data.asset_deletes) // 2

        restricted_assets = len(flush_data.restricted_strings)
        freezes = len(flush_data.restricted_freezes)
        tags = len(flush_data.h160_qualifier)
        quals = len(flush_data.qualifier_associations)

        broadcasts = len(flush_data.asset_broadcasts)

        with self.asset_db.write_batch() as batch:
            # Spends
            batch_delete = batch.delete
            for key in sorted(flush_data.asset_deletes):
                batch_delete(key)
            flush_data.asset_deletes.clear()

            # Qualifiers
            for key in sorted(flush_data.h160_qualifier_deletes):
                batch_delete(b't' + key)
            flush_data.h160_qualifier_deletes.clear()

            for key in sorted(flush_data.restricted_freezes_deletes):
                batch_delete(b'f' + key)
            flush_data.restricted_freezes_deletes.clear()

            for key in sorted(flush_data.restricted_strings_deletes):
                batch_delete(b'r' + key)
            flush_data.restricted_strings_deletes.clear()

            for key in sorted(flush_data.qualifier_associations_deletes):
                batch_delete(b'q' + key)
            flush_data.qualifier_associations_deletes.clear()

            for key in sorted(flush_data.asset_broadcasts_del):
                batch_delete(b'b' + key)
            flush_data.asset_broadcasts_del.clear()

            # New Assets
            batch_put = batch.put
            for key, value in flush_data.asset_adds.items():
                # suffix = tx_idx + tx_num
                # key tx_hash (32), tx_idx (4)
                # value = hashx (11) + tx_num (5) + u64 sat val(8)+ namelen(1) + asset name
                hashX = value[:HASHX_LEN]
                suffix = key[-4:] + value[HASHX_LEN:5+HASHX_LEN]
                batch_put(b'h' + key[:4] + suffix, hashX)
                batch_put(b'u' + hashX + suffix, value[5+HASHX_LEN:])
            flush_data.asset_adds.clear()

            # New undo information
            self.flush_undo_infos(batch_put, flush_data.asset_undo_infos)
            flush_data.asset_undo_infos.clear()

            # New h160 tags
            for key, value in flush_data.h160_qualifier.items():
                # key: h160 + asset
                # value: idx + txnumb + flag
                batch_put(b't' + key, value)
            flush_data.h160_qualifier.clear()
            self.flush_asset_h160_tag_undos(batch_put, flush_data.h160_qualifier_undos)
            flush_data.h160_qualifier_undos.clear()

            # New restricted strings
            for key, value in flush_data.restricted_strings.items():
                batch_put(b'r' + key, value)
            flush_data.restricted_strings.clear()
            self.flush_restricted_string_undos(batch_put, flush_data.restricted_strings_undos)
            flush_data.restricted_strings_undos.clear()

            # New restricted freezes
            for key, value in flush_data.restricted_freezes.items():
                batch_put(b'f' + key, value)
            flush_data.restricted_freezes.clear()
            self.flush_restricted_freeze_undos(batch_put, flush_data.restricted_freezes_undos)
            flush_data.restricted_freezes_undos.clear()

            # New qualifier association
            for key, value in flush_data.qualifier_associations.items():
                batch_put(b'q' + key, value)
            flush_data.qualifier_associations.clear()
            self.flush_qualifier_associations_undos(batch_put, flush_data.qualifier_associations_undos)
            flush_data.qualifier_associations_undos.clear()

            # Asset broadcasts
            for key, value in flush_data.asset_broadcasts.items():
                batch_put(b'b' + key, value)
            flush_data.asset_broadcasts.clear()

            self.flush_asset_broadcast_undos(batch_put, flush_data.asset_broadcasts_undo)
            flush_data.asset_broadcasts_undo.clear()

        if self.asset_db.for_sync:
            elapsed = time.monotonic() - start_time
            self.logger.info(f'{add_count:,d} Asset adds, '
                             f'{spend_count:,d} spends in, '
                             f'{restricted_assets:,d} restricted assets modified, '
                             f'{freezes:,d} retricted asset freezes, '
                             f'{tags:,d} addresses tagged, '
                             f'{quals:,d} qualifier associations changed, '
                             f'{broadcasts:,d} messages broadcast, '
                             f'{elapsed:.1f}s, committing...')


    def flush_utxo_db(self, flush_data):
        '''Flush the cached DB writes and UTXO set to the batch.'''
        # Care is needed because the writes generated by flushing the
        # UTXO state may have keys in common with our write cache or
        # may be in the DB already.
        start_time = time.monotonic()
        add_count = len(flush_data.adds)
        spend_count = len(flush_data.deletes) // 2
        with self.utxo_db.write_batch() as batch:
            # Spends
            batch_delete = batch.delete
            for key in sorted(flush_data.deletes):
                batch_delete(key)
            flush_data.deletes.clear()

            # New UTXOs
            batch_put = batch.put
            for key, value in flush_data.adds.items():
                # suffix = tx_idx + tx_num
                hashX = value[:-13]
                suffix = key[-4:] + value[-13:-8]
                batch_put(b'h' + key[:4] + suffix, hashX)
                batch_put(b'u' + hashX + suffix, value[-8:])
            flush_data.adds.clear()

            # New undo information
            self.flush_undo_infos(batch_put, flush_data.undo_infos)
            flush_data.undo_infos.clear()

            if self.utxo_db.for_sync:
                block_count = flush_data.state.height - self.state.height
                tx_count = flush_data.state.tx_count - self.state.tx_count
                size = (flush_data.state.chain_size - self.state.chain_size) / 1_000_000_000
                elapsed = time.monotonic() - start_time
                self.logger.info(f'flushed {block_count:,d} blocks size {size:.1f} GB with '
                                 f'{tx_count:,d} txs, {add_count:,d} UTXO adds, '
                                 f'{spend_count:,d} spends in '
                                 f'{elapsed:.1f}s, committing...')

            self.state = flush_data.state.copy()
            self.write_utxo_state(batch)

    def flush_backup(self, flush_data, touched):
        '''Like flush_dbs() but when backing up.  All UTXOs are flushed.'''
        assert not flush_data.headers
        assert not flush_data.block_tx_hashes
        assert flush_data.state.height < self.state.height
        self.history.assert_flushed()

        start_time = time.time()
        
        self.backup_fs(flush_data.state.height, flush_data.state.tx_count, flush_data.state.asset_count)
        self.history.backup(touched, flush_data.state.tx_count)

        self.flush_utxo_db(flush_data)
        self.flush_asset_db(flush_data)
        self.flush_asset_info_db(flush_data)

        elapsed = time.time() - start_time
        tx_delta = flush_data.state.tx_count - self.last_flush_state.tx_count
        asset_delta = flush_data.state.asset_count - self.last_flush_state.asset_count
        size_delta = flush_data.state.chain_size - self.last_flush_state.chain_size

        self.logger.info(f'backup flush #{self.history.flush_count:,d} took '
                         f'{elapsed:.1f}s.  Height {flush_data.state.height:,d} '
                         f'txs: {flush_data.state.tx_count:,d} ({tx_delta:+,d}) '
                         f'assets: {flush_data.state.asset_count:,d} ({asset_delta:+,d}) '
                         f'size: {flush_data.state.chain_size:,d} ({size_delta:+,d})')

        self.last_flush_state = flush_data.state.copy()

    def backup_fs(self, height, tx_count, asset_count):
        '''Back up during a reorg.  This just updates our pointers.'''
        self.fs_height = height
        self.fs_tx_count = tx_count
        self.fs_asset_count = asset_count
        # Truncate header_mc: header count is 1 more than the height.
        self.header_mc.truncate(height + 1)

    async def raw_header(self, height):
        '''Return the binary header at the given height.'''
        header, n = await self.read_headers(height, 1)
        if n != 1:
            raise IndexError(f'height {height:,d} out of range')
        return header

    async def read_headers(self, start_height, count):
        '''Requires start_height >= 0, count >= 0.  Reads as many headers as are available
        starting at start_height up to count.  This would be zero if start_height is
        beyond state.height, for example.

        Returns a (binary, n) pair where binary is the concatenated binary headers, and n
        is the count of headers returned.
        '''
        if start_height < 0 or count < 0:
            raise self.DBError(f'{count:,d} headers starting at '
                               f'{start_height:,d} not on disk')

        def read_headers():
            # Read some from disk
            disk_count = max(0, min(count, self.state.height + 1 - start_height))
            if disk_count:
                offset = self.header_offset(start_height)
                size = self.header_offset(start_height + disk_count) - offset
                return self.headers_file.read(offset, size), disk_count
            return b'', 0

        return await run_in_thread(read_headers)

    def fs_tx_hash(self, tx_num):
        '''Return a pair (tx_hash, tx_height) for the given tx number.

        If the tx_height is not on disk, returns (None, tx_height).'''
        tx_height = bisect_right(self.tx_counts, tx_num)
        if tx_height > self.state.height:
            tx_hash = None
        else:
            tx_hash = self.hashes_file.read(tx_num * 32, 32)
        return tx_hash, tx_height

    def fs_tx_hashes_at_blockheight(self, block_height):
        '''Return a list of tx_hashes at given block height,
        in the same order as in the block.
        '''
        if block_height > self.state.height:
            raise self.DBError(f'block {block_height:,d} not on disk (>{self.state.height:,d})')
        assert block_height >= 0
        if block_height > 0:
            first_tx_num = self.tx_counts[block_height - 1]
        else:
            first_tx_num = 0
        num_txs_in_block = self.tx_counts[block_height] - first_tx_num
        tx_hashes = self.hashes_file.read(first_tx_num * 32, num_txs_in_block * 32)
        assert num_txs_in_block == len(tx_hashes) // 32
        return [tx_hashes[idx * 32: (idx+1) * 32] for idx in range(num_txs_in_block)]

    async def tx_hashes_at_blockheight(self, block_height):
        return await run_in_thread(self.fs_tx_hashes_at_blockheight, block_height)

    async def fs_block_hashes(self, height, count):
        headers_concat, headers_count = await self.read_headers(height, count)
        if headers_count != count:
            raise self.DBError(f'only got {headers_count:,d} headers starting at {height:,d}, '
                               f'not {count:,d}')
        offset = 0
        headers = []
        for n in range(count):
            hlen = self.header_len(height + n)
            headers.append(headers_concat[offset:offset + hlen])
            offset += hlen

        return [self.coin.header_hash(header) for header in headers]

    async def limited_history(self, hashX, *, limit=1000):
        '''Return an unpruned, sorted list of (tx_hash, height) tuples of
        confirmed transactions that touched the address, earliest in
        the blockchain first.  Includes both spending and receiving
        transactions.  By default returns at most 1000 entries.  Set
        limit to None to get them all.
        '''
        def read_history():
            tx_nums = list(self.history.get_txnums(hashX, limit))
            fs_tx_hash = self.fs_tx_hash
            return [fs_tx_hash(tx_num) for tx_num in tx_nums]

        while True:
            history = await run_in_thread(read_history)
            if all(hash is not None for hash, height in history):
                return history
            self.logger.warning('limited_history: tx hash not found (reorg?), retrying...')
            await sleep(0.25)

    # -- Undo information

    def min_undo_height(self, max_height):
        '''Returns a height from which we should store undo info.'''
        return max_height - self.env.reorg_limit + 1

    def undo_key_meta(self, height):
        return b'u' + pack_be_uint32(height)

    def undo_key_broadcast(self, height):
        return b'B' + pack_be_uint32(height)

    def undo_key_tag(self, height):
        return b'T' + pack_be_uint32(height)

    def undo_key_res_string(self, height):
        return b'R' + pack_be_uint32(height)

    def undo_key_res_freeze(self, height):
        return b'F' + pack_be_uint32(height)

    def undo_key_qual(self, height):
        return b'Q' + pack_be_uint32(height)

    def undo_key(self, height):
        '''DB key for undo information at the given height.'''
        return b'U' + pack_be_uint32(height)

    def read_asset_broadcast_undo_info(self, height):
        return self.asset_info_db.get(self.undo_key_broadcast(height))

    def read_h160_tag_undo_info(self, height):
        return self.asset_db.get(self.undo_key_tag(height))

    def read_res_freeze_undo_info(self, height):
        return self.asset_db.get(self.undo_key_res_freeze(height))

    def read_res_string_undo_info(self, height):
        return self.asset_db.get(self.undo_key_res_string(height))

    def read_qual_undo_info(self, height):
        return self.asset_db.get(self.undo_key_qual(height))

    def read_asset_meta_undo_info(self, height):
        return self.asset_info_db.get(self.undo_key(height))

    def read_asset_undo_info(self, height):
        return self.asset_db.get(self.undo_key(height))

    def read_undo_info(self, height):
        '''Read undo information from a file for the current height.'''
        return self.utxo_db.get(self.undo_key(height))

    def flush_qualifier_associations_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_qual(height), b''.join(undo_info))

    def flush_restricted_freeze_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_res_freeze(height), b''.join(undo_info))

    def flush_restricted_string_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_res_string(height), b''.join(undo_info))

    def flush_asset_h160_tag_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_tag(height), b''.join(undo_info))

    def flush_asset_broadcast_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_broadcast(height), b''.join(undo_info))

    def flush_asset_meta_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_meta(height), b''.join(undo_info))

    

    def flush_undo_infos(self, batch_put, undo_infos):
        '''undo_infos is a list of (undo_info, height) pairs.'''
        for undo_info, height in undo_infos:
            batch_put(self.undo_key(height), b''.join(undo_info))

    def clear_excess_undo_info(self):
        '''Clear excess undo info.  Only most recent N are kept.'''
        prefix = b'U'
        min_height = self.min_undo_height(self.state.height)
        keys = []
        for key, _hist in self.utxo_db.iterator(prefix=prefix):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        if keys:
            with self.utxo_db.write_batch() as batch:
                for key in keys:
                    batch.delete(key)
            self.logger.info(f'deleted {len(keys):,d} stale undo entries')

        keys = []
        for key, _hist in self.asset_info_db.iterator(prefix=prefix):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)
        if keys:
            with self.asset_info_db.write_batch() as batch:
                for key in keys:
                    batch.delete(key)
            self.logger.info(f'deleted {len(keys):,d} stale asset meta undo entries')

        keys = []
        for key, _hist in self.asset_db.iterator(prefix=prefix):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'T'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'F'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'R'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'Q'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'B'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        if keys:
            with self.asset_db.write_batch() as batch:
                for key in keys:
                    batch.delete(key)
                self.logger.info(f'deleted {len(keys):,d} stale asset undo entries')

    # -- UTXO database

    def read_utxo_state(self):
        now = time.time()
        state = self.utxo_db.get(b'state')
        if not state:
            state = ChainState(height=-1, tx_count=0, asset_count=0, chain_size=0, tip=bytes(32),
                    flush_count=0, sync_time=0, flush_time=now,
                    first_sync=True, db_version=max(self.DB_VERSIONS))
        else:
            state = ast.literal_eval(state.decode())
            if not isinstance(state, dict):
                raise self.DBError('failed reading state from DB')
            
            if state['genesis'] != self.coin.GENESIS_HASH:
                raise self.DBError(f'DB genesis hash {state["genesis"]} does not match '
                                   f'coin {self.coin.GENESIS_HASH}')

            state = ChainState(
                height=state['height'],
                tx_count=state['tx_count'],
                asset_count=state['asset_count'],
                chain_size=state.get('chain_size', 0),
                tip=state['tip'],
                flush_count=state['utxo_flush_count'],
                sync_time=state['wall_time'],
                flush_time=now,
                first_sync=state['first_sync'],
                db_version=state['db_version'],
            )

        self.state = state
        self.last_flush_state = state.copy()
        if state.db_version not in self.DB_VERSIONS:
            raise self.DBError(f'your UTXO DB version is {state.db_version} but this '
                               f'software only handles versions {self.DB_VERSIONS}')

        # These are as we flush data to disk ahead of DB state
        self.fs_height = state.height
        self.fs_tx_count = state.tx_count
        self.fs_asset_count = state.asset_count

        # Log some stats
        self.logger.info('UTXO DB version: {:d}'.format(state.db_version))
        self.logger.info('coin: {}'.format(self.coin.NAME))
        self.logger.info(f'height: {state.height:,d}')
        self.logger.info(f'tip: {hash_to_hex_str(state.tip)}')
        self.logger.info(f'tx count: {state.tx_count:,d}')
        self.logger.info(f'chain size: {state.chain_size // 1_000_000_000} GB '
                         f'({state.chain_size:,d} bytes)')
        self.logger.info('VOUT debugging: {}'.format(self.env.write_bad_vouts_to_file))
        if self.utxo_db.for_sync:
            self.logger.info(f'flushing DB cache at {self.env.cache_MB:,d} MB')
        if self.state.first_sync:
            self.logger.info(f'sync time so far: {util.formatted_time(state.sync_time)}')

    def write_utxo_state(self, batch):
        '''Write (UTXO) state to the batch.'''
        state = {
            'genesis': self.coin.GENESIS_HASH,
            'height': self.state.height,
            'tx_count': self.state.tx_count,
            'asset_count': self.state.asset_count,
            'chain_size': self.state.chain_size,
            'tip': self.state.tip,
            'utxo_flush_count': self.state.flush_count,
            'wall_time': self.state.sync_time,
            'first_sync': self.state.first_sync,
            'db_version': self.state.db_version,
        }
        batch.put(b'state', repr(state).encode())

    def set_flush_count(self, count):
        self.state.flush_count = count
        self.write_utxo_state(self.utxo_db)

    async def all_assets(self, hashX):
        def read_assets():
            assets = []
            assets_append = assets.append
            prefix = b'u' + hashX
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                value, = unpack_le_uint64(db_value[:8])
                name = db_value[9:].decode('ascii')
                assets_append(ASSET(tx_num, tx_pos, tx_hash, height, name, value))
            return assets

        while True:
            assets = await run_in_thread(read_assets)
            if all(asset.tx_hash is not None for asset in assets):
                return assets
            self.logger.warning('all_assets: tx hash not found (reorg?), retrying...')
            await sleep(0.25)

    async def all_utxos(self, hashX):
        '''Return all UTXOs for an address sorted in no particular order.'''
        def read_utxos():
            utxos = []
            utxos_append = utxos.append
            # Key: b'u' + address_hashX + tx_idx + tx_num
            # Value: the UTXO value as a 64-bit unsigned integer
            prefix = b'u' + hashX
            for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                value, = unpack_le_uint64(db_value)
                if value > 0:
                    # Values of 0 will only be assets.
                    # Get them from all_assets
                    tx_pos, = unpack_le_uint32(db_key[-9:-5])
                    tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                    tx_hash, height = self.fs_tx_hash(tx_num)
                    utxos_append(UTXO(tx_num, tx_pos, tx_hash, height, value))
            return utxos

        while True:
            utxos = await run_in_thread(read_utxos)
            if all(utxo.tx_hash is not None for utxo in utxos):
                return utxos
            self.logger.warning('all_utxos: tx hash not found (reorg?), retrying...')
            await sleep(0.25)

    async def lookup_utxos(self, prevouts):
        '''For each prevout, lookup it up in the DB and return a (hashX,
        value) pair or None if not found.

        Used by the mempool code.
        '''
        def lookup_hashXs():
            '''Return (hashX, suffix) pairs, or None if not found,
            for each prevout.
            '''
            def lookup_hashX(tx_hash, tx_idx):
                idx_packed = pack_le_uint32(tx_idx)

                # Key: b'h' + compressed_tx_hash + tx_idx + tx_num
                # Value: hashX
                prefix = b'h' + tx_hash[:4] + idx_packed

                # Find which entry, if any, the TX_HASH matches.
                for db_key, hashX in self.utxo_db.iterator(prefix=prefix):
                    tx_num_packed = db_key[-5:]
                    tx_num, = unpack_le_uint64(tx_num_packed + bytes(3))
                    fs_hash, _height = self.fs_tx_hash(tx_num)
                    if fs_hash == tx_hash:
                        return hashX, idx_packed + tx_num_packed
                return None, None
            return [lookup_hashX(*prevout) for prevout in prevouts]

        def lookup_utxos(hashX_pairs):
            def lookup_utxo(hashX, suffix):
                if not hashX:
                    # This can happen when the daemon is a block ahead
                    # of us and has mempool txs spending outputs from
                    # that new block
                    return None
                # Key: b'u' + address_hashX + tx_idx + tx_num
                # Value: the UTXO value as a 64-bit unsigned integer
                key = b'u' + hashX + suffix
                db_value = self.utxo_db.get(key)
                if not db_value:
                    # This can happen if the DB was updated between
                    # getting the hashXs and getting the UTXOs
                    return None
                value, = unpack_le_uint64(db_value)
                # possible for assets
                #if value == 0:
                #    return None
                return hashX, value
            return [lookup_utxo(*hashX_pair) for hashX_pair in hashX_pairs]

        hashX_pairs = await run_in_thread(lookup_hashXs)
        return [i for i in await run_in_thread(lookup_utxos, hashX_pairs) if i]

    # For external use
    
    async def is_h160_qualified(self, h160: bytes, qualifier: bytes):
        def lookup_h160():
            key = b't' + bytes([len(h160)]) + h160 + bytes([len(qualifier)]) + qualifier
            db_ret = self.asset_db.get(key, None)
            ret_val = {}
            if db_ret:
                tx_pos, = unpack_le_uint32(db_ret[:4])
                tx_num, = unpack_le_uint64(db_ret[4:9] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                flag = db_ret[-1]
                ret_val['flag'] = True if flag != 0 else False
                ret_val['height'] = height
                ret_val['tx_hash'] = hash_to_hex_str(tx_hash)
                ret_val['tx_pos'] = tx_pos
            else:
                ret_val['flag'] = False
            return ret_val
        return await run_in_thread(lookup_h160)

    async def qualifications_for_h160(self, h160: bytes):
        def lookup_quals():
            prefix = b't' + bytes([len(h160)]) + h160
            prefix_len = len(prefix) + 1
            ret_val = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_value[:4])
                tx_num, = unpack_le_uint64(db_value[4:9] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                flag = db_value[-1]
                asset_name = db_key[prefix_len:].decode('ascii')
                ret_val[asset_name] = {
                    'flag': True if flag != 0 else False,
                    'height': height,
                    'tx_hash': hash_to_hex_str(tx_hash),
                    'tx_pos': tx_pos
                }
            return ret_val
        return await run_in_thread(lookup_quals)

    async def is_restricted_frozen(self, asset: bytes):
        def lookup_restricted():
            key = b'f' + bytes([len(asset)]) + asset
            db_ret = self.asset_db.get(key, None)
            ret_val = {}
            if db_ret:
                tx_pos, = unpack_le_uint32(db_ret[:4])
                tx_num, = unpack_le_uint64(db_ret[4:9] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                flag = db_ret[-1]
                ret_val['frozen'] = True if flag != 0 else False
                ret_val['height'] = height
                ret_val['tx_hash'] = hash_to_hex_str(tx_hash)
                ret_val['tx_pos'] = tx_pos
            else:
                ret_val['frozen'] = False
            return ret_val
        return await run_in_thread(lookup_restricted)

    async def get_restricted_string(self, asset: bytes):
        def lookup_restricted():
            key = b'r' + bytes([len(asset)]) + asset
            db_ret = self.asset_db.get(key, None)
            ret_val = {}
            if db_ret:
                restricted_tx_pos, = unpack_le_uint32(db_ret[:4])
                qualifying_tx_pos, = unpack_le_uint32(db_ret[4:8])
                tx_num, = unpack_le_uint64(db_ret[8:13] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                string = db_ret[14:].decode('ascii')
                ret_val['string'] = string
                ret_val['height'] = height
                ret_val['tx_hash'] = hash_to_hex_str(tx_hash)
                ret_val['restricted_tx_pos'] = restricted_tx_pos
                ret_val['qualifying_tx_pos'] = qualifying_tx_pos
            return ret_val
        return await run_in_thread(lookup_restricted)

    async def lookup_qualifier_associations(self, asset:bytes):
        def lookup_associations():
            prefix = b'q' + bytes([len(asset)]) + asset
            prefix_len = len(prefix) + 1
            ret_val = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                restricted_tx_pos, = unpack_le_uint32(db_value[:4])
                qualifying_tx_pos, = unpack_le_uint32(db_value[4:8])
                tx_num, = unpack_le_uint64(db_value[8:13] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                flag = db_value[-1]
                asset_name = db_key[prefix_len:].decode('ascii')
                ret_val[asset_name] = {
                    'associated': True if flag != 0 else False,
                    'height': height,
                    'tx_hash': hash_to_hex_str(tx_hash),
                    'restricted_tx_pos': restricted_tx_pos,
                    'qualifying_tx_pos': qualifying_tx_pos
                }
            return ret_val
        return await run_in_thread(lookup_associations)

    async def lookup_messages(self, asset_name: bytes):
        def read_messages():
            prefix = b'b' + bytes([len(asset_name)]) + asset_name
            ret_val = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                hash = db_value[:34]
                expire, = unpack_le_uint64(db_value[34:])
                ret_val[hash_to_hex_str(tx_hash)] = {
                    'data': base_encode(hash, 58),
                    'expiration': expire,
                    'height': height,
                    'tx_pos': tx_pos,
                }
            return ret_val
        return await run_in_thread(read_messages)

    async def get_assets_with_prefix(self, prefix: bytes):
        def find_assets():
            return [asset.decode('ascii') for asset, _ in self.asset_info_db.iterator(prefix=prefix)]
        return await run_in_thread(find_assets)

    async def lookup_asset_meta(self, asset_name):
        def read_assets_meta():
            b = self.asset_info_db.get(asset_name)
            if not b:
                return {}

            # outpoint types:
            # 0: latest
            # 1: div loc
            # 2: ipfs loc

            # (total sats: 8) 
            # (div amt: 1)
            # (reissueable: 1)
            # (has ipfs: 1)
            # (ipfs: 34: conditional: has ipfs)
            # (outpoint count: 1)
            # (outpoint type: 1)
            # (outpoint 1 idx: 4)
            # (outpoint 1 numb: 5)
            # (outpoint type: 1)
            # (outpoint 2 idx: 4: conditional: div is 0xff)
            # (outpoint 2 numb: 5: conditional: div is 0xff)
            # (outpoint type: 1)
            # (outpoint 3 idx: 4: conditional: has ipfs but remains unchanged)
            # (outpoint 3 numb: 5: conditional: has ipfs but remains unchanged)
            
            data_parser = util.DataParser(b)
            sats_in_circulation = data_parser.read_bytes(8)
            div_amt = data_parser.read_int()
            reissuable = data_parser.read_boolean()
            has_ipfs = data_parser.read_boolean()
            to_ret = {
                'sats_in_circulation': int.from_bytes(sats_in_circulation, 'little', signed=False),
                'divisions': div_amt,
                'reissuable': reissuable,
                'has_ipfs': has_ipfs
            }
            if has_ipfs:
                ipfs_data = data_parser.read_bytes(34)
                to_ret['ipfs'] = base_encode(ipfs_data, 58)

            for _ in range(data_parser.read_int()):
                outpoint_type = data_parser.read_int()
                idx = data_parser.read_bytes(4)
                tx_numb = data_parser.read_bytes(5)

                tx_pos, = unpack_le_uint32(idx)
                tx_num, = unpack_le_uint64(tx_numb + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                
                if outpoint_type == 0:
                    key = 'source'
                elif outpoint_type == 1:
                    key = 'source_divisions'
                elif outpoint_type == 2:
                    key = 'source_ipfs'
                else:
                    key = 'unknown_outpoint'
                to_ret[key] = {
                        'tx_hash': hash_to_hex_str(tx_hash),
                        'tx_pos': tx_pos,
                        'height': height
                    }

            return to_ret
        return await run_in_thread(read_assets_meta)

    async def lookup_assets(self, prevouts):
        '''For each prevout, lookup it up in the DB and return a (hashX,
        value) pair or None if not found.

        Used by the mempool code.
        '''
        def lookup_hashXs():
            '''Return (hashX, suffix) pairs, or None if not found,
            for each prevout.
            '''
            def lookup_hashX(tx_hash, tx_idx):
                idx_packed = pack_le_uint32(tx_idx)

                # Key: b'h' + compressed_tx_hash + tx_idx + tx_num
                # Value: hashX
                prefix = b'h' + tx_hash[:4] + idx_packed

                # Find which entry, if any, the TX_HASH matches.
                for db_key, hashX in self.asset_db.iterator(prefix=prefix):
                    tx_num_packed = db_key[-5:]
                    tx_num, = unpack_le_uint64(tx_num_packed + bytes(3))
                    fs_hash, _height = self.fs_tx_hash(tx_num)
                    if fs_hash == tx_hash:
                        return hashX, idx_packed + tx_num_packed
                return None, None
            return [lookup_hashX(*prevout) for prevout in prevouts]

        def lookup_assets(hashX_pairs):
            def lookup_asset(hashX, suffix):
                if not hashX:
                    # This can happen when the daemon is a block ahead
                    # of us and has mempool txs spending outputs from
                    # that new block
                    return None
                # Key: b'u' + address_hashX + tx_idx + tx_num
                # Value: the UTXO value as a 64-bit unsigned integer
                key = b'u' + hashX + suffix
                db_value = self.asset_db.get(key)
                if not db_value:
                    # This can happen if the DB was updated between
                    # getting the hashXs and getting the UTXOs
                    return None

                value, = unpack_le_uint64(db_value[:8])
                name = db_value[9:].decode('ascii')

                return hashX, value, name
            return [lookup_asset(*hashX_pair) for hashX_pair in hashX_pairs]

        hashX_pairs = await run_in_thread(lookup_hashXs)
        return [i for i in await run_in_thread(lookup_assets, hashX_pairs) if i]