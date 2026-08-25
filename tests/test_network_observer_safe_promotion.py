# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for SRV-04: the monitor must only promote a certified
backend to SAFE on a clean chain comparison that is independently
corroborated, either by a second independent operator group or by a
trusted reference observation. A verdict of CONFLICT_SUSPECTED or
TEMPORARY_LAG must never promote, and neither may a single
self-consistent operator group, no matter how many hostnames or how high
a height it claims - a lone attacker is always internally consistent
with itself.

These run the real (async) run_discovery(), the actual code path that
had the bug, with a fake Crawler standing in for the network.
"""

import asyncio
import json

import pytest

from network_observer import cli
from network_observer.classify import ChainObservation
from network_observer.model import (
    AssetSupport, EndpointId, Limits, ProbeResult, Security, Thresholds, Transport,
)
from network_observer.store import Store

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
CERTIFIED_POLICY = {
    "releases": [{
        "repository": "2miners/Ravencoin", "tag": "v4.8.0", "version": "4.8.0",
        "commit": CERTIFIED_COMMIT, "status": "KNOWN_SAFE",
        "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "PASS"},
    }]
}


def _backend_payload():
    core = {
        "name": "Ravencoin Core", "version": "4.8.0", "versionNumber": 4_080_000,
        "subversion": "/Ravencoin:4.8.0/", "network": "main",
        "blocks": 4_494_000, "headers": 4_494_000, "initialBlockDownload": False,
        "identity": {"evidence": "BUILD_IDENTITY_VERIFIED",
                     "sourceRepository": "2miners/Ravencoin", "sourceCommit": CERTIFIED_COMMIT},
    }
    return {
        "server": "ElectrumX-RVN", "serverVersion": "ElectrumX-RVN 1.13.0.dev1",
        "backend": core,
        "compatibility": {
            "minimumSafeCore": "4.8.0", "safetyProfile": "rvn-consensus-2026-08-v1",
            "coreSafe": True, "networkMatches": True,
            "backendSynchronized": True, "kawpowHeightValidation": True,
            "checkpoint4487775": True,
        },
        "observedAt": 1786790000,
    }


class _FakeCrawler:
    """Stands in for monitor.crawl.Crawler: returns pre-built probe results
    instead of touching the network."""

    def __init__(self, results, *, limits=None, allow_private=False):
        self._results = results

    async def crawl(self, seeds):
        return dict(self._results), []


def _write_seeds(tmp_path, entries):
    """entries: list of (hostname, port, operatorGroup)."""
    seeds = {"seeds": [
        {"hostname": host, "sslPort": port, "operatorGroup": group}
        for host, port, group in entries
    ]}
    path = tmp_path / "seeds.json"
    path.write_text(json.dumps(seeds), encoding="utf-8")
    return path


def _probe_result(endpoint, *, height, tip_hash):
    return ProbeResult(
        endpoint=endpoint, reachable=True,
        server_version="ElectrumX-RVN 1.13.0.dev1",
        height=height, tip_hash=tip_hash, backend=_backend_payload(),
        asset_support=AssetSupport.CAPABLE,
    )


async def _run(monkeypatch, tmp_path, entries, *, reference=None):
    """entries: list of (hostname, port, operatorGroup, height, tip_hash)."""
    seed_entries = [(host, port, group) for host, port, group, _h, _t in entries]
    seeds_path = _write_seeds(tmp_path, seed_entries)
    registry_path = tmp_path / "registry.json"  # intentionally absent

    results = {}
    for host, port, _group, height, tip_hash in entries:
        endpoint = EndpointId(host, port, Transport.TLS)
        results[endpoint] = _probe_result(endpoint, height=height, tip_hash=tip_hash)

    monkeypatch.setattr(cli, "Crawler", lambda **kw: _FakeCrawler(results, **kw))

    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        await cli.run_discovery(
            store, seeds_path=seeds_path, registry_path=registry_path,
            policy=CERTIFIED_POLICY, limits=Limits(), thresholds=Thresholds(),
            reference=reference)
        return {state.endpoint.hostname: state.security for state in store.all_states()}
    finally:
        store.close()


def test_single_endpoint_alone_is_not_sufficient_for_safe(monkeypatch, tmp_path):
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
    ]))
    assert security["solo.example.org"] is not Security.SAFE


def test_two_endpoints_same_operator_are_not_independent(monkeypatch, tmp_path):
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
        ("two.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
    ]))
    assert security["one.example.org"] is not Security.SAFE
    assert security["two.example.org"] is not Security.SAFE


def test_two_independent_agreeing_endpoints_are_eligible_for_safe(monkeypatch, tmp_path):
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64),
    ]))
    assert security["one.example.org"] is Security.SAFE
    assert security["two.example.org"] is Security.SAFE


def test_two_independent_conflicting_endpoints_fail_closed(monkeypatch, tmp_path):
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "b" * 64),
    ]))
    assert security["one.example.org"] is not Security.SAFE
    assert security["two.example.org"] is not Security.SAFE


def test_conflict_suspected_does_not_promote(monkeypatch, tmp_path):
    """A single disagreement (one confirmation) is CONFLICT_SUSPECTED, not a
    confirmed CHAIN_CONFLICT - it must still not promote anyone to SAFE."""
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "b" * 64),
        ("three.example.org", 50002, "OPERATOR-C", 4_500_000, "a" * 64),
    ]))
    assert all(value is not Security.SAFE for value in security.values())


def test_highest_self_reported_height_alone_does_not_promote(monkeypatch, tmp_path):
    """An attacker claiming the highest height, alone, must not become a
    trust anchor that grants itself SAFE: it is still exactly one operator
    group with no independent corroboration."""
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("attacker.example.org", 50002, "ATTACKER", 99_999_999, "f" * 64),
    ]))
    assert security["attacker.example.org"] is not Security.SAFE


def test_many_hostnames_under_one_operator_do_not_manufacture_quorum(monkeypatch, tmp_path):
    """Many self-consistent hostnames under a single operator group are
    still one group: SRV-05's Sybil concern applied to the SRV-04 gate."""
    entries = [(f"sybil{i}.example.org", 50002, "ATTACKER", 4_500_000, "a" * 64)
              for i in range(10)]
    security = asyncio.run(_run(monkeypatch, tmp_path, entries))
    assert all(value is not Security.SAFE for value in security.values())


