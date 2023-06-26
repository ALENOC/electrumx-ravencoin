import os
from functools import partial
import hashlib

class Storage(object):
    '''Abstract base class of the DB backend abstraction.'''

    def __init__(self, name, for_sync):
        self.is_new = not os.path.exists(name)
        self.for_sync = for_sync or self.is_new
        self.open(name, create=self.is_new)

    @classmethod
    def import_module(cls):
        '''Import the DB engine module.'''
        raise NotImplementedError

    def open(self, name, create):
        '''Open an existing database or create a new one.'''
        raise NotImplementedError

    def close(self):
        '''Close an existing database.'''
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def put(self, key, value):
        raise NotImplementedError

    def write_batch(self):
        '''Return a context manager that provides `put` and `delete`.

        Changes should only be committed when the context manager
        closes without an exception.
        '''
        raise NotImplementedError

    def iterator(self, prefix=b'', reverse=False):
        '''Return an iterator that yields (key, value) pairs from the
        database sorted by key.

        If `prefix` is set, only keys starting with `prefix` will be
        included.  If `reverse` is True the items are returned in
        reverse order.
        '''
        raise NotImplementedError

# pylint:disable=W0223


class LevelDB(Storage):
    '''LevelDB database engine.'''

    @classmethod
    def import_module(cls):
        import plyvel    # pylint:disable=E0401
        cls.module = plyvel

    def open(self, name, create):
        mof = 512 if self.for_sync else 128
        # Use snappy compression (the default)
        self.db = self.module.DB(name, create_if_missing=create,
                                 max_open_files=mof)
        self.close = self.db.close
        self.get = self.db.get
        self.put = self.db.put
        self.iterator = self.db.iterator
        self.write_batch = partial(self.db.write_batch, transaction=True,
                                   sync=True)


def hash_160(x: bytes) -> bytes:
    try:
        md = hashlib.new('ripemd160')
        md.update(hashlib.sha256(x))
        return md.digest()
    except BaseException:
        from . import ripemd
        md = ripemd.new(hashlib.sha256(x))
        return md.digest()

__b58chars = b'123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz'
assert len(__b58chars) == 58


def base_encode(v: bytes, base: int) -> str:
    """ encode v, which is a string of bytes, to base58."""
    if base not in (58,):
        raise ValueError('not supported base: {}'.format(base))
    chars = __b58chars
    long_value = 0
    for (i, c) in enumerate(v[::-1]):
        long_value += (256 ** i) * c
    result = bytearray()
    while long_value >= base:
        div, mod = divmod(long_value, base)
        result.append(chars[mod])
        long_value = div
    result.append(chars[long_value])
    # Bitcoin does a little leading-zero-compression:
    # leading 0-bytes in the input become leading-1s
    nPad = 0
    for c in v:
        if c == 0x00:
            nPad += 1
        else:
            break
    result.extend([chars[0]] * nPad)
    result.reverse()
    return result.decode('ascii')

def hash160_to_b58_address(h160: bytes, addrtype: bytes) -> str:
    s = addrtype + h160
    s = s + hashlib.sha256(hashlib.sha256(s).digest()).digest()[0:4]
    return base_encode(s, base=58)

def main():
    LevelDB.import_module()
    os.chdir('/home/work/electrumx_db')

    #utxo_db = LevelDB('utxo', False)
    asset_db = LevelDB('asset', False)
    #asset_info_db = LevelDB('asset_info', False)

    script = bytes.fromhex('76a914dda3d21797ff26cb8ae9a769bdc68cf4567f5bba88ac')
    scripthash = hashlib.sha256(script).digest()
    #print(bytes(reversed(scripthash)).hex())
    #return

    prefix = b'1\x04$'
    #prefix += scripthash[:11]

    prefix = b't\x14\x03P\x8d\x13L\x10\x91HSk|\xbc1\x930D-_\xe4\xce\x04$RUN'

    for (key, value), _ in zip(asset_db.iterator(prefix=prefix), range(20)):
        print(f'{key=}')
        print(f'{value=}')

    h160 = b'\x8e8mS\xfc\xf5\x92\xd5\x84\xd1\xa7\xfc\x01\xf0\xae\x9a\x8f\x8cc\xef'
    #print(hash160_to_b58_address(h160, b'\x6f'))

if __name__ == '__main__':
    main()
