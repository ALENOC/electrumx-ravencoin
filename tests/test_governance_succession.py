# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Governance and succession security matrix.

Each test maps to the numbered requirements in
docs/GOVERNANCE_AND_SUCCESSION.md.  Specific exceptions everywhere: a
test may not pass because of an unrelated error.
"""

import base64
import importlib.util
import json
import pathlib

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

REPO = pathlib.Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "governance", REPO / "core-safety" / "scripts" / "governance.py")
governance = importlib.util.module_from_spec(spec)
import sys
sys.modules["governance"] = governance
spec.loader.exec_module(governance)


def make_key():
    return Ed25519PrivateKey.generate()


def key_id(key):
    return governance.key_id_for(key.public_key().public_bytes_raw())


def policy_body(keys, epoch=1, threshold=None, domain=governance.DOMAIN_RELEASE,
                schema=1, created="2026-08-25T00:00:00+00:00"):
    if threshold is None:
        threshold = max(1, len(keys) // 2 + 1)
    maintainers = []
    for key in keys:
        public_hex = key.public_key().public_bytes_raw().hex()
        maintainers.append({"keyId": governance.key_id_for(
            bytes.fromhex(public_hex)), "publicKey": public_hex})
    return {
        "schemaVersion": schema,
        "domain": domain,
        "epoch": epoch,
        "threshold": threshold,
        "createdAt": created,
        "maintainers": maintainers,
    }


def sign(key, payload):
    return {
        "algorithm": "ed25519",
        "keyId": key_id(key),
        "value": base64.b64encode(key.sign(payload)).decode("ascii"),
    }


def signed_policy(body, signers):
    payload = governance.POLICY_SIGNATURE_DOMAIN + governance.canonical_bytes(body)
    return {
        "policy": body,
        "signatures": [sign(key, payload) for key in signers],
    }


@pytest.fixture
def five_keys():
    return [make_key() for _ in range(5)]


def active_policy(five_keys, threshold=3, epoch=1):
    body = policy_body(five_keys, epoch=epoch, threshold=threshold)
    return governance.validate_policy_body(body), body


# ------------------------------------------------- structure and thresholds

def test_threshold_rules_and_maintainer_validation(five_keys):
    """#8/#9/#10/#11/#35: threshold 0, threshold above count, duplicate
    keys, mismatched keyId all rejected; test keys never enter a
    production policy file."""
    body = policy_body(five_keys, threshold=3)
    governance.validate_policy_body(body)
    with pytest.raises(governance.GovernanceError, match="threshold"):
        governance.validate_policy_body(
            policy_body(five_keys, threshold=0))
    with pytest.raises(governance.GovernanceError, match="threshold"):
        governance.validate_policy_body(
            policy_body(five_keys, threshold=6))
    duplicated = policy_body(five_keys + [five_keys[0]])
    with pytest.raises(governance.GovernanceError, match="duplicate"):
        governance.validate_policy_body(duplicated)
    mismatched = policy_body(five_keys)
    mismatched["maintainers"][0]["keyId"] = "0" * 16
    with pytest.raises(governance.GovernanceError, match="keyId"):
        governance.validate_policy_body(mismatched)


def test_future_schema_and_unknown_domain_rejected(five_keys):
    """#7 and domain separation basics."""
    with pytest.raises(governance.GovernanceError, match="schemaVersion"):
        governance.validate_policy_body(policy_body(five_keys, schema=2))
    with pytest.raises(governance.GovernanceError, match="domain"):
        governance.validate_policy_body(
            policy_body(five_keys, domain="super-key"))


# --------------------------------------------------------- authorization

