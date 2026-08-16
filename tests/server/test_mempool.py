import datetime
import logging
import os
from collections import defaultdict
from functools import partial
from random import randrange, choice, seed
from types import SimpleNamespace

import pytest
from aiorpcx import Event, TaskGroup, sleep, ignore_after

from electrumx.lib.coins import Ravencoin
from electrumx.lib.hash import HASHX_LEN, hex_str_to_hash, hash_to_hex_str, double_sha256
from electrumx.lib.script import OpCodes, Script
from electrumx.lib.tx import Tx, TxInput, TxOutput
from electrumx.server.mempool import MemPool, MemPoolAPI, MemPoolTx

coin = Ravencoin
env = SimpleNamespace(coin=coin)
# Change seed daily
seed(datetime.date.today().toordinal())


def random_tx(hash160s, utxos):
    '''Create a random TX paying to some of the hash160s using some of the
    UTXOS.  Return the TX.  UTXOs is updated for the effects of the TX.
    '''
    inputs = []
    n_inputs = min(randrange(1, 4), len(utxos))
    input_value = 0
    # Create inputs spending random UTXOs.  total the inpu
    for n in range(n_inputs):
        prevout = choice(list(utxos))
        hashX, asset, value = utxos.pop(prevout)
        inputs.append(TxInput(prevout[0], prevout[1], b'', 4294967295))
        input_value += value

    # Seomtimes add a generation/coinbase like input that is present
    # in some coins
    if randrange(0, 10) == 0:
        inputs.append(TxInput(bytes(32), 4294967295, b'', 4294967295))

    fee = min(input_value, randrange(500))
    input_value -= fee
    outputs = []
    n_outputs = randrange(1, 4)
    for n in range(n_outputs):
        value = randrange(input_value + 1)
        input_value -= value
        pk_script = coin.hash160_to_P2PKH_script(choice(hash160s))
        outputs.append(TxOutput(value, pk_script))

    tx = Tx(2, inputs, outputs, 0, [])
    tx_bytes = tx.serialize()
    tx_hash = double_sha256(tx_bytes)
    for n, output in enumerate(tx.outputs):
        utxos[(tx_hash, n)] = (coin.hashX_from_script(output.pk_script),
                               None, output.value)
    return tx, tx_hash, tx_bytes


