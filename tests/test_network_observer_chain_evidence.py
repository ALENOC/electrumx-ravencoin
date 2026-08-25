# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for SRV-03: the monitor's chain evidence must be the
real Ravencoin block hash (KAWPOW light verification for post-fork
headers), not a Bitcoin-style double-SHA256 of the first 80 header bytes,
and the checkpoint comparison in compare_chains() must actually be fed
real, independently-fetched checkpoint evidence instead of staying dead
code.
"""

import asyncio
import hashlib
import json


from electrumx.lib.coins import Ravencoin
from electrumx.lib.hash import hash_to_hex_str

from network_observer.classify import ChainObservation, compare_chains
from network_observer.crawl import PROBE_CALLS, _ravencoin_header_hash, probe_endpoint
from network_observer.model import EndpointId, Limits, Thresholds, Transport

# Real mainnet fixtures, shared with tests/lib/test_ravencoin_headers.py:
# the header at the incident checkpoint height and the first header after it.
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


def _bitcoin_style_pseudo_hash(header: bytes) -> str:
    """The old, wrong computation: double-SHA256 of the first 80 bytes."""
    return hashlib.sha256(hashlib.sha256(header[:80]).digest()).hexdigest()


def endpoint(host="electrum.example.org", port=50002, transport=Transport.TCP):
    return EndpointId(host, port, transport)


def test_helper_reproduces_the_real_pinned_checkpoint_hash():
    assert _ravencoin_header_hash(CHECKPOINT_HEADER.hex(), 4_487_775) == (
        Ravencoin.INCIDENT_CHECKPOINT_HASH)


def test_helper_disagrees_with_the_bitcoin_style_pseudo_hash():
    """The pseudo hash the monitor used to compute is not the real chain
    hash for a KAWPOW-era header: it cannot be cross-checked against Core
    or an explorer, which is exactly the SRV-03 defect."""
    assert _bitcoin_style_pseudo_hash(CHECKPOINT_HEADER) != (
        Ravencoin.INCIDENT_CHECKPOINT_HASH)


def test_malformed_or_short_header_yields_no_evidence():
    assert _ravencoin_header_hash("00" * 10, 4_487_775) is None
    assert _ravencoin_header_hash("zz", 4_487_775) is None


class _FakeWriter:
    def write(self, data):
        pass

    async def drain(self):
        pass

    def close(self):
        pass

    async def wait_closed(self):
        pass

    def get_extra_info(self, name):
        return None


def _connector_with(responses_by_method):
    async def connector(target, context):
        reader = asyncio.StreamReader()
        lines = []
        for index, (method, _params) in enumerate(PROBE_CALLS, start=1):
            if method in responses_by_method:
                lines.append(json.dumps(
                    {"id": index, "result": responses_by_method[method]}) + "\n")
        reader.feed_data("".join(lines).encode())
        reader.feed_eof()
        return reader, _FakeWriter()
    return connector


def test_probe_reports_canonical_kawpow_tip_and_checkpoint_hash():
    """End-to-end through probe_endpoint(): the tip hash advertised by an
    endpoint must be the real KAWPOW block hash of the header it returned,
    and independently-fetched checkpoint evidence must be populated -
    not left as the None that made compare_chains' comparison dead code.
    """
    connector = _connector_with({
        "server.version": ["ElectrumX-RVN 1.13.0.dev1", "1.4"],
        "server.features": {"genesis_hash": Ravencoin.GENESIS_HASH},
        "blockchain.headers.subscribe": {
            "height": 4_487_776, "hex": FIRST_POST_INCIDENT_HEADER.hex()},
        "server.ravencoin_backend": {},
        "server.peers.subscribe": [],
        "blockchain.block.header": CHECKPOINT_HEADER.hex(),
    })

    result = asyncio.run(probe_endpoint(
        endpoint("127.0.0.1"), connector=connector, allow_private=True))

    assert result.reachable
    assert result.height == 4_487_776
    assert result.tip_hash == hash_to_hex_str(
        Ravencoin.header_hash(FIRST_POST_INCIDENT_HEADER))
    # The old bug's output would not match; confirm the fix actually changed
    # what gets reported, not just that it runs.
    assert result.tip_hash != _bitcoin_style_pseudo_hash(FIRST_POST_INCIDENT_HEADER)
    assert result.checkpoint_hash == Ravencoin.INCIDENT_CHECKPOINT_HASH


def test_probe_yields_no_checkpoint_evidence_when_peer_omits_it():
    connector = _connector_with({
        "server.version": ["ElectrumX-RVN 1.13.0.dev1", "1.4"],
        "server.features": {},
        "blockchain.headers.subscribe": {
            "height": 4_487_776, "hex": FIRST_POST_INCIDENT_HEADER.hex()},
        "server.ravencoin_backend": {},
        "server.peers.subscribe": [],
        # blockchain.block.header intentionally omitted.
    })
    result = asyncio.run(probe_endpoint(
        endpoint("127.0.0.1"), connector=connector, allow_private=True))
    assert result.checkpoint_hash is None


# --------------------------------------------------------- compare_chains

def _observation(group, *, tip_hash, checkpoint_hash, height=4_500_000):
    return ChainObservation(
        endpoint=endpoint(f"{group.lower()}.example.org"),
        height=height, tip_hash=tip_hash, checkpoint_hash=checkpoint_hash,
        operator_group=group)


def test_same_canonical_checkpoint_agrees():
    observations = [
        _observation("OPERATOR-A", tip_hash="a" * 64,
                     checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH),
        _observation("OPERATOR-B", tip_hash="a" * 64,
                     checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH),
    ]
    verdict = compare_chains(observations)
    assert verdict.status == "VALID"


def test_different_checkpoint_is_at_least_suspected_conflict():
    observations = [
        _observation("OPERATOR-A", tip_hash="a" * 64,
                     checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH),
        _observation("OPERATOR-B", tip_hash="a" * 64, checkpoint_hash="f" * 64),
    ]
    verdict = compare_chains(observations)
    assert verdict.status in ("CONFLICT_SUSPECTED", "CHAIN_CONFLICT")
    assert "OPERATOR-B" in verdict.conflicting_groups


def test_different_checkpoint_confirmed_twice_is_a_hard_conflict():
    observations = [
        _observation("OPERATOR-A", tip_hash="a" * 64,
                     checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH),
        _observation("OPERATOR-B", tip_hash="a" * 64, checkpoint_hash="f" * 64),
    ]
    thresholds = Thresholds(conflict_confirmations=2)
    verdict = compare_chains(observations, thresholds=thresholds, confirmations=2)
    assert verdict.status == "CHAIN_CONFLICT"


def test_missing_checkpoint_evidence_alone_does_not_manufacture_a_conflict():
    """Absence of checkpoint evidence must not itself be treated as proof of
    anything, in either direction: it is not a conflict, and (separately,
    enforced by the promotion gate) it must never grant SAFE on its own."""
    observations = [
        _observation("OPERATOR-A", tip_hash="a" * 64, checkpoint_hash=None),
        _observation("OPERATOR-B", tip_hash="a" * 64, checkpoint_hash=None),
    ]
    verdict = compare_chains(observations)
    assert verdict.status == "VALID"


def test_forged_noncanonical_header_produces_a_mismatching_checkpoint_hash():
    """A peer returning a bit-flipped (forged) checkpoint header must not
    compute to the real checkpoint hash, so it disagrees with an honest
    peer instead of silently passing."""
    forged = bytearray(CHECKPOINT_HEADER)
    forged[4] ^= 1
    forged_hash = _ravencoin_header_hash(bytes(forged).hex(), 4_487_775)
    assert forged_hash != Ravencoin.INCIDENT_CHECKPOINT_HASH

    observations = [
        _observation("OPERATOR-A", tip_hash="a" * 64,
                     checkpoint_hash=Ravencoin.INCIDENT_CHECKPOINT_HASH),
        _observation("OPERATOR-B", tip_hash="a" * 64, checkpoint_hash=forged_hash),
    ]
    verdict = compare_chains(observations)
    assert verdict.status in ("CONFLICT_SUSPECTED", "CHAIN_CONFLICT")
