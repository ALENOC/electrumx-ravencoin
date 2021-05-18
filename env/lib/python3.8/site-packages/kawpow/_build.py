# kawpow: C/C++ implementation of Kawpow, the Ravencoin Proof of Work algorithm.
# Copyright 2019 Pawel Bylica.
# Licensed under the Apache License, Version 2.0.

# The CFFI build script for kawpow library.
# It expects the library is installed in the dist/ directory.
# The installation can be performed by
#
#     cmake . -DCMAKE_INSTALL_PREFIX=dist
#     make
#     make install

from cffi import FFI
import sys

ffibuilder = FFI()

stdlib = []
if sys.platform == 'linux':
    stdlib.append('stdc++')

ffibuilder.set_source(
    "_kawpow",
    r"""
    #include <kawpow/keccak.h>
    #include <kawpow/kawpow.h>
     """,
    include_dirs=['include'],
    libraries=['kawpow', 'keccak'] + stdlib,
)

ffibuilder.cdef("""

union kawpow_hash256
{
    ...;
    char str[32];
};

union kawpow_hash512
{
    ...;
    char str[64];
};

struct kawpow_result
{
    union kawpow_hash256 final_hash;
    union kawpow_hash256 mix_hash;
};


union kawpow_hash256 kawpow_keccak256(const uint8_t* data, size_t size);

union kawpow_hash512 kawpow_keccak512(const uint8_t* data, size_t size);

const struct kawpow_epoch_context* kawpow_get_global_epoch_context(int epoch_number);

struct kawpow_result kawpow_hash(const struct kawpow_epoch_context* context,
    const union kawpow_hash256* header_hash, uint64_t nonce);
    
union kawpow_hash256 light_verify_2(const union kawpow_hash256* header_hash, const union kawpow_hash256* mix_hash, uint64_t nonce);
    
bool kawpow_verify(const struct kawpow_epoch_context* context,
    const union kawpow_hash256* header_hash, const union kawpow_hash256* mix_hash, uint64_t nonce,
    const union kawpow_hash256* boundary);

""")

if __name__ == "__main__":
    ffibuilder.compile(verbose=True)
