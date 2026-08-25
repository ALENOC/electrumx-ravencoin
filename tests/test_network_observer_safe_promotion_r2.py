# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for the second remediation round (R-01, R-02, R-03):
the SRV-04 promotion gate's own new machinery (the --reference anchor and
the corroboration count) must not itself become a bypass.

R-01: a --reference with no comparable evidence must not let an endpoint
whose height was never actually compared to anything ride the reference
alone to SAFE.

R-02: two independently corroborating, attested operator groups must not
let a third endpoint at an uncompared height ("a rider") ride their
corroboration to SAFE -- even when that rider honestly reports the real,
public checkpoint hash, since that only proves it knows old history, not
that its claimed new tip is real.

R-03: a chain conflict that a second, independent crawl still observes
must actually reach CHAIN_CONFLICT and demote the offending group, and
recovery from a confirmed conflict must require a positively verified
clean comparison, not merely one crawl where the offender went quiet.

These all run the real (async) run_discovery(), the code path that had
the bugs, with a fake Crawler standing in for the network -- the same
pattern as tests/test_network_observer_safe_promotion.py.
"""

import asyncio
import json

from electrumx.lib.coins import Ravencoin

from network_observer import cli
from network_observer.classify import ChainObservation
from network_observer.model import (
    AssetSupport, Availability, EndpointId, Limits, ProbeResult, Security, Thresholds,
    Transport,
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

CANONICAL_CHECKPOINT = Ravencoin.INCIDENT_CHECKPOINT_HASH


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


def _probe_result(endpoint, *, height, tip_hash, checkpoint_hash=None, genesis_hash=None,
                  reachable=True):
    if not reachable:
        return ProbeResult(endpoint=endpoint, reachable=False, error="connection refused")
    return ProbeResult(
        endpoint=endpoint, reachable=True,
        server_version="ElectrumX-RVN 1.13.0.dev1",
        height=height, tip_hash=tip_hash, checkpoint_hash=checkpoint_hash,
        genesis_hash=genesis_hash,
        backend=_backend_payload(),
        asset_support=AssetSupport.CAPABLE,
    )


def _reference(height, tip, *, checkpoint_hash=None, genesis_hash=None):
    return ChainObservation(
        endpoint=EndpointId("(operator reference)", 0, Transport.TCP),
        height=height, tip_hash=tip, checkpoint_hash=checkpoint_hash,
        genesis_hash=genesis_hash, operator_group="TRUSTED-REFERENCE")


async def _run(monkeypatch, tmp_path, entries, *, reference=None, store=None,
               db_name="network-observer.sqlite3"):
    """entries: list of (hostname, port, operatorGroup, height, tip_hash[, checkpoint_hash])."""
    seed_entries = [(e[0], e[1], e[2]) for e in entries]
    seeds_path = _write_seeds(tmp_path, seed_entries)
    registry_path = tmp_path / "registry.json"

    results = {}
    for entry in entries:
        host, port, height, tip_hash = entry[0], entry[1], entry[3], entry[4]
        checkpoint_hash = entry[5] if len(entry) > 5 else None
        endpoint = EndpointId(host, port, Transport.TLS)
        if tip_hash is None and height is None:
            results[endpoint] = _probe_result(endpoint, height=None, tip_hash=None,
                                              reachable=False)
        else:
            results[endpoint] = _probe_result(
                endpoint, height=height, tip_hash=tip_hash, checkpoint_hash=checkpoint_hash)

    monkeypatch.setattr(cli, "Crawler", lambda **kw: _FakeCrawler(results, **kw))

    owns_store = store is None
    store = store or Store(str(tmp_path / db_name))
    try:
        summary = await cli.run_discovery(
            store, seeds_path=seeds_path, registry_path=registry_path,
            policy=CERTIFIED_POLICY, limits=Limits(), thresholds=Thresholds(),
            reference=reference)
        security = {state.endpoint.hostname: state.security for state in store.all_states()}
        availability = {state.endpoint.hostname: state.availability
                        for state in store.all_states()}
        return summary, security, availability
    finally:
        if owns_store:
            store.close()


REF_HEIGHT, REF_TIP = 4_500_000, "a" * 64


# --------------------------------------------------------------------- R-01


def test_r01_attacker_ahead_of_reference_alone_is_not_safe(monkeypatch, tmp_path):
    """A --reference with only a height/tip is not comparable evidence for
    an endpoint that claims to be ahead of it: nothing was ever compared."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("attacker.example.org", 50002, "ATTACKER", REF_HEIGHT + 500, "f" * 64),
    ], reference=_reference(REF_HEIGHT, REF_TIP)))
    assert security["attacker.example.org"] is not Security.SAFE


