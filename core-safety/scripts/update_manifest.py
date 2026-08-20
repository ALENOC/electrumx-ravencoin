# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Build, sign and verify the signed ElectrumX update manifest.

Trust model this module encodes: a running node never observes
RavenProject/Ravencoin releases directly. It observes only signed
ALENOC/electrumx-ravencoin releases, and each release carries this manifest
as the single source of truth for what Ravencoin Core identity it bundles and
what our own certification found for that identity.

  RavenProject new Core -> our certification PASS -> new ElectrumX release
  -> signed ElectrumX update manifest -> node self-update

A node must never update Ravencoin Core by watching RavenProject directly.

The manifest is signed with a dedicated Ed25519 key that exists for this
purpose alone, distinct from the safe-Core policy signing key
(core-safety/scripts/policy.py). Reusing one key for both purposes would let
a compromise of either trust root forge documents for the other; a shared
SIGNATURE_DOMAIN prefix would have the same effect even with distinct keys,
which is why the domain string below is also distinct from the policy one.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

SCHEMA_VERSION = 1
SIGNATURE_ALGORITHM = "ed25519"
SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1\x00"
VALID_CHANNELS = ("stable", "security")
TRUSTED_CORE_REPOSITORIES = ("RavenProject/Ravencoin",)

REQUIRED_FIELDS = (
    "electrumxVersion",
    "channel",
    "releaseTimestamp",
    "artifactDigest",
    "architecture",
    "coreVersion",
    "coreRepository",
    "coreTag",
    "coreCommit",
    "certificationReportDigest",
    "safeCorePolicyVersion",
    "requiredUpdaterVersion",
    "configCompatibility",
    "dbCompatibility",
    "rollbackSafe",
    "consensusImpact",
    "autoUpdateEligible",
    "installerFilename",
    "installerDigest",
)


class ManifestError(ValueError):
    """The update manifest is unusable: malformed, unsigned, or untrusted."""


