# Copyright (c) 2016-2018, Neil Booth
#
# All rights reserved.
#
# See the file "LICENCE" for information about the copyright
# and warranty status of this software.

'''Mempool handling.'''

import itertools
import logging
import time
from abc import ABC, abstractmethod
from collections import defaultdict

import attr
from aiorpcx import TaskGroup, run_in_thread, sleep

from electrumx.lib.addresses import public_key_to_address
from electrumx.lib.hash import hash_to_hex_str, hex_str_to_hash
from electrumx.lib.script import OpCodes, ScriptPubKey
from electrumx.lib.util import class_logger, chunks
from electrumx.server.db import UTXO, ASSET


@attr.s(slots=True)
class MemPoolTx(object):
    prevouts = attr.ib()
    # (hashX, value, is_asset, asset_name) tuple
    in_pairs = attr.ib()
    out_pairs = attr.ib()
    fee = attr.ib()
    size = attr.ib()


@attr.s(slots=True)
class MemPoolTxSummary(object):
    hash = attr.ib()
    fee = attr.ib()
    has_unconfirmed_inputs = attr.ib()


class DBSyncError(Exception):
    pass


class MemPoolAPI(ABC):
    '''A concrete instance of this class is passed to the MemPool object
    and used by it to query DB and blockchain state.'''

    @abstractmethod
    async def height(self):
        '''Query bitcoind for its height.'''

    @abstractmethod
    def cached_height(self):
        '''Return the height of bitcoind the last time it was queried,
        for any reason, without actually querying it.
        '''

    @abstractmethod
    def db_height(self):
        '''Return the height flushed to the on-disk DB.'''

    @abstractmethod
    async def mempool_hashes(self):
        '''Query bitcoind for the hashes of all transactions in its
        mempool, returned as a list.'''

    @abstractmethod
    async def raw_transactions(self, hex_hashes):
        '''Query bitcoind for the serialized raw transactions with the given
        hashes.  Missing transactions are returned as None.

        hex_hashes is an iterable of hexadecimal hash strings.'''

    @abstractmethod
    async def lookup_utxos(self, prevouts):
        '''Return a list of (hashX, value) pairs each prevout if unspent,
        otherwise return None if spent or not found.

        prevouts - an iterable of (hash, index) pairs
        '''

    @abstractmethod
    async def lookup_assets(self, prevouts):
        pass

    @abstractmethod
    async def on_mempool(self, touched, height):
        '''Called each time the mempool is synchronized.  touched is a set of
        hashXs touched since the previous call.  height is the
        daemon's height at the time the mempool was obtained.'''


