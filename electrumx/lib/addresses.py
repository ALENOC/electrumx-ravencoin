'''
This file is solely for functions dealing with converting
pubkeys into addresses solely for the purpose of converting
back into pubkeyhash scripts for db purposes.
'''

import hashlib

from .hash import sha256, double_sha256
from .util import base_encode


def hash_160(x: bytes) -> bytes:
    try:
        md = hashlib.new('ripemd160')
        md.update(sha256(x))
        return md.digest()
    except BaseException:
        from . import ripemd
        md = ripemd.new(sha256(x))
        return md.digest()

def hash160_to_b58_address(h160: bytes, addrtype: bytes) -> str:
    s = addrtype + h160
    s = s + double_sha256(s)[0:4]
    return base_encode(s, base=58)

def public_key_to_address(public_key: bytes, addrtype: bytes) -> str:
    return hash160_to_b58_address(hash_160(public_key), addrtype)
