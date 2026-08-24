# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Adversarial tests for Network Observer Phase 1.

Every test here is an attack: Sybil operator keys, absurd future
heights, replayed bundles, rolled-back declarations, tampered
signatures, selective serving, height-confused asset comparisons.  The
numbered comments map to the adversarial requirements list in
docs/network-observer.md.
"""

import base64
import datetime
import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from monitor.assets import (
    AssetDataVerdict, AssetSample, canonical_digest, compare_asset_samples,
    entries_at_height, reconstruct_state, summarize_capability,
)
from monitor.classify import ChainObservation, classify_index_lag
from monitor.model import EndpointId, IndexHealth, Thresholds, Transport
from monitor.observer import (
    build_observation_bundle, canonical_bytes, sign_observation_bundle,
    verify_observation_bundle,
)
from monitor.operators import (
    IdentityState, verify_operator_declaration,
)
from monitor.quorum import (
    ChallengeRecord, ChallengeSet, ChallengeVerdict, build_challenge_set,
    derive_challenge_heights, evaluate_challenges, new_challenge_nonce,
    select_stable_anchor,
)
from monitor.snapshot import (
    build_snapshot, sign_snapshot, verify_snapshot,
)
from monitor.store import SCHEMA_VERSION, Store
from monitor.vantage import compare_vantage_views, views_from_bundles

NOW = datetime.datetime(2026, 8, 24, 12, 0, 0,
                        tzinfo=datetime.timezone.utc)


def endpoint(host="a.example.org"):
    return EndpointId(host, 50002, Transport.TLS)


def observation(group, height, tip="a" * 64, host="a.example.org"):
    return ChainObservation(endpoint=endpoint(host), height=height,
                            tip_hash=tip, operator_group=group)


# ------------------------------------------------------------- anchor (1.1)

def test_absurd_future_height_cannot_self_anchor():
    """#4: one endpoint claiming height 9_999_999 among honest groups
    must not move the anchor above what the others support."""
    obs = [observation("G1", 1_000_000), observation("G2", 999_998),
           observation("G3", 999_950),
           observation("G-EVIL", 9_999_999)]
    anchor = select_stable_anchor(obs)
    # k=2: second-highest attested height is 1_000_000, minus margin 6.
    assert anchor == 1_000_000 - 6


def test_unknown_operators_never_manufacture_anchor():
    """#2/#3: twenty unknown-group observations cannot create an anchor."""
    obs = [observation(None, 9_000_000 + i, host=f"h{i}.example.org")
           for i in range(20)]
    assert select_stable_anchor(obs) is None


def test_one_lagging_server_does_not_drag_forever():
    """k-th highest: a single lagging group among three is not in the
    top two, so the anchor stays high."""
    obs = [observation("G1", 1_000_000), observation("G2", 999_999),
           observation("G3", 1_000_000), observation("G-SLOW", 900_000)]
    assert select_stable_anchor(obs) == 1_000_000 - 6


def test_anchor_needs_two_attested_groups():
    obs = [observation("G1", 1_000_000)]
    assert select_stable_anchor(obs) is None


# ----------------------------------------------------------- challenges

def test_challenge_heights_are_deterministic_and_bounded():
    """#12: same nonce -> same heights, all within [1, anchor]."""
    nonce = "00" * 16
    first = derive_challenge_heights(4_500_000, nonce)
    second = derive_challenge_heights(4_500_000, nonce)
    assert [h.height for h in first] == [h.height for h in second]
    assert all(1 <= h.height <= 4_500_000 for h in first)
    kinds = {h.kind for h in first}
    assert {"anchor", "short", "medium", "long", "checkpoint"} <= kinds
    assert sum(1 for k in kinds if k.startswith("random")) == \
        Thresholds().random_challenges
    # Below the checkpoint height it is simply not part of the set.
    low = derive_challenge_heights(500_000, nonce)
    assert "checkpoint" not in {h.kind for h in low}


def test_challenge_nonce_is_cryptographically_random():
    assert new_challenge_nonce() != new_challenge_nonce()
    assert len(new_challenge_nonce()) == 32