def test_r01_ahead_attacker_not_promoted_by_honest_reference_agreement(monkeypatch, tmp_path):
    """An honest endpoint that genuinely agrees with the reference must not
    smuggle a second, unrelated, ahead-of-reference endpoint to SAFE."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("honest.example.org", 50002, "HONEST-OP", REF_HEIGHT, REF_TIP),
        ("attacker.example.org", 50002, "ATTACKER", REF_HEIGHT + 3, "f" * 64),
    ], reference=_reference(REF_HEIGHT, REF_TIP)))
    assert security["honest.example.org"] is Security.SAFE
    assert security["attacker.example.org"] is not Security.SAFE


def test_r01_fake_checkpoint_against_reference_is_a_conflict(monkeypatch, tmp_path):
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("attacker.example.org", 50002, "ATTACKER", REF_HEIGHT + 1, "f" * 64,
         "deadbeef" + "0" * 56),
    ], reference=_reference(REF_HEIGHT, REF_TIP)))
    assert security["attacker.example.org"] is not Security.SAFE


def test_r01_reference_with_fake_checkpoint_of_its_own_does_not_certify(monkeypatch, tmp_path):
    """A reference is not special: if the operator hands it a checkpoint
    hash that disagrees with the real network, endpoints agreeing with
    that fake checkpoint must still conflict, never become SAFE."""
    reference = _reference(REF_HEIGHT, REF_TIP, checkpoint_hash="c" * 64)
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", REF_HEIGHT, REF_TIP, "c" * 64),
    ], reference=reference))
    assert security["solo.example.org"] is not Security.SAFE


def test_r01_reference_plus_genuine_canonical_agreement_is_still_eligible(monkeypatch, tmp_path):
    """The fix must not make --reference useless: an endpoint that
    genuinely agrees at the reference's own height, with the real
    canonical checkpoint, is still eligible for SAFE."""
    reference = _reference(REF_HEIGHT, REF_TIP, checkpoint_hash=CANONICAL_CHECKPOINT)
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", REF_HEIGHT, REF_TIP, CANONICAL_CHECKPOINT),
    ], reference=reference))
    assert security["solo.example.org"] is Security.SAFE


# --------------------------------------------------------------------- R-02


def test_r02_rider_with_genuine_public_checkpoint_is_not_promoted(monkeypatch, tmp_path):
    """The precise exploit: two attested groups genuinely corroborate each
    other at height H; an attacker at H+1 honestly answers the checkpoint
    probe (it is old, public history -- free for anyone to reproduce) but
    the tip it claims was never compared to anything.  A checkpoint match
    must not stand in for verifying the claimed new tip."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("attacker.example.org", 50002, None, 4_500_001, "f" * 64, CANONICAL_CHECKPOINT),
    ]))
    assert security["one.example.org"] is Security.SAFE
    assert security["two.example.org"] is Security.SAFE
    assert security["attacker.example.org"] is not Security.SAFE


def test_r02_highest_height_is_never_its_own_corroboration(monkeypatch, tmp_path):
    """Without a reference, the comparison anchor for conflict detection is
    the highest self-reported height -- which can be the attacker itself.
    That must not let it silently agree with itself and pass as verified."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("attacker.example.org", 50002, "ATTACKER", 99_999_999, "f" * 64,
         CANONICAL_CHECKPOINT),
    ]))
    assert security["attacker.example.org"] is not Security.SAFE


def test_r02_rider_below_corroborated_height_with_mismatch_still_conflicts(monkeypatch, tmp_path):
    """A + B corroborate N/HN; C, below that height, disagrees about the
    checkpoint: a real conflict, not silently accepted as lag."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("three.example.org", 50002, "OPERATOR-C", 4_499_990, "z" * 64, "f" * 64),
    ]))
    assert all(v is not Security.SAFE for v in security.values())


