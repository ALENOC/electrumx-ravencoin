# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Generate, sign and verify the signed safe-Core policy.

The policy is the only artifact a wallet consumes at runtime.  It lists exact
certified release identities, never version ranges, and it is signed with a
dedicated Ed25519 key that exists for this purpose alone.

Design rules encoded here:

* the signature covers a canonical serialization, so re-serializing cannot
  change what was signed;
* ``policyVersion`` is monotonic, which is what makes rollback detectable;
* a release can be revoked by a later policy, and revocation is sticky in the
  sense that a verifier must refuse to go backwards to a policy that predates it;
* signing keys can be rotated by listing more than one trusted key, but a policy
  can never introduce a new trust root by itself.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
from typing import Iterable, Optional, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
VALID_RELEASE_STATUSES = ("KNOWN_SAFE", "KNOWN_UNSAFE", "REVOKED")


class PolicyError(ValueError):
    """The policy document is unusable: malformed, unsigned, stale or untrusted."""


def canonical_bytes(document: dict) -> bytes:
    """Serialize deterministically so a signature is over one exact byte string."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True).encode("utf-8")


def policy_digest(document: dict) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def build_policy(*, policy_version: int, safety_profile: str, releases: Sequence[dict],
                 generated_at: Optional[str] = None,
                 valid_for_days: Optional[int] = 90,
                 certification_reports: Optional[Sequence[dict]] = None) -> dict:
    """Assemble an unsigned policy body."""
    if not isinstance(policy_version, int) or policy_version < 1:
        raise PolicyError("policyVersion must be a positive integer")
    if not safety_profile:
        raise PolicyError("a safety profile id is required")

    now = datetime.datetime.now(datetime.timezone.utc)
    generated = generated_at or now.replace(microsecond=0).isoformat()
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": policy_version,
        "generatedAt": generated,
        "safetyProfile": safety_profile,
        "releases": [_normalize_release(entry) for entry in releases],
    }
    if valid_for_days is not None:
        expiry = (now + datetime.timedelta(days=valid_for_days)).replace(microsecond=0)
        body["expiresAt"] = expiry.isoformat()
    if certification_reports:
        body["certificationReports"] = list(certification_reports)
    return body


def _normalize_release(entry: dict) -> dict:
    required = ("repository", "tag", "version", "commit", "status")
    for key in required:
        if key not in entry:
            raise PolicyError(f"release entry is missing {key!r}")
    if entry["status"] not in VALID_RELEASE_STATUSES:
        raise PolicyError(f"invalid release status {entry['status']!r}")
    normalized = {key: entry[key] for key in required}
    for optional in ("certification", "artifactSha256", "publishedAt", "reportDigest",
                     "revokedAt", "revocationReason"):
        if optional in entry:
            normalized[optional] = entry[optional]
    if normalized["status"] == "REVOKED" and "revocationReason" not in normalized:
        raise PolicyError("a revoked release must carry a revocationReason")
    if normalized["status"] == "KNOWN_SAFE":
        certification = normalized.get("certification") or {}
        if certification.get("result") != "PASS":
            raise PolicyError(
                "a KNOWN_SAFE release must reference a passing certification")
    return normalized


def sign_policy(body: dict, private_key: Ed25519PrivateKey, *, key_id: str) -> dict:
    """Wrap a policy body with its detached signature."""
    if not key_id:
        raise PolicyError("a signing key id is required")
    signature = private_key.sign(canonical_bytes(body))
    return {
        "policy": body,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "keyId": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_policy(document: dict, trusted_keys: dict, *,
                  minimum_policy_version: int = 0,
                  now: Optional[datetime.datetime] = None) -> dict:
    """Verify a signed policy and return its body, or raise PolicyError.

    ``trusted_keys`` maps key id to raw 32-byte Ed25519 public key material.
    More than one entry is how key rotation works: an old and a new key are both
    accepted during the overlap, and a policy can never add a key to this map.
    """
    if not isinstance(document, dict):
        raise PolicyError("policy document must be an object")
    body = document.get("policy")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        raise PolicyError("policy document must contain policy and signature")

    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise PolicyError(f"unsupported signature algorithm {signature.get('algorithm')!r}")
    key_id = signature.get("keyId")
    if key_id not in trusted_keys:
        raise PolicyError(f"policy signed by unknown key id {key_id!r}")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise PolicyError("signature is not valid base64") from exc

    public_key = Ed25519PublicKey.from_public_bytes(trusted_keys[key_id])
    try:
        public_key.verify(raw_signature, canonical_bytes(body))
    except InvalidSignature as exc:
        raise PolicyError("policy signature does not verify") from exc

    validate_body(body)

    if body["policyVersion"] < minimum_policy_version:
        raise PolicyError(
            f"policy version {body['policyVersion']} is older than the last accepted "
            f"version {minimum_policy_version}; refusing a rollback"
        )

    current = now or datetime.datetime.now(datetime.timezone.utc)
    expires_at = body.get("expiresAt")
    if expires_at:
        try:
            expiry = datetime.datetime.fromisoformat(expires_at)
        except ValueError as exc:
            raise PolicyError("expiresAt is not a valid timestamp") from exc
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=datetime.timezone.utc)
        if current > expiry:
            raise PolicyError("policy has expired")
    return body


def validate_body(body: dict) -> None:
    """Schema validation, independent of the signature."""
    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise PolicyError(f"unsupported policy schemaVersion {body.get('schemaVersion')!r}")
    version = body.get("policyVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise PolicyError("policyVersion must be a positive integer")
    if not isinstance(body.get("safetyProfile"), str) or not body["safetyProfile"]:
        raise PolicyError("safetyProfile must be a non-empty string")
    generated_at = body.get("generatedAt")
    if not isinstance(generated_at, str):
        raise PolicyError("generatedAt must be a string")
    try:
        datetime.datetime.fromisoformat(generated_at)
    except ValueError as exc:
        raise PolicyError("generatedAt is not a valid timestamp") from exc
    releases = body.get("releases")
    if not isinstance(releases, list):
        raise PolicyError("releases must be a list")
    seen = set()
    for entry in releases:
        if not isinstance(entry, dict):
            raise PolicyError("each release must be an object")
        _normalize_release(entry)
        identity = (entry["repository"], entry["commit"])
        if identity in seen:
            raise PolicyError(f"duplicate release identity {identity}")
        seen.add(identity)


def lookup_release(body: dict, repository: str, commit: str) -> Optional[dict]:
    """Find a release by its identity.  Never matches on version alone."""
    for entry in body.get("releases", []):
        if entry["repository"] == repository and entry["commit"] == commit:
            return entry
    return None


def merge_baseline(baseline_body: dict, remote_body: dict) -> dict:
    """Combine the built-in baseline with a verified remote policy.

    The remote policy may add releases and may restrict or revoke anything,
    including a baseline entry.  It may never relax a baseline restriction: a
    release the baseline calls unsafe or revoked stays that way.
    """
    validate_body(baseline_body)
    validate_body(remote_body)
    merged = {entry["repository"] + "@" + entry["commit"]: dict(entry)
              for entry in baseline_body["releases"]}
    for entry in remote_body["releases"]:
        key = entry["repository"] + "@" + entry["commit"]
        existing = merged.get(key)
        if existing and existing["status"] in ("KNOWN_UNSAFE", "REVOKED") \
                and entry["status"] == "KNOWN_SAFE":
            # Remote policy is not allowed to rehabilitate what the built-in
            # baseline already refuses.
            continue
        merged[key] = dict(entry)
    body = dict(remote_body)
    body["releases"] = sorted(merged.values(),
                              key=lambda item: (item["repository"], item["commit"]))
    return body


def generate_keypair() -> tuple:
    """Return (private_key, public_bytes).  Used by tests and by key setup."""
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_bytes


def key_id_for(public_bytes: bytes) -> str:
    """Stable short identifier derived from the public key itself."""
    return hashlib.sha256(public_bytes).hexdigest()[:16]