class API(MemPoolAPI):

    def __init__(self):
        self._height = 0
        self._cached_height = self._db_height = self._height
        # Create a pool of hash160s.  Map them to their script hashes
        # Create a bunch of UTXOs paying to those script hashes
        # Create a bunch of TXs that spend from the UTXO set and create
        # new outpus, which are added to the UTXO set for later TXs to
        # spend
        self.db_utxos = {}
        self.on_mempool_calls = []
        self.hashXs = []
        # Maps of mempool txs from tx_hash to raw and Tx object forms
        self.raw_txs = {}
        self.txs = {}
        self.ordered_adds = []

    def initialize(self, addr_count=100, db_utxo_count=100, mempool_size=50):
        hash160s = [os.urandom(20) for n in range(addr_count)]
        self.hashXs = [coin.hash160_to_P2PKH_hashX(hash160)
                       for hash160 in hash160s]
        prevouts = [(os.urandom(32), randrange(0, 10))
                    for n in range (db_utxo_count)]
        random_value = partial(randrange, coin.VALUE_PER_COIN * 10)
        self.db_utxos = {prevout: (choice(self.hashXs), None, random_value())
                         for prevout in prevouts}

        unspent_utxos = self.db_utxos.copy()
        for n in range(mempool_size):
            tx, tx_hash, raw_tx = random_tx(hash160s, unspent_utxos)
            self.raw_txs[tx_hash] = raw_tx
            self.txs[tx_hash] = tx
            self.ordered_adds.append(tx_hash)

    def mempool_utxos(self):
        utxos = {}
        for tx_hash, tx in self.txs.items():
            for n, output in enumerate(tx.outputs):
                hashX = coin.hashX_from_script(output.pk_script)
                utxos[(tx_hash, n)] = (hashX, None, output.value)
        return utxos

    def mempool_spends(self):
        return [(input.prev_hash, input.prev_idx)
                for tx in self.txs.values() for input in tx.inputs
                if not input.is_generation()]

    def balance_deltas(self):
        # Return mempool balance deltas indexed by hashX
        deltas = defaultdict(int)
        utxos = self.mempool_utxos()
        for tx_hash, tx in self.txs.items():
            for n, input in enumerate(tx.inputs):
                if input.is_generation():
                    continue
                prevout = (input.prev_hash, input.prev_idx)
                if prevout in utxos:
                    utxos.pop(prevout)
                else:
                    hashX, asset, value = self.db_utxos[prevout]
                    deltas[hashX] -= value
        for hashX, asset, value in utxos.values():
            deltas[hashX] += value
        return deltas

    def spends(self):
        # Return spends indexed by hashX
        spends = defaultdict(list)
        utxos = self.mempool_utxos()
        for tx_hash, tx in self.txs.items():
            for n, input in enumerate(tx.inputs):
                if input.is_generation():
                    continue
                prevout = (input.prev_hash, input.prev_idx)
                if prevout in utxos:
                    hashX, asset, value = utxos.pop(prevout)
                else:
                    hashX, asset, value = self.db_utxos[prevout]
                spends[hashX].append(prevout)
        return spends

    def summaries(self):
        # Return lists of (tx_hash, fee, has_unconfirmed_inputs) by hashX
        summaries = defaultdict(list)
        utxos = self.mempool_utxos()
        for tx_hash, tx in self.txs.items():
            fee = 0
            hashXs = set()
            has_ui = False
            for n, input in enumerate(tx.inputs):
                if input.is_generation():
                    continue
                has_ui = has_ui or (input.prev_hash in self.txs)
                prevout = (input.prev_hash, input.prev_idx)
                if prevout in utxos:
                    hashX, asset, value = utxos[prevout]
                else:
                    hashX, asset, value = self.db_utxos[prevout]
                hashXs.add(hashX)
                fee += value

            for output in tx.outputs:
                hashXs.add(coin.hashX_from_script(output.pk_script))
                fee -= output.value

            summary = (tx_hash, fee, has_ui)
            for hashX in hashXs:
                summaries[hashX].append(summary)
        return summaries

    def touched(self, tx_hashes):
        touched = set()
        utxos = self.mempool_utxos()
        for tx_hash in tx_hashes:
            tx = self.txs[tx_hash]
            for n, input in enumerate(tx.inputs):
                if input.is_generation():
                    continue
                prevout = (input.prev_hash, input.prev_idx)
                if prevout in utxos:
                    hashX, asset, value = utxos[prevout]
                else:
                    hashX, asset, value = self.db_utxos[prevout]
                touched.add(hashX)

            for output in tx.outputs:
                touched.add(coin.hashX_from_script(output.pk_script))
        return touched

    def UTXOs(self):
        # Return lists of UTXO 6-tuples by hashX
        utxos = defaultdict(list)
        for tx_hash, tx in self.txs.items():
            for n, output in enumerate(tx.outputs):
                hashX = coin.hashX_from_script(output.pk_script)
                utxos[hashX].append((-1, n, tx_hash, 0, None, output.value))
        return utxos

    async def height(self):
        await sleep(0)
        self._cached_height = self._height
        return self._height

    def db_height(self):
        return self._db_height

    def cached_height(self):
        return self._cached_height

    async def mempool_hashes(self):
        '''Query bitcoind for the hashes of all transactions in its
        mempool, returned as a list.'''
        await sleep(0)
        return [hash_to_hex_str(hash) for hash in self.txs]

    async def raw_transactions(self, hex_hashes):
        '''Query bitcoind for the serialized raw transactions with the given
        hashes.  Missing transactions are returned as None.

        hex_hashes is an iterable of hexadecimal hash strings.'''
        await sleep(0)
        hashes = [hex_str_to_hash(hex_hash) for hex_hash in hex_hashes]
        return [self.raw_txs.get(hash) for hash in hashes]

    async def lookup_utxos(self, prevouts):
        '''Return (hashX, asset, value) for each unspent prevout,
        otherwise return None if spent or not found.

        prevouts - an iterable of (hash, index) pairs
        '''
        await sleep(0)
        return [self.db_utxos.get(prevout) for prevout in prevouts]

    async def on_mempool(self, touched, height, assets, *_asset_changes):
        '''Called each time the mempool is synchronized.  touched is a set of
        hashXs touched since the previous call.  height is the
        daemon's height at the time the mempool was obtained.'''
        self.on_mempool_calls.append((touched, height, assets))
        await sleep(0)