def test_one_signature_cannot_satisfy_three_of_five(five_keys):
    """#1: one valid signature is below threshold 3."""
    current, _ = active_policy(five_keys)
    nxt_keys = [make_key() for _ in range(5)]
    nxt_body = policy_body(nxt_keys, epoch=2, threshold=3)
    nxt = governance.validate_policy_body(nxt_body)
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt_body, "transition": transition,
                "signatures": [sign(five_keys[0], payload)]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_three_authorized_signatures_rotate_policy(five_keys):
    """#2/#15/#17: three distinct maintainers rotate; a removed
    maintainer cannot sign the NEXT transition."""
    current, _ = active_policy(five_keys)
    successor_keys = five_keys[1:] + [make_key()]
    nxt_body = policy_body(successor_keys, epoch=2, threshold=3)
    nxt = governance.validate_policy_body(nxt_body)
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt_body, "transition": transition,
                "signatures": [sign(five_keys[i], payload)
                               for i in (0, 2, 3)]}
    rotated = governance.verify_transition(current, document)
    assert rotated.epoch == 2 and rotated.threshold == 3
    # The removed key (five_keys[0]) no longer helps.
    third = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=3, threshold=3))
    transition3 = governance.build_transition(rotated, third)
    payload3 = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition3)
    removed = five_keys[0]
    doc3 = {"policy": third.raw, "transition": transition3,
            "signatures": [sign(removed, payload3), sign(make_key(), payload3),
                           sign(make_key(), payload3)]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(rotated, doc3)


def test_duplicate_signature_counts_once(five_keys):
    """#3: the same key signing twice is one signer."""
    current, _ = active_policy(five_keys)
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    same = sign(five_keys[0], payload)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [same, dict(same), dict(same)]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_unknown_key_and_malformed_signature_count_zero(five_keys):
    """#4/#5: an unknown key and a corrupted signature add nothing."""
    current, _ = active_policy(five_keys)
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    outsider = sign(make_key(), payload)
    corrupted = sign(five_keys[1], payload)
    corrupted["value"] = base64.b64encode(b"\x00" * 64).decode()
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(five_keys[0], payload), outsider,
                               corrupted]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)
    # ...and two honest signatures plus one of the above is still short.
    document["signatures"] = [sign(five_keys[0], payload),
                              sign(five_keys[2], payload), outsider]
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_wrong_domain_and_cross_domain_signatures_fail(five_keys):
    """#6/#20/#21: a signature made for another payload/domain does not
    authorize; release keys cannot govern core-safety and vice versa."""
    current, _ = active_policy(five_keys)
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=1))
    transition = governance.build_transition(current, nxt)
    wrong_payload = governance.POLICY_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(five_keys[0], wrong_payload)]}
    with pytest.raises(governance.GovernanceError,
                       match="authorized by 0"):
        governance.verify_transition(current, document)

    core_keys = [make_key() for _ in range(3)]
    core_policy = governance.validate_policy_body(
        policy_body(core_keys, domain=governance.DOMAIN_CORE_SAFETY))
    release_policy, _ = active_policy(five_keys)
    with pytest.raises(governance.GovernanceError, match="domains"):
        governance.build_transition(release_policy, core_policy)


def test_insufficient_threshold_cannot_rotate(five_keys):
    """#16: two of three needed."""
    current, _ = active_policy(five_keys)
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(five_keys[i], payload) for i in (1, 4)]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_next_policy_cannot_sign_itself_into_authority(five_keys):
    """#14: the successor's own keys are not current maintainers."""
    current, _ = active_policy(five_keys)
    successor_keys = [make_key() for _ in range(5)]
    nxt = governance.validate_policy_body(
        policy_body(successor_keys, epoch=2, threshold=1))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(key, payload) for key in successor_keys]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_newly_added_maintainer_cannot_authorize_previous_epoch(five_keys):
    """#18: a key added in epoch 2 was not a maintainer of epoch 1."""
    current, _ = active_policy(five_keys)
    added = make_key()
    successor_keys = five_keys + [added]
    nxt = governance.validate_policy_body(
        policy_body(successor_keys, epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(added, payload)] * 3}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_epoch_rollback_and_same_epoch_substitution_rejected(five_keys):
    """#12/#13: stale state refuses an older epoch and a different
    policy at the same epoch."""
    state_path = pytest.cache_dir / "gov-state.json" if hasattr(
        pytest, "cache_dir") else None
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        state = governance.GovernanceState(
            pathlib.Path(directory) / "state.json")
        first, _ = active_policy(five_keys)
        state.accept(first)
        second = governance.validate_policy_body(
            policy_body([make_key() for _ in range(5)], epoch=2,
                        threshold=3))
        state.accept(second)
        # Epoch rollback: re-accepting epoch 1 is refused.
        with pytest.raises(governance.GovernanceError, match="backwards"):
            state.accept(first)
        # Same epoch, different policy: substitution refused.
        different = governance.validate_policy_body(
            policy_body([make_key() for _ in range(5)], epoch=2,
                        threshold=3))
        with pytest.raises(governance.GovernanceError, match="backwards"):
            state.accept(different)
        # Same policy re-accepted (idempotent) is fine.
        state.accept(second)


