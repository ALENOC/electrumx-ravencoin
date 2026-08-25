# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for SRV-05: an endpoint with no known operator identity
must never count toward independent-operator diversity, no matter how many
such hostnames appear. count_independent_operators() and known_group_count()
(the latter feeding the SRV-04 SAFE-promotion gate in cli.py) must agree on
this, and pure peer-gossip discovery must never itself grant an endpoint an
operator identity.
"""

from network_observer.classify import (
    ChainObservation, count_independent_operators, count_unknown_safe_endpoints,
    independent_groups, known_group_count,
)
from network_observer.model import DiscoverySource, EndpointId, EndpointState, Security, Transport
from network_observer.store import Store


def endpoint(host, port=50002, transport=Transport.TLS):
    return EndpointId(host, port, transport)


def test_known_same_operator_many_endpoints_is_one_group():
    states = [
        EndpointState(endpoint=endpoint("a.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("b.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("c.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
    ]
    counts = count_independent_operators(states)
    assert counts == {"ALENOC": 3}
    assert len(counts) == 1


def test_unknown_hostnames_are_not_counted_as_independent_identities():
    """Three SAFE endpoints with no known operator must not display as three
    independent operator groups: count_independent_operators() must exclude
    them, and known_group_count() (the SRV-04 promotion gate's own count)
    must agree."""
    states = [
        EndpointState(endpoint=endpoint(f"unknown{i}.example.org"),
                      security=Security.SAFE, operator_group=None)
        for i in range(3)
    ]
    counts = count_independent_operators(states)
    assert counts == {}
    assert count_unknown_safe_endpoints(states) == 3

    groups = independent_groups([
        ChainObservation(endpoint=state.endpoint, height=100, tip_hash="a" * 64,
                         operator_group=None)
        for state in states
    ])
    assert len(groups) == 3          # still three distinct raw groups for conflict detection...
    assert known_group_count(groups) == 0  # ...but zero count toward attested diversity


def test_alenoc_and_a_known_independent_operator_is_two():
    states = [
        EndpointState(endpoint=endpoint("a.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("b.alenoc.example"),
                      security=Security.SAFE, operator_group="ALENOC"),
        EndpointState(endpoint=endpoint("a.cipig.net"),
                      security=Security.SAFE, operator_group="CIPIG"),
    ]
    counts = count_independent_operators(states)
    assert counts == {"ALENOC": 2, "CIPIG": 1}
    assert len(counts) == 2


def test_known_and_unknown_mixed_only_counts_the_known_one():
    states = [
        EndpointState(endpoint=endpoint("a.cipig.net"),
                      security=Security.SAFE, operator_group="CIPIG"),
        EndpointState(endpoint=endpoint("shadow.example.org"),
                      security=Security.SAFE, operator_group=None),
    ]
    counts = count_independent_operators(states)
    assert counts == {"CIPIG": 1}
    assert count_unknown_safe_endpoints(states) == 1


def test_pure_gossip_discovery_never_grants_an_operator_identity(tmp_path):
    """Directory/peer-gossip discovery metadata alone must not manufacture
    operator independence: an endpoint learned only via record_peer_edge
    (never listed in the operator's own seeds or registry file) must come
    back with operator_group unset."""
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        source = endpoint("known.example.org")
        gossiped = endpoint("stranger.example.org")
        store.upsert_endpoint(source, source=DiscoverySource.BOOTSTRAP,
                              operator_group="OPERATOR-A")
        store.record_peer_edge(source, gossiped)

        state = store.load_state(gossiped)
        assert state is not None
        assert state.operator_group is None
    finally:
        store.close()


def test_registry_label_alone_is_the_trust_boundary_not_a_bug():
    """Residual, documented limitation: operator_group values themselves
    always come from this operator's own local seeds/registry files, which
    are trusted by construction (see docs/electrum-monitor.md). Two
    completely unrelated hostnames the operator's own registry labels with
    two distinct group names ARE counted as two independent operators - the
    monitor has no way to verify that assertion beyond the operator's local
    configuration, and does not claim to."""
    states = [
        EndpointState(endpoint=endpoint("a.example.org"),
                      security=Security.SAFE, operator_group="CLAIMED-OPERATOR-1"),
        EndpointState(endpoint=endpoint("b.example.org"),
                      security=Security.SAFE, operator_group="CLAIMED-OPERATOR-2"),
    ]
    counts = count_independent_operators(states)
    assert len(counts) == 2
