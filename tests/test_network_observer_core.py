# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Tests for the public Electrum monitor.

No test opens a network connection.  Peer responses, DNS answers and probes are
all supplied by the test, which is the only way to test a crawler's behaviour
against hostile input without being hostile to somebody's real server.
"""

import asyncio
import datetime
import json

import pytest

from network_observer.classify import (
    ChainObservation, classify_assets, classify_backend, compare_chains,
    count_independent_operators, independent_groups, suggest_operator_group,
)
from network_observer.crawl import Crawler, RateLimiter, resolve_endpoint
from network_observer.directory import (
    DirectoryError, build_directory, candidates_from_directory, sign_directory,
    verify_directory,
)
from network_observer.model import (
    AssetSupport, Availability, DiscoverySource, EndpointId, EndpointState, Limits,
    ProbeResult, Security, Thresholds, Transport,
)
from network_observer.netsafety import (
    UnsafeTarget, normalize_hostname, normalize_port, parse_peer_record,
    parse_peers_response, safe_resolved_addresses,
)
from network_observer.store import Store

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
OTHER_COMMIT = "d" * 40

CERTIFIED_POLICY = {
    "releases": [{
        "repository": "2miners/Ravencoin", "tag": "v4.8.0", "version": "4.8.0",
        "commit": CERTIFIED_COMMIT, "status": "KNOWN_SAFE",
        "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "PASS"},
    }]
}


def endpoint(host="electrum.example.org", port=50002, transport=Transport.TLS):
    return EndpointId(host, port, transport)


def backend_payload(*, repository="2miners/Ravencoin", commit=CERTIFIED_COMMIT,
                    version="4.8.0", network="main", core_safe=True,
                    synchronized=True, checkpoint=True, kawpow=True, identity=True):
    core = {
        "name": "Ravencoin Core", "version": version, "versionNumber": 4_080_000,
        "subversion": f"/Ravencoin:{version}/", "network": network,
        "blocks": 4_494_000, "headers": 4_494_000, "initialBlockDownload": False,
    }
    if identity:
        core["identity"] = {"evidence": "BUILD_IDENTITY_VERIFIED",
                            "sourceRepository": repository, "sourceCommit": commit}
    return {
        "server": "ElectrumX-RVN", "serverVersion": "ElectrumX-RVN 1.13.0.dev1",
        "backend": core,
        "compatibility": {
            "minimumSafeCore": "4.8.0", "safetyProfile": "rvn-consensus-2026-08-v1",
            "coreSafe": core_safe, "networkMatches": True,
            "backendSynchronized": synchronized, "kawpowHeightValidation": kawpow,
            "checkpoint4487775": checkpoint,
        },
        "observedAt": 1786790000,
    }


# ------------------------------------------------------------------ SSRF rules
@pytest.mark.parametrize("hostname", [
    "127.0.0.1", "10.1.2.3", "172.16.5.4", "192.168.1.50", "169.254.169.254",
    "::1", "fe80::1", "fc00::1", "0.0.0.0", "100.100.100.200",
])
def test_private_and_local_literals_are_refused(hostname):
    with pytest.raises(UnsafeTarget):
        normalize_hostname(hostname)


@pytest.mark.parametrize("hostname", [
    "localhost", "server", "has space.example.org", "http://example.org",
    "user@example.org", "example.org:50002", "..", "-bad.example.org",
    "toolong" + "a" * 300 + ".example.org",
])
def test_malformed_hostnames_are_refused(hostname):
    with pytest.raises(UnsafeTarget):
        normalize_hostname(hostname)


@pytest.mark.parametrize("hostname", [
    "electrum.example.org", "ELECTRUM.Example.ORG", "node1.rvn.example.com",
    "electrum.example.org.", "8.8.8.8",
])
def test_public_hostnames_are_accepted(hostname):
    assert normalize_hostname(hostname) == hostname.strip(".").lower()


def test_onion_addresses_are_shape_checked():
    valid = "a" * 56 + ".onion"
    assert normalize_hostname(valid) == valid
    with pytest.raises(UnsafeTarget):
        normalize_hostname("short.onion")


def test_dns_answer_is_filtered_not_just_the_first_address():
    mixed = ["8.8.8.8", "127.0.0.1", "10.0.0.5", "2001:4860:4860::8888", "::1"]
    safe = safe_resolved_addresses(mixed)
    assert "127.0.0.1" not in safe and "10.0.0.5" not in safe and "::1" not in safe
    assert "8.8.8.8" in safe and "2001:4860:4860::8888" in safe


def test_documentation_ranges_are_also_refused():
    """RFC 5737 and RFC 3849 ranges are not routable, so they are never probed."""
    assert safe_resolved_addresses(["203.0.113.10", "198.51.100.4", "2001:db8::1"]) == []


def test_hostname_resolving_only_to_private_addresses_is_refused():
    async def resolver(hostname, port):
        import socket
        return [(socket.AF_INET, None, None, None, ("192.168.1.50", port))]

    with pytest.raises(UnsafeTarget):
        asyncio.run(resolve_endpoint(endpoint(), Limits(), resolver=resolver))


def test_development_override_is_explicit_and_never_from_peer_data():
    async def resolver(hostname, port):
        import socket
        return [(socket.AF_INET, None, None, None, ("127.0.0.1", port))]

    ipv4, _ipv6 = asyncio.run(resolve_endpoint(endpoint(), Limits(),
                                               allow_private=True, resolver=resolver))
    assert ipv4 == ["127.0.0.1"]


@pytest.mark.parametrize("port", [0, -1, 70000, "abc", None, True])
def test_invalid_ports_are_refused(port):
    with pytest.raises(UnsafeTarget):
        normalize_port(port)


# -------------------------------------------------------------- peer parsing
def test_valid_peer_record_yields_both_transports():
    endpoints, version = parse_peer_record(
        ["203.0.113.7", "electrum.example.org", ["v1.4", "s50002", "t50001"]])
    assert version == "1.4"
    assert {item.transport for item in endpoints} == {Transport.TLS, Transport.TCP}
    assert {item.port for item in endpoints} == {50002, 50001}


def test_peer_reported_ip_is_ignored_in_favour_of_the_hostname():
    endpoints, _ = parse_peer_record(
        ["127.0.0.1", "electrum.example.org", ["s50002"]])
    assert all(item.hostname == "electrum.example.org" for item in endpoints)


@pytest.mark.parametrize("record", [
    "not a list",
    ["only", "two"],
    ["1.2.3.4", "localhost", ["s50002"]],
    ["1.2.3.4", "electrum.example.org", "not a list"],
    ["1.2.3.4", "electrum.example.org", []],
    ["1.2.3.4", "electrum.example.org", ["v1.4"]],
    ["1.2.3.4", "192.168.1.5", ["s50002"]],
])
def test_malformed_or_unsafe_peer_records_are_refused(record):
    with pytest.raises(UnsafeTarget):
        parse_peer_record(record)


def test_peer_flood_is_bounded_and_deduplicated():
    flood = [["1.2.3.4", f"node{index}.example.org", ["s50002"]]
             for index in range(5000)]
    flood.extend([["1.2.3.4", "node1.example.org", ["s50002"]]] * 50)
    limits = Limits(max_peers_per_response=25)
    discovered = parse_peers_response(flood, limits)
    assert len(discovered) <= 25


def test_one_malformed_peer_does_not_discard_the_rest():
    response = [
        ["1.2.3.4", "good.example.org", ["s50002"]],
        "garbage",
        ["1.2.3.4", "127.0.0.1", ["s50002"]],
        ["1.2.3.4", "also-good.example.org", ["t50001"]],
    ]
    hosts = {item.hostname for item in parse_peers_response(response)}
    assert hosts == {"good.example.org", "also-good.example.org"}


# ------------------------------------------------------------------- crawling
def make_probe(peers_by_host):
    async def prober(target, *, limits=None, allow_private=False):
        result = ProbeResult(endpoint=target, reachable=True,
                             server_version="ElectrumX-RVN 1.13.0.dev1",
                             height=4_494_000, tip_hash="a" * 64)
        result.peers = tuple(peers_by_host.get(target.hostname, ()))
        return result
    return prober


def test_crawl_follows_gossip_and_records_edges():
    seed = endpoint("seed.example.org")
    peer = endpoint("peer.example.org")
    results, edges = asyncio.run(Crawler(prober=make_probe({
        "seed.example.org": [peer],
    })).crawl([seed]))
    assert seed in results and peer in results
    assert (seed, peer) in edges


def test_crawl_respects_the_depth_limit():
    chain = {f"n{index}.example.org": [endpoint(f"n{index + 1}.example.org")]
             for index in range(10)}
    limits = Limits(max_crawl_depth=2)
    results, _edges = asyncio.run(
        Crawler(limits=limits, prober=make_probe(chain)).crawl(
            [endpoint("n0.example.org")]))
    assert len(results) == 3  # depth 0, 1 and 2


def test_crawl_respects_the_candidate_limit():
    many = {"seed.example.org": [endpoint(f"n{index}.example.org")
                                 for index in range(500)]}
    limits = Limits(max_new_candidates_per_crawl=10)
    results, _edges = asyncio.run(
        Crawler(limits=limits, prober=make_probe(many)).crawl(
            [endpoint("seed.example.org")]))
    assert len(results) == 11  # the seed plus ten new candidates


def test_crawl_terminates_on_a_peer_loop():
    loop = {"a.example.org": [endpoint("b.example.org")],
            "b.example.org": [endpoint("a.example.org")]}
    results, _edges = asyncio.run(
        Crawler(prober=make_probe(loop)).crawl([endpoint("a.example.org")]))
    assert set(results) == {endpoint("a.example.org"), endpoint("b.example.org")}


def test_rate_limiter_stops_hammering_one_host():
    limiter = RateLimiter(Limits(max_probes_per_host_per_hour=3))
    allowed = [limiter.allow("electrum.example.org", now=100 + index)
               for index in range(5)]
    assert allowed == [True, True, True, False, False]


# --------------------------------------------------------- availability model
def test_failure_hysteresis_walks_down_and_recovery_walks_up():
    thresholds = Thresholds()
    state = EndpointState(endpoint=endpoint(), availability=Availability.REACHABLE)
    assert state.register_failure(thresholds) is Availability.DEGRADED
    state.register_failure(thresholds)
    assert state.register_failure(thresholds) is Availability.OFFLINE
    assert state.register_success(thresholds) is Availability.DEGRADED
    assert state.register_success(thresholds) is Availability.REACHABLE


def test_long_term_failure_becomes_stale():
    thresholds = Thresholds(failures_to_stale=5)
    state = EndpointState(endpoint=endpoint())
    for _ in range(5):
        state.register_failure(thresholds)
    assert state.availability is Availability.STALE


def test_offline_backoff_grows_with_consecutive_failures():
    thresholds = Thresholds(jitter_fraction=0.0)
    intervals = [thresholds.next_interval(Availability.OFFLINE, Security.UNKNOWN, count)
                 for count in (3, 4, 5, 6, 20)]
    assert intervals == sorted(intervals)
    assert intervals[0] < intervals[-1]


def test_safe_endpoints_are_polled_less_often_than_unverified_ones():
    thresholds = Thresholds(jitter_fraction=0.0)
    safe = thresholds.next_interval(Availability.REACHABLE, Security.SAFE, 0)
    other = thresholds.next_interval(Availability.REACHABLE, Security.UNVERIFIED, 0)
    assert safe > other


# ------------------------------------------------------------ classification
def test_certified_backend_is_only_unverified_until_the_chain_agrees():
    security, reason = classify_backend(backend_payload(), CERTIFIED_POLICY)
    assert security is Security.UNVERIFIED
    assert "chain validation still required" in reason


def test_missing_backend_capability_is_tracked_not_hidden():
    security, reason = classify_backend(None, CERTIFIED_POLICY)
    assert security is Security.BACKEND_MISSING
    assert "does not implement" in reason


def test_unreviewed_future_core_is_not_safe():
    payload = backend_payload(commit=OTHER_COMMIT, version="4.9.0")
    security, _reason = classify_backend(payload, CERTIFIED_POLICY)
    assert security is Security.UNREVIEWED_CORE


def test_known_unsafe_core_is_unsafe():
    security, _reason = classify_backend(backend_payload(version="4.7.0"),
                                         CERTIFIED_POLICY)
    assert security is Security.UNSAFE


def test_revoked_core_is_unsafe():
    revoked = {"releases": [dict(CERTIFIED_POLICY["releases"][0],
                                 status="REVOKED", revocationReason="regression")]}
    security, _reason = classify_backend(backend_payload(), revoked)
    assert security is Security.UNSAFE


def test_server_without_identity_cannot_be_matched_to_the_policy():
    security, _reason = classify_backend(backend_payload(identity=False),
                                         CERTIFIED_POLICY)
    assert security is Security.UNREVIEWED_CORE


def test_wrong_network_is_unsafe():
    security, _reason = classify_backend(backend_payload(network="test"),
                                         CERTIFIED_POLICY)
    assert security is Security.UNSAFE


def test_unsynchronized_backend_is_not_promoted():
    security, _reason = classify_backend(backend_payload(synchronized=False),
                                         CERTIFIED_POLICY)
    assert security is Security.UNVERIFIED


def test_asset_capability_from_probed_methods():
    assert classify_assets(None, {"blockchain.asset.get_meta": True,
                                  "blockchain.asset.get_assets_with_prefix": True}) \
        is AssetSupport.CAPABLE
    assert classify_assets(None, {"blockchain.asset.get_meta": True,
                                  "blockchain.asset.get_assets_with_prefix": False}) \
        is AssetSupport.PARTIAL
    assert classify_assets(None, {"blockchain.asset.get_meta": False,
                                  "blockchain.asset.get_assets_with_prefix": False}) \
        is AssetSupport.UNSUPPORTED
    assert classify_assets(None, None) is AssetSupport.UNKNOWN


# -------------------------------------------------------------- operator groups
def observation(host, group, height=4_494_000, tip="a" * 64, genesis="g" * 64):
    return ChainObservation(endpoint=endpoint(host), height=height, tip_hash=tip,
                            genesis_hash=genesis, operator_group=group)


def test_three_endpoints_of_one_operator_are_one_group():
    groups = independent_groups([
        observation("electrum1.cipig.net", "CIPIG"),
        observation("electrum2.cipig.net", "CIPIG"),
        observation("electrum3.cipig.net", "CIPIG"),
    ])
    assert list(groups) == ["CIPIG"]


def test_two_groups_are_counted_separately():
    groups = independent_groups([
        observation("electrum1.cipig.net", "CIPIG"),
        observation("electrum2.cipig.net", "CIPIG"),
        observation("electrum.alenoc.example", "ALENOC"),
    ])
    assert set(groups) == {"CIPIG", "ALENOC"}


def test_two_alenoc_endpoints_are_one_operator():
    """Multiple ALENOC-run endpoints must not be counted as separate,
    independent operators; that would manufacture diversity that does not
    exist. See network_observer/config/operator-registry.json."""
    groups = independent_groups([
        observation("electrum-a.alenoc.example", "ALENOC"),
        observation("electrum-b.alenoc.example", "ALENOC"),
    ])
    assert list(groups) == ["ALENOC"]

    states = [
        EndpointState(endpoint=endpoint("electrum-a.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("electrum-b.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("electrum1.cipig.net"),
                      security=Security.SAFE, operator_group="CIPIG"),
    ]
    counts = count_independent_operators(states)
    # Two endpoints for ALENOC, one for CIPIG, but the number of independent
    # operators represented is two, not three: endpoint count is not operator
    # count.
    assert counts == {"ALENOC": 2, "CIPIG": 1}
    assert len(counts) == 2


def test_endpoint_majority_does_not_override_an_independent_conflict():
    """Two endpoints from one operator do not outvote one from another."""
    observations = [
        observation("electrum1.cipig.net", "CIPIG", tip="a" * 64),
        observation("electrum2.cipig.net", "CIPIG", tip="a" * 64),
        observation("electrum.other.example", "OTHER", tip="b" * 64),
    ]
    verdict = compare_chains(observations, confirmations=2)
    assert verdict.status == "CHAIN_CONFLICT"
    assert "OTHER" in verdict.conflicting_groups


def test_conflict_needs_confirmation_before_it_is_declared():
    observations = [
        observation("a.example.org", "A", tip="a" * 64),
        observation("b.example.org", "B", tip="b" * 64),
    ]
    assert compare_chains(observations, confirmations=1).status == "CONFLICT_SUSPECTED"


def test_height_lag_is_not_a_conflict():
    observations = [
        observation("a.example.org", "A", height=4_494_000, tip="a" * 64),
        observation("b.example.org", "B", height=4_493_999, tip="c" * 64),
    ]
    assert compare_chains(observations, confirmations=2).status == "VALID"


def test_large_lag_is_reported_as_lag_not_conflict():
    observations = [
        observation("a.example.org", "A", height=4_494_000, tip="a" * 64),
        observation("b.example.org", "B", height=4_490_000, tip="c" * 64),
    ]
    assert compare_chains(observations, confirmations=2).status == "TEMPORARY_LAG"


def test_different_genesis_is_always_a_conflict():
    observations = [
        observation("a.example.org", "A"),
        observation("b.example.org", "B", genesis="z" * 64),
    ]
    assert compare_chains(observations, confirmations=2).status == "CHAIN_CONFLICT"


def test_unknown_operators_are_not_merged_into_one_group():
    groups = independent_groups([
        ChainObservation(endpoint=endpoint("a.example.org"), height=1, tip_hash="a"),
        ChainObservation(endpoint=endpoint("b.example.org"), height=1, tip_hash="a"),
    ])
    assert len(groups) == 2


def test_operator_group_suggestion_is_only_a_suggestion():
    known = {"electrum1.cipig.net": "CIPIG"}
    assert suggest_operator_group("electrum9.cipig.net", known) == "CIPIG"
    assert suggest_operator_group("electrum.elsewhere.org", known) is None


def test_independent_operator_count_ignores_unsafe_endpoints():
    states = [
        EndpointState(endpoint=endpoint("a.cipig.net"), security=Security.SAFE,
                      operator_group="CIPIG"),
        EndpointState(endpoint=endpoint("b.cipig.net"), security=Security.SAFE,
                      operator_group="CIPIG"),
        EndpointState(endpoint=endpoint("c.example.org"), security=Security.UNSAFE,
                      operator_group="OTHER"),
    ]
    counts = count_independent_operators(states)
    assert counts == {"CIPIG": 2}
    assert len(counts) == 1


# --------------------------------------------------------------------- store
def test_store_round_trip_and_multiple_sources(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    target = endpoint()
    store.upsert_endpoint(target, source=DiscoverySource.BOOTSTRAP, now=100)
    store.upsert_endpoint(target, source=DiscoverySource.GOSSIP,
                          operator_group="EXAMPLE", now=200)
    state = store.load_state(target)
    assert state.sources == {DiscoverySource.BOOTSTRAP, DiscoverySource.GOSSIP}
    assert state.operator_group == "EXAMPLE"
    assert len(store.all_states()) == 1
    store.close()


def test_store_tracks_address_changes_for_a_dynamic_host(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    target = endpoint()
    store.upsert_endpoint(target, now=100)
    for address, when in (("203.0.113.7", 100), ("203.0.113.9", 200)):
        store.record_probe(ProbeResult(endpoint=target, reachable=True,
                                       resolved_ipv4=(address,)), now=when)
    addresses = {row["address"] for row in store.addresses_for(target)}
    assert addresses == {"203.0.113.7", "203.0.113.9"}
    store.close()


def test_store_keeps_observations_separate_by_vantage_point(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    target = endpoint()
    store.upsert_endpoint(target, now=100)
    store.record_probe(ProbeResult(endpoint=target, reachable=False,
                                   vantage_point="probe-a"), now=100)
    store.record_probe(ProbeResult(endpoint=target, reachable=False,
                                   vantage_point="probe-b"), now=101)
    rows = store.connection.execute(
        "SELECT vantage_point FROM observations ORDER BY observed_at").fetchall()
    assert [row["vantage_point"] for row in rows] == ["probe-a", "probe-b"]
    store.close()


def test_store_records_peer_edges_as_provenance(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    store.record_peer_edge(endpoint("a.example.org"), endpoint("b.example.org"),
                           now=100)
    store.record_peer_edge(endpoint("a.example.org"), endpoint("b.example.org"),
                           now=200)
    edges = store.peer_edges()
    assert len(edges) == 1
    assert edges[0]["announcements"] == 2
    store.close()


def test_store_prunes_old_observations(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    target = endpoint()
    store.upsert_endpoint(target, now=0)
    store.record_probe(ProbeResult(endpoint=target, reachable=True), now=0)
    store.record_probe(ProbeResult(endpoint=target, reachable=True), now=10_000_000)
    removed = store.prune(keep_observation_days=7, now=10_000_000)
    assert removed == 1
    store.close()


def test_store_refuses_a_newer_schema(tmp_path):
    path = str(tmp_path / "network-observer.sqlite3")
    store = Store(path)
    store.connection.execute("UPDATE schema_version SET version = 99")
    store.connection.commit()
    store.close()
    with pytest.raises(RuntimeError, match="newer than this code"):
        Store(path)


# ----------------------------------------------------------------- directory
def keypair():
    import hashlib
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, {hashlib.sha256(public_bytes).hexdigest()[:16]: public_bytes}


def sample_states():
    return [
        EndpointState(endpoint=endpoint("a.cipig.net"), availability=Availability.REACHABLE,
                      security=Security.SAFE, operator_group="CIPIG", last_success=100),
        EndpointState(endpoint=endpoint("legacy.example.org"),
                      availability=Availability.REACHABLE,
                      security=Security.BACKEND_MISSING, operator_group="LEGACY"),
    ]


def test_directory_is_signed_and_verifies():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    body = build_directory(sample_states(), directory_version=3)
    verified = verify_directory(sign_directory(body, private_key, key_id=key_id),
                                trusted)
    assert verified["directoryVersion"] == 3
    assert len(verified["servers"]) == 2


def test_directory_states_plainly_that_it_is_not_trust():
    body = build_directory(sample_states(), directory_version=1)
    assert "independently verify" in body["note"]


def test_tampered_directory_is_refused():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    document = sign_directory(build_directory(sample_states(), directory_version=1),
                              private_key, key_id=key_id)
    document["directory"]["servers"][1]["security"] = "SAFE"
    with pytest.raises(DirectoryError, match="does not verify"):
        verify_directory(document, trusted)


def test_directory_rollback_is_refused():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    document = sign_directory(build_directory(sample_states(), directory_version=2),
                              private_key, key_id=key_id)
    with pytest.raises(DirectoryError, match="rollback"):
        verify_directory(document, trusted, minimum_version=5)


def test_expired_directory_is_refused():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    body = build_directory(sample_states(), directory_version=1, valid_for_hours=1)
    document = sign_directory(body, private_key, key_id=key_id)
    future = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=5)
    with pytest.raises(DirectoryError, match="expired"):
        verify_directory(document, trusted, now=future)


def test_unknown_directory_signing_key_is_refused():
    private_key, _trusted = keypair()
    _other_key, other_trusted = keypair()
    document = sign_directory(build_directory(sample_states(), directory_version=1),
                              private_key, key_id="deadbeefdeadbeef")
    with pytest.raises(DirectoryError, match="unknown key id"):
        verify_directory(document, other_trusted)


def test_duplicate_directory_entries_are_refused():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    body = build_directory(sample_states(), directory_version=1)
    body["servers"].append(dict(body["servers"][0]))
    with pytest.raises(DirectoryError, match="duplicate"):
        verify_directory(sign_directory(body, private_key, key_id=key_id), trusted)


def test_unknown_security_state_is_refused():
    private_key, trusted = keypair()
    key_id = next(iter(trusted))
    body = build_directory(sample_states(), directory_version=1)
    body["servers"][0]["security"] = "TOTALLY_FINE"
    with pytest.raises(DirectoryError, match="unknown security state"):
        verify_directory(sign_directory(body, private_key, key_id=key_id), trusted)


def test_candidates_include_entries_the_directory_does_not_call_safe():
    body = build_directory(sample_states(), directory_version=1)
    candidates = candidates_from_directory(body)
    hints = {item["hostname"]: item["hint"] for item in candidates}
    assert hints["legacy.example.org"] == "BACKEND_MISSING"
    assert hints["a.cipig.net"] == "SAFE"


def test_store_migrates_schema_v3_database_to_v4(tmp_path):
    """A database created before chain_conflicts existed (schema v3, the
    SRV-02 policy_state release) must upgrade cleanly and gain the new
    table, without disturbing what it already had."""
    path = str(tmp_path / "network-observer.sqlite3")
    store = Store(path)
    store.record_policy_version(5)
    store.close()

    import sqlite3
    connection = sqlite3.connect(path)
    connection.execute("DROP TABLE chain_conflicts")
    connection.execute("UPDATE schema_version SET version = 3")
    connection.commit()
    connection.close()

    reopened = Store(path)
    try:
        assert reopened.load_minimum_policy_version() == 5
        assert reopened.conflict_confirmations("ANY-GROUP") == 0
        assert reopened.record_conflict("ANY-GROUP", now=100) == 1
    finally:
        reopened.close()


def test_store_conflict_confirmations_increment_and_clear(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        assert store.conflict_confirmations("GROUP-A") == 0
        assert store.record_conflict("GROUP-A", now=100) == 1
        assert store.record_conflict("GROUP-A", now=200) == 2
        assert store.record_conflict("GROUP-A", now=300) == 3
        assert store.conflict_confirmations("GROUP-A") == 3
        store.clear_conflict("GROUP-A")
        assert store.conflict_confirmations("GROUP-A") == 0
        # Clearing a group with no row is a harmless no-op.
        store.clear_conflict("GROUP-B")
    finally:
        store.close()


def test_store_prunes_stale_conflict_confirmations(tmp_path):
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        store.record_conflict("STALE", now=0)
        store.record_conflict("FRESH", now=9_000_000)
        store.prune(keep_observation_days=7, now=9_000_000)
        assert store.conflict_confirmations("STALE") == 0
        assert store.conflict_confirmations("FRESH") == 1
    finally:
        store.close()
