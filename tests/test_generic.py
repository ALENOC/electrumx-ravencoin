import os
from functools import partial
from hashlib import sha256

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


def main():
    LevelDB.import_module()
    os.chdir('/home/work/electrumx_db')

    #utxo_db = LevelDB('utxo', False)
    asset_db = LevelDB('asset', False)
    #asset_info_db = LevelDB('asset_info', False)

    script = bytes.fromhex('76a914dda3d21797ff26cb8ae9a769bdc68cf4567f5bba88ac')
    scripthash = sha256(script).digest()
    #print(bytes(reversed(scripthash)).hex())
    #return

    prefix = b'b'
    #prefix += scripthash[:11]

    for (key, value), _ in zip(asset_db.iterator(prefix=prefix), range(20)):
        print(f'{key=}')
        print(f'{value=}')

if __name__ == '__main__':
    main()
