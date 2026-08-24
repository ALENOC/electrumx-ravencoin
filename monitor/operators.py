# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Cryptographic operator identity (Ed25519 operator declarations).

The existing operatorGroup registry (monitor/config/operator-registry.json)
groups endpoints by configured name and is kept intact: it remains valid
with its existing semantics, and this module adds a *preferred*
cryptographic layer on top.  An operator key signs a declaration binding
one operator identity to a set of endpoints; endpoints signed by the
same accepted key remain ONE operator for quorum, exactly as configured
groups already are.

Trust semantics, stated plainly because they are the whole point:

* a valid signature proves stable self-consistency, nothing more;
* any attacker can generate unlimited Ed25519 keys, so SELF_SIGNED
  identities never count as independent quorum just because their keys
  differ;
* only a key id listed in the local attestation policy is
  REGISTRY_ATTESTED, and only attested identities ever join
  operator-diversity counts;
* precedence when several sources claim an endpoint:
  REGISTRY_ATTESTED declaration > configured operatorGroup > UNKNOWN-*
  placeholder.  The old registry is never silently invalidated; a
  configured group still groups its endpoints when no accepted
  declaration covers them.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Mapping, Optional

DECLARATION_SCHEMA_VERSION = 1
SIGNATURE_DOMAIN = b"ALENOC-RVN-OPERATOR-DECLARATION-v1\x00"