def test_r02_two_group_corroboration_with_uncomparable_extension_stays_unverified(
        monkeypatch, tmp_path):
    """A + B corroborate N/HN; C claims a higher, uncompared tip with no
    checkpoint evidence at all.  This must classify as simply unverified
    (no promotion), not crash and not silently conflict-flag an endpoint
    for which no positive evidence of wrongdoing exists."""
    summary, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("three.example.org", 50002, "OPERATOR-C", 4_500_050, "z" * 64),
    ]))
    assert summary["chain"] == "VALID"
    assert security["one.example.org"] is Security.SAFE
    assert security["two.example.org"] is Security.SAFE
    assert security["three.example.org"] is not Security.SAFE
    assert security["three.example.org"] is not Security.CONFLICT


# ------------------------------------------------------------- positive path


def test_positive_path_two_groups_real_canonical_evidence_reaches_safe(monkeypatch, tmp_path):
    """Section-29 fixture: valid network, real canonical checkpoint, two
    independently attested, genuinely agreeing operator groups, no
    conflict.  The fix must not make SAFE unreachable for the legitimate
    case."""
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "CIPIG", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "MOONTREE", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
    ]))
    assert security["one.example.org"] is Security.SAFE
    assert security["two.example.org"] is Security.SAFE


def test_positive_path_reference_plus_one_group_real_canonical_evidence(monkeypatch, tmp_path):
    reference = _reference(4_500_000, "a" * 64, checkpoint_hash=CANONICAL_CHECKPOINT)
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("solo.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
    ], reference=reference))
    assert security["solo.example.org"] is Security.SAFE


# --------------------------------------------------------------------- R-03


def test_r03_first_mismatch_is_only_suspected(monkeypatch, tmp_path):
    summary, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
    ]))
    assert summary["chain"] == "CONFLICT_SUSPECTED"
    assert security["two.example.org"] is not Security.CONFLICT
    assert security["two.example.org"] is not Security.SAFE


def test_r03_second_independent_crawl_confirms_and_demotes(monkeypatch, tmp_path):
    """The same group conflicting again on a later, independent crawl
    (a second run_discovery() call against the same store) must actually
    reach CHAIN_CONFLICT and demote it -- the finding was that this path
    was dead code in production."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        entries = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
        ]
        summary1, security1, _ = asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        assert summary1["chain"] == "CONFLICT_SUSPECTED"

        summary2, security2, _ = asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        assert summary2["chain"] == "CHAIN_CONFLICT"
        assert security2["two.example.org"] is Security.CONFLICT
    finally:
        store.close()


def test_r03_same_crawl_does_not_double_count(monkeypatch, tmp_path):
    """Multiple conflicting endpoints under one group within a single crawl
    must bump that group's confirmation counter once, not once per
    endpoint."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        entries = [
            ("anchor.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64,
             CANONICAL_CHECKPOINT),
            ("liar1.example.org", 50002, "LIARS", 4_500_000, "b" * 64, "f" * 64),
            ("liar2.example.org", 50002, "LIARS", 4_500_000, "c" * 64, "f" * 64),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        assert store.conflict_confirmations("LIARS") == 1
    finally:
        store.close()