class DropAPI(API):

    def __init__(self, drop_count):
        super().__init__()
        self.drop_count = drop_count
        self.dropped = False

    async def raw_transactions(self, hex_hashes):
        if not self.dropped:
            self.dropped = True
            for hash in self.ordered_adds[-self.drop_count:]:
                del self.raw_txs[hash]
                del self.txs[hash]
        return await super().raw_transactions(hex_hashes)


def in_caplog(caplog, message):
    return any(message in record.message for record in caplog.records)


def _transfer_asset_script(hash160, asset_name, value=1):
    # A standard P2PKH prefix followed by an OP_RVN_ASSET transfer payload,
    # matching what mempool.py's deserialize_txs() (elif 0 < op_ptr:) parses:
    # a 3-byte marker, a 1-byte type ('t'), a var_bytes asset name, and an
    # 8-byte LE value.
    payload = b'rvn' + b't' + bytes([len(asset_name)]) + asset_name.encode('ascii') \
        + value.to_bytes(8, 'little')
    return (coin.hash160_to_P2PKH_script(hash160) + bytes([OpCodes.OP_RVN_ASSET])
            + Script.push_data(payload))



def _null_verifier_script(verifier_string):
    # ASSET_NULL_VERIFIER_TEMPLATE: OP_RVN_ASSET OP_RESERVED <push>, with no
    # P2PKH prefix (op_ptr == 0), matching deserialize_txs()'s op_ptr == 0
    # branch's ASSET_NULL_VERIFIER_TEMPLATE case.
    payload = bytes([len(verifier_string)]) + verifier_string.encode('ascii')
    return bytes([OpCodes.OP_RVN_ASSET, OpCodes.OP_RESERVED]) + Script.push_data(payload)


def _plain_tx(hash160s):
    tx = Tx(2, [TxInput(bytes(32), 4294967295, b'', 4294967295)],
             [TxOutput(1000, coin.hash160_to_P2PKH_script(hash160s[0]))], 0, [])
    raw = tx.serialize()
    return double_sha256(raw), raw


async def _fetch_and_accept_in_order(mempool, hashes):
    # Calls the lower-level per-batch fetch directly with an explicit,
    # ordered `hashes` tuple, bypassing MemPool.keep_synchronized()'s
    # set-based hash collection entirely (see RA-1 in
    # test_verifier_state_does_not_leak_across_transactions for why that
    # matters). `hashes` is iterated in this exact order by
    # deserialize_txs()'s `for tx_hash, raw_tx in zip(hashes, raw_txs):`.
    all_hashes = frozenset(hashes)
    return await mempool._fetch_and_accept(
        tuple(hashes), all_hashes,
        set(), set(), set(), set(), set(), set(), set(), set(),
    )


