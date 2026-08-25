#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Founder-independent governance and succession for ElectrumX-RVN.

Goal: the project must be able to survive its original maintainer.  The
mechanism is explicit, signed, epoch-based, anti-rollback and
fail-closed.  There is deliberately NO inactivity escape hatch: if the
governance quorum is lost, existing nodes keep serving, the automatic
updater refuses unauthenticated successors, and adopting a new trust
root requires one explicit local operator action binding an exact
fingerprint.

Domains stay separate.  A governance policy authorizes exactly one
domain (release governance, core-safety governance, ...); keys of one
domain never grant authority in another, and the Ravencoin blockchain
remains the only consensus authority, entirely external to this file.

N-of-M is N distinct valid Ed25519 signatures from the M maintainer
keys of the active policy.  No custom threshold cryptography is
invented here on purpose.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import pathlib
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

GOVERNANCE_SCHEMA_VERSION = 1
POLICY_SIGNATURE_DOMAIN = b"RVN-ELECTRUMX-GOVERNANCE-POLICY-v1\x00"
TRANSITION_SIGNATURE_DOMAIN = b"RVN-ELECTRUMX-GOVERNANCE-TRANSITION-v1\x00"
#: Release payloads get their own domain so a maintainer signature over
#: a policy body can never be replayed as authorization of a release
#: artifact (or vice versa), even if a future payload shape collides.
RELEASE_SIGNATURE_DOMAIN = b"RVN-ELECTRUMX-GOVERNANCE-RELEASE-v1\x00"

#: The governance domains.  Release and core-safety governance are the
#: two software-trust domains; observer/operator identities are local
#: self-signed models and are NOT governed here.
DOMAIN_RELEASE = "electrumx-release"
DOMAIN_CORE_SAFETY = "electrumx-core-safety"
VALID_DOMAINS = (DOMAIN_RELEASE, DOMAIN_CORE_SAFETY)

MAX_MAINTAINERS = 32


class GovernanceError(ValueError):
    """Governance material is malformed, untrusted, stale or rolled back."""


def key_id_for(public_bytes: bytes) -> str:
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def canonical_bytes(body: Mapping) -> bytes:
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def policy_digest(body: Mapping) -> str:
    return hashlib.sha256(
        POLICY_SIGNATURE_DOMAIN + canonical_bytes(body)).hexdigest()


@dataclass(frozen=True)
class GovernancePolicy:
    """A validated governance policy for one domain."""

    domain: str
    epoch: int
    threshold: int
    maintainers: tuple  # tuple of (key_id, public_hex)
    created_at: str
    digest: str
    raw: dict


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GovernanceError(message)


def validate_policy_body(body: Mapping) -> GovernancePolicy:
    """Structural validation of a policy body.  Fail closed on: wrong
    schema, unknown domain, bad epoch/threshold, duplicate or malformed
    maintainer keys, keyId/publicKey mismatch."""
    _require(isinstance(body, Mapping), "policy body must be an object")
    _require(body.get("schemaVersion") == GOVERNANCE_SCHEMA_VERSION,
             "unsupported governance schemaVersion")
    domain = body.get("domain")
    _require(domain in VALID_DOMAINS, f"unknown governance domain {domain!r}")
    epoch = body.get("epoch")
    _require(isinstance(epoch, int) and not isinstance(epoch, bool)
             and epoch >= 1, "epoch must be a positive integer")
    threshold = body.get("threshold")
    maintainers = body.get("maintainers")
    _require(isinstance(maintainers, list) and 1 <= len(maintainers)
             <= MAX_MAINTAINERS, "maintainers must be a bounded non-empty list")
    _require(isinstance(threshold, int) and not isinstance(threshold, bool)
             and 1 <= threshold <= len(maintainers),
             "threshold must satisfy 1 <= threshold <= maintainer count")
    seen = set()
    keys = []
    for entry in maintainers:
        _require(isinstance(entry, Mapping), "maintainer entry must be an object")
        public_hex = entry.get("publicKey")
        _require(isinstance(public_hex, str) and len(public_hex) == 64,
                 "maintainer publicKey must be 32 bytes of hex")
        try:
            public_bytes = bytes.fromhex(public_hex)
        except ValueError as exc:
            raise GovernanceError("maintainer publicKey is not hex") from exc
        derived = key_id_for(public_bytes)
        _require(entry.get("keyId") == derived,
                 "maintainer keyId does not derive from its publicKey")
        _require(derived not in seen, "duplicate maintainer key")
        seen.add(derived)
        keys.append((derived, public_hex))
    created_at = body.get("createdAt")
    _require(isinstance(created_at, str) and created_at,
             "createdAt must be a string")
    return GovernancePolicy(
        domain=domain, epoch=epoch, threshold=threshold,
        maintainers=tuple(keys), created_at=created_at,
        digest=policy_digest(body), raw=dict(body))