def test_r03_recovery_requires_a_verified_clean_crawl_not_mere_silence(monkeypatch, tmp_path):
    """The laundering case the fix must close: an offending group must not
    clear its confirmation count by simply going quiet (omitting
    checkpoint evidence) for one crawl -- only a positively verified clean
    comparison clears it."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        conflicting = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
        ]
        summary1, _, _ = asyncio.run(_run(monkeypatch, tmp_path, conflicting, store=store))
        assert summary1["chain"] == "CONFLICT_SUSPECTED"
        assert store.conflict_confirmations("OPERATOR-B") == 1

        # OPERATOR-B goes quiet: reachable, but with neither a matching tip
        # at the anchor's height nor checkpoint evidence -- it lags beyond
        # the alarm threshold instead, so it is neither conflicting nor
        # verified this crawl.  Must not be treated as recovery.
        quiet = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_200, "e" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "x" * 64, None),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, quiet, store=store))
        assert store.conflict_confirmations("OPERATOR-B") == 1, (
            "a quiet crawl with no comparable evidence cleared a confirmed conflict")

        # OPERATOR-B conflicts again: must resume from 1, reach 2, and demote.
        conflicting_again = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_100, "d" * 64,
             CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_100, "d" * 64, "f" * 64),
        ]
        summary3, security3, _ = asyncio.run(
            _run(monkeypatch, tmp_path, conflicting_again, store=store))
        assert summary3["chain"] == "CHAIN_CONFLICT"
        assert security3["two.example.org"] is Security.CONFLICT
    finally:
        store.close()


def test_r03_recovery_clears_after_a_verified_clean_crawl(monkeypatch, tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        conflicting = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, conflicting, store=store))
        assert store.conflict_confirmations("OPERATOR-B") == 1

        clean = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_050, "e" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_050, "e" * 64, CANONICAL_CHECKPOINT),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, clean, store=store))
        assert store.conflict_confirmations("OPERATOR-B") == 0

        # A fresh, unrelated conflict must start again at 1, not resume
        # from a stale count.
        conflicting_again = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_100, "d" * 64,
             CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_100, "d" * 64, "f" * 64),
        ]
        summary3, _, _ = asyncio.run(_run(monkeypatch, tmp_path, conflicting_again, store=store))
        assert summary3["chain"] == "CONFLICT_SUSPECTED"
    finally:
        store.close()


def test_r03_confirmed_conflict_survives_the_endpoint_going_unreachable(monkeypatch, tmp_path):
    """Once demoted, an endpoint that stops answering must not silently
    regain SAFE: its state.security must stay CONFLICT, and its stale
    persisted confirmation count must not be cleared just because it
    disappeared from the crawl."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        entries = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        summary2, security2, _ = asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        assert summary2["chain"] == "CHAIN_CONFLICT"
        assert security2["two.example.org"] is Security.CONFLICT

        gone = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_050, "e" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", None, None),
        ]
        _, security3, availability3 = asyncio.run(_run(monkeypatch, tmp_path, gone, store=store))
        assert security3["two.example.org"] is Security.CONFLICT
        assert availability3["two.example.org"] is not Availability.REACHABLE
        assert store.conflict_confirmations("OPERATOR-B") == 2
    finally:
        store.close()


def test_r03_confirmations_persist_across_a_store_restart(monkeypatch, tmp_path):
    """The confirmation counter is SQLite-backed like the rest of the
    monitor's state, so it survives a process restart -- a long-running
    monitor's memory of a conflict is not lost across a redeploy."""
    entries = [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
    ]
    db_path = tmp_path / "network-observer.sqlite3"

    store1 = Store(str(db_path))
    try:
        asyncio.run(_run(monkeypatch, tmp_path, entries, store=store1))
    finally:
        store1.close()

    store2 = Store(str(db_path))
    try:
        assert store2.conflict_confirmations("OPERATOR-B") == 1
        summary, security, _ = asyncio.run(_run(monkeypatch, tmp_path, entries, store=store2))
        assert summary["chain"] == "CHAIN_CONFLICT"
        assert security["two.example.org"] is Security.CONFLICT
    finally:
        store2.close()


# --------------------------------------------------------------- interaction


def test_interaction_conflict_suspected_blocks_promotion_even_with_two_groups(
        monkeypatch, tmp_path):
    """A three-way split (one pair conflicts, a third agrees with one side)
    must not let the agreeing pair reach SAFE while the split is only
    suspected: the whole crawl's verdict fails closed, matching the
    existing shipped conflict_suspected test."""
    summary, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "b" * 64, "f" * 64),
        ("three.example.org", 50002, "OPERATOR-C", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
    ]))
    assert summary["chain"] == "CONFLICT_SUSPECTED"
    assert all(v is not Security.SAFE for v in security.values())


