# Copyright (c) 2016-2020, Neil Booth
# Copyright (c) 2017, the ElectrumX authors
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Interface to the blockchain database.'''


import array
import ast
import os
import time
from bisect import bisect_right
from collections import namedtuple
from glob import glob

import attr
from aiorpcx import run_in_thread, sleep

import electrumx.lib.util as util
from electrumx.lib.hash import hash_to_hex_str, HASHX_LEN
from electrumx.lib.merkle import Merkle, MerkleCache
from electrumx.lib.util import (
    formatted_time, pack_be_uint16, pack_be_uint32, pack_le_uint32,
    unpack_le_uint32, unpack_be_uint32, unpack_le_uint64, base_encode
)
from electrumx.server.history import History
from electrumx.server.storage import db_class

ASSET = namedtuple("ASSET", "tx_num tx_pos tx_hash height name value")
UTXO = namedtuple("UTXO", "tx_num tx_pos tx_hash height value")


@attr.s(slots=True)
class FlushData(object):
    height = attr.ib()
    tx_count = attr.ib()
    headers = attr.ib()
    block_tx_hashes = attr.ib()
    # The following are flushed to the UTXO DB if undo_infos is not None
    undo_infos = attr.ib()
    adds = attr.ib()
    deletes = attr.ib()
    tip = attr.ib()
    # Assets
    asset_adds = attr.ib()
    asset_deletes = attr.ib()
    asset_meta_adds = attr.ib()
    asset_meta_reissues = attr.ib()
    asset_undo_infos = attr.ib()
    asset_meta_undos = attr.ib()
    asset_meta_deletes = attr.ib()
    asset_count = attr.ib()

    # Asset Qualifiers
    asset_restricted2qual = attr.ib()
    asset_restricted2qual_del = attr.ib()
    asset_restricted2qual_undo = attr.ib()
    asset_current_associations = attr.ib()

    asset_restricted_freezes = attr.ib()
    asset_restricted_freezes_current = attr.ib()
    asset_restricted_freezes_del = attr.ib()
    asset_restricted_freezes_undo = attr.ib()

    asset_tag2pub = attr.ib()
    asset_tag2pub_current = attr.ib()
    asset_tag2pub_del = attr.ib()
    asset_tag2pub_undo = attr.ib()

    # Broadcasts
    asset_broadcasts = attr.ib()
    asset_broadcasts_undo = attr.ib()
    asset_broadcasts_del = attr.ib()


class DB(object):
    '''Simple wrapper of the backend database for querying.

    Performs no DB update, though the DB will be cleaned on opening if
    it was shutdown uncleanly.
    '''

    DB_VERSIONS = [6, 7, 8]

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
        self.utxo_flush_count = 0
        self.fs_height = -1
        self.fs_tx_count = 0
        self.fs_asset_count = 0
        self.db_height = -1
        self.db_tx_count = 0
        self.db_asset_count = 0
        self.db_tip = None
        self.tx_counts = None
        self.last_flush = time.time()
        self.last_flush_tx_count = 0
        self.last_flush_asset_count = 0
        self.wall_time = 0
        self.first_sync = True
        self.db_version = -1

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
        size = (self.db_height + 1) * 8
        tx_counts = self.tx_counts_file.read(0, size)
        assert len(tx_counts) == size
        self.tx_counts = array.array('Q', tx_counts)
        if self.tx_counts:
            assert self.db_tx_count == self.tx_counts[-1]
        else:
            assert self.db_tx_count == 0

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
        self.read_asset_state()
        self.asset_info_db = self.db_class('asset_info', for_sync)

        # Then history DB
        self.utxo_flush_count = self.history.open_db(self.db_class, for_sync,
                                                     self.utxo_flush_count,
                                                     compacting)
        self.clear_excess_undo_info()

        # Read TX counts (requires meta directory)
        await self._read_tx_counts()

    async def open_for_compacting(self):
        await self._open_dbs(True, True)

    async def open_for_sync(self):
        '''Open the databases to sync to the daemon.

        When syncing we want to reserve a lot of open files for the
        synchronization.  When serving clients we want the open files for
        serving network connections.
        '''
        await self._open_dbs(True, False)

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
        await self._open_dbs(False, False)

    # Header merkle cache
    async def populate_header_merkle_cache(self):
        self.logger.info('populating header merkle cache...')
        length = max(1, self.db_height - self.env.reorg_limit)
        start = time.monotonic()
        await self.header_mc.initialize(length)
        elapsed = time.monotonic() - start
        self.logger.info(f'header merkle cache populated in {elapsed:.1f}s')

    async def header_branch_and_root(self, length, height):
        return await self.header_mc.branch_and_root(length, height)

    # Flushing
    def assert_flushed(self, flush_data):
        '''Asserts state is fully flushed.'''
        assert flush_data.tx_count == self.fs_tx_count == self.db_tx_count
        assert flush_data.asset_count == self.fs_asset_count == self.db_asset_count
        assert flush_data.height == self.fs_height == self.db_height
        assert flush_data.tip == self.db_tip
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

        assert not flush_data.asset_restricted2qual
        assert not flush_data.asset_restricted2qual_del
        assert not flush_data.asset_restricted2qual_undo
        assert not flush_data.asset_current_associations

        assert not flush_data.asset_restricted_freezes
        assert not flush_data.asset_restricted_freezes_current
        assert not flush_data.asset_restricted_freezes_del
        assert not flush_data.asset_restricted_freezes_undo

        assert not flush_data.asset_tag2pub
        assert not flush_data.asset_tag2pub_del
        assert not flush_data.asset_tag2pub_undo
        assert not flush_data.asset_tag2pub_current

        assert not flush_data.asset_broadcasts
        assert not flush_data.asset_broadcasts_undo
        assert not flush_data.asset_broadcasts_del

        self.history.assert_flushed()

    def flush_dbs(self, flush_data, flush_utxos, estimate_txs_remaining):
        '''Flush out cached state.  History is always flushed; UTXOs are
        flushed if flush_utxos.'''
        if flush_data.height == self.db_height:
            self.assert_flushed(flush_data)
            return

        start_time = time.time()
        prior_flush = self.last_flush
        tx_delta = flush_data.tx_count - self.last_flush_tx_count
        asset_delta = flush_data.asset_count - self.last_flush_asset_count

        # Flush to file system
        self.flush_fs(flush_data)

        # Then history
        self.flush_history()

        with self.asset_db.write_batch() as batch:
            if flush_utxos:
                self.flush_asset_db(batch, flush_data)
            self.flush_asset_state(batch)

        with self.asset_info_db.write_batch() as batch:
            if flush_utxos:
                self.flush_asset_info_db(batch, flush_data)

        # Flush state last as it reads the wall time.
        with self.utxo_db.write_batch() as batch:
            if flush_utxos:
                self.flush_utxo_db(batch, flush_data)
            self.flush_state(batch)

        # Update and put the wall time again - otherwise we drop the
        # time it took to commit the batch
        self.flush_state(self.utxo_db)

        elapsed = self.last_flush - start_time
        self.logger.info(f'flush #{self.history.flush_count:,d} took '
                         f'{elapsed:.1f}s.  Height {flush_data.height:,d} '
                         f'txs: {flush_data.tx_count:,d} ({tx_delta:+,d}) '
                         f'assets: {flush_data.asset_count:,d} ({asset_delta:+,d})')

        # Catch-up stats
        if self.utxo_db.for_sync:
            flush_interval = self.last_flush - prior_flush
            tx_per_sec_gen = int(flush_data.tx_count / self.wall_time)
            tx_per_sec_last = 1 + int(tx_delta / flush_interval)
            eta = estimate_txs_remaining() / tx_per_sec_last
            self.logger.info(f'tx/sec since genesis: {tx_per_sec_gen:,d}, '
                             f'since last flush: {tx_per_sec_last:,d}')
            self.logger.info(f'sync time: {formatted_time(self.wall_time)}  '
                             f'ETA: {formatted_time(eta)}')

    def flush_fs(self, flush_data):
        '''Write headers, tx counts and block tx hashes to the filesystem.

        The first height to write is self.fs_height + 1.  The FS
        metadata is all append-only, so in a crash we just pick up
        again from the height stored in the DB.
        '''
        prior_tx_count = (self.tx_counts[self.fs_height]
                          if self.fs_height >= 0 else 0)
        assert len(flush_data.block_tx_hashes) == len(flush_data.headers)
        assert flush_data.height == self.fs_height + len(flush_data.headers)
        assert flush_data.tx_count == (self.tx_counts[-1] if self.tx_counts
                                       else 0)
        assert len(self.tx_counts) == flush_data.height + 1
        hashes = b''.join(flush_data.block_tx_hashes)
        flush_data.block_tx_hashes.clear()
        assert len(hashes) % 32 == 0
        assert len(hashes) // 32 == flush_data.tx_count - prior_tx_count

        # Write the headers, tx counts, and tx hashes
        start_time = time.monotonic()
        height_start = self.fs_height + 1
        offset = self.header_offset(height_start)
        self.headers_file.write(offset, b''.join(flush_data.headers))
        flush_data.headers.clear()

        offset = height_start * self.tx_counts.itemsize
        self.tx_counts_file.write(offset,
                                  self.tx_counts[height_start:].tobytes())
        offset = prior_tx_count * 32
        self.hashes_file.write(offset, hashes)

        self.fs_height = flush_data.height
        self.fs_tx_count = flush_data.tx_count
        self.fs_asset_count = flush_data.asset_count

        if self.utxo_db.for_sync:
            elapsed = time.monotonic() - start_time
            self.logger.info(f'flushed filesystem data in {elapsed:.2f}s')

    def flush_history(self):
        self.history.flush()

    def flush_asset_info_db(self, batch, flush_data: FlushData):
        start_time = time.monotonic()
        adds = len(flush_data.asset_meta_adds)
        reissues = len(flush_data.asset_meta_reissues)
        broadcasts = len(flush_data.asset_broadcasts)

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
                             f'{broadcasts:,d} messages broadcast, '
                             f'{elapsed:.1f}s, committing...')

    def flush_asset_db(self, batch, flush_data: FlushData):
        start_time = time.monotonic()
        add_count = len(flush_data.asset_adds)
        spend_count = len(flush_data.asset_deletes) // 2

        restricted_assets = len(flush_data.asset_restricted2qual)
        freezes = len(flush_data.asset_restricted_freezes)
        tags = len(flush_data.asset_tag2pub)

        # Spends
        batch_delete = batch.delete
        for key in sorted(flush_data.asset_deletes):
            batch_delete(key)
        flush_data.asset_deletes.clear()

        # Qualifiers
        for key in sorted(flush_data.asset_restricted_freezes_del):
            batch_delete(key)
        flush_data.asset_restricted_freezes_del.clear()

        for key in sorted(flush_data.asset_restricted2qual_del):
            batch_delete(key)
        flush_data.asset_restricted2qual_del.clear()

        for key in sorted(flush_data.asset_tag2pub_del):
            batch_delete(key)
        flush_data.asset_tag2pub_del.clear()

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

        # FIXME: For current values, maybe have them in the db if they are true
        # or omit them if they are false. For now, there are not enough qualifiers
        # or restricted assets for this to be a problem.

        # tx_hash + idx (uint32le): asset + tx_num (uint64le[:5]) + pubkey + flag
        for key, value in flush_data.asset_tag2pub.items():
            idx = key[-4:]
            asset_len = value[0]
            value = value[1:]
            asset_name = value[:asset_len]
            value = value[asset_len:]
            tx_num = value[:5]
            value = value[5:]
            pubkey_len = value[0]
            value = value[1:]
            pubkey = value[:pubkey_len]
            value = value[pubkey_len:]
            flag = value[0]

            suffix = idx + tx_num
            # pubkey -> asset & flag w/ tx info
            batch_put(b'p' + bytes([pubkey_len]) + pubkey + suffix,
                      bytes([asset_len]) + asset_name + bytes([flag]))

            # asset -> pubkey & flag w/ tx info
            batch_put(b'a' + bytes([asset_len]) + asset_name + suffix,
                      bytes([pubkey_len]) + pubkey + bytes([flag]))

        flush_data.asset_tag2pub.clear()

        self.flush_t2p_undo_infos(batch_put, flush_data.asset_tag2pub_undo)
        flush_data.asset_tag2pub_undo.clear()

        for key, value in flush_data.asset_tag2pub_current.items():
            # This stores / overwrites the latest qualifications given an asset and pubkey hash

            len_asset = key[0]
            key = key[1:]
            asset = key[:len_asset]
            key = key[len_asset:]
            len_h160 = key[0]
            key = key[1:]
            h160 = key[:len_h160]

            put_data = value[0]

            if put_data != 0:
                batch_put(b'Q' + bytes([len_asset]) + asset + bytes([len_h160]) + h160, value[1:])
                batch_put(b'Q' + bytes([len_h160]) + h160 + bytes([len_asset]) + asset, value[1:])
            else:
                batch_delete(b'Q' + bytes([len_asset]) + asset + bytes([len_h160]) + h160)
                batch_delete(b'Q' + bytes([len_h160]) + h160 + bytes([len_asset]) + asset)

        flush_data.asset_tag2pub_current.clear()

        for key, value in flush_data.asset_restricted_freezes.items():
            idx = key[-4:]
            asset_len = value[0]
            value = value[1:]
            asset_name = value[:asset_len]
            value = value[asset_len:]
            tx_numb = value[:5]
            value = value[5:]
            flag = value[0]

            batch_put(b'f' + bytes([asset_len]) + asset_name + idx + tx_numb, bytes([flag]))
        flush_data.asset_restricted_freezes.clear()

        self.flush_freezes_undo_info(batch_put, flush_data.asset_restricted_freezes_undo)
        flush_data.asset_restricted_freezes_undo.clear()

        for key, value in flush_data.asset_restricted_freezes_current.items():
            # This stores / overwrites the latest frozen status of an asset

            put_data = value[0]

            if put_data != 0:
                batch_put(b'l' + key, value[1:])
            else:
                batch_delete(b'l' + key)

        flush_data.asset_restricted_freezes_current.clear()

        for key, value in flush_data.asset_restricted2qual.items():
            # tx_hash + idx (uint32le) + idx_quals: restricted + tx_num (uint64le[:5]) + num quals + quals
            # incoming key -> value

            prefix = b'q'

            restricted_len = key[0]
            key = key[1:]
            restricted = key[:restricted_len]
            key = key[restricted_len:]
            res_idx = key[:4]
            key = key[4:]
            quals_idx = key[:4]
            key = key[4:]
            tx_numb = key[:5]

            quals = []
            diss = []
            num_quals = value[0]
            value = value[1:]
            for _ in range(num_quals):
                qual_len = value[0]
                value = value[1:]
                qual = value[:qual_len]
                value = value[qual_len:]
                quals.append(qual)

            num_dis = value[0]
            value = value[1:]
            for _ in range(num_dis):
                qual_len = value[0]
                value = value[1:]
                qual = value[:qual_len]
                value = value[qual_len:]
                diss.append(qual)

            # We want ->
            # restricted + restricted idx + quals idx + txnumb: num_assoc + num_quals + quals + num_dis + num_quals + quals
            # qualifier + restricted idx + quals idx + txnum: 1 + restricted

            batch_put(prefix + bytes([restricted_len]) + restricted + res_idx + quals_idx + tx_numb,
                      bytes([len(quals)]) + b''.join([bytes([len(qual)]) + qual for qual in quals]) +
                      bytes([len(diss)]) + b''.join([bytes([len(qual)]) + qual for qual in quals]))

            for qual in quals:
                batch_put(prefix + bytes([len(qual)]) + qual + res_idx + quals_idx + tx_numb,
                          b'\x01' + bytes([restricted_len]) + restricted + b'\0')

            for qual in diss:
                batch_put(prefix + bytes([len(qual)]) + qual + res_idx + quals_idx + tx_numb,
                          b'\0\x01' + bytes([restricted_len]) + restricted)


        flush_data.asset_restricted2qual.clear()

        self.flush_restricted2qual_undo_info(batch_put, flush_data.asset_restricted2qual_undo)
        flush_data.asset_restricted2qual_undo.clear()

        for key, value in flush_data.asset_current_associations.items():
            # This stores / overwrites the latest restricted to qual associations
            print('Associating')
            print(key)
            print('with')
            print(value)
            if value[0] != 0:
                batch_put(b'r' + key, value[1:])
            else:
                print('deleting')
                print(key)
                batch_delete(b'r' + key)
        flush_data.asset_current_associations.clear()

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
                             f'{elapsed:.1f}s, committing...')

        self.db_asset_count = flush_data.asset_count

    def flush_utxo_db(self, batch, flush_data):
        '''Flush the cached DB writes and UTXO set to the batch.'''
        # Care is needed because the writes generated by flushing the
        # UTXO state may have keys in common with our write cache or
        # may be in the DB already.
        start_time = time.monotonic()
        add_count = len(flush_data.adds)
        spend_count = len(flush_data.deletes) // 2

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
            block_count = flush_data.height - self.db_height
            tx_count = flush_data.tx_count - self.db_tx_count
            elapsed = time.monotonic() - start_time
            self.logger.info(f'flushed {block_count:,d} blocks with '
                             f'{tx_count:,d} txs, {add_count:,d} UTXO adds, '
                             f'{spend_count:,d} spends in '
                             f'{elapsed:.1f}s, committing...')

        self.utxo_flush_count = self.history.flush_count
        self.db_height = flush_data.height
        self.db_tx_count = flush_data.tx_count
        self.db_tip = flush_data.tip

    def flush_asset_state(self, batch):
        self.last_flush_asset_count = self.fs_asset_count
        self.write_asset_state(batch)

    def flush_state(self, batch):
        '''Flush chain state to the batch.'''
        now = time.time()
        self.wall_time += now - self.last_flush
        self.last_flush = now
        self.last_flush_tx_count = self.fs_tx_count
        self.write_utxo_state(batch)

    def flush_backup(self, flush_data, touched):
        '''Like flush_dbs() but when backing up.  All UTXOs are flushed.'''
        assert not flush_data.headers
        assert not flush_data.block_tx_hashes
        assert flush_data.height < self.db_height
        self.history.assert_flushed()

        start_time = time.time()
        tx_delta = flush_data.tx_count - self.last_flush_tx_count
        asset_delta = flush_data.asset_count - self.last_flush_asset_count

        self.backup_fs(flush_data.height, flush_data.tx_count, flush_data.asset_count)
        self.history.backup(touched, flush_data.tx_count)
        with self.utxo_db.write_batch() as batch:
            self.flush_utxo_db(batch, flush_data)
            # Flush state last as it reads the wall time.
            self.flush_state(batch)

        with self.asset_db.write_batch() as batch:
            self.flush_asset_db(batch, flush_data)
            self.flush_asset_state(batch)

        with self.asset_info_db.write_batch() as batch:
            self.flush_asset_info_db(batch, flush_data)

        elapsed = self.last_flush - start_time
        self.logger.info(f'backup flush #{self.history.flush_count:,d} took '
                         f'{elapsed:.1f}s.  Height {flush_data.height:,d} '
                         f'txs: {flush_data.tx_count:,d} ({tx_delta:+,d}) '
                         f'assets: {flush_data.asset_count:,d} ({asset_delta:+,d})')

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
        '''Requires start_height >= 0, count >= 0.  Reads as many headers as
        are available starting at start_height up to count.  This
        would be zero if start_height is beyond self.db_height, for
        example.
        Returns a (binary, n) pair where binary is the concatenated
        binary headers, and n is the count of headers returned.
        '''
        if start_height < 0 or count < 0:
            raise self.DBError(f'{count:,d} headers starting at '
                               f'{start_height:,d} not on disk')

        def read_headers():
            # Read some from disk
            disk_count = max(0, min(count, self.db_height + 1 - start_height))
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
        if tx_height > self.db_height:
            tx_hash = None
        else:
            tx_hash = self.hashes_file.read(tx_num * 32, 32)
        return tx_hash, tx_height

    def fs_tx_hashes_at_blockheight(self, block_height):
        '''Return a list of tx_hashes at given block height,
        in the same order as in the block.
        '''
        if block_height > self.db_height:
            raise self.DBError(f'block {block_height:,d} not on disk (>{self.db_height:,d})')
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
            raise self.DBError('only got {:,d} headers starting at {:,d}, not '
                               '{:,d}'.format(headers_count, height, count))
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

    def undo_key_broadcast(self, height):
        return b'B' + pack_be_uint32(height)

    def undo_key(self, height):
        '''DB key for undo information at the given height.'''
        return b'U' + pack_be_uint32(height)

    def undo_res2qual_key(self, height):
        return b'R' + pack_be_uint32(height)

    def undo_freeze_key(self, height):
        return b'F' + pack_be_uint32(height)

    def undo_tag_key(self, height):
        return b'T' + pack_be_uint32(height)

    def read_asset_broadcast_undo_info(self, height):
        return self.asset_info_db.get(self.undo_key_broadcast(height))

    def read_asset_meta_undo_info(self, height):
        return self.asset_info_db.get(self.undo_key(height))

    def read_asset_undo_res2qual_key(self, height):
        return self.asset_db.get(self.undo_res2qual_key(height))

    def read_asset_undo_freeze_info(self, height):
        return self.asset_db.get(self.undo_freeze_key(height))

    def read_asset_undo_tag_info(self, height):
        return self.asset_db.get(self.undo_tag_key(height))

    def read_asset_undo_info(self, height):
        return self.asset_db.get(self.undo_key(height))

    def read_undo_info(self, height):
        '''Read undo information from a file for the current height.'''
        return self.utxo_db.get(self.undo_key(height))

    def flush_asset_broadcast_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key_broadcast(height), b''.join(undo_info))

    def flush_asset_meta_undos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_key(height), b''.join(undo_info))

    def flush_t2p_undo_infos(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_tag_key(height), b''.join(undo_info))

    def flush_freezes_undo_info(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_freeze_key(height), b''.join(undo_info))

    def flush_restricted2qual_undo_info(self, batch_put, undo_infos):
        for undo_info, height in undo_infos:
            if len(undo_info) > 0:
                batch_put(self.undo_res2qual_key(height), b''.join(undo_info))

    def flush_undo_infos(self, batch_put, undo_infos):
        '''undo_infos is a list of (undo_info, height) pairs.'''
        for undo_info, height in undo_infos:
            batch_put(self.undo_key(height), b''.join(undo_info))

    def raw_block_prefix(self):
        return 'meta/block'

    def raw_block_path(self, height):
        return f'{self.raw_block_prefix()}{height:d}'

    def read_raw_block(self, height):
        '''Returns a raw block read from disk.  Raises FileNotFoundError
        if the block isn't on-disk.'''
        with util.open_file(self.raw_block_path(height)) as f:
            return f.read(-1)

    def write_raw_block(self, block, height):
        '''Write a raw block to disk.'''
        with util.open_truncate(self.raw_block_path(height)) as f:
            f.write(block)
        # Delete old blocks to prevent them accumulating
        try:
            del_height = self.min_undo_height(height) - 1
            os.remove(self.raw_block_path(del_height))
        except FileNotFoundError:
            pass

    def clear_excess_undo_info(self):
        '''Clear excess undo info.  Only most recent N are kept.'''
        prefix = b'U'
        min_height = self.min_undo_height(self.db_height)
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
        for key, _hist in self.asset_db.iterator(prefix=prefix):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'R'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'F'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        for key, _hist in self.asset_db.iterator(prefix=b'T'):
            height, = unpack_be_uint32(key[-4:])
            if height >= min_height:
                break
            keys.append(key)

        if keys:
            with self.asset_db.write_batch() as batch:
                for key in keys:
                    batch.delete(key)

        # delete old block files
        prefix = self.raw_block_prefix()
        paths = [path for path in glob(f'{prefix}[0-9]*')
                 if len(path) > len(prefix)
                 and int(path[len(prefix):]) < min_height]
        if paths:
            for path in paths:
                try:
                    os.remove(path)
                except FileNotFoundError:
                    pass
            self.logger.info(f'deleted {len(paths):,d} stale block files')

    # -- Asset database

    def read_asset_state(self):
        state = self.asset_db.get(b'state')
        if not state:
            self.db_asset_count = 0
        else:
            state = ast.literal_eval(state.decode())
            if not isinstance(state, dict):
                raise self.DBError('failed reading state from asset DB')
            self.db_asset_count = state['asset_count']

        self.fs_asset_count = self.db_asset_count
        self.last_flush_asset_count = self.fs_asset_count

    # -- UTXO database

    def read_utxo_state(self):
        state = self.utxo_db.get(b'state')
        if not state:
            self.db_height = -1
            self.db_tx_count = 0
            self.db_tip = b'\0' * 32
            self.db_version = max(self.DB_VERSIONS)
            self.utxo_flush_count = 0
            self.wall_time = 0
            self.first_sync = True
        else:
            state = ast.literal_eval(state.decode())
            if not isinstance(state, dict):
                raise self.DBError('failed reading state from DB')
            self.db_version = state['db_version']
            if self.db_version not in self.DB_VERSIONS:
                raise self.DBError('your UTXO DB version is {} but this '
                                   'software only handles versions {}'
                                   .format(self.db_version, self.DB_VERSIONS))
            # backwards compat
            genesis_hash = state['genesis']
            if isinstance(genesis_hash, bytes):
                genesis_hash = genesis_hash.decode()
            if genesis_hash != self.coin.GENESIS_HASH:
                raise self.DBError('DB genesis hash {} does not match coin {}'
                                   .format(genesis_hash,
                                           self.coin.GENESIS_HASH))
            self.db_height = state['height']
            self.db_tx_count = state['tx_count']
            self.db_tip = state['tip']
            self.utxo_flush_count = state['utxo_flush_count']
            self.wall_time = state['wall_time']
            self.first_sync = state['first_sync']

        # These are our state as we move ahead of DB state
        self.fs_height = self.db_height
        self.fs_tx_count = self.db_tx_count
        self.last_flush_tx_count = self.fs_tx_count

        # Upgrade DB
        if self.db_version != max(self.DB_VERSIONS):
            self.upgrade_db()

        # Log some stats
        self.logger.info('UTXO DB version: {:d}'.format(self.db_version))
        self.logger.info('coin: {}'.format(self.coin.NAME))
        self.logger.info('network: {}'.format(self.coin.NET))
        self.logger.info('height: {:,d}'.format(self.db_height))
        self.logger.info('tip: {}'.format(hash_to_hex_str(self.db_tip)))
        self.logger.info('tx count: {:,d}'.format(self.db_tx_count))
        self.logger.info('VOUT debugging: {}'.format(self.env.write_bad_vouts_to_file))
        if self.utxo_db.for_sync:
            self.logger.info(f'flushing DB cache at {self.env.cache_MB:,d} MB')
        if self.first_sync:
            self.logger.info('sync time so far: {}'
                             .format(util.formatted_time(self.wall_time)))

    def upgrade_db(self):
        self.logger.info(f'UTXO DB version: {self.db_version}')
        self.logger.info('Upgrading your DB; this can take some time...')

        def upgrade_u_prefix(prefix):
            count = 0
            with self.utxo_db.write_batch() as batch:
                batch_delete = batch.delete
                batch_put = batch.put
                # Key: b'u' + address_hashX + tx_idx + tx_num
                for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                    if len(db_key) == 21:
                        return
                    break
                if self.db_version == 6:
                    for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                        count += 1
                        batch_delete(db_key)
                        batch_put(db_key[:14] + b'\0\0' + db_key[14:] + b'\0', db_value)
                else:
                    for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                        count += 1
                        batch_delete(db_key)
                        batch_put(db_key + b'\0', db_value)
            return count

        last = time.monotonic()
        count = 0
        for cursor in range(65536):
            prefix = b'u' + pack_be_uint16(cursor)
            count += upgrade_u_prefix(prefix)
            now = time.monotonic()
            if now > last + 10:
                last = now
                self.logger.info(f'DB 1 of 3: {count:,d} entries updated, '
                                 f'{cursor * 100 / 65536:.1f}% complete')
        self.logger.info('DB 1 of 3 upgraded successfully')

        def upgrade_h_prefix(prefix):
            count = 0
            with self.utxo_db.write_batch() as batch:
                batch_delete = batch.delete
                batch_put = batch.put
                # Key: b'h' + compressed_tx_hash + tx_idx + tx_num
                for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                    if len(db_key) == 14:
                        return
                    break
                if self.db_version == 6:
                    for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                        count += 1
                        batch_delete(db_key)
                        batch_put(db_key[:7] + b'\0\0' + db_key[7:] + b'\0', db_value)
                else:
                    for db_key, db_value in self.utxo_db.iterator(prefix=prefix):
                        count += 1
                        batch_delete(db_key)
                        batch_put(db_key + b'\0', db_value)
            return count

        last = time.monotonic()
        count = 0
        for cursor in range(65536):
            prefix = b'h' + pack_be_uint16(cursor)
            count += upgrade_h_prefix(prefix)
            now = time.monotonic()
            if now > last + 10:
                last = now
                self.logger.info(f'DB 2 of 3: {count:,d} entries updated, '
                                 f'{cursor * 100 / 65536:.1f}% complete')

        # Upgrade tx_counts file
        size = (self.db_height + 1) * 8
        tx_counts = self.tx_counts_file.read(0, size)
        if len(tx_counts) == (self.db_height + 1) * 4:
            tx_counts = array.array('I', tx_counts)
            tx_counts = array.array('Q', tx_counts)
            self.tx_counts_file.write(0, tx_counts.tobytes())

        self.db_version = max(self.DB_VERSIONS)
        with self.utxo_db.write_batch() as batch:
            self.write_utxo_state(batch)
        self.logger.info('DB 2 of 3 upgraded successfully')

    def write_asset_state(self, batch):
        state = {
            'asset_count': self.db_asset_count,
        }
        batch.put(b'state', repr(state).encode())

    def write_utxo_state(self, batch):
        '''Write (UTXO) state to the batch.'''
        state = {
            'genesis': self.coin.GENESIS_HASH,
            'height': self.db_height,
            'tx_count': self.db_tx_count,
            'tip': self.db_tip,
            'utxo_flush_count': self.utxo_flush_count,
            'wall_time': self.wall_time,
            'first_sync': self.first_sync,
            'db_version': self.db_version,
        }
        batch.put(b'state', repr(state).encode())

    def set_flush_count(self, count):
        self.utxo_flush_count = count
        with self.utxo_db.write_batch() as batch:
            self.write_utxo_state(batch)

    async def restricted_asset_data(self, restricted_asset: str, only_current: bool):
        def read_restricted():
            pass
        while True:
            assets = await run_in_thread(read_restricted)
            if all(asset.tx_hash is not None for asset in assets):
                return assets
            self.logger.warning('read_restricted: tx hash not found (reorg?), retrying...')
            await sleep(0.25)

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
                if value == 0:
                    return None
                return hashX, value
            return [lookup_utxo(*hashX_pair) for hashX_pair in hashX_pairs]

        hashX_pairs = await run_in_thread(lookup_hashXs)
        return [i for i in await run_in_thread(lookup_utxos, hashX_pairs) if i]

    # For external use
    async def get_associations_from_asset(self, asset: bytes, history: bool = False):
        def get_current_info():
            res = self.get_associated_assets_from(asset)
            if res is None:
                return {}
            is_restricted, data = res
            if is_restricted:
                tx_numb, res_idx, qual_idx, names, old_names = data
                res_pos, = unpack_le_uint32(res_idx)
                qual_pos, = unpack_le_uint32(qual_idx)
                tx_num, = unpack_le_uint64(tx_numb + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                to_ret = {}
                for qualifier in names:
                    to_ret[qualifier.decode('ascii')] = {
                        'associated': True,
                        'height': height,
                        'txid': hash_to_hex_str(tx_hash),
                        'restricted_pos': res_pos,
                        'qualifier_pos': qual_pos
                    }
                for qualifier in old_names:
                    to_ret[qualifier.decode('ascii')] = {
                        'associated': False,
                        'height': height,
                        'txid': hash_to_hex_str(tx_hash),
                        'restricted_pos': res_pos,
                        'qualifier_pos': qual_pos
                    }
                return to_ret
            else:
                to_ret = {}
                for type, asset_name, tx_numb, res_idx, qual_idx in data:
                    res_pos, = unpack_le_uint32(res_idx)
                    qual_pos, = unpack_le_uint32(qual_idx)
                    tx_num, = unpack_le_uint64(tx_numb + bytes(3))
                    tx_hash, height = self.fs_tx_hash(tx_num)
                    to_ret[asset_name.decode('ascii')] = {
                        'associated': type,
                        'height': height,
                        'txid': hash_to_hex_str(tx_hash),
                        'restricted_pos': res_pos,
                        'qualifier_pos': qual_pos
                    }

                return to_ret

        def get_asset_history():
            prefix = b'q' + bytes([len(asset)]) + asset
            history = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                res_tx_pos, = unpack_le_uint32(db_key[-13:-9])
                qual_tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)

                num_associates = db_value[0]
                db_value = db_value[1:]
                associates = []
                for _ in range(num_associates):
                    name_len = db_value[0]
                    db_value = db_value[1:]
                    asset_b = db_value[:name_len]
                    db_value = db_value[name_len:]
                    associates.append(asset_b.decode('ascii'))

                num_un_associates = db_value[0]
                db_value = db_value[1:]
                disassociates = []
                for _ in range(num_associates):
                    name_len = db_value[0]
                    db_value = db_value[1:]
                    asset_b = db_value[:name_len]
                    db_value = db_value[name_len:]
                    disassociates.append(asset_b.decode('ascii'))

                history[hash_to_hex_str(tx_hash)] = {
                    'associations': associates,
                    'disassociations': disassociates,
                    'height': height,
                    'restricted_pos': res_tx_pos,
                    'qualifier_pos': qual_tx_pos,
                }

                return history

        def calc_ret():
            ret = {
                'current': get_current_info(),
            }
            if history:
                ret['history'] = get_asset_history()
            return ret

        return await run_in_thread(calc_ret)

    # For external use
    async def get_h160s_associated_with_asset(self, asset: bytes, history: bool = False):
        def get_current_info():
            asset_len = len(asset)
            prefix = b'Q' + bytes([asset_len]) + asset
            tags = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                flag = db_value[0]
                tx_pos, = unpack_le_uint32(db_value[-9:-5])
                tx_num, = unpack_le_uint64(db_value[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)

                db_key = db_key[asset_len+2:]
                h160_len = db_key[0]
                h160 = db_key[1:1 + h160_len]  # type: bytes

                tags[h160.hex()] = {
                    'is_qualified': False if flag == 0 else True,
                    'height': height,
                    'txid': hash_to_hex_str(tx_hash),
                    'tx_pos': tx_pos,
                }
            return tags

        def get_h160_history():
            prefix = b'a' + bytes([len(asset)]) + asset
            history = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)

                h160_len = db_value[0]
                db_value = db_value[1:]
                h160 = db_value[:h160_len]
                db_value = db_value[h160_len:]
                flag = db_value[0]

                history[hash_to_hex_str(tx_hash)] = {
                    'pubkey': h160.hex(),
                    'qualified': False if flag == 0 else True,
                    'height': height,
                    'tx_pos': tx_pos
                }

            return history

        def calc_ret():
            ret = {
                'current': get_current_info(),
            }
            if history:
                ret['history'] = get_h160_history()
            return ret

        return await run_in_thread(calc_ret)

    # For external use
    async def get_tags_associated_with_h160(self, h160: bytes, history: bool = False):
        def get_current_info():
            h160_len = len(h160)
            prefix = b'Q' + bytes([h160_len]) + h160
            tags = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                flag = db_value[0]
                tx_pos, = unpack_le_uint32(db_value[-9:-5])
                tx_num, = unpack_le_uint64(db_value[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)

                db_key = db_key[h160_len + 2:]
                asset_len = db_key[0]
                asset = db_key[1:1 + asset_len]  # type: bytes

                print(asset.hex())
                tags[asset.decode('ascii')] = {
                    'is_qualified': False if flag == 0 else True,
                    'height': height,
                    'txid': hash_to_hex_str(tx_hash),
                    'tx_pos': tx_pos,
                }
            return tags

        def get_h160_history():
            prefix = b'p' + bytes([len(h160)]) + h160
            history = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)

                asset_len = db_value[0]
                db_value = db_value[1:]
                asset_name = db_value[:asset_len]
                db_value = db_value[asset_len:]
                flag = db_value[0]

                history[hash_to_hex_str(tx_hash)] = {
                    'tag': asset_name.decode('ascii'),
                    'qualified': False if flag == 0 else True,
                    'height': height,
                    'tx_pos': tx_pos
                }

            return history

        def calc_ret():
            ret = {
                'current': get_current_info(),
            }
            if history:
                ret['history'] = get_h160_history()
            return ret

        return await run_in_thread(calc_ret)

    # For external use
    async def get_frozen_status_of_restricted(self, asset: bytes, get_history: bool = False):
        def get_current_info():
            key = b'l' + bytes([len(asset)]) + asset
            value = self.asset_db.get(key)
            if value is None:
                return {}
            else:
                is_frozen = value[0]
                idx = value[1:5]
                tx_numb = value[5:10]
                tx_pos, = unpack_le_uint32(idx)
                tx_num, = unpack_le_uint64(tx_numb + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                return {
                    'is_frozen': False if is_frozen == 0 else True,
                    'height': height,
                    'txid': hash_to_hex_str(tx_hash),
                    'tx_pos': tx_pos,
                }

        def get_frozen_history():
            prefix = b'f' + bytes([len(asset)]) + asset
            history = {}
            for db_key, db_value in self.asset_db.iterator(prefix=prefix):
                tx_pos, = unpack_le_uint32(db_key[-9:-5])
                tx_num, = unpack_le_uint64(db_key[-5:] + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num)
                frozen_flag = False if db_value[0] == 0 else True
                history[hash_to_hex_str(tx_hash)] = {
                    'frozen_flag': frozen_flag,
                    'height': height,
                    'tx_pos': tx_pos,
                }
            return history

        def calc_ret():
            ret = {
                'current': get_current_info(),
            }
            if get_history:
                ret['history'] = get_frozen_history()
            return ret

        return await run_in_thread(calc_ret)

    def raw_assocation_data_to_tuple(self, value: bytes):
        is_restricted = value[0]
        value = value[1:]

        if is_restricted:
            qualifiers = []
            old_quals = []
            num_quals = value[0]
            value = value[1:]
            for _ in range(num_quals):
                qual_len = value[0]
                value = value[1:]
                qual = value[:qual_len]
                value = value[qual_len:]
                qualifiers.append(qual)
            num_dis = value[0]
            value = value[1:]
            for _ in range(num_quals):
                qual_len = value[0]
                value = value[1:]
                qual = value[:qual_len]
                value = value[qual_len:]
                old_quals.append(qual)
            res_idx = value[:4]
            value = value[4:]
            qual_idx = value[:4]
            value = value[4:]
            tx_numb = value[:5]
            value = value[5:]
            return True, (tx_numb, res_idx, qual_idx, qualifiers, old_quals)
        else:
            restricted = []
            num_res = value[0]
            value = value[1:]
            for _ in range(num_res):
                type = value[0]
                value = value [1:]
                res_len = value[0]
                value = value[1:]
                res = value[:res_len]
                value = value[res_len:]
                res_idx = value[:4]
                value = value[4:]
                qual_idx = value[:4]
                value = value[4:]
                tx_numb = value[:5]
                value = value[5:]
                restricted.append(((False if type == 0 else True), res, tx_numb, res_idx, qual_idx))
            return False, restricted

    def tuple_to_raw_assocation_data(self, tuple) -> bytes:
        b, data = tuple
        ret_val = b'\x01' if b else b'\0'
        if b:
            tx_numb, res_idx, qual_idx, qualifiers, old_quals = data
            ret_val += bytes([len(qualifiers)]) + b''.join([bytes([len(qual)]) + qual for qual in qualifiers])
            ret_val += bytes([len(old_quals)]) + b''.join([bytes([len(qual)]) + qual for qual in old_quals])
            ret_val += res_idx + qual_idx + tx_numb
        else:
            restricted_info = []
            for type, res, tx_numb, res_idx, qual_idx in data:
                restricted_info.append((b'\x01' if type else b'\0') + bytes([len(res)]) + res +
                                       res_idx + qual_idx + tx_numb)
            ret_val += bytes([len(restricted_info)]) + b''.join(restricted_info)
        return ret_val

    def get_associated_assets_from(self, asset: bytes):
        key = b'r' + asset
        value = self.asset_db.get(key)

        if value is None:
            return None
        return self.raw_assocation_data_to_tuple(value)

    # For external use
    async def is_qualified(self, asset, h160):
        def check():
            return self.check_if_qualified(asset, h160)
        return await run_in_thread(check)

    # For internal use
    def check_if_qualified(self, asset: bytes, pubkey: bytes):
        key = b'Q' + bytes([len(asset)]) + asset + bytes([len(pubkey)]) + pubkey
        value = self.asset_db.get(key)
        if value is None:
            return None
        else:
            return value

    # For internal use
    def check_if_frozen(self, asset: bytes):
        key = b'l' + bytes([len(asset)]) + asset
        value = self.asset_db.get(key)
        if value is None:
            return None
        else:
            return value

    async def lookup_asset_meta(self, asset_name):
        def read_assets_meta():
            b = self.asset_info_db.get(asset_name)
            if not b:
                return {}
            print(b)
            div_amt = b[0]
            b = b[1:]
            reissuable = b[0]
            b = b[1:]
            has_ipfs = b[0]
            b = b[1:]
            to_ret = {
                'divisions': div_amt,
                'reissuable': reissuable,
                'has_ipfs': has_ipfs
            }
            if has_ipfs != 0:
                ipfs_data = b[:34]
                b = b[34:]
                to_ret['ipfs'] = base_encode(ipfs_data, 58)

            idx = b[:4]
            b = b[4:]
            tx_numb = b[:5]
            b = b[5:]

            tx_pos, = unpack_le_uint32(idx)
            tx_num, = unpack_le_uint64(tx_numb + bytes(3))
            tx_hash, height = self.fs_tx_hash(tx_num)

            to_ret['source'] = {
                'tx_hash': hash_to_hex_str(tx_hash),
                'tx_pos': tx_pos,
                'height': height
            }

            has_prev = b[0]
            b = b[1:]

            if has_prev != 0:
                idx_prev = b[:4]
                b = b[4:]
                tx_numb_prev = b[:5]

                tx_pos, = unpack_le_uint32(idx_prev)
                tx_num_prev, = unpack_le_uint64(tx_numb_prev + bytes(3))
                tx_hash, height = self.fs_tx_hash(tx_num_prev)
                to_ret['source_prev'] = {
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