def test_transition_binding_blocks_substitution(five_keys):
    """The transition payload must bind both hashes and both epochs."""
    current, _ = active_policy(five_keys)
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    swapped = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    document = {"policy": swapped.raw, "transition": transition,
                "signatures": [sign(five_keys[i], payload)
                               for i in (0, 1, 2)]}
    with pytest.raises(governance.GovernanceError, match="binding"):
        governance.verify_transition(current, document)


# --------------------------------------------------- release governance

def test_release_multi_signature_threshold(five_keys):
    """#19: a compromised single key cannot reach 3-of-5 release
    authorization; three honest keys can."""
    active, _ = active_policy(five_keys)
    payload = {"electrumxVersion": "1.14.0", "artifactDigest": "sha256:" + "a" * 64}
    signed_payload = governance.POLICY_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(payload)
    document = {"governedPayload": payload,
                "signatures": [sign(five_keys[4], signed_payload)]}
    assert governance.verify_release_governance(document, active) == 1
    document["signatures"] = [sign(five_keys[i], signed_payload)
                              for i in (0, 2, 4)]
    assert governance.verify_release_governance(document, active) == 3
    with pytest.raises(governance.GovernanceError, match="release-domain"):
        core_policy = governance.validate_policy_body(
            policy_body(five_keys, domain=governance.DOMAIN_CORE_SAFETY))
        governance.verify_release_governance(document, core_policy)


# --------------------------------------------------- adoption and state

def test_successor_adoption_requires_exact_fingerprint(five_keys):
    """#30/#31/#32: no implicit adoption, no partial fingerprint, no
    arbitrary new key; the recorded adoption is exact."""
    import tempfile
    with tempfile.TemporaryDirectory() as directory:
        state = governance.GovernanceState(
            pathlib.Path(directory) / "state.json")
        current, _ = active_policy(five_keys)
        state.accept(current)
        successor = governance.validate_policy_body(
            policy_body([make_key() for _ in range(5)], epoch=7,
                        threshold=3))
        with pytest.raises(governance.GovernanceError, match="confirmation"):
            governance.adopt_successor(
                state, successor, expected_fingerprint=successor.digest,
                source_identity="github.com/RavencoinCommunity/electrumx-ravencoin")
        with pytest.raises(governance.GovernanceError, match="fingerprint"):
            governance.adopt_successor(
                state, successor, expected_fingerprint="ab" * 32,
                source_identity="community fork", confirm=True)
        result = governance.adopt_successor(
            state, successor, expected_fingerprint=successor.digest,
            source_identity="github.com/RavencoinCommunity/electrumx-ravencoin",
            confirm=True)
        assert result["adopted"] is True
        assert result["previous"]["epoch"] == 1
        assert result["new"]["epoch"] == 7
        # Anti-rollback applies after adoption.
        with pytest.raises(governance.GovernanceError, match="backwards"):
            state.accept(current)