@pytest.mark.asyncio
async def test_verifier_state_does_not_leak_across_transactions():
    # RVN-03: verifier_string/restricted_asset were declared once, outside
    # the per-tx loop in deserialize_txs(). A tx that set them left the
    # values in place for whatever tx was processed after it, even if that
    # next tx had nothing to do with a restricted asset at all.
    #
    # RA-1 design note: an earlier version of this test drove the scenario
    # through MemPool.keep_synchronized(), whose hash-collection pipeline is
    # set-based (`hashes = set(...)` in _refresh_hashes,
    # `new_hashes = list(all_hashes.difference(txs))` in _process_mempool).
    # Set iteration order for bytes depends on both the interpreter's hash
    # seed and the actual byte content, and both were effectively
    # randomized here (os.urandom -> unpredictable tx hash), so which
    # transaction got parsed first -- and therefore whether the leak's
    # precondition (the restricted tx parsed BEFORE the unrelated one) was
    # even exercised -- was not deterministic. Reproduced empirically: with
    # the RVN-03 fix reverted, the old test failed only 4-6 times out of 10
    # repeated runs, under the default randomized hash seed and under
    # several fixed PYTHONHASHSEED values alike (a fixed seed alone does
    # not fix it, since the hashed byte content differs every run too).
    #
    # This version calls the lower-level MemPool._fetch_and_accept directly
    # with an explicit, ordered `hashes` tuple (see
    # _fetch_and_accept_in_order above), bypassing set-based ordering
    # entirely, and exercises both orderings explicitly instead of leaving
    # the ordering to chance.
    api = API()
    hash160 = os.urandom(20)

    restricted_script = _transfer_asset_script(hash160, '$RESTRICTED')
    verifier_script = _null_verifier_script('SOMEQUALIFIER')
    tx1 = Tx(2, [TxInput(bytes(32), 4294967295, b'', 4294967295)],
             [TxOutput(0, restricted_script), TxOutput(0, verifier_script)], 0, [])
    raw1 = tx1.serialize()
    tx1_hash = double_sha256(raw1)

    tx2_hash, raw2 = _plain_tx([hash160])

    api.raw_txs = {tx1_hash: raw1, tx2_hash: raw2}

    # Ordering A: the restricted/verifier tx is parsed BEFORE the unrelated
    # tx. This is the ordering that actually exercises the leak.
    mempool_a = MemPool(env, api)
    await _fetch_and_accept_in_order(mempool_a, (tx1_hash, tx2_hash))
    assert tx1_hash in mempool_a.verifiers.get('$RESTRICTED', {})
    assert tx2_hash not in mempool_a.verifiers.get('$RESTRICTED', {}), (
        'a transaction with no restricted asset or verifier output must '
        'not inherit a prior transaction\'s parsing state')
    assert mempool_a.txs[tx2_hash].out_pairs[0][2] is None

    # Ordering B: the reverse. The unrelated tx is parsed first (nothing to
    # leak into it yet), then the restricted/verifier tx. Confirms the
    # reset happens on every iteration, not just once, and that the second
    # tx still correctly picks up its own state after a prior unrelated tx.
    mempool_b = MemPool(env, api)
    await _fetch_and_accept_in_order(mempool_b, (tx2_hash, tx1_hash))
    assert tx1_hash in mempool_b.verifiers.get('$RESTRICTED', {})
    assert tx2_hash not in mempool_b.verifiers.get('$RESTRICTED', {})
    assert mempool_b.txs[tx2_hash].out_pairs[0][2] is None


@pytest.mark.asyncio
async def test_malformed_null_template_output_falls_back_not_fatal():
    # RVN-05 (mempool side, op_ptr == 0 branch): a malformed
    # qualifier/verifier/freeze null-template output must fall back to a
    # standard hashX for that output, symmetric to the op_ptr > 0 branch's
    # existing fallback, rather than raising out of deserialize_txs().
    api = API()
    hash160 = os.urandom(20)
    # ASSET_NULL_TEMPLATE shape (OP_RVN_ASSET, <20-byte push>, <push>) but
    # with an empty asset-portion push, which DataParser cannot read a
    # length-prefixed name from.
    malformed_script = (bytes([OpCodes.OP_RVN_ASSET]) + Script.push_data(hash160)
                         + Script.push_data(b''))
    tx = Tx(2, [TxInput(bytes(32), 4294967295, b'', 4294967295)],
             [TxOutput(1000, malformed_script)], 0, [])
    raw = tx.serialize()
    tx_hash = double_sha256(raw)

    api.raw_txs = {tx_hash: raw}
    api.txs = {tx_hash: object()}
    api.ordered_adds = [tx_hash]

    mempool = MemPool(env, api)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        await group.cancel_remaining()

    assert tx_hash in mempool.txs
    assert mempool.txs[tx_hash].out_pairs[0][2] is None


