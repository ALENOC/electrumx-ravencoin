# Copyright (c) 2016-2017, Neil Booth
# Copyright (c) 2017, the ElectrumX authors
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Block prefetcher and chain processor.'''

import asyncio
import os
import re
import hashlib
import logging
import os
import traceback
from datetime import datetime
from asyncio import sleep
from struct import error as struct_error
from typing import Callable, Dict, Optional, List

from aiorpcx import CancelledError, run_in_thread, spawn

import electrumx
from electrumx.lib.addresses import public_key_to_address
from electrumx.lib.hash import hash_to_hex_str, HASHX_LEN
from electrumx.lib.script import is_unspendable_legacy, \
    is_unspendable_genesis, OpCodes, Script, ScriptError
from electrumx.lib.tx import Deserializer
from electrumx.lib.util import (
    class_logger, pack_le_uint32, pack_le_uint64, unpack_le_uint64, base_encode, DataParser, 
    deep_getsizeof, open_file, unpack_le_uint32
)
from electrumx.server.db import FlushData



class OPPushDataGeneric:
    def __init__(self, pushlen: Callable = None):
        if pushlen is not None:
            self.check_data_len = pushlen

    @classmethod
    def check_data_len(cls, datalen: int) -> bool:
        # Opcodes below OP_PUSHDATA4 all just push data onto stack, and are
        return OpCodes.OP_PUSHDATA4 >= datalen >= 0

    @classmethod
    def is_instance(cls, item):
        # accept objects that are instances of this class
        # or other classes that are subclasses
        return isinstance(item, cls) \
               or (isinstance(item, type) and issubclass(item, cls))


SCRIPTPUBKEY_TEMPLATE_P2PK = [OPPushDataGeneric(lambda x: x in (33, 65)), OpCodes.OP_CHECKSIG]

# Marks an address as valid for restricted assets via qualifier or restricted itself.
ASSET_NULL_TEMPLATE = [OpCodes.OP_RVN_ASSET, OPPushDataGeneric(lambda x: x == 20), OPPushDataGeneric()]
# Used with creating restricted assets. Dictates the qualifier assets associated.
ASSET_NULL_VERIFIER_TEMPLATE = [OpCodes.OP_RVN_ASSET, OpCodes.OP_RESERVED, OPPushDataGeneric()]
# Stop all movements of a restricted asset.
ASSET_GLOBAL_RESTRICTION_TEMPLATE = [OpCodes.OP_RVN_ASSET, OpCodes.OP_RESERVED, OpCodes.OP_RESERVED,
                                     OPPushDataGeneric()]


# -1 if doesn't match, positive if does. Indicates index in script
def match_script_against_template(script, template) -> int:
    """Returns whether 'script' matches 'template'."""
    if script is None:
        return -1
    if len(script) < len(template):
        return -1
    ctr = 0
    for i in range(len(template)):
        ctr += 1
        template_item = template[i]
        script_item = script[i]
        if OPPushDataGeneric.is_instance(template_item) and template_item.check_data_len(script_item[0]):
            continue
        if template_item != script_item[0]:
            return -1
    return ctr

logger = class_logger(__name__, 'BlockProcessor')


class OnDiskBlock:

    path = 'meta/blocks'
    del_regex = re.compile('([0-9a-f]{64}\\.tmp)$')
    legacy_del_regex = re.compile('block[0-9]{1,7}$')
    block_regex = re.compile('([0-9]{1,8})-([0-9a-f]{64})$')
    chunk_size = 25_000_000
    # On-disk blocks. hex_hash->(height, size) pair
    blocks = {}
    # Map from hex hash to prefetch task
    tasks = {}
    # If set it logs the next time a block is processed
    log_block = False
    daemon = None
    state = None

    def __init__(self, coin, hex_hash, height, size):
        self.hex_hash = hex_hash
        self.coin = coin
        self.height = height
        self.size = size
        self.block_file = None
        self.header = None

    @classmethod
    def filename(cls, hex_hash, height):
        return os.path.join(cls.path, f'{height:d}-{hex_hash}')

    def __enter__(self):
        self.block_file = open_file(self.filename(self.hex_hash, self.height))
        self.header = self._read(self.coin.static_header_len(self.height))
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.block_file.close()

    def _read(self, size):
        result = self.block_file.read(size)
        if not result:
            raise RuntimeError(f'truncated block file for block {self.hex_hash} '
                               f'height {self.height:,d}')
        return result

    def _read_at_pos(self, pos, size):
        self.block_file.seek(pos, os.SEEK_SET)
        result = self.block_file.read(size)
        if len(result) != size:
            raise RuntimeError(f'truncated block file for block {self.hex_hash} '
                               f'height {self.height:,d}')
        return result

    def date_str(self):
        timestamp, = unpack_le_uint32(self.header[68:72])
        return datetime.utcfromtimestamp(timestamp).strftime('%Y-%m-%d %H:%M:%S')

    def iter_txs(self):
        # Asynchronous generator of (tx, tx_hash) pairs
        raw = self._read(self.chunk_size)
        deserializer = Deserializer(raw)
        tx_count = deserializer.read_varint()

        if self.log_block:
            logger.info(f'height {self.height:,d} of {self.daemon.cached_height():,d} '
                        f'{self.hex_hash} {self.date_str()} '
                        f'{self.size / 1_000_000:.3f}MB {tx_count:,d} txs '
                        f'chain {self.state.chain_size / 1_000_000_000:.3f}GB')
            OnDiskBlock.log_block = False

        count = 0
        while True:
            read = deserializer.read_tx_and_hash
            try:
                while True:
                    cursor = deserializer.cursor
                    yield read()
                    count += 1
            except (AssertionError, IndexError, struct_error):
                pass

            if tx_count == count:

                return
            raw = raw[cursor:] + self._read(self.chunk_size)
            deserializer = Deserializer(raw)

    def _chunk_offsets(self):
        '''Iterate the transactions forwards to find their boundaries.'''
        base_offset = self.block_file.tell()
        assert base_offset in (80, 120)
        raw = self._read(self.chunk_size)
        deserializer = Deserializer(raw)
        tx_count = deserializer.read_varint()
        logger.info(f'backing up block {self.hex_hash} height {self.height:,d} '
                    f'tx_count {tx_count:,d}')
        offsets = [base_offset + deserializer.cursor]

        while True:
            read = deserializer.read_tx
            count = 0
            try:
                while True:
                    cursor = deserializer.cursor
                    read()
                    count += 1
            except (AssertionError, IndexError, struct_error):
                pass

            if count:
                offsets.append(base_offset + cursor)
                base_offset += cursor
            tx_count -= count
            if tx_count == 0:
                return offsets
            raw = raw[cursor:] + self._read(self.chunk_size)
            deserializer = Deserializer(raw)

    def iter_txs_reversed(self):
        # Iterate the block transactions in reverse order.  We need to iterate the
        # transactions forwards first to find their boundaries.
        offsets = self._chunk_offsets()
        for n in reversed(range(len(offsets) - 1)):
            start = offsets[n]
            size = offsets[n + 1] - start
            deserializer = Deserializer(self._read_at_pos(start, size))
            pairs = []
            while deserializer.cursor < size:
                pairs.append(deserializer.read_tx_and_hash())
            for item in reversed(pairs):
                yield item

    @classmethod
    async def delete_stale(cls, items, log):
        def delete(paths):
            count = total_size = 0
            for path, size in paths.items():
                try:
                    os.remove(path)
                    count += 1
                    total_size += size
                except FileNotFoundError as e:
                    logger.error(f'could not delete stale block file {path}: {e}')
            return count, total_size

        if not items:
            return
        paths = {}
        for item in items:
            if isinstance(item, os.DirEntry):
                paths[item.path] = item.stat().st_size
            else:
                height, size = cls.blocks.pop(item)
                paths[cls.filename(item, height)] = size

        count, total_size = await run_in_thread(delete, paths)
        if log:
            logger.info(f'deleted {count:,d} stale block files, total size {total_size:,d} bytes')

    @classmethod
    async def delete_blocks(cls, min_height, log):
        blocks_to_delete = [hex_hash for hex_hash, (height, size) in cls.blocks.items()
                            if height < min_height]
        await cls.delete_stale(blocks_to_delete, log)

    @classmethod
    async def scan_files(cls):
        # Remove stale block files
        def scan():
            to_delete = []
            with os.scandir(cls.path) as it:
                for dentry in it:
                    if dentry.is_file():
                        match = cls.block_regex.match(dentry.name)
                        if match:
                            to_delete.append(dentry)
            return to_delete

        def find_legacy_blocks():
            with os.scandir('meta') as it:
                return [dentry for dentry in it
                        if dentry.is_file() and cls.legacy_del_regex.match(dentry.name)]

        try:
            # This only succeeds the first time with the new code
            os.mkdir(cls.path)
            logger.info(f'created block directory {cls.path}')
            await cls.delete_stale(await run_in_thread(find_legacy_blocks), True)
        except FileExistsError:
            pass

        logger.info(f'scanning block directory {cls.path}...')
        to_delete = await run_in_thread(scan)
        await cls.delete_stale(to_delete, True)

    @classmethod
    async def prefetch_many(cls, daemon, pairs, kind):
        async def prefetch_one(hex_hash, height):
            '''Read a block in chunks to a temporary file.  Rename the file only when done so
            as not to have incomplete blocks considered complete.
            '''
            try:
                filename = cls.filename(hex_hash, height)
                size = await daemon.get_block(hex_hash, filename)
                cls.blocks[hex_hash] = (height, size)
                if kind == 'new':
                    logger.info(f'fetched new block height {height:,d} hash {hex_hash}')
                elif kind == 'reorg':
                    logger.info(f'fetched reorged block height {height:,d} hash {hex_hash}')
            except Exception as e:
                logger.error(f'error prefetching {hex_hash}: {e}')
            finally:
                cls.tasks.pop(hex_hash)

        # Pairs is a (height, hex_hash) iterable
        for height, hex_hash in pairs:
            if hex_hash not in cls.tasks and hex_hash not in cls.blocks:
                cls.tasks[hex_hash] = await spawn(prefetch_one, hex_hash, height)

    @classmethod
    async def streamed_block(cls, coin, hex_hash):
        # Waits for a block to come in.
        task = cls.tasks.get(hex_hash)
        if task:
            await task
        item = cls.blocks.get(hex_hash)
        if not item:
            logger.error(f'block {hex_hash} missing')            
            return None
        height, size = item
        return cls(coin, hex_hash, height, size)

    @classmethod
    async def stop_prefetching(cls):
        for task in cls.tasks.values():
            task.cancel()
        logger.info('prefetcher stopped')


class ChainError(Exception):
    '''Raised on error processing blocks.'''


class BlockProcessor:
    '''Process blocks and update the DB state to match.  Prefetch blocks so they are
    immediately available when the processor is ready for a new block.  Coordinate backing
    up in case of chain reorganisations.
    '''

    polling_delay = 5

    def __init__(self, env, db, daemon, notifications):
        self.env = env
        self.db = db
        self.daemon = daemon
        self.notifications = notifications

        self.bad_vouts_path = os.path.join(self.env.db_dir, 'invalid_chain_vouts')

        self.coin = env.coin
        
        # Meta
        self.caught_up = False
        self.ok = True
        self.touched = set()
        # A count >= 0 is a user-forced reorg; < 0 is a natural reorg
        self.reorg_count = None
        self.force_flush_arg = None

         # State.  Initially taken from DB;
        self.state = None

        # Caches of unflushed items.
        self.headers = []
        self.tx_hashes = []
        self.undo_infos = []

        # UTXO cache
        self.utxo_cache = {}
        self.db_deletes = []

        # Asset cache

        # Same as utxo cache but for assets.
        # All keys in this dict will also be in the
        # utxo_cache because assets are normal tx's with no RVN value
        self.asset_cache = {}
        # Same as above.
        self.asset_deletes = []
        self.asset_undo_infos = []

        # A dict of the asset name -> asset data
        self.asset_data_new = {}
        self.asset_data_reissued = {}

        self.asset_data_undo_infos = []
        self.asset_data_deletes = []

        # To notify clients about reissuances
        self.asset_touched = set()

        
        # h160 qualifications
        # h160 + asset : idx + txnumb + flag
        self.h160_qualified = {}
        self.h160_qualified_undo_infos = []
        self.h160_qualified_deletes = []

        # Restricted freezes
        # asset : idx + txnumb + flag
        self.restricted_freezes = {}
        self.restricted_freezes_undo_infos = []
        self.restricted_freezes_deletes = []

        # Restricted string
        # asset : idx + txnumb + string
        self.restricted_strings = {}
        self.restricted_strings_undo_infos = []
        self.restricted_strings_deletes = []

        # Qual associated
        # qual + restricted : idx + txnumb + flag
        self.qualifier_associations = {}
        self.qualifier_associations_undo_infos = []
        self.qualifier_associations_deletes = []

        self.current_restricted_asset = None  # type: Optional[bytes]
        self.restricted_idx = b''
        self.current_qualifiers = None  # type: Optional[bytes]
        self.current_restricted_string = ''
        self.qualifiers_idx = b''

        # Asset broadcasts
        self.asset_broadcast = {}
        self.asset_broadcast_undos = []
        self.asset_broadcast_dels = []

        self.backed_up_event = asyncio.Event()

        # When the lock is acquired, in-memory chain state is consistent with state.height.        # This is a requirement for safe flushing.
        self.state_lock = asyncio.Lock()

    async def run_with_lock(self, coro):
        # Shielded so that cancellations from shutdown don't lose work.  Cancellation will
        # cause fetch_and_process_blocks to block on the lock in flush(), the task completes,
        # and then the data is flushed.  We also don't want user-signalled reorgs to happen
        # in the middle of processing blocks; they need to wait.
        async def run_locked():
            async with self.state_lock:
                return await coro
        return await asyncio.shield(run_locked())

    async def next_block_hashes(self):
        daemon_height = await self.daemon.height()
        first = self.state.height + 1
        count = min(daemon_height - first + 1, self.coin.prefetch_limit(first))
        if count:
            hex_hashes = await self.daemon.block_hex_hashes(first, count)
            kind = 'new' if self.caught_up else 'sync'
            await OnDiskBlock.prefetch_many(self.daemon, enumerate(hex_hashes, start=first), kind)
        else:
            hex_hashes = []

        # Remove stale blocks
        await OnDiskBlock.delete_blocks(first - 5, False)

        return hex_hashes[:(count + 1) // 2], daemon_height

    async def reorg_chain(self, count):
        '''Handle a chain reorganisation.

        Count is the number of blocks to simulate a reorg, or None for a real reorg.
        This is passed in as self.reorg_count may change asynchronously.
        '''
        if count < 0:
            logger.info('chain reorg detected')
        else:
            logger.info(f'faking a reorg of {count:,d} blocks')
        await self.flush(True)

        start, hex_hashes = await self._reorg_hashes(count)
        pairs = reversed(list(enumerate(hex_hashes, start=start)))
        await OnDiskBlock.prefetch_many(self.daemon, pairs, 'reorg')

        for hex_hash in reversed(hex_hashes):
            if hex_hash != hash_to_hex_str(self.state.tip):
                logger.error(f'block {hex_hash} is not tip; cannot back up')
                return
            block = await OnDiskBlock.streamed_block(self.coin, hex_hash)
            if not block:
                break
            await self.run_with_lock(run_in_thread(self.backup_block, block))       
        
        logger.info(f'backed up to height {self.state.height:,d}')
        self.backed_up_event.set()
        self.backed_up_event.clear()

    async def _reorg_hashes(self, count):
        '''Return a pair (start, hashes) of blocks to back up during a
        reorg.

        The hashes are returned in order of increasing height.  Start
        is the height of the first hash, last of the last.
        '''
        start, count = await self._calc_reorg_range(count)
        last = start + count - 1
        
        if count == 1:
            logger.info(f'chain was reorganised replacing 1 block at height {start:,d}')
        else:
            logger.info(f'chain was reorganised replacing {count:,d} blocks at heights '
                        f'{start:,d}-{last:,d}')

        hashes = await self.db.fs_block_hashes(start, count)
        hex_hashes = [hash_to_hex_str(block_hash) for block_hash in hashes]
        return start, hex_hashes

    async def _calc_reorg_range(self, count):
        '''Calculate the reorg range'''

        def diff_pos(hashes1, hashes2):
            '''Returns the index of the first difference in the hash lists.
            If both lists match returns their length.'''
            for n, (hash1, hash2) in enumerate(zip(hashes1, hashes2)):
                if hash1 != hash2:
                    return n
            return len(hashes)

        height = self.state.height
        if count < 0:
            # A real reorg
            start = height - 1
            count = 1
            while start > 0:
                hashes = await self.db.fs_block_hashes(start, count)
                hex_hashes = [hash_to_hex_str(hash) for hash in hashes]
                d_hex_hashes = await self.daemon.block_hex_hashes(start, count)
                n = diff_pos(hex_hashes, d_hex_hashes)
                if n > 0:
                    start += n
                    break
                count = min(count * 2, start)
                start -= count

            count = (height - start) + 1
        else:
            start = (height - count) + 1

        return start, count

    # - Flushing
    def flush_data(self):
        '''The data for a flush.'''        
        return FlushData(self.state, self.headers,
                         self.tx_hashes, self.undo_infos, self.utxo_cache,
                         self.db_deletes,
                         self.asset_cache, self.asset_deletes,
                         self.asset_data_new, self.asset_data_reissued,
                         self.asset_undo_infos, self.asset_data_undo_infos, self.asset_data_deletes,
                         self.h160_qualified, self.h160_qualified_undo_infos, self.h160_qualified_deletes,
                         self.restricted_freezes, self.restricted_freezes_undo_infos, self.restricted_freezes_deletes,
                         self.restricted_strings, self.restricted_strings_undo_infos, self.restricted_strings_deletes,
                         self.qualifier_associations, self.qualifier_associations_undo_infos, self.qualifier_associations_deletes,
                         self.asset_broadcast, self.asset_broadcast_undos, self.asset_broadcast_dels)

    async def flush(self, flush_utxos):
        self.force_flush_arg = None
        # Estimate size remaining
        daemon_height = self.daemon.cached_height()
        tail_blocks = max(0, (daemon_height - max(self.state.height, self.coin.CHAIN_SIZE_HEIGHT)))
        size_remaining = (max(self.coin.CHAIN_SIZE - self.state.chain_size, 0) +
                          tail_blocks * self.coin.AVG_BLOCK_SIZE)
        await run_in_thread(self.db.flush_dbs, self.flush_data(), flush_utxos, size_remaining)

    async def check_cache_size_loop(self):
        '''Signal to flush caches if they get too big.'''
        # Good average estimates based on traversal of subobjects and
        # requesting size from Python (see deep_getsizeof).

        one_MB = 1000 * 1000
        cache_MB = self.env.cache_MB
        OnDiskBlock.daemon = self.daemon

        while True:
            utxo_cache_size = len(self.utxo_cache) * 205
            db_deletes_size = len(self.db_deletes) * 57
            hist_cache_size = self.db.history.unflushed_memsize()
            # Roughly ntxs * 32 + nblocks * 42
            tx_hash_size = ((self.state.tx_count - self.db.fs_tx_count) * 32
                            + (self.state.height - self.db.fs_height) * 42)

            # TODO Fix/add these approximations
            # These are average case approximations
            asset_cache_size = len(self.asset_cache) * 235  # Added 30 bytes for the max name length
            asset_data_new_size = len(self.asset_data_new) * 160
            asset_data_reissue_size = len(self.asset_data_reissued) * 180
            asset_broadcast_size = len(self.asset_broadcast) * 170

            # These have a 1v1 correspondance, so average case is acceptable
            h160_quals_size = len(self.h160_qualified) * 230
            h160_quals_deletes = len(self.h160_qualified_deletes) * 87

            restricted_freezes_size = len(self.restricted_freezes) * 87
            restricted_freezes_deletes = len(self.restricted_freezes_deletes) * 31

            restricted_strings_size = len(self.restricted_strings) * 137
            restricted_strings_deletes = len(self.restricted_strings_deletes) * 31

            quals_size = len(self.qualifier_associations) * 72
            quals_deletes = len(self.qualifier_associations_deletes) * 62

            utxo_MB = (db_deletes_size + utxo_cache_size) // one_MB
            hist_MB = (hist_cache_size + tx_hash_size) // one_MB
            asset_MB = (asset_data_new_size + asset_data_reissue_size +
                        asset_cache_size + asset_broadcast_size +
                        h160_quals_size + h160_quals_deletes +
                        restricted_freezes_size + restricted_freezes_deletes +
                        restricted_strings_size + restricted_strings_deletes +
                        quals_size + quals_deletes) // one_MB

            OnDiskBlock.log_block = True
            if hist_cache_size:
                logger.info(f'UTXOs {utxo_MB:,d}MB Assets {asset_MB:,d}MB hist {hist_MB:,d}MB')

            # Flush history if it takes up over 20% of cache memory.
            # Flush UTXOs once they take up 80% of cache memory.
            if asset_MB + utxo_MB + hist_MB >= cache_MB or hist_MB >= cache_MB // 5:
                self.force_flush_arg = (utxo_MB + asset_MB) >= cache_MB * 4 // 5
            await sleep(30)

    async def advance_blocks(self, hex_hashes):
        '''Process the blocks passed.  Detects and handles reorgs.'''

        async def advance_and_maybe_flush(block):
            await run_in_thread(self.advance_block, block)
            if self.force_flush_arg is not None:
                await self.flush(self.force_flush_arg)

        for hex_hash in hex_hashes:
            # Stop if we must flush
            if self.reorg_count is not None:
                break
            block = await OnDiskBlock.streamed_block(self.coin, hex_hash)
            if not block:
                break
            await self.run_with_lock(advance_and_maybe_flush(block))
            
        # If we've not caught up we have no clients for the touched set
        if not self.caught_up:
            self.touched = set()
            self.asset_touched = set()

    def advance_block(self, block):
        '''Advance once block.  It is already verified they correctly connect onto our tip.'''
        is_unspendable = (is_unspendable_genesis if block.height >= self.coin.GENESIS_ACTIVATION
                          else is_unspendable_legacy)

        #(undo_info, asset_undo_info, asset_meta_undo_info,
        # t2a_undo_info, freezes_undo_info, r2q_undo_info,
        # asset_broadcast_undo_info) = self.advance_txs(block.transactions, is_unspendable)

        #if height >= min_height:
        #    self.undo_infos.append((undo_info, height))
        #    self.asset_undo_infos.append((asset_undo_info, height))
        #    self.asset_data_undo_infos.append((asset_meta_undo_info, height))
        #    self.tag_to_address_undos.append((t2a_undo_info, height))
        #    self.global_freezes_undos.append((freezes_undo_info, height))
        #    self.restricted_to_qualifier_undos.append((r2q_undo_info, height))
        #    self.asset_broadcast_undos.append((asset_broadcast_undo_info, height))
        #    self.db.write_raw_block(block.raw, height)

        #self.height = height
        #self.headers.append(block.header)
        #self.tip = self.coin.header_hash(block.header)

        #await sleep(0)

        # Use local vars for speed in the loops
        state = self.state
        tx_hashes = []
        undo_info = []
        asset_undo_info = []
        asset_meta_undo_info = []

        internal_h160_qualified_undo_infos = []
        internal_restricted_freezes_undo_infos = []
        internal_restricted_strings_undo_infos = []
        internal_qualifier_associations_undo_infos = []
        
        asset_broadcast_undo_info = []

        tx_num = state.tx_count
        asset_num = state.asset_count
        script_hashX = self.coin.hashX_from_script
        put_utxo = self.utxo_cache.__setitem__

        put_asset = self.asset_cache.__setitem__
        put_asset_data_new = self.asset_data_new.__setitem__
        put_asset_data_reissued = self.asset_data_reissued.__setitem__
        put_asset_broadcast = self.asset_broadcast.__setitem__

        put_h160_qualified = self.h160_qualified.__setitem__
        def pop_h160_qualified(x):
            return self.h160_qualified.pop(x, None)
        h160_qualified_undo_infos_append = internal_h160_qualified_undo_infos.append

        put_restricted_freezes = self.restricted_freezes.__setitem__
        def pop_restricted_freezes(x):
            return self.restricted_freezes.pop(x, None)
        restricted_freezes_undo_infos_append = internal_restricted_freezes_undo_infos.append

        put_restricted_strings = self.restricted_strings.__setitem__
        def pop_restricted_strings(x):
            return self.restricted_strings.pop(x, None)
        restricted_strings_undo_infos_append = internal_restricted_strings_undo_infos.append

        put_qualifier_associations = self.qualifier_associations.__setitem__
        def pop_qualifier_associations(x):
            return self.qualifier_associations.pop(x, None)
        qualifier_associations_undo_infos_append = internal_qualifier_associations_undo_infos.append

        spend_utxo = self.spend_utxo
        spend_asset = self.spend_asset
        undo_info_append = undo_info.append
        asset_undo_info_append = asset_undo_info.append
        asset_meta_undo_info_append = asset_meta_undo_info.append
        update_touched = self.touched.update
        hashXs_by_tx = []
        append_hashXs = hashXs_by_tx.append
        append_tx_hash = tx_hashes.append
        to_le_uint32 = pack_le_uint32
        to_le_uint64 = pack_le_uint64

        with block as block:
            if self.coin.header_prevhash(block.header) != self.state.tip:
                self.reorg_count = -1
                return
            
            self.ok = False
            for tx, tx_hash in block.iter_txs():
                hashXs = []
                append_hashX = hashXs.append
                tx_numb = to_le_uint64(tx_num)[:5]
                is_asset = False
                self.current_restricted_asset = None
                self.current_qualifiers = None
                self.current_restricted_string = ''
                # Spend the inputs
                for txin in tx.inputs:
                    if txin.is_generation():  # Don't spend block rewards
                        continue
                    cache_value = spend_utxo(bytes(txin.prev_hash), txin.prev_idx)
                    asset_cache_value = spend_asset(bytes(txin.prev_hash), txin.prev_idx)
                    undo_info_append(cache_value)
                    asset_undo_info_append(asset_cache_value)
                    append_hashX(cache_value[:-13])

                # Add the new UTXOs
                for idx, txout in enumerate(tx.outputs):
                    # Ignore unspendable outputs
                    if is_unspendable(txout.pk_script):
                        continue

                    # Many scripts are malformed. This is very problematic...
                    # We cannot assume scripts are valid just because they are from a node
                    # We need to check for:
                    # Bitcoin PUSHOPs
                    # Standard VARINTs

                    if len(txout.pk_script) == 0:
                        hashX = script_hashX(txout.pk_script)
                        append_hashX(hashX)
                        put_utxo(tx_hash + to_le_uint32(idx),
                            hashX + tx_numb + to_le_uint64(txout.value))
                        continue

                    # deserialize the script pubkey
                    ops = Script.get_ops(txout.pk_script)

                    if ops[0][0] == -1:
                        # Quick check for invalid script.
                        # Hash as-is for possible spends and continue.
                        hashX = script_hashX(txout.pk_script)
                        append_hashX(hashX)
                        put_utxo(tx_hash + to_le_uint32(idx),
                                hashX + tx_numb + to_le_uint64(txout.value))
                        if self.env.write_bad_vouts_to_file:
                            b = bytearray(tx_hash)
                            b.reverse()
                            file_name = base_encode(hashlib.md5(tx_hash + txout.pk_script).digest(), 58)
                            with open(os.path.join(self.bad_vouts_path, str(block.height) + '_BADOPS_' + file_name),
                                    'w') as f:
                                f.write('TXID : {}\n'.format(b.hex()))
                                f.write('SCRIPT : {}\n'.format(txout.pk_script.hex()))
                                f.write('OPS : {}\n'.format(str(ops)))
                        continue

                    # This variable represents the op tuple where the OP_RVN_ASSET would be
                    op_ptr = match_script_against_template(ops, SCRIPTPUBKEY_TEMPLATE_P2PK)

                    if op_ptr > -1:
                        # This is a P2PK script. Not used in favor of P2PKH. Convert to P2PKH for hashing DB Purposes.

                        # Get the address bytes.
                        addr_bytes = ops[0][2]
                        addr = public_key_to_address(addr_bytes, self.coin.P2PKH_VERBYTE)
                        hashX = self.coin.address_to_hashX(addr)
                    else:
                        invalid_script = False
                        for i in range(len(ops)):
                            op = ops[i][0]  # The OpCode
                            if op == OpCodes.OP_RVN_ASSET:
                                op_ptr = i
                                break
                            if op == -1:
                                invalid_script = True
                                break

                        if invalid_script:
                            # This script could not be parsed properly before any OP_RVN_ASSETs.
                            # Hash as-is for possible spends and continue.
                            hashX = script_hashX(txout.pk_script)
                            append_hashX(hashX)
                            put_utxo(tx_hash + to_le_uint32(idx),
                                    hashX + tx_numb + to_le_uint64(txout.value))
                            if self.env.write_bad_vouts_to_file:
                                b = bytearray(tx_hash)
                                b.reverse()
                                file_name = base_encode(hashlib.md5(tx_hash + txout.pk_script).digest(), 58)
                                with open(os.path.join(self.bad_vouts_path, str(block.height) + '_BADOPS_' + file_name),
                                        'w') as f:
                                    f.write('TXID : {}\n'.format(b.hex()))
                                    f.write('SCRIPT : {}\n'.format(txout.pk_script.hex()))
                                    f.write('OPS : {}\n'.format(str(ops)))
                            continue

                        if op_ptr > 0:
                            # This script has OP_RVN_ASSET. Use everything before this for the script hash.
                            # Get the raw script bytes ending ptr from the previous opcode.
                            script_hash_end = ops[op_ptr - 1][1]
                            hashX = script_hashX(txout.pk_script[:script_hash_end])
                        elif op_ptr == 0:
                            # This is an asset tag

                            # continue is called after this block

                            idx = to_le_uint32(idx)

                            try:
                                if match_script_against_template(ops, ASSET_NULL_TEMPLATE) > -1:
                                    # This is what tags an address with a qualifier
                                    h160 = ops[1][2]
                                    asset_portion = ops[2][2]
                                    asset_portion_deserializer = DataParser(asset_portion)
                                    name_byte_len, asset_name = asset_portion_deserializer.read_var_bytes_tuple_bytes()
                                    flag = asset_portion_deserializer.read_byte()

                                    current_key = bytes([len(h160)]) + h160 + name_byte_len + asset_name

                                    old_data = pop_h160_qualified(current_key)
                                    if not old_data:
                                        old_data = self.db.asset_db.get(b't' + current_key)

                                    if old_data:
                                        # We have a previous tag
                                        h160_qualified_undo_infos_append(current_key + old_data)
                                    else:
                                        # We don't have a previous tag; set to delete
                                        h160_qualified_undo_infos_append(current_key + idx + tx_numb + b'\xff')

                                    put_h160_qualified(current_key, idx + tx_numb + flag)

                                elif match_script_against_template(ops, ASSET_NULL_VERIFIER_TEMPLATE) > -1:
                                    # This associates a restricted asset with qualifier tags in a boolean logic string
                                    qualifiers_b = ops[2][2]
                                    qualifiers_deserializer = DataParser(qualifiers_b)
                                    asset_names = qualifiers_deserializer.read_var_bytes_as_ascii()
                                    self.current_restricted_string = asset_names
                                    self.current_qualifiers = re.findall(r'([A-Z0-9_.]+)', asset_names)
                                    self.qualifiers_idx = idx
                                elif match_script_against_template(ops, ASSET_GLOBAL_RESTRICTION_TEMPLATE) > -1:
                                    # This globally freezes a restricted asset
                                    asset_portion = ops[3][2]

                                    asset_portion_deserializer = DataParser(asset_portion)
                                    asset_name_len, asset_name = asset_portion_deserializer.read_var_bytes_tuple_bytes()
                                    flag = asset_portion_deserializer.read_byte()

                                    current_key = asset_name_len + asset_name

                                    old_data = pop_restricted_freezes(current_key)
                                    if not old_data:
                                        old_data = self.db.asset_db.get(b'f' + current_key)

                                    if old_data:
                                        # We have info
                                        restricted_freezes_undo_infos_append(current_key + old_data)
                                    else:
                                        # We don't have any previous info; set to delete
                                        restricted_freezes_undo_infos_append(current_key + idx + tx_numb + b'\xff')

                                    put_restricted_freezes(current_key, idx + tx_numb + flag)

                                else:
                                    raise Exception('Bad null asset script ops')
                            except Exception as e:
                                if self.env.write_bad_vouts_to_file:
                                    b = bytearray(tx_hash)
                                    b.reverse()
                                    file_name = base_encode(hashlib.md5(tx_hash + txout.pk_script).digest(), 58)
                                    with open(os.path.join(self.bad_vouts_path,
                                                        str(block.height) + '_NULLASSET_' + file_name), 'w') as f:
                                        f.write('TXID : {}\n'.format(b.hex()))
                                        f.write('SCRIPT : {}\n'.format(txout.pk_script.hex()))
                                        f.write('OpCodes : {}\n'.format(str(ops)))
                                        f.write('Exception : {}\n'.format(repr(e)))
                                        f.write('Traceback : {}\n'.format(traceback.format_exc()))
                                if isinstance(e, (DataParser.ParserException, KeyError)):
                                    raise e

                            # Get the hashx and continue
                            hashX = script_hashX(txout.pk_script)
                            append_hashX(hashX)
                            put_utxo(tx_hash + idx,
                                hashX + tx_numb + to_le_uint64(txout.value))
                            
                            continue
                        else:
                            # There is no OP_RVN_ASSET. Hash as-is.
                            hashX = script_hashX(txout.pk_script)

                    # Add UTXO info to the database
                    append_hashX(hashX)
                    put_utxo(tx_hash + to_le_uint32(idx),
                            hashX + tx_numb + to_le_uint64(txout.value))

                    # Now try and add asset info
                    def try_parse_asset(asset_deserializer: DataParser, second_loop=False):
                        op = asset_deserializer.read_bytes(3)
                        if op != b'rvn':
                            raise Exception("Expected {}, was {}".format(b'rvn', op))
                        script_type = asset_deserializer.read_byte()
                        asset_name_len, asset_name = asset_deserializer.read_var_bytes_tuple_bytes()
                        if asset_name[0] == b'$'[0]:
                            self.current_restricted_asset = asset_name
                            self.restricted_idx = to_le_uint32(idx)
                        if script_type == b'o':
                            # This is an ownership asset. It does not have any metadata.
                            # Just assign it with a value of 1
                            put_asset(tx_hash + to_le_uint32(idx),
                                    hashX + tx_numb + to_le_uint64(100_000_000) +
                                    asset_name_len + asset_name)

                                # (total sats: 8) 
                                # (div amt: 1)
                                # (reissueable: 1)
                                # (has ipfs: 1)
                                # (outpoint count: 1)
                                # (outpoint type: 1)
                                # (outpoint 1 idx: 4)
                                # (outpoint 1 numb: 5)

                            put_asset_data_new(asset_name, to_le_uint64(100_000_000) + b'\0\0\0\x01\0' + to_le_uint32(idx) + tx_numb)
                            asset_meta_undo_info_append(  # Set previous meta to null in case of roll back
                                asset_name_len + asset_name + b'\0')
                            self.asset_touched.add(asset_name.decode('ascii'))
                        else:  # Not an owner asset; has a sat amount
                            sats = asset_deserializer.read_bytes(8)
                            if script_type == b'q':  # A new asset issuance

                                # (total sats: 8) 
                                # (div amt: 1)
                                # (reissueable: 1)
                                # (has ipfs: 1)
                                # (outpoint count: 1)
                                # (outpoint type: 1)
                                # (outpoint 1 idx: 4)
                                # (outpoint 1 numb: 5)

                                asset_data = asset_deserializer.read_bytes(2)
                                has_meta = asset_deserializer.read_byte()
                                asset_data += has_meta
                                if has_meta != b'\0':
                                    asset_data += asset_deserializer.read_bytes(34)

                                # To tell the client where this data came from
                                asset_data += b'\x01\0' + to_le_uint32(idx) + tx_numb

                                # Put DB functions at the end to prevent them from pushing before any errors
                                put_asset_data_new(asset_name, sats + asset_data)  # Add meta for this asset
                                asset_meta_undo_info_append(  # Set previous meta to null in case of roll back
                                    asset_name_len + asset_name + b'\0')
                                put_asset(tx_hash + to_le_uint32(idx),
                                        hashX + tx_numb + sats +
                                        asset_name_len + asset_name)
                                self.asset_touched.add(asset_name.decode('ascii'))
                            elif script_type == b'r':  # An asset re-issuance
                                divisions = asset_deserializer.read_byte()
                                reissuable = asset_deserializer.read_byte()

                                # Quicker check, but it's far more likely to be in the db
                                popped_from_new = True
                                old_data = self.asset_data_new.get(asset_name, None)
                                if not old_data:
                                    popped_from_new = False
                                    old_data = self.asset_data_reissued.get(asset_name, None)
                                if not old_data:
                                    old_data = self.db.asset_info_db.get(asset_name)
                                assert old_data # If reissuing, we should have it

                                # old_data structure:
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


                                old_data_parser = DataParser(old_data)
                                old_sats = int.from_bytes(old_data_parser.read_bytes(8), 'little')
                                new_sats = int.from_bytes(sats, 'little')

                                # How many outpoints we need to save
                                have_old_div = False
                                have_old_ipfs = False

                                total_sats = old_sats + new_sats

                                old_divisions = old_data_parser.read_byte()
                                if divisions == b'\xff':  # Unchanged division amount
                                    have_old_div = True
                                    divisions = old_divisions
                                
                                _old_reissue = old_data_parser.read_boolean()
                                if not _old_reissue:
                                    raise ValueError('We are reissuing a non-reissuable asset!')

                                if asset_deserializer.is_finished():
                                    ipfs = None
                                else:
                                    if second_loop:
                                        if asset_deserializer.cursor + 34 <= asset_deserializer.length:
                                            ipfs = asset_deserializer.read_bytes(34)
                                        else:
                                            ipfs = None
                                    else:
                                        ipfs = asset_deserializer.read_bytes(34)

                                old_boolean = old_data_parser.read_boolean()
                                if old_boolean:
                                    old_ipfs = old_data_parser.read_bytes(34)

                                if not ipfs and old_boolean:
                                    have_old_ipfs = True
                                    ipfs = old_ipfs

                                old_outpoint = None
                                old_ipfs_outpoint = None
                                old_div_outpoint = None

                                for _ in range(old_data_parser.read_int()):
                                    outpoint_type = old_data_parser.read_int()
                                    if outpoint_type == 0:
                                        old_outpoint = old_data_parser.read_bytes(4+5)
                                    elif outpoint_type == 1:
                                        old_div_outpoint = old_data_parser.read_bytes(4+5)
                                    elif outpoint_type == 2:
                                        old_ipfs_outpoint = old_data_parser.read_bytes(4+5)
                                    else:
                                        raise ValueError(f'Unknown outpoint type: {outpoint_type}')

                                assert old_outpoint

                                this_outpoint = to_le_uint32(idx) + tx_numb
                                
                                this_data = b''
                                this_data += (total_sats.to_bytes(8, 'little'))
                                this_data += (divisions)
                                this_data += (reissuable)
                                this_data += (b'\x01' if ipfs else b'\0')
                                if ipfs:
                                    this_data += (ipfs)
                                this_data += bytes([1 + (1 if have_old_div else 0) + (1 if have_old_ipfs else 0)])
                                this_data += (b'\0')
                                this_data += (this_outpoint)
                                if have_old_div:
                                    this_data += (b'\x01')
                                    if old_div_outpoint:
                                        this_data += (old_div_outpoint)
                                    else:
                                        this_data += (old_outpoint)
                                if have_old_ipfs:
                                    this_data += (b'\x02')
                                    if old_ipfs_outpoint:
                                        this_data += (old_ipfs_outpoint)
                                    else:
                                        this_data += (old_outpoint)

                                # Put DB functions at the end to prevent them from pushing before any errors

                                if popped_from_new:
                                    self.asset_data_new.pop(asset_name, None)
                                else:
                                    self.asset_data_reissued.pop(asset_name, None)

                                self.asset_touched.add(asset_name.decode('ascii'))
                                put_asset_data_reissued(asset_name, this_data)
                                asset_meta_undo_info_append(
                                    asset_name_len + asset_name +
                                    bytes([len(old_data)]) + old_data)
                                
                                put_asset(tx_hash + to_le_uint32(idx),
                                        hashX + tx_numb + sats +
                                        asset_name_len + asset_name)
                            elif script_type == b't':
                                # Put DB functions at the end to prevent them from pushing before any errors
                                put_asset(tx_hash + to_le_uint32(idx),
                                        hashX + tx_numb + sats +
                                        asset_name_len + asset_name)

                                if not asset_deserializer.is_finished():
                                    if (b'!' in asset_name or b'~' in asset_name) and hashX in hashXs:  # This hashX was also in the inputs; we are sending to ourself; this is a broadcast
                                        if second_loop:
                                            if asset_deserializer.cursor + 34 <= asset_deserializer.length:
                                                data = asset_deserializer.read_bytes(34)
                                                timestamp = b'\0\0\0\0\0\0\0\0'
                                                if not asset_deserializer.is_finished():
                                                    timestamp = asset_deserializer.read_bytes(8)
                                                # This is a message broadcast
                                                put_asset_broadcast(
                                                    asset_name_len + asset_name + to_le_uint32(idx) + tx_numb, data + timestamp)
                                                asset_broadcast_undo_info.append(
                                                    asset_name_len + asset_name + to_le_uint32(idx) + tx_numb)
                                        else:
                                            data = asset_deserializer.read_bytes(34)
                                            timestamp = b'\0\0\0\0\0\0\0\0'
                                            if not asset_deserializer.is_finished():
                                                timestamp = asset_deserializer.read_bytes(8)
                                            # This is a message broadcast
                                            put_asset_broadcast(asset_name_len + asset_name + to_le_uint32(idx) + tx_numb,
                                                                data + timestamp)
                                            asset_broadcast_undo_info.append(
                                                asset_name_len + asset_name + to_le_uint32(idx) + tx_numb)
                            else:
                                raise Exception('Unknown asset type: {}'.format(script_type))

                    # function for malformed asset
                    def try_parse_asset_iterative(script: bytes):
                        while script[:3] != b'rvn' and len(script) > 0:
                            script = script[1:]
                        assert script[:3] == b'rvn'
                        return try_parse_asset(DataParser(script), True)

                    # Me @ core devs
                    # https://www.youtube.com/watch?v=iZlpsneDGBQ

                    if 0 < op_ptr < len(ops):
                        assert ops[op_ptr][0] == OpCodes.OP_RVN_ASSET  # Sanity check
                        try:
                            next_op = ops[op_ptr + 1]
                            if next_op[0] == -1:
                                # This contains the raw data. Deserialize.
                                asset_script_deserializer = DataParser(next_op[2])
                                asset_script = asset_script_deserializer.read_var_bytes()
                            elif len(ops) > op_ptr + 4 and \
                                    ops[op_ptr + 2][0] == b'r'[0] and \
                                    ops[op_ptr + 3][0] == b'v'[0] and \
                                    ops[op_ptr + 4][0] == b'n'[0]:
                                asset_script_portion = txout.pk_script[ops[op_ptr][1]:]
                                asset_script_deserializer = DataParser(asset_script_portion)
                                asset_script = asset_script_deserializer.read_var_bytes()
                            else:
                                # Hurray! This i̶s̶ ̶a̶ COULD BE A properly formatted asset script
                                asset_script = next_op[2]

                            asset_deserializer = DataParser(asset_script)
                            try_parse_asset(asset_deserializer)
                            is_asset = True

                        except:
                            try:
                                try_parse_asset_iterative(txout.pk_script[ops[op_ptr][1]:])
                                is_asset = True
                            except Exception as e:
                                is_asset = False
                                if self.env.write_bad_vouts_to_file:
                                    b = bytearray(tx_hash)
                                    b.reverse()
                                    file_name = base_encode(hashlib.md5(tx_hash + txout.pk_script).digest(), 58)
                                    with open(os.path.join(self.bad_vouts_path, str(block.height) + '_' + file_name),
                                            'w') as f:
                                        f.write('TXID : {}\n'.format(b.hex()))
                                        f.write('SCRIPT : {}\n'.format(txout.pk_script.hex()))
                                        f.write('OpCodes : {}\n'.format(str(ops)))
                                        f.write('Exception : {}\n'.format(repr(e)))
                                        f.write('Traceback : {}\n'.format(traceback.format_exc()))

                if self.current_restricted_asset and self.current_restricted_string:
                    res = self.current_restricted_asset  # type: bytes

                    res_to_string_key = bytes([len(res)]) + res

                    old_data = pop_restricted_strings(res_to_string_key)
                    if not old_data:
                        old_data = self.db.asset_db.get(b'r' + res_to_string_key)

                    if old_data:
                        # We have previous data
                        restricted_strings_undo_infos_append(res_to_string_key + old_data)
                    else:
                        # We don't previous data; set to delete
                        restricted_strings_undo_infos_append(res_to_string_key + self.restricted_idx + self.qualifiers_idx + tx_numb + b'\0')

                    put_restricted_strings(res_to_string_key, self.restricted_idx + self.qualifiers_idx + tx_numb + bytes([len(self.current_restricted_string)]) + self.current_restricted_string.encode('ascii'))

                    old_associated_qualifiers = []
                    if old_data:
                        parser = DataParser(old_data)
                        # Get rid of idx + txnumb
                        parser.read_bytes(4 + 4 + 5)
                        old_string = parser.read_var_bytes_as_ascii()
                        old_associated_qualifiers = re.findall(r'([A-Z0-9_.]+)', old_string)

                    for old_qual in old_associated_qualifiers:
                        # If a qualifier was previously associated and is now not
                        if old_qual not in self.current_qualifiers:
                            qual_association_key = bytes([len(old_qual)]) + old_qual.encode('ascii') + bytes([len(res)]) + res

                            old_data = pop_qualifier_associations(qual_association_key)
                            if not old_data:
                                old_data = self.db.asset_db.get(b'q' + qual_association_key)

                            if old_data:
                                qualifier_associations_undo_infos_append(qual_association_key + old_data)
                            else:
                                qualifier_associations_undo_infos_append(qual_association_key + self.restricted_idx + self.qualifiers_idx + tx_numb + b'\xff')

                            put_qualifier_associations(qual_association_key, self.restricted_idx + self.qualifiers_idx + tx_numb + b'\0')

                    for new_qual in self.current_qualifiers:
                        # If a qualifier was not previously associated, update
                        if new_qual not in old_associated_qualifiers:
                            qual_association_key = bytes([len(new_qual)]) + new_qual.encode('ascii') + bytes([len(res)]) + res

                            old_data = pop_qualifier_associations(qual_association_key)
                            if not old_data:
                                old_data = self.db.asset_db.get(b'q' + qual_association_key)

                            if old_data:
                                qualifier_associations_undo_infos_append(qual_association_key + old_data)
                            else:
                                qualifier_associations_undo_infos_append(qual_association_key + self.restricted_idx + self.qualifiers_idx + tx_numb + b'\xff')

                            put_qualifier_associations(qual_association_key, self.restricted_idx + self.qualifiers_idx + tx_numb + b'x\01')


                append_hashXs(hashXs)
                update_touched(hashXs)
                append_tx_hash(tx_hash)
                tx_num += 1
                if is_asset:
                    asset_num += 1
                self.current_restricted_asset = None
                self.current_qualifiers = []
                self.current_restricted_string = ''

         # Do this first - it uses the prior state
        self.tx_hashes.append(b''.join(tx_hashes))
        self.db.history.add_unflushed(hashXs_by_tx, state.tx_count)
        self.db.tx_counts.append(tx_num)

        # Assets aren't always in tx's... remove None types
        asset_undo_info = [i for i in asset_undo_info if i]

        if block.height >= self.db.min_undo_height(self.daemon.cached_height()):
            self.undo_infos.append((undo_info, block.height))
            self.asset_undo_infos.append((asset_undo_info, block.height))
            self.asset_data_undo_infos.append((asset_meta_undo_info, block.height))
            self.h160_qualified_undo_infos.append((internal_h160_qualified_undo_infos, block.height))
            self.restricted_freezes_undo_infos.append((internal_restricted_freezes_undo_infos, block.height))
            self.restricted_strings_undo_infos.append((internal_restricted_strings_undo_infos, block.height))
            self.qualifier_associations_undo_infos.append((internal_qualifier_associations_undo_infos, block.height))
            self.asset_broadcast_undos.append((asset_broadcast_undo_info, block.height))

        self.headers.append(block.header)
        
        #Update State
        state.height = block.height
        state.tip = self.coin.header_hash(block.header)
        state.chain_size += block.size
        state.tx_count = tx_num
        state.asset_count = asset_num 
        self.ok = True

    def backup_block(self, block):
        '''Backup the streamed block.'''
        self.db.assert_flushed(self.flush_data())
        assert block.height > 0
        genesis_activation = self.coin.GENESIS_ACTIVATION
        
        is_unspendable = (is_unspendable_genesis if block.height >= genesis_activation
                          else is_unspendable_legacy)
        
        undo_info = self.db.read_undo_info(block.height)
        asset_undo_info = self.db.read_asset_undo_info(block.height)
        if undo_info is None or asset_undo_info is None:
            raise ChainError(f'no undo information found for height {block.height:,d}')

        data_parser = DataParser(self.db.read_asset_meta_undo_info(block.height))
        while not data_parser.is_finished():  # Stops when None or empty
            name = data_parser.read_var_bytes()
            data_len, data = data_parser.read_var_bytes_tuple()
            if data_len == 0:
                self.asset_data_deletes.append(name)
            else:
                self.asset_data_reissued[name] = data

        data_parser = DataParser(self.db.read_asset_broadcast_undo_info(block.height))
        while not data_parser.is_finished():
            asset_len = data_parser.read_int()
            # bytes([len(asset_name)]) + asset_name + to_le_uint32(idx) + tx_numb
            key_len = asset_len + 4 + 5
            key = data_parser.read_bytes(key_len)
            self.asset_broadcast_dels.append(key)

        data_parser = DataParser(self.db.read_h160_tag_undo_info(block.height))
        while not data_parser.is_finished():
            h160_len, h160 = data_parser.read_var_bytes_tuple_bytes()
            asset_len, asset = data_parser.read_var_bytes_tuple_bytes()
            idx_txnumb = data_parser.read_bytes(9)
            flag = data_parser.read_byte()
            if flag == b'\xff':
                self.h160_qualified_deletes.append(h160_len + h160 + asset_len + asset)
            else:
                self.h160_qualified.__setitem__(h160_len + h160 + asset_len + asset, idx_txnumb + flag)

        data_parser = DataParser(self.db.read_res_freeze_undo_info(block.height))
        while not data_parser.is_finished():
            asset_len, asset = data_parser.read_var_bytes_tuple_bytes()
            idx_txnumb = data_parser.read_bytes(9)
            flag = data_parser.read_byte()
            if flag == b'\xff':
                self.restricted_freezes_deletes.append(asset_len + asset)
            else:
                self.restricted_freezes.__setitem__(asset_len + asset, idx_txnumb + flag)

        data_parser = DataParser(self.db.read_res_string_undo_info(block.height))
        while not data_parser.is_finished():
            asset_len, asset = data_parser.read_var_bytes_tuple_bytes()
            idx_txnumb = data_parser.read_bytes(4 + 4 + 5)
            str_len, tags = data_parser.read_var_bytes_tuple_bytes()
            if str_len == b'\0':
                self.restricted_strings_deletes.append(asset_len + asset)
            else:
                self.restricted_strings.__setitem__(asset_len + asset, idx_txnumb + str_len + tags)

        data_parser = DataParser(self.db.read_qual_undo_info(block.height))
        while not data_parser.is_finished():
            qual_len, qual = data_parser.read_var_bytes_tuple_bytes()
            restricted_len, restricted = data_parser.read_var_bytes_tuple_bytes()
            idx_txnumb = data_parser.read_bytes(4 + 4 + 5)
            flag = data_parser.read_byte()
            if flag == b'\xff':
                self.qualifier_associations_deletes.append(qual_len + qual + restricted_len + restricted)
            else:
                self.qualifier_associations.__setitem__(qual_len + qual + restricted_len + restricted, idx_txnumb + flag)

        n = len(undo_info)
        asset_n = len(asset_undo_info)

        # Use local vars for speed in the loops
        put_utxo = self.utxo_cache.__setitem__
        spend_utxo = self.spend_utxo
        touched_add = self.touched.add        
        undo_entry_len = 13 + HASHX_LEN

        # n is our pointer.
        # Items in our list are ordered, but we want them backwards.

        # Value of the asset cache is:
        # tx_hash + u32 idx + HASHX + TX_NUMB + SATS IN U64 + 1 BYTE OF LEN + NAME
        # 32 + 4 + HASHX_LEN BYTES + 5 BYTES + 8 BYTES + 1 BYTE + VAR BYTES
        def find_asset_undo_len(max):
            assert max <= len(asset_undo_info)
            if max == 0:
                return 0
            else:
                def val_len(ptr):
                    name_len = asset_undo_info[ptr + 32 + 4 + HASHX_LEN + 5 + 8]
                    return name_len + 32 + 4 + HASHX_LEN + 5 + 8 + 1

                last_val_ptr = 0
                while True:
                    next_data = last_val_ptr + val_len(last_val_ptr)
                    if next_data >= max:
                        break
                    last_val_ptr += val_len(last_val_ptr)
                assert next_data == max
                return max - last_val_ptr

        assets = 0
        put_asset = self.asset_cache.__setitem__
        spend_asset = self.spend_asset

        count = 0
        with block as block:
            self.ok = False
            for tx, tx_hash in block.iter_txs_reversed():
                for idx, txout in enumerate(tx.outputs):
                    # Spend the TX outputs.  Be careful with unspendable
                    # outputs - we didn't save those in the first place.
                    if is_unspendable(txout.pk_script):
                        continue

                    cache_value = spend_utxo(tx_hash, idx)

                    # Since we add assets in the UTXO's normally, when backing up
                    # Remove them in the spend.
                    if spend_asset(tx_hash, idx):
                        assets += 1

                    # All assets will be in the normal utxo cache
                    touched_add(cache_value[:-13])

                # Restore the inputs
                for txin in reversed(tx.inputs):
                    prev_idx_bytes = pack_le_uint32(txin.prev_idx)

                    if txin.is_generation():
                        continue
                    n -= undo_entry_len
                    undo_item = undo_info[n:n + undo_entry_len]
                    put_utxo(bytes(txin.prev_hash) + pack_le_uint32(txin.prev_idx), undo_item)
                    touched_add(undo_item[:-13])

                    asset_undo_entry_len = find_asset_undo_len(asset_n)
                    new_asset_n = asset_n - asset_undo_entry_len
                    if new_asset_n >= 0 and asset_undo_entry_len > 0:
                        undo_item = asset_undo_info[new_asset_n:new_asset_n + asset_undo_entry_len]
                        if undo_item[:32] == bytes(txin.prev_hash) and undo_item[32:36] == prev_idx_bytes:
                            put_asset(bytes(txin.prev_hash) + prev_idx_bytes, undo_item[36:])
                            asset_n = new_asset_n
                count += 1

        assert n == 0
        assert asset_n == 0
        
        state = self.state
        state.height -= 1
        state.tip = self.coin.header_prevhash(block.header)
        state.chain_size -= block.size
        state.tx_count -= count
        state.asset_count -= assets

        self.db.tx_counts.pop()

        # self.touched can include other addresses which is harmless, but remove None.
        self.touched.discard(None)
        self.db.flush_backup(self.flush_data(), self.touched)
        self.ok = True

    '''An in-memory UTXO cache, representing all changes to UTXO state
    since the last DB flush.

    We want to store millions of these in memory for optimal
    performance during initial sync, because then it is possible to
    spend UTXOs without ever going to the database (other than as an
    entry in the address history, and there is only one such entry per
    TX not per UTXO).  So store them in a Python dictionary with
    binary keys and values.

      Key:    TX_HASH + TX_IDX           (32 + 4 = 36 bytes)
      Value:  HASHX + TX_NUM + VALUE     (11 + 5 + 8 = 24 bytes)

    That's 60 bytes of raw data in-memory.  Python dictionary overhead
    means each entry actually uses about 205 bytes of memory.  So
    almost 5 million UTXOs can fit in 1GB of RAM.  There are
    approximately 42 million UTXOs on bitcoin mainnet at height
    433,000.

    Semantics:

      add:   Add it to the cache dictionary.

      spend: Remove it if in the cache dictionary.  Otherwise it's
             been flushed to the DB.  Each UTXO is responsible for two
             entries in the DB.  Mark them for deletion in the next
             cache flush.

    The UTXO database format has to be able to do two things efficiently:

      1.  Given an address be able to list its UTXOs and their values
          so its balance can be efficiently computed.

      2.  When processing transactions, for each prevout spent - a (tx_hash,
          idx) pair - we have to be able to remove it from the DB.  To send
          notifications to clients we also need to know any address it paid
          to.

    To this end we maintain two "tables", one for each point above:

      1.  Key: b'u' + address_hashX + tx_idx + tx_num
          Value: the UTXO value as a 64-bit unsigned integer

      2.  Key: b'h' + compressed_tx_hash + tx_idx + tx_num
          Value: hashX

    The compressed tx hash is just the first few bytes of the hash of
    the tx in which the UTXO was created.  As this is not unique there
    will be potential collisions so tx_num is also in the key.  When
    looking up a UTXO the prefix space of the compressed hash needs to
    be searched and resolved if necessary with the tx_num.  The
    collision rate is low (<0.1%).
    '''

    def spend_utxo(self, tx_hash, tx_idx):
        '''Spend a UTXO and return the 33-byte value.

        If the UTXO is not in the cache it must be on disk.  We store
        all UTXOs so not finding one indicates a logic error or DB
        corruption.
        '''
        # Fast track is it being in the cache
        idx_packed = pack_le_uint32(tx_idx)
        cache_value = self.utxo_cache.pop(tx_hash + idx_packed, None)
        if cache_value:
            return cache_value

        # Spend it from the DB.

        # Key: b'h' + compressed_tx_hash + tx_idx + tx_num
        # Value: hashX
        prefix = b'h' + tx_hash[:4] + idx_packed
        candidates = {db_key: hashX for db_key, hashX
                      in self.db.utxo_db.iterator(prefix=prefix)}

        for hdb_key, hashX in candidates.items():
            tx_num_packed = hdb_key[-5:]

            if len(candidates) > 1:
                tx_num, = unpack_le_uint64(tx_num_packed + bytes(3))
                fs_hash, _height = self.db.fs_tx_hash(tx_num)
                if fs_hash != tx_hash:
                    assert fs_hash is not None  # Should always be found
                    continue

            # Key: b'u' + address_hashX + tx_idx + tx_num
            # Value: the UTXO value as a 64-bit unsigned integer
            udb_key = b'u' + hashX + hdb_key[-9:]
            utxo_value_packed = self.db.utxo_db.get(udb_key)
            if utxo_value_packed:
                # Remove both entries for this UTXO
                self.db_deletes.append(hdb_key)
                self.db_deletes.append(udb_key)
                return hashX + tx_num_packed + utxo_value_packed

        raise ChainError(f'UTXO {hash_to_hex_str(tx_hash)} / {tx_idx:,d} not found in "h" table')

    def spend_asset(self, tx_hash, tx_idx):
        # TODO: Find a way to make this faster
        # Fast track is it being in the cache
        idx_packed = pack_le_uint32(tx_idx)
        cache_value = self.asset_cache.pop(tx_hash + idx_packed, None)
        if cache_value:
            return tx_hash + idx_packed + cache_value

        # Spend it from the DB.

        # Key: b'h' + compressed_tx_hash + tx_idx + tx_num
        # Value: hashX
        prefix = b'h' + tx_hash[:4] + idx_packed
        candidates = {db_key: hashX for db_key, hashX
                      in self.db.asset_db.iterator(prefix=prefix)}

        for hdb_key, hashX in candidates.items():
            tx_num_packed = hdb_key[-5:]

            if len(candidates) > 1:
                tx_num, = unpack_le_uint64(tx_num_packed + bytes(3))
                # Assets are on txs
                fs_hash, _height = self.db.fs_tx_hash(tx_num)
                if fs_hash != tx_hash:
                    assert fs_hash is not None  # Should always be found
                    continue

            # Key: b'u' + address_hashX + tx_idx + tx_num
            # Value: the asset amt and name
            udb_key = b'u' + hashX + hdb_key[-9:]
            value = self.db.asset_db.get(udb_key)
            if value:
                # Remove both entries for this Asset
                self.asset_deletes.append(hdb_key)
                self.asset_deletes.append(udb_key)
                return tx_hash + idx_packed + hashX + tx_num_packed + value

        # Asset doesn't need to be found
        # raise ChainError('UTXO {} / {:,d} not found in "h" table'
        # .format(hash_to_hex_str(tx_hash), tx_idx))

    async def on_caught_up(self):
        was_first_sync = self.state.first_sync
        self.state.first_sync = False
        await self.flush(True)
        if self.caught_up:
            # Flush everything before notifying as client queries are performed on the DB
            await self.notifications.on_block(self.touched, self.state.height, self.asset_touched)
            self.touched = set()
            self.asset_touched = set()
        else:
            self.caught_up = True
            if was_first_sync:
                logger.info(f'{electrumx.version} synced to height {self.state.height:,d}')
            # Reopen for serving
            await self.db.open_for_serving()

    # --- External API

    async def fetch_and_process_blocks(self, caught_up_event):
        '''Fetch, process and index blocks from the daemon.

        Sets caught_up_event when first caught up.  Flushes to disk
        and shuts down cleanly if cancelled.

        This is mainly because if, during initial sync ElectrumX is
        asked to shut down when a large number of blocks have been
        processed but not written to disk, it should write those to
        disk before exiting, as otherwise a significant amount of work
        could be lost.
        '''

        if self.env.write_bad_vouts_to_file and not os.path.isdir(self.bad_vouts_path):
            os.mkdir(self.bad_vouts_path)
        
        self.state = OnDiskBlock.state = (await self.db.open_for_sync()).copy()
        await OnDiskBlock.scan_files()
        
        try:
            show_summary = True
            while True:
                hex_hashes, daemon_height = await self.next_block_hashes()
                if show_summary:
                    show_summary = False
                    behind = daemon_height - self.state.height                    
                    if behind > 0:
                        logger.info(f'catching up to daemon height {daemon_height:,d} '
                                    f'({behind:,d} blocks behind)')
                    else:
                        logger.info(f'caught up to daemon height {daemon_height:,d}')

                if hex_hashes:
                    # Shielded so that cancellations from shutdown don't lose work
                    await self.advance_blocks(hex_hashes)
                else:
                    await self.on_caught_up()
                    caught_up_event.set()
                    await sleep(self.polling_delay)

                if self.reorg_count is not None:
                    await self.reorg_chain(self.reorg_count)
                    self.reorg_count = None
                    show_summary = True

        # Don't flush for arbitrary exceptions as they might be a cause or consequence of
        # corrupted data
        except CancelledError:
            await OnDiskBlock.stop_prefetching()
            await self.run_with_lock(self.flush_if_safe())

        except Exception:
            logging.exception('Critical Block Processor Error:')
            raise


    async def flush_if_safe(self):
        if self.ok:
            logger.info('flushing to DB for a clean shutdown...')
            await self.flush(True)
            logger.info('flushed cleanly')
        else:
            logger.warning('not flushing to DB as data in memory is incomplete')


    def force_chain_reorg(self, count):
        '''Force a reorg of the given number of blocks.  Returns True if a reorg is queued.
        During initial sync we don't store undo information so cannot fake a reorg until
        caught up.
        '''
        if self.caught_up:
            self.reorg_count = count
            return True
        return False