def _verify_one_signature(payload: bytes, signature: Mapping,
                          allowed_keys: Dict[str, bytes],
                          expected_algorithm: str = "ed25519") -> Optional[str]:
    """Verify one signature entry; return its key id, or None when the
    entry is malformed or does not verify.  Malformed input never
    raises out of here: it simply counts as no signature (callers
    enforce thresholds)."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(signature, Mapping):
        return None
    if signature.get("algorithm") != expected_algorithm:
        return None
    key_id = signature.get("keyId")
    if key_id not in allowed_keys:
        return None
    try:
        raw = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception:  # noqa: BLE001
        return None
    try:
        Ed25519PublicKey.from_public_bytes(allowed_keys[key_id]).verify(
            raw, payload)
    except InvalidSignature:
        return None
    return key_id


def distinct_valid_signers(payload: bytes, signatures: Sequence,
                           allowed_keys: Dict[str, bytes]) -> int:
    """How many DISTINCT allowed keys produced a valid signature.

    Duplicate signatures from one key count once; unknown keys count
    zero; malformed entries count zero."""
    signers = set()
    for signature in signatures:
        key_id = _verify_one_signature(payload, signature, allowed_keys)
        if key_id is not None:
            signers.add(key_id)
    return len(signers)


def verify_policy_document(document: Mapping, *,
                           authorized_keys: Dict[str, bytes]) -> GovernancePolicy:
    """Verify a policy document whose authority comes from OUTSIDE the
    policy itself (the genesis bootstrap: the legacy single release key
    signs the epoch-1 policy, or an already-active policy's maintainers
    sign the next one through a transition).  A policy never authorizes
    itself: signatures from the policy's own keys are not accepted here
    unless those keys are already authorized by the caller."""
    _require(isinstance(document, Mapping), "policy document must be an object")
    body = document.get("policy")
    signatures = document.get("signatures")
    _require(isinstance(body, Mapping) and isinstance(signatures, list),
             "policy document must contain policy and signatures")
    policy = validate_policy_body(body)
    _require(len(signatures) <= MAX_MAINTAINERS * 2,
             "unreasonable signature count")
    _require(distinct_valid_signers(
        POLICY_SIGNATURE_DOMAIN + canonical_bytes(body),
        signatures, authorized_keys) >= 1,
             "no authorized key validly signed this policy")
    return policy


def build_transition(current: GovernancePolicy, nxt: GovernancePolicy) -> dict:
    """The unsigned transition payload binding an active policy to its
    successor.  Binding current hash, next hash, next epoch and domain
    prevents every substitution attack on the artifacts themselves."""
    _require(current.domain == nxt.domain, "transition crosses domains")
    _require(nxt.epoch == current.epoch + 1,
             "next policy epoch must be exactly current + 1")
    return {
        "schemaVersion": GOVERNANCE_SCHEMA_VERSION,
        "domain": current.domain,
        "fromEpoch": current.epoch,
        "toEpoch": nxt.epoch,
        "currentPolicyHash": current.digest,
        "nextPolicyHash": nxt.digest,
    }


def verify_transition(current: GovernancePolicy, candidate: Mapping,
                      *, signatures: Optional[Sequence] = None
                      ) -> GovernancePolicy:
    """Authorize a successor policy with the CURRENT policy's threshold.

    The new policy cannot sign itself into authority: only signatures
    from the current maintainers count, and exactly threshold distinct
    ones are required.  Removed maintainers stop mattering the moment
    the transition verifies; until then they are still authoritative,
    which is the price and the point of fail-closed rotation."""
    _require(isinstance(candidate, Mapping), "transition document must be an object")
    nxt = validate_policy_body(candidate.get("policy"))
    transition = build_transition(current, nxt)
    _require(candidate.get("transition") == transition,
             "transition binding does not match the two policies")
    signatures = candidate.get("signatures", signatures or [])
    current_keys = {key_id: bytes.fromhex(public_hex)
                    for key_id, public_hex in current.maintainers}
    count = distinct_valid_signers(
        TRANSITION_SIGNATURE_DOMAIN + canonical_bytes(transition),
        signatures, current_keys)
    _require(count >= current.threshold,
             f"transition authorized by {count} maintainer(s); the active "
             f"policy requires {current.threshold}")
    return nxt


def verify_release_governance(document: Mapping, active: GovernancePolicy,
                              ) -> int:
    """Verify a release artifact's multi-signature block against the
    ACTIVE release governance policy.  Returns the number of distinct
    valid maintainer signatures; callers compare against
    ``active.threshold``.  Only the release domain belongs here: a
    core-safety policy key signing a release counts zero."""
    _require(active.domain == DOMAIN_RELEASE,
             "release verification requires a release-domain policy")
    signatures = document.get("signatures")
    _require(isinstance(signatures, list), "document must carry signatures[]")
    keys = {key_id: bytes.fromhex(public_hex)
            for key_id, public_hex in active.maintainers}
    payload = document.get("governedPayload")
    _require(payload is not None, "document must carry governedPayload")
    return distinct_valid_signers(
        RELEASE_SIGNATURE_DOMAIN + canonical_bytes(payload), signatures, keys)


# ------------------------------------------------------------- local state

class GovernanceState:
    """Persisted, monotonic governance acceptance for one node.

    Backed by a small JSON file whose ownership/permissions the caller
    controls.  The high-water epoch and policy hash only move forward:
    an older epoch, or a different policy at the same epoch, is refused
    (rollback and substitution resistance).  Deleting the file does not
    grant anything: it removes the ability to accept transitions, which
    then requires the explicit adoption path with a full fingerprint.
    """

    def __init__(self, path):
        self.path = pathlib.Path(path)
        self._data = {"schemaVersion": 1, "acceptedEpoch": 0,
                      "acceptedPolicyHash": None, "history": []}
        if self.path.exists():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            _require(loaded.get("schemaVersion") == 1,
                     "unsupported governance state schema")
            _require(isinstance(loaded.get("acceptedEpoch", 0), int),
                     "acceptedEpoch must be an integer")
            self._data = loaded

    @property
    def accepted_epoch(self) -> int:
        return int(self._data.get("acceptedEpoch", 0))

    @property
    def accepted_policy_hash(self) -> Optional[str]:
        return self._data.get("acceptedPolicyHash")

    def accept(self, policy: GovernancePolicy, *, note: str = "") -> None:
        """Accept a policy only if it strictly advances the high water:
        a newer epoch, or the same epoch with the identical hash."""
        _require(policy.epoch > self.accepted_epoch or (
            policy.epoch == self.accepted_epoch
            and policy.digest == self.accepted_policy_hash),
            f"refusing to move governance backwards or sideways: accepted "
            f"epoch {self.accepted_epoch} hash {self.accepted_policy_hash}, "
            f"candidate epoch {policy.epoch} hash {policy.digest}")
        self._data["acceptedEpoch"] = policy.epoch
        self._data["acceptedPolicyHash"] = policy.digest
        self._data.setdefault("history", []).append(
            {"epoch": policy.epoch, "hash": policy.digest,
             "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
             "note": note})
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic replace: a crash mid-write must never leave a truncated
        # state file, because a corrupt file takes the node's ability to
        # accept transitions with it (fail closed, but unnecessarily).
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(
            json.dumps(self._data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        import os
        os.replace(temporary, self.path)


def adopt_successor(state: GovernanceState, policy: GovernancePolicy,
                    *, expected_fingerprint: str,
                    source_identity: str,
                    confirm: bool = False) -> dict:
    """EXPLICIT, local-only successor adoption (fork adoption).

    Never triggered remotely, never by the Network Observer, never by
    endpoint majorities.  Requires the operator to supply the COMPLETE
    expected governance fingerprint of the policy being adopted, so
    automation cannot say "trust whatever is in front of me".  The
    adopted policy must be structurally valid and its fingerprint must
    match exactly; the event is recorded and anti-rollback applies
    afterwards through the normal state.
    """
    _require(confirm is True, "adoption requires explicit confirmation")
    _require(isinstance(expected_fingerprint, str)
             and len(expected_fingerprint) == 64,
             "expected_fingerprint must be a full 64-hex policy digest")
    _require(policy.digest == expected_fingerprint,
             f"policy fingerprint {policy.digest} does not match the "
             f"expected {expected_fingerprint}: refusing to adopt")
    previous = {"epoch": state.accepted_epoch,
                "hash": state.accepted_policy_hash}
    state.accept(policy, note=f"explicit successor adoption from "
                              f"{source_identity}")
    return {
        "adopted": True,
        "previous": previous,
        "new": {"epoch": policy.epoch, "hash": policy.digest,
                "domain": policy.domain,
                "threshold": policy.threshold,
                "maintainers": len(policy.maintainers)},
        "source": source_identity,
    }
