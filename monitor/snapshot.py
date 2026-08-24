# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Signed network observation snapshot.

A snapshot is one aggregator's view at one moment: chain challenge
outcome, infrastructure counts, observer coverage, asset sampling.  It
extends the signed directory without touching it, because existing
directory consumers must keep working unchanged.

Same fundamental rule as the directory, stated on the artifact itself:
this is an observation and discovery aid.  The Ravencoin blockchain
remains the consensus authority; a snapshot saying SAFE is one party's
signed opinion at a past moment, and every client must still validate
endpoints itself.
"""

from __future__ import annotations

import base64
import datetime
import json
from typing import Dict, Iterable, Mapping, Optional

from .model import Availability, Security

SCHEMA_VERSION = 1
SNAPSHOT_DOMAIN = b"ALENOC-RVN-NETWORK-SNAPSHOT-v1\x00"

DISCLAIMER = (
    "This document is an observation/discovery aid. Ravencoin consensus "
    "remains authoritative. Clients must independently validate endpoints.")


class SnapshotError(ValueError):
    """The snapshot document is unusable."""


def canonical_bytes(body: Mapping) -> bytes:
    return SNAPSHOT_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def build_snapshot(states: Iterable, *, snapshot_version: int,
                   chain: Mapping, infrastructure: Mapping,
                   observers: Mapping, asset_sampling: Mapping,
                   generated_at: Optional[datetime.datetime] = None,
                   valid_for_hours: int = 24) -> dict:
    if not isinstance(snapshot_version, int) or isinstance(snapshot_version, bool) \
            or snapshot_version < 1:
        raise SnapshotError("snapshotVersion must be a positive integer")
    now = (generated_at or datetime.datetime.now(
        datetime.timezone.utc)).replace(microsecond=0)
    known = reachable = safe = full_asset = index_synced = 0
    servers = []
    for state in states:
        known += 1
        if state.availability is Availability.REACHABLE:
            reachable += 1
        if state.security is Security.SAFE:
            safe += 1
        servers.append({
            "hostname": state.endpoint.hostname,
            "port": state.endpoint.port,
            "transport": state.endpoint.transport.value,
            "availability": state.availability.value,
            "security": state.security.value,
        })
    servers.sort(key=lambda item: (item["hostname"], item["port"]))
    return {
        "schemaVersion": SCHEMA_VERSION,
        "snapshotVersion": snapshot_version,
        "generatedAt": now.isoformat(),
        "expiresAt": (now + datetime.timedelta(
            hours=max(1, valid_for_hours))).isoformat(),
        "disclaimer": DISCLAIMER,
        "chain": dict(chain),
        "infrastructure": {
            "knownEndpoints": known,
            "reachableEndpoints": reachable,
            "safeEndpoints": safe,
            "fullAssetEndpoints": int(infrastructure.get("fullAsset", 0)),
            "indexSyncedEndpoints": int(infrastructure.get("indexSynced", 0)),
        },
        "observers": dict(observers),
        "assetSampling": dict(asset_sampling),
        "servers": servers,
    }


def sign_snapshot(body: Mapping, private_key, *, key_id: str) -> dict:
    signature = private_key.sign(canonical_bytes(body))
    return {
        "snapshot": dict(body),
        "signature": {
            "algorithm": "ed25519",
            "keyId": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_snapshot(document: Mapping, trusted_keys: Dict[str, bytes], *,
                    minimum_version: int = 0,
                    now: Optional[datetime.datetime] = None) -> dict:
    """Verify a signed snapshot: trusted key, Ed25519 signature, exact
    schema, monotonic version anti-rollback, expiry.  Fail closed."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    if not isinstance(document, Mapping):
        raise SnapshotError("snapshot document must be an object")
    body = document.get("snapshot")
    signature = document.get("signature")
    if not isinstance(body, Mapping) or not isinstance(signature, Mapping):
        raise SnapshotError("snapshot must contain snapshot and signature")
    if signature.get("algorithm") != "ed25519":
        raise SnapshotError("unsupported signature algorithm")
    key_id = signature.get("keyId")
    if key_id not in trusted_keys:
        raise SnapshotError(f"snapshot signed by unknown key id {key_id!r}")
    try:
        raw = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise SnapshotError("signature is not valid base64") from exc
    try:
        Ed25519PublicKey.from_public_bytes(trusted_keys[key_id]).verify(
            raw, canonical_bytes(body))
    except InvalidSignature as exc:
        raise SnapshotError("snapshot signature does not verify") from exc

    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise SnapshotError(
            f"unsupported snapshot schemaVersion {body.get('schemaVersion')!r}")
    version = body.get("snapshotVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise SnapshotError("snapshotVersion must be a positive integer")
    if version < minimum_version:
        raise SnapshotError(
            f"snapshot version {version} is older than the accepted "
            f"{minimum_version}; refusing a rollback")
    if body.get("disclaimer") != DISCLAIMER:
        raise SnapshotError("snapshot disclaimer is missing or altered")
    expires_at = body.get("expiresAt")
    if not isinstance(expires_at, str):
        raise SnapshotError("expiresAt is required")
    try:
        expiry = datetime.datetime.fromisoformat(expires_at)
    except ValueError as exc:
        raise SnapshotError("expiresAt is not a valid timestamp") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=datetime.timezone.utc)
    current = now or datetime.datetime.now(datetime.timezone.utc)
    if current > expiry:
        raise SnapshotError("snapshot has expired")
    if not isinstance(body.get("servers"), list):
        raise SnapshotError("servers must be a list")
    return dict(body)