class MemPool(object):
    '''Representation of the daemon's mempool.

        coin - a coin class from coins.py
        api - an object implementing MemPoolAPI

    Updated regularly in caught-up state.  Goal is to enable efficient
    response to the calls in the external interface.  To that end we
    maintain the following maps:

       tx:     tx_hash -> MemPoolTx
       hashXs: hashX   -> set of all hashes of txs touching the hashX
    '''

    def __init__(self, coin, api, refresh_secs=5.0, log_status_secs=60.0):
        assert isinstance(api, MemPoolAPI)
        self.coin = coin
        self.api = api
        self.logger = class_logger(__name__, self.__class__.__name__)
        self.txs = {}
        self.hashXs = defaultdict(set)  # None can be a key
        self.refresh_secs = refresh_secs
        self.log_status_secs = log_status_secs

    async def _logging(self, synchronized_event):
        '''Print regular logs of mempool stats.'''
        self.logger.info('beginning processing of daemon mempool.  '
                         'This can take some time...')
        start = time.monotonic()
        await synchronized_event.wait()
        elapsed = time.monotonic() - start
        self.logger.info(f'synced in {elapsed:.2f}s')
        while True:
            mempool_size = sum(tx.size for tx in self.txs.values()) / 1_000_000
            self.logger.info(f'{len(self.txs):,d} txs {mempool_size:.2f} MB '
                             f'touching {len(self.hashXs):,d} addresses')
            await sleep(self.log_status_secs)
            await synchronized_event.wait()

    def _accept_transactions(self, tx_map, utxo_map, touched):
        '''Accept transactions in tx_map to the mempool if all their inputs
        can be found in the existing mempool or a utxo_map from the
        DB.

        Returns an (unprocessed tx_map, unspent utxo_map) pair.
        '''
        hashXs = self.hashXs
        txs = self.txs

        deferred = {}
        unspent = set(utxo_map)
        # Try to find all prevouts so we can accept the TX
        for tx_hash, tx in tx_map.items():
            in_pairs = []
            try:
                for prevout in tx.prevouts:
                    utxo = utxo_map.get(prevout)
                    if not utxo:
                        prev_hash, prev_index = prevout
                        # Raises KeyError if prev_hash is not in txs
                        utxo = txs[prev_hash].out_pairs[prev_index]
                    in_pairs.append(utxo)
            except KeyError:
                deferred[tx_hash] = tx
                continue

            # Spend the prevouts
            unspent.difference_update(tx.prevouts)

            # Save the in_pairs, compute the fee and accept the TX
            tx.in_pairs = tuple(in_pairs)
            # Avoid negative fees if dealing with generation-like transactions
            # because some in_parts would be missing
            tx.fee = max(0, (sum((v if not is_asset else 0) for _, v, is_asset, _ in tx.in_pairs) -
                             sum((v if not is_asset else 0) for _, v, is_asset, _ in tx.out_pairs)))
            txs[tx_hash] = tx

            for hashX, _value, _, _ in itertools.chain(tx.in_pairs, tx.out_pairs):
                touched.add(hashX)
                hashXs[hashX].add(tx_hash)

        return deferred, {prevout: utxo_map[prevout] for prevout in unspent}

    async def _refresh_hashes(self, synchronized_event):
        '''Refresh our view of the daemon's mempool.'''
        # Touched accumulates between calls to on_mempool and each
        # call transfers ownership
        touched = set()
        while True:
            height = self.api.cached_height()
            hex_hashes = await self.api.mempool_hashes()
            if height != await self.api.height():
                continue
            hashes = set(hex_str_to_hash(hh) for hh in hex_hashes)
            try:
                await self._process_mempool(hashes, touched, height)
            except DBSyncError:
                # The UTXO DB is not at the same height as the
                # mempool; wait and try again
                self.logger.debug('waiting for DB to sync')
            else:
                synchronized_event.set()
                synchronized_event.clear()
                await self.api.on_mempool(touched, height)
                touched = set()
            await sleep(self.refresh_secs)

    async def _process_mempool(self, all_hashes, touched, mempool_height):
        # Re-sync with the new set of hashes
        txs = self.txs
        hashXs = self.hashXs

        if mempool_height != self.api.db_height():
            raise DBSyncError

        # First handle txs that have disappeared
        for tx_hash in set(txs).difference(all_hashes):
            tx = txs.pop(tx_hash)
            tx_hashXs = set(hashX for hashX, value, _, _ in tx.in_pairs)
            tx_hashXs.update(hashX for hashX, value, _, _ in tx.out_pairs)
            for hashX in tx_hashXs:
                hashXs[hashX].remove(tx_hash)
                if not hashXs[hashX]:
                    del hashXs[hashX]
            touched.update(tx_hashXs)

        # Process new transactions
        new_hashes = list(all_hashes.difference(txs))
        if new_hashes:
            group = TaskGroup()
            for hashes in chunks(new_hashes, 200):
                coro = self._fetch_and_accept(hashes, all_hashes, touched)
                await group.spawn(coro)

            tx_map = {}
            utxo_map = {}
            async for task in group:
                deferred, unspent = task.result()
                tx_map.update(deferred)
                utxo_map.update(unspent)

            prior_count = 0
            # FIXME: this is not particularly efficient
            while tx_map and len(tx_map) != prior_count:
                prior_count = len(tx_map)
                tx_map, utxo_map = self._accept_transactions(tx_map, utxo_map,
                                                             touched)
            if tx_map:
                self.logger.error(f'{len(tx_map)} txs dropped')

        return touched

    async def _fetch_and_accept(self, hashes, all_hashes, touched):
        '''Fetch a list of mempool transactions.'''
        hex_hashes_iter = (hash_to_hex_str(hash) for hash in hashes)
        raw_txs = await self.api.raw_transactions(hex_hashes_iter)

        # TODO: Newly created asset data will not be shown

        def deserialize_txs():    # This function is pure
            to_hashX = self.coin.hashX_from_script
            deserializer = self.coin.DESERIALIZER

            txs = {}
            for tx_hash, raw_tx in zip(hashes, raw_txs):
                # The daemon may have evicted the tx from its
                # mempool or it may have gotten in a block
                if not raw_tx:
                    continue
                tx, tx_size = deserializer(raw_tx).read_tx_and_vsize()
                # Convert the inputs and outputs into (hashX, value) pairs
                # Drop generation-like inputs from MemPoolTx.prevouts
                txin_pairs = tuple((txin.prev_hash, txin.prev_idx)
                                   for txin in tx.inputs
                                   if not txin.is_generation())
                txout_tuple_list = []
                for txout in tx.outputs:
                    value = txout.value

                    try:
                        # Create a deserializer for the script pubkey
                        deserializer = self.coin.DESERIALIZER(txout.pk_script)
                        # Get the length of the script
                        # If the next byte is a push to stack, this is a depreciated P2PK
                        if deserializer.binary[deserializer.cursor] <= OpCodes.OP_PUSHDATA4:
                            pubkey = deserializer._read_varbytes()
                            op = deserializer._read_byte()
                            if op != OpCodes.OP_CHECKSIG:
                                raise Exception(
                                    "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                        OpCodes.OP_CHECKSIG, op))
                            # Convert P2PK to P2PKH/P2SH for database purposes
                            addr = public_key_to_address(pubkey, self.coin.P2PKH_VERBYTE)
                            hashX = self.coin.address_to_hashX(addr)
                        else:  # This is either a P2PKH or P2SH
                            op_code = deserializer._read_byte()
                            if op_code == OpCodes.OP_DUP:  # This is a P2PKH
                                op = deserializer._read_byte()
                                if op != OpCodes.OP_HASH160:
                                    raise Exception(
                                        "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                            OpCodes.OP_HASH160, op))
                                hash160 = deserializer._read_varbytes()
                                op = deserializer._read_byte()
                                if op != OpCodes.OP_EQUALVERIFY:
                                    raise Exception(
                                        "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                            OpCodes.OP_EQUALVERIFY, op))
                                op = deserializer._read_byte()
                                if op != OpCodes.OP_CHECKSIG:
                                    raise Exception(
                                        "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                            OpCodes.OP_CHECKSIG, op))
                                hashX = to_hashX(ScriptPubKey.P2PKH_script(hash160))
                            elif op_code == OpCodes.OP_HASH160:  # This is a P2SH
                                hash160 = deserializer._read_varbytes()
                                op = deserializer._read_byte()
                                if op != OpCodes.OP_EQUAL:
                                    raise Exception(
                                        "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                            OpCodes.OP_EQUAL, op))
                                hashX = to_hashX(ScriptPubKey.P2SH_script(hash160))
                            else:
                                raise Exception(
                                    "Unknown pk_script: {}\n OPCODE: {}".format(txout.pk_script.hex(), op_code))
                    except:
                        b = bytearray(tx_hash)
                        b.reverse()
                        logging.exception("TXID: {}, SCRIPT: {}".format(b.hex(), txout.pk_script.hex()))
                        raise

                    try:
                        # Parse any asset data
                        if len(deserializer.binary) != deserializer.cursor and \
                                deserializer._read_byte() == OpCodes.OP_RVN_ASSET:
                            asset_script = deserializer._read_varbytes()
                            asset_deserializer = self.coin.DESERIALIZER(asset_script)
                            op = deserializer._read_byte()
                            if op != OpCodes.OP_DROP:
                                raise Exception(
                                    "Unknown pk_script: {}\nExpected {}, was {}".format(txout.pk_script.hex(),
                                                                                        OpCodes.OP_DROP, op))
                            op = asset_deserializer._read_byte()
                            if op != b'r':
                                raise Exception(
                                    "Unknown asset script: {}\nExpected {}, was {}".format(asset_script.hex(),
                                                                                           b'r', op))
                            op = asset_deserializer._read_byte()
                            if op != b'v':
                                raise Exception(
                                    "Unknown asset script: {}\nExpected {}, was {}".format(asset_script.hex(),
                                                                                           b'v', op))
                            op = asset_deserializer._read_byte()
                            if op != b'n':
                                raise Exception(
                                    "Unknown asset script: {}\nExpected {}, was {}".format(asset_script.hex(),
                                                                                           b'n', op))
                            script_type = asset_deserializer._read_byte()
                            asset_name = asset_deserializer._read_varbytes()
                            if script_type == b'o':
                                # This is an ownership asset. It does not have any metadata.
                                # Just assign it with a value of 1
                                txout_tuple_list.append((hashX, 100_000_000, True, asset_name.decode('ascii')))
                            else:  # Not an owner asset; has a sat amount
                                sats = asset_deserializer._read_le_int64()
                                txout_tuple_list.append((hashX, sats, True, asset_name.decode('ascii')))
                        else:
                            txout_tuple_list.append((hashX, value, False, None))
                    except:
                        b = bytearray(tx_hash)
                        b.reverse()
                        logging.exception("TXID: {}, ASSET: {}".format(b.hex(), asset_script.hex()))
                        raise

                txout_pairs = tuple(txout_tuple_list)
                txs[tx_hash] = MemPoolTx(txin_pairs, None, txout_pairs,
                                         0, tx_size)
            return txs

        # Thread this potentially slow operation so as not to block
        tx_map = await run_in_thread(deserialize_txs)

        # Determine all prevouts not in the mempool, and fetch the
        # UTXO information from the database.  Failed prevout lookups
        # return None - concurrent database updates happen - which is
        # relied upon by _accept_transactions. Ignore prevouts that are
        # generation-like.
        prevouts = tuple(prevout for tx in tx_map.values()
                         for prevout in tx.prevouts
                         if prevout[0] not in all_hashes)

        utxos = []

        for hX, v in await self.api.lookup_utxos(prevouts):
            utxos.append((hX, v, False, None))

        for hX, v, name in await self.api.lookup_assets(prevouts):
            utxos.append((hX, v, True, name))

        utxo_map = {prevout: utxo for prevout, utxo in zip(prevouts, utxos)}

        return self._accept_transactions(tx_map, utxo_map, touched)

    #
    # External interface
    #

    async def keep_synchronized(self, synchronized_event):
        '''Keep the mempool synchronized with the daemon.'''
        async with TaskGroup() as group:
            await group.spawn(self._refresh_hashes(synchronized_event))
            await group.spawn(self._logging(synchronized_event))

            async for task in group:
                if not task.cancelled():
                    task.result()

    async def asset_balance_delta(self, hashX):
        ret = {}
        if hashX in self.hashXs:
            for hash_ in self.hashXs[hashX]:
                tx = self.txs[hash_]
                for hX, v, is_asset, name in tx.in_pairs:
                    if hX == hashX and is_asset:
                        if name not in ret:
                            ret[name] = -v
                        else:
                            ret[name] -= v
                for hX, v, is_asset, name in tx.out_pairs:
                    if hX == hashX and is_asset:
                        if name not in ret:
                            ret[name] = v
                        else:
                            ret[name] += v

        return ret

    async def balance_delta(self, hashX):
        '''Return the unconfirmed amount in the mempool for hashX.

        Can be positive or negative.
        '''
        value = 0
        if hashX in self.hashXs:
            for hash_ in self.hashXs[hashX]:
                tx = self.txs[hash_]
                value -= sum(v for h168, v, is_asset, _ in tx.in_pairs if h168 == hashX and not is_asset)
                value += sum(v for h168, v, is_asset, _ in tx.out_pairs if h168 == hashX and not is_asset)
        return value

    async def potential_spends(self, hashX):
        '''Return a set of (prev_hash, prev_idx) pairs from mempool
        transactions that touch hashX.

        None, some or all of these may be spends of the hashX, but all
        actual spends of it (in the DB or mempool) will be included.
        '''
        result = set()
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs[tx_hash]
            result.update(tx.prevouts)
        return result

    async def transaction_summaries(self, hashX):
        '''Return a list of MemPoolTxSummary objects for the hashX.'''
        result = []
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs[tx_hash]
            has_ui = any(hash in self.txs for hash, idx in tx.prevouts)
            result.append(MemPoolTxSummary(tx_hash, tx.fee, has_ui))
        return result

    async def unordered_UTXOs(self, hashX):
        '''Return an unordered list of UTXO named tuples from mempool
        transactions that pay to hashX.

        This does not consider if any other mempool transactions spend
        the outputs.
        '''
        utxos = []
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs.get(tx_hash)
            for pos, (hX, value, is_asset, _) in enumerate(tx.out_pairs):
                if hX == hashX and not is_asset:
                    utxos.append(UTXO(-1, pos, tx_hash, 0, value))
        return utxos

    async def unordered_ASSETs(self, hashX):
        assets = []
        for tx_hash in self.hashXs.get(hashX, ()):
            tx = self.txs.get(tx_hash)
            for pos, (hX, value, is_asset, name) in enumerate(tx.out_pairs):
                if hX == hashX and is_asset:
                    assets.append(ASSET(-1, pos, tx_hash, 0, name, value))
        return assets