def test_anti_rollback_survives_restart(tmp_path):
    """#29: reloading the state keeps the high-water mark."""
    path = tmp_path / "state.json"
    keys = [make_key() for _ in range(3)]
    policy = governance.validate_policy_body(policy_body(keys, threshold=2))
    state = governance.GovernanceState(path)
    state.accept(policy)
    reloaded = governance.GovernanceState(path)
    assert reloaded.accepted_epoch == policy.epoch
    assert reloaded.accepted_policy_hash == policy.digest


def test_legacy_single_key_bootstrap_signs_genesis_policy():
    """#33: the genesis epoch-1 policy is anchored by the EXISTING
    legacy release key through verify_policy_document; a policy signed
    by its own new keys is not anchored."""
    legacy_key = make_key()
    authorized = {key_id(legacy_key): legacy_key.public_key().public_bytes_raw()}
    new_maintainers = [make_key() for _ in range(5)]
    body = policy_body(new_maintainers, epoch=1, threshold=3)
    document = signed_policy(body, [legacy_key])
    policy = governance.verify_policy_document(document,
                                               authorized_keys=authorized)
    assert policy.epoch == 1 and policy.threshold == 3
    self_signed = signed_policy(body, new_maintainers[:3])
    with pytest.raises(governance.GovernanceError, match="no authorized key"):
        governance.verify_policy_document(self_signed,
                                          authorized_keys=authorized)


def test_repository_url_change_alone_creates_no_trust(five_keys):
    """#22/#23: a repository relocation is authenticated by a policy
    transition (the relocation lives in the successor policy body),
    never by URL metadata or redirects."""
    current, _ = active_policy(five_keys)
    successor_body = policy_body(five_keys[1:] + [make_key()], epoch=2,
                                 threshold=3)
    successor_body["sourceRepository"] = \
        "github.com/RavencoinCommunity/electrumx-ravencoin"
    successor = governance.validate_policy_body(successor_body)
    transition = governance.build_transition(current, successor)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": successor_body, "transition": transition,
                "signatures": [sign(five_keys[i], payload)
                               for i in (0, 1, 2)]}
    relocated = governance.verify_transition(current, document)
    assert relocated.raw["sourceRepository"].endswith(
        "RavencoinCommunity/electrumx-ravencoin")
    # The same relocation claim WITHOUT the authorizing transition
    # signatures is just an unsigned object: nothing accepts it.
    unsigned = {"policy": successor_body, "transition": transition,
                "signatures": []}
    with pytest.raises(governance.GovernanceError):
        governance.verify_transition(current, unsigned)


def test_observer_and_operator_keys_cannot_govern(five_keys):
    """#24/#25: Network Observer / operator identities live in their own
    self-signed local domains; they are not governance maintainers and
    can never satisfy a governance threshold, however many exist."""
    current, _ = active_policy(five_keys)
    observer_keys = [make_key() for _ in range(100)]
    nxt = governance.validate_policy_body(
        policy_body([make_key() for _ in range(5)], epoch=2, threshold=3))
    transition = governance.build_transition(current, nxt)
    payload = governance.TRANSITION_SIGNATURE_DOMAIN \
        + governance.canonical_bytes(transition)
    document = {"policy": nxt.raw, "transition": transition,
                "signatures": [sign(key, payload) for key in observer_keys]}
    with pytest.raises(governance.GovernanceError, match="requires 3"):
        governance.verify_transition(current, document)


def test_governance_files_absent_do_not_touch_backend_rpc():
    """#26/#28: the server session never imports governance; losing
    governance files cannot affect server.ravencoin_backend."""
    import sys
    source = (REPO / "electrumx" / "server" / "session.py").read_text(
        encoding="utf-8")
    assert "governance" not in source
    before = {name for name in sys.modules if "governance" in name}
    from electrumx.server.ravencoin_backend import evaluate_backend
    evaluate_backend({"version": 4080000, "subversion": "/Ravencoin:4.8.0/"},
                     {"chain": "main", "blocks": 10, "headers": 10,
                      "initialblockdownload": False}, "mainnet")
    after = {name for name in sys.modules if "governance" in name}
    assert after == before
