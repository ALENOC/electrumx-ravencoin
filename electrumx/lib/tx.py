# Copyright (c) 2016-2017, Neil Booth
# Copyright (c) 2017, the ElectrumX authors
#
# All rights reserved.
#
# The MIT License (MIT)
#
# Permission is hereby granted, free of charge, to any person obtaining
# a copy of this software and associated documentation files (the
# "Software"), to deal in the Software without restriction, including
# without limitation the rights to use, copy, modify, merge, publish,
# distribute, sublicense, and/or sell copies of the Software, and to
# permit persons to whom the Software is furnished to do so, subject to
# the following conditions:
#
# The above copyright notice and this permission notice shall be
# included in all copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
# EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
# MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
# NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE
# LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
# and warranty status of this software.

'''Transaction-related classes and functions.'''

from collections import namedtuple

from electrumx.lib.hash import double_sha256, hash_to_hex_str
from electrumx.lib.util import (
    unpack_le_int32_from, unpack_le_int64_from, unpack_le_uint16_from,
    unpack_be_uint16_from,
    unpack_le_uint32_from, unpack_le_uint64_from, pack_le_int32, pack_varint,
    pack_le_uint32, pack_le_int64, pack_varbytes,
)

ZERO = bytes(32)
MINUS_1 = 4294967295


class Tx(namedtuple("Tx", "version inputs outputs locktime witness")):
    '''Class representing a transaction.'''

    def serialize(self):
        return b''.join((
            pack_le_int32(self.version),
            pack_varint(len(self.inputs)),
            b''.join(tx_in.serialize() for tx_in in self.inputs),
            pack_varint(len(self.outputs)),
            b''.join(tx_out.serialize() for tx_out in self.outputs),
            pack_le_uint32(self.locktime)
        ))


class TxInput(namedtuple("TxInput", "prev_hash prev_idx script sequence")):
    '''Class representing a transaction input.'''
    def __str__(self):
        script = self.script.hex()
        prev_hash = hash_to_hex_str(self.prev_hash)
        return ("Input({}, {:d}, script={}, sequence={:d})"
                .format(prev_hash, self.prev_idx, script, self.sequence))

    def is_generation(self):
        '''Test if an input is generation/coinbase like'''
        return self.prev_idx == MINUS_1 and self.prev_hash == ZERO

    def serialize(self):
        return b''.join((
            self.prev_hash,
            pack_le_uint32(self.prev_idx),
            pack_varbytes(self.script),
            pack_le_uint32(self.sequence),
        ))


class TxOutput(namedtuple("TxOutput", "value pk_script")):

    def serialize(self):
        return b''.join((
            pack_le_int64(self.value),
            pack_varbytes(self.pk_script),
        ))


class Deserializer:
    '''Deserializes transactions.

    This code is highly optimised and very performance sensitive.
    '''

    def __init__(self, buf, start=0):
        self.view = memoryview(buf)
        self.cursor = start

    def read_tx(self):
        '''Return a deserialized transaction.'''
        tx, self.cursor, hash = read_tx(self.view, self.cursor)
        return tx

    def read_tx_and_hash(self):
        '''Return a (deserialized TX, tx_hash) pair.

        The hash needs to be reversed for human display; for efficiency
        we process it in the natural serialized order.
        '''
        start = self.cursor

        tx, end, hash = read_tx(self.view, self.cursor)
        self.cursor = end
        return tx, hash if hash else double_sha256(self.view[start:end])

    def read_varint(self):
        value, self.cursor = read_varint(self.view, self.cursor)
        return value


def read_varint(buf, cursor):
    n = buf[cursor]
    cursor += 1
    if n < 253:
        return n, cursor
    if n == 253:
        return read_le_uint16(buf, cursor)
    if n == 254:
        return read_le_uint32(buf, cursor)
    return read_le_uint64(buf, cursor)


def read_varbytes(buf, cursor):
    size, cursor = read_varint(buf, cursor)
    end = cursor + size
    return buf[cursor: end], end


def read_le_uint16(buf, cursor):
    result, = unpack_le_uint16_from(buf, cursor)
    return result, cursor + 2


def read_le_uint32(buf, cursor):
    result, = unpack_le_uint32_from(buf, cursor)
    return result, cursor + 4


def read_le_uint64(buf, cursor):
    result, = unpack_le_uint64_from(buf, cursor)
    return result, cursor + 8


def read_le_int32(buf, cursor):
    result, = unpack_le_int32_from(buf, cursor)
    return result, cursor + 4


def read_le_int64(buf, cursor):
    result, = unpack_le_int64_from(buf, cursor)
    return result, cursor + 8


def read_input(buf, cursor):
    start = cursor
    cursor += 32
    prev_hash = buf[start: cursor]
    prev_idx, cursor = read_le_uint32(buf, cursor)
    script, cursor = read_varbytes(buf, cursor)
    sequence, cursor = read_le_uint32(buf, cursor)

    return TxInput(prev_hash, prev_idx, script, sequence), cursor


def read_output(buf, cursor):
    value, cursor = read_le_int64(buf, cursor)
    pk_script, cursor = read_varbytes(buf, cursor)
    return TxOutput(value, pk_script), cursor


def read_witness(buf, cursor, input_len):
    ret = []
    for _ in range(input_len):
        wit_for_in, cursor = read_varint(buf, cursor)
        app_val = []
        for _ in range(wit_for_in):
            data, cursor = read_varbytes(buf, cursor)
            app_val.append(data.hex())
        ret.append(app_val)
    return ret, cursor


def read_many(buf, cursor, reader):
    count, cursor = read_varint(buf, cursor)

    items = []
    append = items.append
    for _ in range(count):
        item, cursor = reader(buf, cursor)
        append(item)

    return items, cursor


def read_tx(buf, cursor):
    '''Deserialize a transaction from a buffer.  Return a (tx, cursor) pair.

    If the buffer does not hold the whole transaction, raises struct.error or IndexError.
    '''
    start = cursor
    version, cursor = read_le_int32(buf, cursor)

    original = bytes(buf[start:cursor])
    
    # Check if flag, if true, has witness info
    check_flag = buf[cursor:cursor+2].hex() == '0001'
    if check_flag:
        flag, cursor = read_le_uint16(buf, cursor)

    start = cursor

    inputs, cursor = read_many(buf, cursor, read_input)
    outputs, cursor = read_many(buf, cursor, read_output)

    original += bytes(buf[start:cursor])

    witness = None
    if check_flag:
        witness, cursor = read_witness(buf, cursor, len(inputs))

    start = cursor

    locktime, cursor = read_le_uint32(buf, cursor)

    original += bytes(buf[start:cursor])

    return Tx(version, inputs, outputs, locktime, witness), cursor, double_sha256(original) if check_flag else None