@pytest.mark.asyncio
async def test_malformed_mempool_tx_is_dropped_not_fatal(caplog):
    # RVN-05 (mempool side): a single malformed mempool transaction must
    # not kill keep_synchronized for every session. The well-formed tx in
    # the same batch must still be accepted.
    api = API()
    hash160 = os.urandom(20)
    good_hash, good_raw = _plain_tx([hash160])
    bad_hash = os.urandom(32)
    bad_raw = b'\xff' * 10  # not a parseable transaction

    api.raw_txs = {good_hash: good_raw, bad_hash: bad_raw}
    api.txs = {good_hash: object(), bad_hash: object()}
    api.ordered_adds = [good_hash, bad_hash]

    mempool = MemPool(env, api)
    event = Event()
    with caplog.at_level(logging.WARNING):
        async with TaskGroup() as group:
            await group.spawn(mempool.keep_synchronized, event)
            await event.wait()
            await group.cancel_remaining()

    assert good_hash in mempool.txs
    assert bad_hash not in mempool.txs
    assert in_caplog(caplog, 'failed to parse mempool tx')


@pytest.mark.asyncio
async def test_keep_synchronized(caplog):
    api = API()
    mempool = MemPool(env, api)
    event = Event()
    with caplog.at_level(logging.INFO):
        async with TaskGroup() as group:
            await group.spawn(mempool.keep_synchronized, event)
            await event.wait()
            await group.cancel_remaining()

    assert in_caplog(caplog, 'beginning processing of daemon mempool')
    assert in_caplog(caplog, 'synced in ')
    assert in_caplog(caplog, '0 txs')
    assert in_caplog(caplog, 'touching 0 addresses')
    assert not in_caplog(caplog, 'txs dropped')


@pytest.mark.asyncio
async def test_balance_delta():
    api = API()
    api.initialize()
    mempool = MemPool(env, api)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        await group.cancel_remaining()

    # Check the default dict is handled properly
    prior_len = len(mempool.hashXs)
    assert await mempool.balance_delta(os.urandom(HASHX_LEN), None) == {}
    assert prior_len == len(mempool.hashXs)

    # Test all hashXs
    deltas = api.balance_deltas()
    for hashX in api.hashXs:
        expected = deltas.get(hashX, 0)
        result = await mempool.balance_delta(hashX, None)
        assert result.get(None, 0) == expected


@pytest.mark.asyncio
async def test_balance_delta_does_not_leak_across_assets():
    # RVN-02: is_asset_valid() closed over the `asset` parameter of
    # balance_delta(), but the per-pair loop used the same name
    # (`for h168, v, asset in tx.in_pairs/out_pairs`), rebinding it on
    # every iteration. Once a tx carried more than one asset, every
    # filter check after the first pair used whichever asset the loop
    # last saw instead of the caller's actual filter -- so a client
    # asking for one asset's unconfirmed balance could see another
    # asset's amount folded in, or its own amount dropped.
    api = API()
    mempool = MemPool(env, api)
    hashX = os.urandom(HASHX_LEN)
    tx_hash = os.urandom(32)
    # A single tx with three outputs to the same address: native RVN,
    # then two different assets. The native-RVN pair is iterated first
    # so a shadowing bug corrupts every check after it.
    tx = MemPoolTx(
        prevouts=(),
        in_pairs=(),
        out_pairs=(
            (hashX, 1000, None),
            (hashX, 500, 'FOO'),
            (hashX, 700, 'BAR'),
        ),
        fee=0, size=200)
    mempool.txs[tx_hash] = tx
    mempool.hashXs[hashX].add(tx_hash)

    assert dict(await mempool.balance_delta(hashX, 'FOO')) == {'FOO': 500}
    assert dict(await mempool.balance_delta(hashX, 'BAR')) == {'BAR': 700}
    assert dict(await mempool.balance_delta(hashX, False)) == {None: 1000}
    # A filter list must isolate FOO from BAR too.
    assert dict(await mempool.balance_delta(hashX, ['FOO'])) == {'FOO': 500}