#: operatorGroup names are security-relevant identifiers; keep them tight.
_GROUP_NAME = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,31}$")
_ENDPOINT = re.compile(
    r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?:(1|s|t)?[0-9]{1,5}$")


class OperatorIdentityError(ValueError):
    """The declaration is malformed, unverifiable, expired or rolled back."""


class IdentityState(Enum):
    UNKNOWN = "UNKNOWN"
    SELF_SIGNED = "SELF_SIGNED"
    REGISTRY_ATTESTED = "REGISTRY_ATTESTED"
    INVALID = "INVALID"
    EXPIRED = "EXPIRED"


def operator_key_id(public_bytes: bytes) -> str:
    """Same stable derivation convention as the signed policy keys."""
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def canonical_bytes(body: Mapping) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


@dataclass(frozen=True)
class OperatorDeclaration:
    """A verified operator declaration plus its trust state."""

    operator_name: str
    operator_group: str
    operator_key_id: str
    public_key_hex: str
    sequence: int
    endpoints: tuple
    valid_from: str
    expires_at: str
    state: IdentityState


def _parse_time(value: object, label: str) -> datetime.datetime:
    if not isinstance(value, str):
        raise OperatorIdentityError(f"{label} must be an ISO-8601 string")
    try:
        parsed = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise OperatorIdentityError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


def verify_operator_declaration(document: Mapping,
                                attested_key_ids: Mapping[str, str],
                                *, now: Optional[datetime.datetime] = None,
                                sequence_high_water: Mapping[str, int] = {},
                                ) -> OperatorDeclaration:
    """Verify one signed operator declaration.

    ``attested_key_ids`` maps operator key id to the operator group this
    deployment's policy attests for it; it comes from local configuration
    only.  ``sequence_high_water`` carries the highest previously
    accepted sequence per key id, for rollback refusal.

    Rejections (all fail closed): malformed document, unknown algorithm,
    bad base64, wrong signature, inconsistent key id, unsupported schema,
    bad group name, duplicate endpoints, non-positive sequence, sequence
    at or below the high-water mark, and validity windows that do not
    cover the verification time.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(document, Mapping):
        raise OperatorIdentityError("declaration must be an object")
    body = document.get("declaration")
    signature = document.get("signature")
    if not isinstance(body, Mapping) or not isinstance(signature, Mapping):
        raise OperatorIdentityError(
            "declaration must contain declaration and signature objects")
    if signature.get("algorithm") != "ed25519":
        raise OperatorIdentityError("unsupported signature algorithm")

    public_hex = body.get("publicKey")
    if not isinstance(public_hex, str) or len(public_hex) != 64:
        raise OperatorIdentityError("publicKey must be 32 bytes of hex")
    try:
        public_bytes = bytes.fromhex(public_hex)
    except ValueError as exc:
        raise OperatorIdentityError("publicKey is not valid hex") from exc
    derived_key_id = operator_key_id(public_bytes)
    if body.get("operatorKeyId") != derived_key_id:
        raise OperatorIdentityError(
            "operatorKeyId does not derive from the declared publicKey")
    if signature.get("keyId") != derived_key_id:
        raise OperatorIdentityError("signature key id does not match the body")

    try:
        raw = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise OperatorIdentityError("signature is not valid base64") from exc
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            raw, canonical_bytes(body))
    except InvalidSignature as exc:
        raise OperatorIdentityError("declaration signature does not verify") from exc

    if body.get("schemaVersion") != DECLARATION_SCHEMA_VERSION:
        raise OperatorIdentityError(
            f"unsupported declaration schemaVersion {body.get('schemaVersion')!r}")
    name = body.get("operatorName")
    group = body.get("operatorGroup")
    if not isinstance(name, str) or not name or len(name) > 64:
        raise OperatorIdentityError("operatorName must be 1..64 characters")
    if not isinstance(group, str) or not _GROUP_NAME.match(group):
        raise OperatorIdentityError(
            "operatorGroup must be 1..32 characters of A-Z, 0-9, _ or -, "
            "starting with a letter or digit")
    sequence = body.get("sequence")
    if not isinstance(sequence, int) or isinstance(sequence, bool) \
            or sequence < 1:
        raise OperatorIdentityError("sequence must be a positive integer")
    high_water = int(sequence_high_water.get(derived_key_id, 0))
    if sequence <= high_water:
        raise OperatorIdentityError(
            f"declaration sequence {sequence} is not above the accepted "
            f"high-water mark {high_water}; refusing a rollback")

    endpoints = body.get("endpoints")
    if not isinstance(endpoints, list) or not endpoints or len(endpoints) > 256:
        raise OperatorIdentityError("endpoints must be a non-empty bounded list")
    normalized = []
    seen = set()
    for value in endpoints:
        if not isinstance(value, str) or ":" not in value:
            raise OperatorIdentityError(f"endpoint {value!r} is malformed")
        host, port_text = value.rsplit(":", 1)
        host = host.lower().strip().rstrip(".")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise OperatorIdentityError(
                f"endpoint {value!r} has a non-integer port") from exc
        if not 1 <= port <= 65535 or not host or len(host) > 253:
            raise OperatorIdentityError(f"endpoint {value!r} is out of range")
        identity = f"{host}:{port}"
        if identity in seen:
            raise OperatorIdentityError(f"duplicate endpoint {identity!r}")
        seen.add(identity)
        normalized.append(identity)

    current = now or datetime.datetime.now(datetime.timezone.utc)
    valid_from = _parse_time(body.get("validFrom"), "validFrom")
    expires_at = _parse_time(body.get("expiresAt"), "expiresAt")
    if expires_at <= valid_from:
        raise OperatorIdentityError("expiresAt must be after validFrom")
    if current < valid_from:
        raise OperatorIdentityError("declaration is not valid yet")
    if current > expires_at:
        raise OperatorIdentityError("declaration has expired")

    attested_group = attested_key_ids.get(derived_key_id)
    if attested_group is None:
        state = IdentityState.SELF_SIGNED
    elif attested_group != group:
        # The policy attests this key for a DIFFERENT group than the
        # declaration claims: that is a configuration conflict, treated
        # as untrusted rather than silently resolved either way.
        state = IdentityState.SELF_SIGNED
    else:
        state = IdentityState.REGISTRY_ATTESTED

    return OperatorDeclaration(
        operator_name=name,
        operator_group=group,
        operator_key_id=derived_key_id,
        public_key_hex=public_hex,
        sequence=sequence,
        endpoints=tuple(normalized),
        valid_from=body["validFrom"],
        expires_at=body["expiresAt"],
        state=state)


def resolve_operator_group(endpoint_identity: str, *,
                           accepted_declarations: List[OperatorDeclaration],
                           configured_group: Optional[str] = None,
                           hostname: str = "") -> tuple:
    """Resolve the operator group for one endpoint, with precedence.

    Returns ``(group_key, source)`` where source is one of
    ``"attested"``, ``"configured"``, ``"unknown"``.  Endpoint identity
    here is ``host:port`` with any transport suffix folded away, since
    one operator binding host:port covers both its TLS and TCP services.
    """
    for declaration in accepted_declarations:
        if declaration.state is not IdentityState.REGISTRY_ATTESTED:
            continue
        if endpoint_identity in declaration.endpoints:
            return declaration.operator_group, "attested"
    if configured_group:
        return configured_group, "configured"
    return f"UNKNOWN-{hostname}" if hostname else "UNKNOWN-", "unknown"
