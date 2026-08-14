import pytest

from electrumx.lib.coins import CoinError, Ravencoin, RavencoinTestnet
from electrumx.lib.hash import hash_to_hex_str


CHECKPOINT_HEADER = bytes.fromhex(
    "000000307a562b7789be7f55ef7e50dfd15469f925bc237f5425ebd643020200"
    "00000000f41fdf2269cc2f6871d6cd68e86f0e8f8903967abee6384dc588fa22"
    "62ccf8973dfd756af2ad051b5f7a44003313e49268e2b7b93f187ed01113c360"
    "6445e72c479a8c62a168bc40c22d48103e8cbb766d125d62"
)
FIRST_POST_INCIDENT_HEADER = bytes.fromhex(
    "00000030fd91a08603c4cf1eb462ec87162925c718e4bbdd766ee00945d60200"
    "00000000c131dc1af83d56f50b907b8cdfc68d1facd3c3346a6cecb59ef06722"
    "31c24335a6db796a35c5051b607a4400dc314c34000000320d004688560998f2"
    "d42f3030cda7a9fb19b8d5b044242d7275112c789b2fad7c"
)


def test_real_incident_checkpoint_header_matches():
    assert len(CHECKPOINT_HEADER) == Ravencoin.KAWPOW_HEADER_SIZE
    Ravencoin.validate_header(CHECKPOINT_HEADER, 4_487_775)
    assert hash_to_hex_str(Ravencoin.header_hash(CHECKPOINT_HEADER)) == (
        Ravencoin.INCIDENT_CHECKPOINT_HASH
    )


def test_real_post_incident_header_declares_actual_height():
    assert int.from_bytes(FIRST_POST_INCIDENT_HEADER[76:80], "little") == 4_487_776
    Ravencoin.validate_header(FIRST_POST_INCIDENT_HEADER, 4_487_776)


def test_forged_kawpow_declared_height_is_rejected():
    forged = bytearray(FIRST_POST_INCIDENT_HEADER)
    forged[76:80] = (1_219_736).to_bytes(4, "little")
    with pytest.raises(CoinError, match="declares nHeight=1219736"):
        Ravencoin.validate_header(bytes(forged), 4_487_776)


def test_checkpoint_mismatch_is_rejected(monkeypatch):
    monkeypatch.setattr(
        Ravencoin, "header_hash", classmethod(lambda cls, header: b"\x00" * 32)
    )
    with pytest.raises(CoinError, match="incident checkpoint mismatch"):
        Ravencoin.validate_header(CHECKPOINT_HEADER, 4_487_775)


def test_real_headers_have_valid_chain_ancestry():
    assert Ravencoin.header_prevhash(FIRST_POST_INCIDENT_HEADER) == (
        Ravencoin.header_hash(CHECKPOINT_HEADER)
    )
    broken = bytearray(FIRST_POST_INCIDENT_HEADER)
    broken[4] ^= 1
    assert Ravencoin.header_prevhash(bytes(broken)) != (
        Ravencoin.header_hash(CHECKPOINT_HEADER)
    )


def test_wrong_header_length_is_rejected():
    with pytest.raises(CoinError, match="119 bytes"):
        Ravencoin.validate_header(FIRST_POST_INCIDENT_HEADER[:-1], 4_487_776)


def test_mainnet_incident_checkpoint_is_not_applied_to_testnet():
    RavencoinTestnet.validate_header(bytes(120), 4_487_775)