@pytest.mark.asyncio
async def test_potential_spends():
    api = API()
    api.initialize()
    mempool = MemPool(env, api)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        await group.cancel_remaining()

    # Check the default dict is handled properly
    prior_len = len(mempool.hashXs)
    assert await mempool.potential_spends(os.urandom(HASHX_LEN)) == set()
    assert prior_len == len(mempool.hashXs)

    # Test all hashXs
    spends = api.spends()
    for hashX in api.hashXs:
        ps = await mempool.potential_spends(hashX)
        assert all(spend in ps for spend in spends[hashX])


async def _test_summaries(mempool, api):
    # Test all hashXs
    summaries = api.summaries()
    for hashX in api.hashXs:
        mempool_result = await mempool.transaction_summaries(hashX)
        mempool_result = [(item.hash, item.fee, item.has_unconfirmed_inputs)
                          for item in mempool_result]
        our_result = summaries.get(hashX, [])
        assert set(our_result) == set(mempool_result)


@pytest.mark.asyncio
async def test_transaction_summaries(caplog):
    api = API()
    api.initialize()
    mempool = MemPool(env, api)
    event = Event()
    with caplog.at_level(logging.INFO):
        async with TaskGroup() as group:
            await group.spawn(mempool.keep_synchronized, event)
            await event.wait()
            await group.cancel_remaining()

    # Check the default dict is handled properly
    prior_len = len(mempool.hashXs)
    assert await mempool.transaction_summaries(os.urandom(HASHX_LEN)) == []
    assert prior_len == len(mempool.hashXs)

    await _test_summaries(mempool, api)
    assert not in_caplog(caplog, 'txs dropped')


@pytest.mark.asyncio
async def test_unordered_UTXOs():
    api = API()
    api.initialize()
    mempool = MemPool(env, api)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        await group.cancel_remaining()

    # Check the default dict is handled properly
    prior_len = len(mempool.hashXs)
    assert await mempool.unordered_UTXOs(os.urandom(HASHX_LEN), None) == []
    assert prior_len == len(mempool.hashXs)

    # Test all hashXs
    utxos = api.UTXOs()
    for hashX in api.hashXs:
        mempool_result = await mempool.unordered_UTXOs(hashX, None)
        our_result = utxos.get(hashX, [])
        assert set(our_result) == set(mempool_result)


@pytest.mark.asyncio
async def test_unordered_UTXOs_scalar_asset_filter_is_exact_and_safe():
    # RVN-02 sibling defect: is_asset_valid() treated a scalar string
    # filter as Iterable (true for str) and did substring/membership
    # matching (`db_asset in asset`) instead of equality, crashing with
    # TypeError as soon as a native-RVN (None) pair was present. This is
    # in the direct call path of RVN-01's hashX_listunspent.
    api = API()
    mempool = MemPool(env, api)
    hashX = os.urandom(HASHX_LEN)
    tx_hash = os.urandom(32)
    tx = MemPoolTx(
        prevouts=(),
        in_pairs=(),
        out_pairs=(
            (hashX, 1000, None),
            (hashX, 500, 'FOO'),
            (hashX, 700, 'FO'),
        ),
        fee=0, size=200)
    mempool.txs[tx_hash] = tx
    mempool.hashXs[hashX].add(tx_hash)

    result = await mempool.unordered_UTXOs(hashX, 'FOO')
    assert [u.name for u in result] == ['FOO']


@pytest.mark.asyncio
async def test_mempool_removals():
    api = API()
    api.initialize()
    mempool = MemPool(env, api, refresh_secs=0.01)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        # Remove half the TXs from the mempool
        start = len(api.ordered_adds) // 2
        for tx_hash in api.ordered_adds[start:]:
            del api.txs[tx_hash]
            del api.raw_txs[tx_hash]
        await event.wait()
        await _test_summaries(mempool, api)
        # Removed hashXs should have key destroyed
        assert all(mempool.hashXs.values())
        # Remove the rest
        api.txs.clear()
        api.raw_txs.clear()
        await event.wait()
        await _test_summaries(mempool, api)
        assert not mempool.hashXs
        assert not mempool.txs
        await group.cancel_remaining()