def challenge(records, tips=None, confirmations=None):
    challenge_set = build_challenge_set(
        [observation(g, h) for g, h in (tips or {"G1": 500_000, "G2": 500_000})])
    return challenge_set, evaluate_challenges(
        records, challenge_set, tips=tips or {"G1": 500_000, "G2": 500_000},
        confirmations=confirmations or {})


def record(group, height, block_hash, host=None):
    return ChallengeRecord(endpoint=endpoint(host or group.lower()),
                           operator_group=group, height=height,
                           block_hash=block_hash)


def test_same_height_different_hash_is_conflict_evidence():
    """#6: hash disagreement at a shared height conflicts."""
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 500_000)])
    good = challenge_set.anchor_height
    records = [record("G1", good, "f" * 64), record("G2", good, "e" * 64)]
    verdict = evaluate_challenges(records, challenge_set,
                                  tips={"G1": 500_000, "G2": 500_000})
    assert verdict.status is ChallengeVerdict.CONFLICT_SUSPECTED
    assert set(verdict.conflicting_groups) == {"G1", "G2"}
    confirmed = evaluate_challenges(
        records, challenge_set, tips={"G1": 500_000, "G2": 500_000},
        confirmations={"G1": 2, "G2": 2})
    assert confirmed.status is ChallengeVerdict.CHAIN_CONFLICT


def test_missing_challenge_response_cannot_agree():
    """#11: omission leaves the round incomplete, never agreement."""
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 500_000)])
    heights = challenge_set.height_values()
    records = [record("G1", h, "f" * 64) for h in heights]
    records += [record("G2", h, "f" * 64) for h in heights[:-1]]
    verdict = evaluate_challenges(records, challenge_set,
                                  tips={"G1": 500_000, "G2": 500_000})
    assert verdict.status is ChallengeVerdict.CHALLENGE_INCOMPLETE


def test_lagging_group_is_lag_not_conflict():
    """#5: a group below the anchor is TEMPORARY_LAG."""
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 499_000)])
    heights = [h for h in challenge_set.height_values() if h < 499_000]
    records = [record("G1", h, "f" * 64) for h in heights]
    records += [record("G2", h, "f" * 64) for h in heights]
    verdict = evaluate_challenges(
        records, challenge_set, tips={"G1": 500_000, "G2": 499_000})
    assert verdict.status in (ChallengeVerdict.VALID,
                              ChallengeVerdict.TEMPORARY_LAG)
    assert "G2" not in verdict.conflicting_groups


def test_malformed_header_is_no_evidence():
    """#9: None answers cannot corroborate or conflict."""
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 500_000)])
    heights = challenge_set.height_values()
    records = [record("G1", h, "f" * 64) for h in heights]
    records += [record("G2", h, None) for h in heights]
    verdict = evaluate_challenges(records, challenge_set,
                                  tips={"G1": 500_000, "G2": 500_000})
    assert verdict.status is ChallengeVerdict.CHALLENGE_INCOMPLETE
    assert not verdict.corroborated_hashes


def test_checkpoint_disagreement_is_immediately_serious():
    from electrumx.lib.coins import Ravencoin
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 500_000)])
    records = [
        record("G1", Ravencoin.INCIDENT_CHECKPOINT_HEIGHT,
               Ravencoin.INCIDENT_CHECKPOINT_HASH),
        record("G2", Ravencoin.INCIDENT_CHECKPOINT_HEIGHT, "bad" * 20 + "1"),
    ]
    verdict = evaluate_challenges(records, challenge_set,
                                  tips={"G1": 500_000, "G2": 500_000})
    assert verdict.status is ChallengeVerdict.CONFLICT_SUSPECTED


def test_single_group_cannot_corroborate_itself():
    """#1: one operator with 20 endpoints agreeing is one operator."""
    challenge_set = build_challenge_set(
        [observation("G1", 500_000), observation("G2", 500_000)])
    heights = challenge_set.height_values()
    records = [record("CIPIG", h, "f" * 64, host=f"h{i}.example.org")
               for i in range(20) for h in heights]
    verdict = evaluate_challenges(records, challenge_set,
                                  tips={"CIPIG": 500_000})
    assert verdict.status is ChallengeVerdict.INSUFFICIENT_CORROBORATION