def test_reference_anchor_missing_is_conservative(monkeypatch, tmp_path):
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
    ], reference=None))
    assert security["solo.example.org"] is not Security.SAFE


def test_reference_anchor_present_allows_promotion_with_one_group(monkeypatch, tmp_path):
    reference = ChainObservation(
        endpoint=EndpointId("(operator reference)", 0, Transport.TCP),
        height=4_500_000, tip_hash="a" * 64, operator_group="TRUSTED-REFERENCE")
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
    ], reference=reference))
    assert security["solo.example.org"] is Security.SAFE


def test_unknown_operator_hostnames_do_not_manufacture_promotion_quorum(monkeypatch, tmp_path):
    """SRV-05 applied to the SRV-04 gate: endpoints with no known operator
    identity (operatorGroup absent from seeds/registry) fall back to a
    per-hostname UNKNOWN-<hostname> group each. Two agreeing unknown
    hostnames must not satisfy the two-independent-groups requirement,
    since minting more of them costs an attacker nothing."""
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("unknown1.example.org", 50002, None, 4_500_000, "a" * 64),
        ("unknown2.example.org", 50002, None, 4_500_000, "a" * 64),
    ]))
    assert all(value is not Security.SAFE for value in security.values())


def test_one_known_and_one_unknown_group_does_not_promote(monkeypatch, tmp_path):
    """A single attested operator plus an unrelated unknown hostname is
    still only one attested independent group."""
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("known.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
        ("unknown.example.org", 50002, None, 4_500_000, "a" * 64),
    ]))
    assert all(value is not Security.SAFE for value in security.values())


def test_reference_anchor_present_but_disagreeing_does_not_promote(monkeypatch, tmp_path):
    reference = ChainObservation(
        endpoint=EndpointId("(operator reference)", 0, Transport.TCP),
        height=4_500_000, tip_hash="c" * 64, operator_group="TRUSTED-REFERENCE")
    security = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64),
    ], reference=reference))
    assert security["solo.example.org"] is not Security.SAFE
