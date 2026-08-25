# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""The published server directory.

This is a **discovery hint and nothing more**.  A wallet that reads it still has
to run every check itself: the directory saying SAFE is one party's opinion,
formed at a moment in the past, about a server the wallet is about to talk to
directly.  Signing it prevents tampering in transit; it does not turn an opinion
into proof.

The signature, monotonic version and expiry exist so a stale or forged snapshot
cannot be replayed, not so a wallet can skip validation.
"""

from __future__ import annotations

import base64
import datetime
import json
from typing import Dict, Iterable, Mapping, Optional

from .model import Availability, Security

SCHEMA_VERSION = 1
SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUM-DIRECTORY-v1\x00"


class DirectoryError(ValueError):
    """The directory document is unusable."""


def canonical_bytes(body: Mapping) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def build_directory(states: Iterable, *, directory_version: int,
                    generated_at: Optional[str] = None,
                    valid_for_hours: int = 24,
                    chain_status: Optional[Mapping] = None) -> dict:
    """Render the current classification into a compact snapshot.

    Only what a wallet needs to decide where to try first.  No fingerprints, no
    internal identifiers, no operator contact details.
    """
    if not isinstance(directory_version, int) or directory_version < 1:
        raise DirectoryError("directoryVersion must be a positive integer")
    now = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    servers = []
    for state in states:
        entry = {
            "hostname": state.endpoint.hostname,
            "port": state.endpoint.port,
            "transport": state.endpoint.transport.value,
            "availability": state.availability.value,
            "security": state.security.value,
            "operatorGroup": state.operator_group or "UNKNOWN",
        }
        if state.last_success:
            entry["lastSeen"] = int(state.last_success)
        if chain_status and str(state.endpoint) in chain_status:
            observed = chain_status[str(state.endpoint)]
            if observed.get("height") is not None:
                entry["height"] = observed["height"]
            if observed.get("status"):
                entry["chainStatus"] = observed["status"]
        servers.append(entry)

    servers.sort(key=lambda item: (item["hostname"], item["port"]))
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "directoryVersion": directory_version,
        "generatedAt": generated_at or now.isoformat(),
        "expiresAt": (now + datetime.timedelta(hours=valid_for_hours)).isoformat(),
        "note": "Discovery hint only. A client must independently verify every "
                "endpoint before using it, including servers listed as SAFE.",
        "servers": servers,
    }
    return body


def sign_directory(body: Mapping, private_key, *, key_id: str) -> dict:
    signature = private_key.sign(canonical_bytes(body))
    return {
        "directory": dict(body),
        "signature": {
            "algorithm": "ed25519",
            "keyId": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_directory(document: Mapping, trusted_keys: Dict[str, bytes], *,
                     minimum_version: int = 0,
                     now: Optional[datetime.datetime] = None) -> dict:
    """Verify a signed directory.  Returns the body or raises DirectoryError."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(document, Mapping):
        raise DirectoryError("directory document must be an object")
    body = document.get("directory")
    signature = document.get("signature")
    if not isinstance(body, Mapping) or not isinstance(signature, Mapping):
        raise DirectoryError("directory document must contain directory and signature")
    if signature.get("algorithm") != "ed25519":
        raise DirectoryError("unsupported signature algorithm")
    key_id = signature.get("keyId")
    if key_id not in trusted_keys:
        raise DirectoryError(f"directory signed by unknown key id {key_id!r}")
    try:
        raw = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise DirectoryError("signature is not valid base64") from exc
    try:
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(
            raw, canonical_bytes(body))
    except InvalidSignature as exc:
        raise DirectoryError("directory signature does not verify") from exc

    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise DirectoryError("unsupported directory schemaVersion")
    version = body.get("directoryVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise DirectoryError("directoryVersion must be a positive integer")
    if version < minimum_version:
        raise DirectoryError(
            f"directory version {version} is older than the accepted "
            f"{minimum_version}; refusing a rollback")
    servers = body.get("servers")
    if not isinstance(servers, list):
        raise DirectoryError("servers must be a list")
    seen = set()
    for entry in servers:
        if not isinstance(entry, Mapping):
            raise DirectoryError("each server entry must be an object")
        for key in ("hostname", "port", "transport", "availability", "security"):
            if key not in entry:
                raise DirectoryError(f"server entry is missing {key!r}")
        identity = (entry["hostname"], entry["port"], entry["transport"])
        if identity in seen:
            raise DirectoryError(f"duplicate directory entry for {identity}")
        seen.add(identity)
        if entry["security"] not in {item.value for item in Security}:
            raise DirectoryError(f"unknown security state {entry['security']!r}")
        if entry["availability"] not in {item.value for item in Availability}:
            raise DirectoryError(f"unknown availability state {entry['availability']!r}")

    expires_at = body.get("expiresAt")
    if expires_at:
        try:
            expiry = datetime.datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise DirectoryError("expiresAt is not a valid timestamp") from exc
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        current = now or datetime.datetime.now(datetime.timezone.utc)
        if current > expiry:
            raise DirectoryError("directory snapshot has expired")
    return dict(body)


def candidates_from_directory(body: Mapping) -> list:
    """Extract connection candidates.

    Deliberately returns candidates, not approved servers, and includes entries
    the directory does not call safe: the caller validates, and a wallet that
    only ever saw servers somebody else already approved could never notice that
    the directory was wrong.
    """
    candidates = []
    for entry in body.get("servers", []):
        candidates.append({
            "hostname": entry["hostname"],
            "port": entry["port"],
            "transport": entry["transport"],
            "hint": entry.get("security"),
            "operatorGroup": entry.get("operatorGroup", "UNKNOWN"),
        })
    return candidates