# --------------------------------------------------------- observer bundles

def make_key():
    return Ed25519PrivateKey.generate()


def key_id_for(public_bytes):
    import hashlib
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def make_bundle(private_key, observer_id="EU", sequence=1,
                observations=None, generated_at=NOW, schema=None):
    public_hex = private_key.public_key().public_bytes_raw().hex()
    body = build_observation_bundle(
        observer_id=observer_id, observer_key_id=key_id_for(
            bytes.fromhex(public_hex)),
        sequence=sequence, crawl_id="c1", challenge_nonce="ab" * 16,
        challenge_heights=[500_000, 499_994],
        observations=observations if observations is not None else
        [{"endpoint": "a.example.org:50002/TLS", "reachable": True}],
        generated_at=generated_at)
    if schema is not None:
        body["schemaVersion"] = schema
    return sign_observation_bundle(
        body, private_key, key_id=key_id_for(bytes.fromhex(public_hex)))


def trusted(private_key):
    public = private_key.public_key().public_bytes_raw()
    return {key_id_for(public): public}


def test_bundle_round_trip_and_tampering_fails():
    """#13: any tampering breaks verification."""
    key = make_key()
    document = make_bundle(key)
    body = verify_observation_bundle(
        document, trusted(key), observer_sequence_high_water={}, now=NOW)
    assert body["observerId"] == "EU"
    document["observation"]["observations"][0]["reachable"] = False
    with pytest.raises(Exception):
        verify_observation_bundle(
            document, trusted(key), observer_sequence_high_water={}, now=NOW)


def test_wrong_and_unknown_observer_keys_rejected():
    """#14/#15."""
    signer = make_key()
    document = make_bundle(signer)
    other = trusted(make_key())
    with pytest.raises(Exception):
        verify_observation_bundle(
            document, other, observer_sequence_high_water={}, now=NOW)
    with pytest.raises(Exception):
        verify_observation_bundle(
            document, {}, observer_sequence_high_water={}, now=NOW)


def test_expired_or_replayed_bundle_rejected():
    """#16/#17: expiry, and sequence at/below the high-water mark."""
    key = make_key()
    expired = make_bundle(key, generated_at=NOW - datetime.timedelta(hours=25))
    with pytest.raises(Exception):
        verify_observation_bundle(
            expired, trusted(key), observer_sequence_high_water={}, now=NOW)
    document = make_bundle(key, sequence=7)
    with pytest.raises(Exception):
        verify_observation_bundle(
            document, trusted(key),
            observer_sequence_high_water={key_id_for(
                document["signature"]["keyId"]): 7}, now=NOW)
    ok = verify_observation_bundle(
        make_bundle(key, sequence=8), trusted(key),
        observer_sequence_high_water={document["signature"]["keyId"]: 7},
        now=NOW)
    assert ok["sequence"] == 8


def test_future_schema_refused():
    """#18."""
    key = make_key()
    document = make_bundle(key, schema=99)
    with pytest.raises(Exception):
        verify_observation_bundle(
            document, trusted(key), observer_sequence_high_water={}, now=NOW)


def test_clock_skew_tolerance():
    key = make_key()
    slightly_old = make_bundle(
        key, generated_at=NOW - datetime.timedelta(seconds=120))
    verify_observation_bundle(
        slightly_old, trusted(key), observer_sequence_high_water={}, now=NOW)
    too_old = make_bundle(
        key, generated_at=NOW - datetime.timedelta(hours=2))
    with pytest.raises(Exception):
        verify_observation_bundle(
            too_old, trusted(key), observer_sequence_high_water={}, now=NOW)


# ------------------------------------------------------ operator identity