@pytest.mark.asyncio
async def test_daemon_drops_txs():
    # Tests things work if the daemon drops some transactions between
    # returning their hashes and the mempool requesting the raw txs
    api = DropAPI(10)
    api.initialize()
    mempool = MemPool(env, api, refresh_secs=0.01)
    event = Event()
    async with TaskGroup() as group:
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        await _test_summaries(mempool, api)
        await group.cancel_remaining()


@pytest.mark.asyncio
async def test_notifications(caplog):
    # Tests notifications over a cycle of:
    # 1) A first batch of txs come in
    # 2) A second batch of txs come in
    # 3) A block comes in confirming the first batch only
    api = API()
    api.initialize()
    mempool = MemPool(env, api, refresh_secs=0.001, log_status_secs=0)
    event = Event()

    n = len(api.ordered_adds) // 2
    raw_txs = api.raw_txs.copy()
    txs = api.txs.copy()
    first_hashes = api.ordered_adds[:n]
    first_touched = api.touched(first_hashes)
    second_hashes = api.ordered_adds[n:]
    second_touched = api.touched(second_hashes)

    caplog.set_level(logging.DEBUG)

    async with TaskGroup() as group:
        # First batch enters the mempool
        api.raw_txs = {hash: raw_txs[hash] for hash in first_hashes}
        api.txs = {hash: txs[hash] for hash in first_hashes}
        first_utxos = api.mempool_utxos()
        first_spends = api.mempool_spends()
        await group.spawn(mempool.keep_synchronized, event)
        await event.wait()
        assert len(api.on_mempool_calls) == 1
        touched, height, assets = api.on_mempool_calls[0]
        assert height == api._height == api._db_height == api._cached_height
        assert touched == first_touched
        # Second batch enters the mempool
        api.raw_txs = raw_txs
        api.txs = txs
        await event.wait()
        assert len(api.on_mempool_calls) == 2
        touched, height, assets = api.on_mempool_calls[1]
        assert height == api._height == api._db_height == api._cached_height
        # Touched is incremental
        assert touched == second_touched
        # Block found; first half confirm
        new_height = 2
        api._height = new_height
        api.raw_txs = {hash: raw_txs[hash] for hash in second_hashes}
        api.txs = {hash: txs[hash] for hash in second_hashes}
        # Delay the DB update
        assert not in_caplog(caplog, 'waiting for DB to sync')
        async with ignore_after(max(mempool.refresh_secs * 2, 0.5)):
            await event.wait()
        assert in_caplog(caplog, 'waiting for DB to sync')
        assert len(api.on_mempool_calls) == 2
        assert not event.is_set()
        assert api._height == api._cached_height == new_height
        assert touched == second_touched
        # Now update the DB
        api.db_utxos.update(first_utxos)
        api._db_height = new_height
        for spend in first_spends:
            del api.db_utxos[spend]
        await event.wait()
        assert len(api.on_mempool_calls) == 3
        touched, height, assets = api.on_mempool_calls[2]
        assert height == api._db_height == new_height
        assert touched == first_touched
        await group.cancel_remaining()


@pytest.mark.asyncio
async def test_dropped_txs(caplog):
    api = API()
    api.initialize()
    mempool = MemPool(env, api)
    event = Event()
    # Remove a single TX_HASH that is used in another mempool tx
    for prev_hash, prev_idx in api.mempool_spends():
        if prev_hash in api.txs:
            del api.txs[prev_hash]

    with caplog.at_level(logging.INFO):
        async with TaskGroup() as group:
            await group.spawn(mempool.keep_synchronized, event)
            await event.wait()
            await group.cancel_remaining()

    assert in_caplog(caplog, 'txs dropped')