def canonical_bytes(document: dict) -> bytes:
    """Serialize deterministically so a signature is over one exact byte string."""
    return SIGNATURE_DOMAIN + json.dumps(
        document, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def manifest_digest(document: dict) -> str:
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


CONSENSUS_IMPACT_NONE = "NONE"
CONSENSUS_IMPACT_COMPATIBILITY = "COMPATIBILITY"
CONSENSUS_IMPACT_CONSENSUS_CHANGE = "CONSENSUS_CHANGE"
VALID_CONSENSUS_IMPACT_CLASSES = (
    CONSENSUS_IMPACT_NONE,
    CONSENSUS_IMPACT_COMPATIBILITY,
    CONSENSUS_IMPACT_CONSENSUS_CHANGE,
)


def classify_consensus_impact(classification: str) -> tuple:
    """Derive the wire-level (consensusImpact, autoUpdateEligible) booleans.

    The manifest only carries a boolean because that is what the update
    decision/apply gates already check (update_decision.py,
    update_apply.py). A boolean has no way to represent "not yet
    classified" as distinct from "classified as safe", so classification
    must happen here, upstream of the boolean, and must fail closed:
    anything other than the three known values is refused rather than
    silently treated as NONE.
    """
    if classification == CONSENSUS_IMPACT_NONE:
        return False, True
    if classification == CONSENSUS_IMPACT_COMPATIBILITY:
        return False, False
    if classification == CONSENSUS_IMPACT_CONSENSUS_CHANGE:
        return True, False
    raise ManifestError(
        f"unclassifiable consensus impact {classification!r}; refusing to "
        f"guess. Must be one of {VALID_CONSENSUS_IMPACT_CLASSES!r}")


def build_manifest(*, electrumx_version: str, channel: str, artifact_digest: str,
                    architecture: str, core_version: str, core_repository: str,
                    core_tag: str, core_commit: str, certification_report_digest: str,
                    safe_core_policy_version: int, required_updater_version: str,
                    config_compatibility: dict, db_compatibility: dict,
                    rollback_safe: bool, consensus_impact: bool,
                    auto_update_eligible: bool, installer_filename: str,
                    installer_digest: str,
                    release_timestamp: Optional[str] = None) -> dict:
    """Assemble an unsigned update manifest body.

    ``config_compatibility`` and ``db_compatibility`` are free-form structured
    metadata; callers describe exactly what changed. ``dbCompatibility`` at
    minimum should carry a ``schemaVersion`` and, if a migration is required,
    a ``migration`` description. ``rollbackSafe`` must be false whenever
    ``dbCompatibility`` describes an irreversible migration: the updater must
    never attempt a blind rollback across one.
    """
    if channel not in VALID_CHANNELS:
        raise ManifestError(f"invalid channel {channel!r}")
    if core_repository not in TRUSTED_CORE_REPOSITORIES:
        raise ManifestError(
            f"repository {core_repository!r} is not an approved Core trust source")
    if not isinstance(rollback_safe, bool):
        raise ManifestError("rollbackSafe must be a boolean")
    if not isinstance(consensus_impact, bool):
        raise ManifestError("consensusImpact must be a boolean")
    if not isinstance(auto_update_eligible, bool):
        raise ManifestError("autoUpdateEligible must be a boolean")
    if consensus_impact and auto_update_eligible:
        raise ManifestError(
            "autoUpdateEligible cannot be true when consensusImpact is true")
    if not installer_filename:
        raise ManifestError("installerFilename is required")
    if not installer_digest:
        raise ManifestError("installerDigest is required")
    if not isinstance(db_compatibility, dict) or "schemaVersion" not in db_compatibility:
        raise ManifestError("dbCompatibility must include a schemaVersion")
    if db_compatibility.get("migration", {}).get("reversible") is False and rollback_safe:
        raise ManifestError(
            "rollbackSafe cannot be true when dbCompatibility declares an "
            "irreversible migration")

    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = release_timestamp or now.replace(microsecond=0).isoformat()

    body = {
        "schemaVersion": SCHEMA_VERSION,
        "electrumxVersion": electrumx_version,
        "channel": channel,
        "releaseTimestamp": timestamp,
        "artifactDigest": artifact_digest,
        "architecture": architecture,
        "coreVersion": core_version,
        "coreRepository": core_repository,
        "coreTag": core_tag,
        "coreCommit": core_commit,
        "certificationReportDigest": certification_report_digest,
        "safeCorePolicyVersion": safe_core_policy_version,
        "requiredUpdaterVersion": required_updater_version,
        "configCompatibility": config_compatibility,
        "dbCompatibility": db_compatibility,
        "rollbackSafe": rollback_safe,
        "consensusImpact": consensus_impact,
        "autoUpdateEligible": auto_update_eligible,
        "installerFilename": installer_filename,
        "installerDigest": installer_digest,
    }
    validate_body(body)
    return body


def validate_body(body: dict) -> None:
    """Schema validation, independent of the signature."""
    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError(f"unsupported manifest schemaVersion {body.get('schemaVersion')!r}")
    for field in REQUIRED_FIELDS:
        if field not in body:
            raise ManifestError(f"manifest is missing required field {field!r}")
    if body["channel"] not in VALID_CHANNELS:
        raise ManifestError(f"invalid channel {body['channel']!r}")
    if body["coreRepository"] not in TRUSTED_CORE_REPOSITORIES:
        raise ManifestError(
            f"repository {body['coreRepository']!r} is not an approved Core trust source")
    if not isinstance(body["rollbackSafe"], bool):
        raise ManifestError("rollbackSafe must be a boolean")
    if not isinstance(body["consensusImpact"], bool):
        raise ManifestError("consensusImpact must be a boolean")
    if not isinstance(body["autoUpdateEligible"], bool):
        raise ManifestError("autoUpdateEligible must be a boolean")
    if body["consensusImpact"] and body["autoUpdateEligible"]:
        raise ManifestError(
            "autoUpdateEligible cannot be true when consensusImpact is true")
    if not body["installerFilename"]:
        raise ManifestError("installerFilename is required")
    if not body["installerDigest"]:
        raise ManifestError("installerDigest is required")
    try:
        datetime.datetime.fromisoformat(body["releaseTimestamp"])
    except (TypeError, ValueError) as exc:
        raise ManifestError("releaseTimestamp is not a valid timestamp") from exc


def sign_manifest(body: dict, private_key: Ed25519PrivateKey, *, key_id: str) -> dict:
    """Wrap a manifest body with its detached signature."""
    if not key_id:
        raise ManifestError("a signing key id is required")
    signature = private_key.sign(canonical_bytes(body))
    return {
        "manifest": body,
        "signature": {
            "algorithm": SIGNATURE_ALGORITHM,
            "keyId": key_id,
            "value": base64.b64encode(signature).decode("ascii"),
        },
    }


def verify_manifest(document: dict, trusted_keys: dict) -> dict:
    """Verify a signed manifest and return its body, or raise ManifestError.

    ``trusted_keys`` maps key id to raw 32-byte Ed25519 public key material,
    normally just the one pinned update-signing public key
    (core-safety/production/update-signing-public-key.hex), passed in by the
    caller rather than read from disk here, so tests can inject an ephemeral
    key instead of ever needing a real private key in the repository.
    """
    if not isinstance(document, dict):
        raise ManifestError("manifest document must be an object")
    body = document.get("manifest")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        raise ManifestError("manifest document must contain manifest and signature")

    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ManifestError(f"unsupported signature algorithm {signature.get('algorithm')!r}")
    key_id = signature.get("keyId")
    if key_id not in trusted_keys:
        raise ManifestError(f"manifest signed by unknown key id {key_id!r}")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ManifestError("signature is not valid base64") from exc

    public_key = Ed25519PublicKey.from_public_bytes(trusted_keys[key_id])
    try:
        public_key.verify(raw_signature, canonical_bytes(body))
    except InvalidSignature as exc:
        raise ManifestError("manifest signature does not verify") from exc

    validate_body(body)
    return body


def load_trusted_key(hex_path: str) -> dict:
    """Read the pinned public update-signing key file into a trusted_keys map."""
    with open(hex_path, "r", encoding="ascii") as handle:
        public_hex = handle.read().strip()
    public_bytes = bytes.fromhex(public_hex)
    return {key_id_for(public_bytes): public_bytes}


def generate_keypair() -> tuple:
    """Return (private_key, public_bytes). Used by tests and by key setup."""
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_bytes


def key_id_for(public_bytes: bytes) -> str:
    """Stable short identifier derived from the public key itself."""
    return hashlib.sha256(public_bytes).hexdigest()[:16]