def make_declaration(group="NEWCO", sequence=1, endpoints=None,
                     key=None, valid_days=365):
    key = key or make_key()
    public_hex = key.public_key().public_bytes_raw().hex()
    from monitor.operators import canonical_bytes as op_canonical
    body = {
        "schemaVersion": 1,
        "operatorName": "New Co",
        "operatorGroup": group,
        "operatorKeyId": key_id_for(bytes.fromhex(public_hex)),
        "publicKey": public_hex,
        "sequence": sequence,
        "validFrom": NOW.isoformat(),
        "expiresAt": (NOW + datetime.timedelta(days=valid_days)).isoformat(),
        "endpoints": endpoints or ["node1.example.org:50002"],
    }
    signature = key.sign(op_canonical(body))
    return {
        "declaration": body,
        "signature": {
            "algorithm": "ed25519",
            "keyId": key_id_for(bytes.fromhex(public_hex)),
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }, key


def test_declaration_tampering_and_rollback_rejected():
    """#22/#23."""
    document, key = make_declaration()
    attested = {document["signature"]["keyId"]: "NEWCO"}
    verify_operator_declaration(document, attested, now=NOW)
    tampered = json.loads(json.dumps(document))
    tampered["declaration"]["operatorGroup"] = "EVIL"
    with pytest.raises(Exception):
        verify_operator_declaration(tampered, attested, now=NOW)
    rolled_back, _ = make_declaration(sequence=1)
    with pytest.raises(Exception):
        verify_operator_declaration(
            rolled_back, attested, now=NOW,
            sequence_high_water={rolled_back["signature"]["keyId"]: 5})


def test_self_signed_is_not_attested_quorum():
    """#25: valid signature, unattested key -> SELF_SIGNED, never
    REGISTRY_ATTESTED, however many distinct keys appear."""
    document, _ = make_declaration(group="SYBIL-1")
    result = verify_operator_declaration(document, {}, now=NOW)
    assert result.state is IdentityState.SELF_SIGNED
    keys = [make_declaration(group=f"SYBIL-{i}") for i in range(20)]
    assert all(verify_operator_declaration(d, {}, now=NOW).state
               is IdentityState.SELF_SIGNED for d, _ in keys)


def test_attested_declaration_and_duplicate_endpoints():
    """#24: one accepted key binding many endpoints stays one group."""
    document, _ = make_declaration(
        group="CIPIG",
        endpoints=[f"electrum{i}.example.org:50002" for i in range(1, 4)])
    attested = {document["signature"]["keyId"]: "CIPIG"}
    result = verify_operator_declaration(document, attested, now=NOW)
    assert result.state is IdentityState.REGISTRY_ATTESTED
    assert len(result.endpoints) == 3
    dup, _ = make_declaration(endpoints=["a.example.org:50002",
                                         "a.example.org:50002"])
    with pytest.raises(Exception):
        verify_operator_declaration(dup, attested, now=NOW)


# ------------------------------------------------------------ index lag

def test_index_lag_classified_separately():
    """#26."""
    assert classify_index_lag(1_000, 1_000)[0] is IndexHealth.SYNCED
    assert classify_index_lag(1_000, 998)[0] is IndexHealth.LAGGING
    assert classify_index_lag(1_000, 900)[0] is IndexHealth.STALE
    assert classify_index_lag(None, 1_000)[0] is IndexHealth.UNKNOWN


# --------------------------------------------------------- asset quorum

def test_capability_requires_working_methods():
    """#27: a claim without working methods is LEGACY, not FULL."""
    matrix = {"blockchain.asset.get_meta": True,
              "blockchain.asset.get_assets_with_prefix": False,
              "blockchain.asset.get_meta_history": False,
              "blockchain.tag.qualifier.history": False}
    assert summarize_capability(matrix).value == "ASSET_PARTIAL"
    assert summarize_capability(None, {"ravencoin": {"assets": True}}).value \
        == "ASSET_LEGACY"
    full = dict.fromkeys(matrix, True)
    assert summarize_capability(full).value == "ASSET_CAPABLE"


def test_height_bound_reconstruction_exact():
    """#31/#32: canonical form stable, history order preserved."""
    history = [
        {"height": 10, "flag": True, "h160": "aa"},
        {"height": 12, "flag": False, "h160": "aa"},
        {"height": 20, "flag": True, "h160": "bb"},
    ]
    assert entries_at_height(history, 12) == history[:2]
    assert reconstruct_state("qualifier_history", history, 11) == {"aa": True}
    assert reconstruct_state("qualifier_history", history, 20) == \
        {"aa": False, "bb": True}
    state = reconstruct_state("qualifier_history", history, 20)
    assert canonical_digest("qualifier_history", state) == \
        canonical_digest("qualifier_history", {"aa": False, "bb": True})
    assert canonical_digest("qualifier_history", state) != \
        canonical_digest("meta_history", state)


def test_different_heights_not_false_conflict():
    """#28: samples at different heights are NOT_COMPARABLE, period."""
    samples = [AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G1"),
               AssetSample("meta_history", "SENT", 499_000, "b" * 64, "G2")]
    verdict, _, _ = compare_asset_samples(samples, chain_comparable=True)
    assert verdict is AssetDataVerdict.NOT_COMPARABLE


def test_same_height_mismatch_detected_and_needs_confirmation():
    """#29/#30."""
    samples = [AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G1"),
               AssetSample("meta_history", "SENT", 500_000, "b" * 64, "G2")]
    verdict, detail, _ = compare_asset_samples(samples, chain_comparable=True)
    assert verdict is AssetDataVerdict.MISMATCH_SUSPECTED
    assert "500000" in detail
    agree = [AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G1"),
             AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G2")]
    verdict, _, _ = compare_asset_samples(agree, chain_comparable=True)
    assert verdict is AssetDataVerdict.AGREE
    # One observation never confirms a conflict.
    from monitor.assets import escalate_asset_verdict
    assert escalate_asset_verdict(AssetDataVerdict.MISMATCH_SUSPECTED, 1) \
        is AssetDataVerdict.MISMATCH_SUSPECTED
    assert escalate_asset_verdict(AssetDataVerdict.MISMATCH_SUSPECTED, 2) \
        is AssetDataVerdict.CONFLICT


def test_asset_quorum_requires_comparable_chain_and_attested_groups():
    """#6.1/#6.5: non-comparable chain context and unknown operators."""
    samples = [AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G1"),
               AssetSample("meta_history", "SENT", 500_000, "a" * 64, "G2")]
    verdict, _, _ = compare_asset_samples(samples, chain_comparable=False)
    assert verdict is AssetDataVerdict.NOT_COMPARABLE
    unknown = [AssetSample("meta_history", "SENT", 500_000, "a" * 64,
                           f"UNKNOWN-h{i}.example.org") for i in range(5)]
    verdict, _, _ = compare_asset_samples(unknown, chain_comparable=True)
    assert verdict is AssetDataVerdict.INSUFFICIENT_QUORUM


# ------------------------------------------------------------- vantage

def bundle_body(observer, fingerprint, hashes, version="1.13.10"):
    return {
        "observerId": observer,
        "generatedAt": NOW.isoformat(),
        "observations": [{
            "endpoint": "a.example.org:50002/TLS",
            "addressFamilies": ["ipv4"],
            "tlsFingerprint": fingerprint,
            "serverVersion": version,
            "backendClaim": {"repository": "RavenProject/Ravencoin",
                             "commit": "a" * 40},
            "challengeHashes": hashes,
            "assetCapability": {},
        }],
    }


def test_multi_vantage_consistent_and_dns_variance():
    """#19/#21: three observers, one operator; DNS variance stays
    DNS_VARIANCE and never becomes a chain conflict."""
    views = views_from_bundles([
        bundle_body("EU", "fp1", {"500000": "f" * 64}),
        bundle_body("US", "fp1", {"500000": "f" * 64}),
        bundle_body("ASIA", "fp1", {"500000": "f" * 64}),
    ])
    (summary,) = compare_vantage_views(views)
    assert summary.observers == ["ASIA", "EU", "US"]
    assert summary.agreement.value == "MULTI_VANTAGE_CONSISTENT"

    dns_variant = bundle_body("US", "fp1", {"500000": "f" * 64})
    dns_variant["observations"][0]["addressFamilies"] = ["ipv4", "ipv6"]
    (summary,) = compare_vantage_views(
        views_from_bundles([bundle_body("EU", "fp1", {"500000": "f" * 64}),
                            dns_variant]))
    assert summary.agreement.value == "DNS_VARIANCE"


def test_selective_chain_serving_surfaced():
    """#20: same endpoint, same height, different hashes per observer."""
    (summary,) = compare_vantage_views(views_from_bundles([
        bundle_body("EU", "fp1", {"500000": "f" * 64}),
        bundle_body("US", "fp1", {"500000": "e" * 64}),
    ]))
    assert summary.agreement.value == "CHAIN_SELECTIVE_SERVING_SUSPECTED"


# ------------------------------------------------------- store migration

def test_store_upgrades_from_v4_and_refuses_future(tmp_path):
    """#37/#38: migration from schema 4 works; a newer schema refuses."""
    import sqlite3
    path = tmp_path / "monitor.sqlite3"
    connection = sqlite3.connect(path)
    connection.executescript("""
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        INSERT INTO schema_version VALUES (4);
        CREATE TABLE endpoints (id INTEGER PRIMARY KEY, hostname TEXT,
            port INTEGER, transport TEXT, availability TEXT, security TEXT,
            reason TEXT DEFAULT '', operator TEXT, operator_group TEXT,
            sources TEXT DEFAULT '[]', consecutive_failures INTEGER DEFAULT 0,
            consecutive_successes INTEGER DEFAULT 0, first_seen INTEGER,
            last_seen INTEGER, last_probe INTEGER, last_success INTEGER,
            UNIQUE (hostname, port, transport));
        CREATE TABLE observations (id INTEGER PRIMARY KEY,
            endpoint_id INTEGER REFERENCES endpoints(id), observed_at INTEGER,
            reachable INTEGER, error_category TEXT, server_version TEXT,
            height INTEGER, tip_hash TEXT, rpc_latency_ms REAL,
            backend_json TEXT, vantage_point TEXT DEFAULT 'local');
    """)
    connection.commit()
    connection.close()
    store = Store(str(path))
    assert store.observer_high_water() == {}
    store.record_accepted_observation("k1", "EU", 3, "{}")
    assert store.observer_high_water() == {"k1": 3}
    store.close()

    future = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(future)
    connection.executescript(
        "CREATE TABLE schema_version (version INTEGER NOT NULL);"
        "INSERT INTO schema_version VALUES (9999);")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError):
        Store(str(future))


def test_prune_preserves_high_water_marks(tmp_path):
    """#39."""
    store = Store(str(tmp_path / "m.sqlite3"))
    store.record_accepted_observation("k1", "EU", 5, "{}")
    store.record_snapshot(9, "{}")
    store.prune(keep_observation_days=0, now=10 ** 12)
    assert store.observer_high_water() == {"k1": 5}
    assert store.minimum_snapshot_version() == 9
    store.close()


# ------------------------------------------------------------- snapshot

def test_snapshot_sign_verify_and_rollback(tmp_path):
    from monitor.model import Availability, EndpointState, Security
    state = EndpointState(endpoint=endpoint(),
                          availability=Availability.REACHABLE,
                          security=Security.SAFE)
    body = build_snapshot(
        [state], snapshot_version=3, chain={"stableHeight": 500_000},
        infrastructure={"fullAsset": 1, "indexSynced": 1},
        observers={"observerCount": 3}, asset_sampling={"samples": 4},
        generated_at=NOW)
    assert "Ravencoin consensus remains authoritative" in body["disclaimer"]
    key = make_key()
    document = sign_snapshot(body, key, key_id=key_id_for(
        key.public_key().public_bytes_raw()))
    verified = verify_snapshot(document, trusted(key), now=NOW)
    assert verified["chain"]["stableHeight"] == 500_000
    with pytest.raises(Exception):
        verify_snapshot(document, trusted(key), minimum_version=4, now=NOW)
    with pytest.raises(Exception):
        verify_snapshot(document, {}, now=NOW)
    document["snapshot"]["chain"]["stableHeight"] = 1
    with pytest.raises(Exception):
        verify_snapshot(document, trusted(key), now=NOW)
    expired = build_snapshot(
        [state], snapshot_version=4, chain={}, infrastructure={},
        observers={}, asset_sampling={},
        generated_at=NOW - datetime.timedelta(days=30))
    expired["expiresAt"] = NOW.isoformat()
    expired_doc = sign_snapshot(expired, key, key_id=key_id_for(
        key.public_key().public_bytes_raw()))
    with pytest.raises(Exception):
        verify_snapshot(expired_doc, trusted(key),
                        now=NOW + datetime.timedelta(days=2))