def test_interaction_confirmed_conflict_remains_effective_as_highest_endpoint_moves(
        monkeypatch, tmp_path):
    """Once a group is confirmed conflicting, it must not regain effect
    merely by changing what height or hash it claims next -- the
    demotion re-applies on the very next crawl where it is still lying."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        entries = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, "f" * 64),
        ]
        asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))
        asyncio.run(_run(monkeypatch, tmp_path, entries, store=store))

        moved = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_777, "e" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_999, "z" * 64, "g" * 64),
        ]
        summary, security, _ = asyncio.run(_run(monkeypatch, tmp_path, moved, store=store))
        assert summary["chain"] == "CHAIN_CONFLICT"
        assert security["two.example.org"] is Security.CONFLICT
    finally:
        store.close()


def test_interaction_unknown_operator_with_reference_height_only_not_safe(monkeypatch, tmp_path):
    reference = _reference(REF_HEIGHT, REF_TIP)
    _, security, _ = asyncio.run(_run(monkeypatch, tmp_path, [
        ("unknown.example.org", 50002, None, REF_HEIGHT + 10, "f" * 64),
    ], reference=reference))
    assert security["unknown.example.org"] is not Security.SAFE


def test_interaction_invalid_policy_plus_two_agreeing_groups_never_safe(monkeypatch, tmp_path):
    async def run_with_empty_policy():
        entries = [
            ("one.example.org", 50002, "OPERATOR-A", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
            ("two.example.org", 50002, "OPERATOR-B", 4_500_000, "a" * 64, CANONICAL_CHECKPOINT),
        ]
        seeds_path = _write_seeds(tmp_path, [(h, p, g) for h, p, g, _hh, _t, _c in entries])
        registry_path = tmp_path / "registry.json"
        results = {}
        for host, port, _group, height, tip, checkpoint in entries:
            endpoint = EndpointId(host, port, Transport.TLS)
            results[endpoint] = _probe_result(
                endpoint, height=height, tip_hash=tip, checkpoint_hash=checkpoint)
        monkeypatch.setattr(cli, "Crawler", lambda **kw: _FakeCrawler(results, **kw))
        store = Store(str(tmp_path / "network-observer.sqlite3"))
        try:
            await cli.run_discovery(
                store, seeds_path=seeds_path, registry_path=registry_path,
                policy={"releases": []}, limits=Limits(), thresholds=Thresholds())
            return {s.endpoint.hostname: s.security for s in store.all_states()}
        finally:
            store.close()

    security = asyncio.run(run_with_empty_policy())
    assert all(v is not Security.SAFE for v in security.values())


# --------------------------------------------------------------- CLI wiring


def test_cli_wires_optional_reference_checkpoint_and_genesis_hash(monkeypatch, tmp_path):
    """--reference-checkpoint-hash / --reference-genesis-hash (added so an
    operator can hand the monitor a reference with real comparable
    evidence, not just a height/tip pair) must actually reach the
    ChainObservation passed to run_discovery()."""
    captured = {}

    async def fake_run_discovery(store, **kwargs):
        captured.update(kwargs)
        return {"probed": 0, "edges": 0, "chain": "UNKNOWN", "chain_detail": ""}

    monkeypatch.setattr(cli, "run_discovery", fake_run_discovery)
    seeds_path = _write_seeds(tmp_path, [])
    db_path = tmp_path / "network-observer.sqlite3"
    argv = [
        "--database", str(db_path),
        "--seeds", str(seeds_path),
        "--registry", str(tmp_path / "registry.json"),
        "--reference-height", "4500000",
        "--reference-tip-hash", "a" * 64,
        "--reference-checkpoint-hash", CANONICAL_CHECKPOINT,
        "--reference-genesis-hash", "b" * 64,
        "discover-now",
    ]
    rc = cli.main(argv)
    assert rc == 0
    reference = captured["reference"]
    assert reference.height == 4_500_000
    assert reference.tip_hash == "a" * 64
    assert reference.checkpoint_hash == CANONICAL_CHECKPOINT
    assert reference.genesis_hash == "b" * 64
