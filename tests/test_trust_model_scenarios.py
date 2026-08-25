# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""End-to-end trust-model scenarios (A-J).

These pin down the four distinct trust states the discovery pipeline must
never collapse into one another:

    DISCOVERED            an endpoint answered a probe at all
    CAPABILITY_SUPPORTED  it answers server.ravencoin_backend
    BACKEND_VERIFIED      its self-reported Core build matches a certified
                           policy entry and its chain evidence was actually
                           compared and agreed
    TRUSTED_BY_OPERATOR   an operator's own configuration decided to trust it

Most of the underlying behaviour is already covered in depth by
test_network_observer_safe_promotion.py,
test_network_observer_safe_promotion_r2.py,
test_network_observer_chain_evidence.py,
test_network_observer_policy_verification.py,
test_network_observer_operator_diversity.py and
test_network_observer.py (directory signing).
This file adds only the scenarios not already exercised elsewhere (scenario
J), and otherwise calls the real classify.py/ravencoin_backend.py/
directory.py primitives directly so the A-J mapping is explicit in one
place rather than scattered.
"""

import datetime

import pytest

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from electrumx.server.ravencoin_backend import BackendIdentity, IdentityEvidence
from network_observer.classify import ChainObservation, classify_backend, compare_chains, is_corroborated
from network_observer.directory import (
    build_directory, sign_directory, verify_directory,
)
from network_observer.model import Availability, EndpointId, EndpointState, Security, Thresholds, Transport

CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
CERTIFIED_POLICY = {
    "releases": [{
        "repository": "RavenProject/Ravencoin", "commit": CERTIFIED_COMMIT,
        "status": "KNOWN_SAFE",
    }]
}


def _endpoint(host):
    return EndpointId(host, 50002, Transport.TCP)


def _compatible_flags(**overrides):
    flags = {
        "coreSafe": True, "backendSynchronized": True,
        "kawpowHeightValidation": True, "checkpoint4487775": True,
    }
    flags.update(overrides)
    return flags


# A: valid seed, reachable, no backend RPC at all -> DISCOVERED only, never
# elevated to a security verdict better than BACKEND_MISSING.
def test_a_reachable_with_no_backend_report_is_discovered_not_verified():
    security, reason = classify_backend(None, CERTIFIED_POLICY)
    assert security is Security.BACKEND_MISSING
    assert "does not implement" in reason


# B: backend answers, but the Core build it reports is not a certified
# release (e.g. an uncertified pre-4.8.0 build) -> rejected, not SAFE.
def test_b_uncertified_core_build_is_not_elevated():
    backend = {
        "backend": {"network": "main", "version": "4.7.0",
                    "identity": {"sourceRepository": "RavenProject/Ravencoin",
                                 "sourceCommit": "a" * 40}},
        "compatibility": _compatible_flags(),
    }
    security, reason = classify_backend(backend, CERTIFIED_POLICY)
    assert security is Security.UNSAFE
    assert "known unsafe" in reason


# C: Core >= 4.8.0 but the backend only reports VERSION_ONLY identity
# evidence (no sourceRepository/sourceCommit) -> must NOT be elevated to
# BUILD_IDENTITY_VERIFIED, and classify_backend must not certify it either.
def test_c_version_only_identity_is_never_self_elevated():
    identity = BackendIdentity.from_config()
    assert identity.evidence == IdentityEvidence.VERSION_ONLY

    backend = {
        "backend": {"network": "main", "version": "4.8.0", "identity": {}},
        "compatibility": _compatible_flags(),
    }
    security, reason = classify_backend(backend, CERTIFIED_POLICY)
    assert security is Security.UNREVIEWED_CORE
    assert "does not report which Core build" in reason


# D: sourceRepository is not RavenProject/Ravencoin -> not eligible to be
# certified, regardless of what commit or version it claims.
def test_d_non_ravenproject_repository_is_not_trusted():
    with pytest.raises(ValueError, match="not one of"):
        BackendIdentity.from_config(
            repository="attacker/Ravencoin", commit="a" * 40)

    backend = {
        "backend": {"network": "main", "version": "4.8.0",
                    "identity": {"sourceRepository": "attacker/Ravencoin",
                                 "sourceCommit": CERTIFIED_COMMIT}},
        "compatibility": _compatible_flags(),
    }
    security, _reason = classify_backend(backend, CERTIFIED_POLICY)
    assert security is Security.UNREVIEWED_CORE


# E: two independent groups disagree about the chain at a shared height ->
# fail closed as a conflict, never resolved by picking the higher one.
def test_e_checkpoint_mismatch_fails_closed():
    a = ChainObservation(endpoint=_endpoint("a.example"), height=100,
                         tip_hash="tip", operator_group="A")
    b = ChainObservation(endpoint=_endpoint("b.example"), height=100,
                         tip_hash="different-tip", operator_group="B")
    verdict = compare_chains([a, b])
    assert verdict.status in ("CONFLICT_SUSPECTED", "CHAIN_CONFLICT")
    assert not is_corroborated(verdict, reference_supplied=False)


# F: backend reports the wrong network -> UNSAFE, fail closed.
def test_f_wrong_network_fails_closed():
    backend = {
        "backend": {"network": "test", "version": "4.8.0",
                    "identity": {"sourceRepository": "RavenProject/Ravencoin",
                                 "sourceCommit": CERTIFIED_COMMIT}},
        "compatibility": _compatible_flags(),
    }
    security, reason = classify_backend(backend, CERTIFIED_POLICY)
    assert security is Security.UNSAFE
    assert "network" in reason


# G: backend otherwise certified but reports it is not synchronized (still
# in IBD) -> never elevated past UNVERIFIED, never a healthy trusted
# backend.
def test_g_unsynchronized_backend_is_not_selected_as_safe():
    backend = {
        "backend": {"network": "main", "version": "4.8.0",
                    "identity": {"sourceRepository": "RavenProject/Ravencoin",
                                 "sourceCommit": CERTIFIED_COMMIT}},
        "compatibility": _compatible_flags(backendSynchronized=False),
    }
    security, reason = classify_backend(backend, CERTIFIED_POLICY)
    assert security is Security.UNVERIFIED
    assert "backendSynchronized" in reason


# H: given a faster-but-unverified peer and a slower-but-verified peer,
# policy must be able to prefer the verified one - a height strictly ahead
# of the corroborated anchor is never itself corroboration.
def test_h_policy_can_prefer_verified_over_merely_faster():
    reference = ChainObservation(endpoint=_endpoint("ref"), height=100,
                                 tip_hash="tip-100")
    verified_peer = ChainObservation(endpoint=_endpoint("slow.example"), height=100,
                                     tip_hash="tip-100", operator_group="HONEST")
    faster_unverified = ChainObservation(endpoint=_endpoint("fast.example"), height=105,
                                         tip_hash="tip-105-self-reported",
                                         operator_group="UNVERIFIED-OP")
    verdict = compare_chains([verified_peer, faster_unverified], reference=reference)
    assert "HONEST" in verdict.verified_groups
    assert "UNVERIFIED-OP" not in verdict.verified_groups


# I: a peer entry is manipulated inside an otherwise-validly-signed
# discovery document -> signature verification must fail.
def test_i_tampered_entry_inside_signed_directory_is_rejected():
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    key_id = "test-key"
    states = [EndpointState(endpoint=_endpoint("a.example"),
                            availability=Availability.REACHABLE,
                            security=Security.BACKEND_MISSING,
                            operator_group="A")]
    document = sign_directory(build_directory(states, directory_version=1),
                              private_key, key_id=key_id)
    document["directory"]["servers"][0]["security"] = Security.SAFE.value
    from network_observer.directory import DirectoryError
    with pytest.raises(DirectoryError, match="does not verify"):
        verify_directory(document, {key_id: public_bytes})


# J: a directory entry carries a field this code does not know about (a
# capability added by a newer publisher) -> verification must remain
# backward compatible and must not crash or reject the document outright.
def test_j_unknown_field_in_directory_entry_is_tolerated():
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    key_id = "test-key"
    states = [EndpointState(endpoint=_endpoint("a.example"),
                            availability=Availability.REACHABLE,
                            security=Security.BACKEND_MISSING,
                            operator_group="A")]
    body = build_directory(states, directory_version=1)
    body["servers"][0]["futureCapability"] = "quantum-proof-signatures"
    document = sign_directory(body, private_key, key_id=key_id)
    verified = verify_directory(document, {key_id: public_bytes})
    assert verified["servers"][0]["futureCapability"] == "quantum-proof-signatures"
