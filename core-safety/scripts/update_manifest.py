# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""Build, sign and verify the signed ElectrumX update manifest.

Schema v2 adds a monotonic ``artifact_revision`` and a signed provenance
digest. A running node observes only signed ALENOC/electrumx-ravencoin
releases. Core trust remains a separate signed safe-Core policy rooted solely
in official RavenProject/Ravencoin identities. The release/update signing key
is distinct from the Core-policy signing key and uses a distinct signature
domain.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import json
import re
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from packaging.version import InvalidVersion, Version

SCHEMA_VERSION = 2
SIGNATURE_ALGORITHM = "ed25519"
SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v2\x00"
VALID_CHANNELS = ("stable", "security")
TRUSTED_CORE_REPOSITORIES = ("RavenProject/Ravencoin",)
SUPPORTED_ARCHITECTURES = ("linux/amd64", "linux/arm64")

SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

REQUIRED_FIELDS = (
    "electrumxVersion",
    "artifact_revision",
    "channel",
    "releaseTimestamp",
    "artifactDigest",
    "provenanceDigest",
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
    """Map the three release-review states onto the signed booleans."""
    if classification == CONSENSUS_IMPACT_NONE:
        return False, True
    if classification == CONSENSUS_IMPACT_COMPATIBILITY:
        return False, False
    if classification == CONSENSUS_IMPACT_CONSENSUS_CHANGE:
        return True, False
    raise ManifestError(
        f"unclassifiable consensus impact {classification!r}; refusing to guess. "
        f"Must be one of {VALID_CONSENSUS_IMPACT_CLASSES!r}")


def consensus_classification(body: dict) -> str:
    impact = body.get("consensusImpact")
    eligible = body.get("autoUpdateEligible")
    if impact is False and eligible is True:
        return CONSENSUS_IMPACT_NONE
    if impact is False and eligible is False:
        return CONSENSUS_IMPACT_COMPATIBILITY
    if impact is True and eligible is False:
        return CONSENSUS_IMPACT_CONSENSUS_CHANGE
    raise ManifestError("signed consensus-impact booleans do not encode a valid class")


def _valid_version(value, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise ManifestError(f"{field} must be a non-empty version string")
    try:
        Version(value)
    except InvalidVersion as exc:
        raise ManifestError(f"{field} is not a valid version") from exc


def _architecture_values(value) -> tuple[str, ...]:
    if isinstance(value, str):
        values = tuple(item.strip() for item in value.split(",") if item.strip())
    elif isinstance(value, list):
        values = tuple(value)
    else:
        raise ManifestError("architecture must be a string or list")
    if not values or any(not isinstance(item, str) for item in values):
        raise ManifestError("architecture list is empty or malformed")
    if len(set(values)) != len(values):
        raise ManifestError("architecture contains duplicate targets")
    unknown = sorted(set(values) - set(SUPPORTED_ARCHITECTURES))
    if unknown:
        raise ManifestError(f"unsupported architecture target(s): {unknown}")
    return values


def build_manifest(*, electrumx_version: str, artifact_revision: int,
                    channel: str, artifact_digest: str,
                    provenance_digest: str, architecture,
                    core_version: str, core_repository: str,
                    core_tag: str, core_commit: str,
                    certification_report_digest: str,
                    safe_core_policy_version: int,
                    required_updater_version: str,
                    config_compatibility: dict, db_compatibility: dict,
                    rollback_safe: bool, consensus_impact: bool,
                    auto_update_eligible: bool, installer_filename: str,
                    installer_digest: str,
                    release_timestamp: Optional[str] = None) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    timestamp = release_timestamp or now.replace(microsecond=0).isoformat()
    body = {
        "schemaVersion": SCHEMA_VERSION,
        "electrumxVersion": electrumx_version,
        "artifact_revision": artifact_revision,
        "channel": channel,
        "releaseTimestamp": timestamp,
        "artifactDigest": artifact_digest,
        "provenanceDigest": provenance_digest,
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
    """Strict schema validation independent of the signature."""
    if not isinstance(body, dict):
        raise ManifestError("manifest body must be an object")
    if body.get("schemaVersion") != SCHEMA_VERSION:
        raise ManifestError(
            f"unsupported manifest schemaVersion {body.get('schemaVersion')!r}")
    missing = [field for field in REQUIRED_FIELDS if field not in body]
    if missing:
        raise ManifestError(f"manifest is missing required field(s): {missing}")
    unknown = set(body) - ({"schemaVersion"} | set(REQUIRED_FIELDS))
    if unknown:
        raise ManifestError(f"manifest contains unknown field(s): {sorted(unknown)}")

    _valid_version(body["electrumxVersion"], "electrumxVersion")
    _valid_version(body["coreVersion"], "coreVersion")
    _valid_version(body["requiredUpdaterVersion"], "requiredUpdaterVersion")

    revision = body["artifact_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ManifestError("artifact_revision must be a non-negative integer")

    if body["channel"] not in VALID_CHANNELS:
        raise ManifestError(f"invalid channel {body['channel']!r}")
    if not isinstance(body["releaseTimestamp"], str):
        raise ManifestError("releaseTimestamp must be a string")
    stamp = body["releaseTimestamp"]
    if stamp.endswith(("Z", "z")):
        stamp = stamp[:-1] + "+00:00"
    try:
        parsed_time = datetime.datetime.fromisoformat(stamp)
    except ValueError as exc:
        raise ManifestError("releaseTimestamp is not a valid ISO-8601 timestamp") from exc
    if parsed_time.tzinfo is None:
        raise ManifestError("releaseTimestamp must include a timezone")

    for field in ("artifactDigest", "provenanceDigest", "installerDigest"):
        if not SHA256_RE.fullmatch(str(body[field])):
            raise ManifestError(f"{field} must be sha256:<64 lowercase hex>")
    if not RAW_SHA256_RE.fullmatch(str(body["certificationReportDigest"])):
        raise ManifestError("certificationReportDigest must be 64 lowercase hex")

    _architecture_values(body["architecture"])
    if body["coreRepository"] not in TRUSTED_CORE_REPOSITORIES:
        raise ManifestError(
            f"repository {body['coreRepository']!r} is not an approved Core trust source")
    if not TAG_RE.fullmatch(str(body["coreTag"])):
        raise ManifestError("coreTag is malformed")
    if not COMMIT_RE.fullmatch(str(body["coreCommit"])):
        raise ManifestError("coreCommit must be a full 40-character lowercase hex SHA")

    policy_version = body["safeCorePolicyVersion"]
    if not isinstance(policy_version, int) or isinstance(policy_version, bool) or \
            policy_version < 1:
        raise ManifestError("safeCorePolicyVersion must be a positive integer")
    if not isinstance(body["configCompatibility"], dict):
        raise ManifestError("configCompatibility must be an object")

    db = body["dbCompatibility"]
    if not isinstance(db, dict):
        raise ManifestError("dbCompatibility must be an object")
    schema = db.get("schemaVersion")
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        raise ManifestError("dbCompatibility.schemaVersion must be a positive integer")
    migration = db.get("migration")
    if migration is not None:
        if not isinstance(migration, dict):
            raise ManifestError("dbCompatibility.migration must be an object")
        required = {"fromSchema", "toSchema", "reversible"}
        if not required <= set(migration):
            raise ManifestError(
                "dbCompatibility.migration must include fromSchema, toSchema, reversible")
        if not isinstance(migration["fromSchema"], int) or isinstance(
                migration["fromSchema"], bool) or migration["fromSchema"] < 1:
            raise ManifestError("migration.fromSchema must be a positive integer")
        if migration["toSchema"] != schema:
            raise ManifestError("migration.toSchema must equal dbCompatibility.schemaVersion")
        if not isinstance(migration["reversible"], bool):
            raise ManifestError("migration.reversible must be boolean")

    for field in ("rollbackSafe", "consensusImpact", "autoUpdateEligible"):
        if not isinstance(body[field], bool):
            raise ManifestError(f"{field} must be boolean")
    consensus_classification(body)
    if migration is not None and migration["reversible"] is False and body["rollbackSafe"]:
        raise ManifestError(
            "rollbackSafe cannot be true for an irreversible DB migration")

    filename = body["installerFilename"]
    if not isinstance(filename, str) or not FILENAME_RE.fullmatch(filename):
        raise ManifestError("installerFilename is malformed")
    if filename != "electrumx-ravencoin-install.py":
        raise ManifestError("installerFilename is not the canonical installer name")


def sign_manifest(body: dict, private_key: Ed25519PrivateKey, *, key_id: str) -> dict:
    validate_body(body)
    if not isinstance(key_id, str) or not key_id:
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
    if not isinstance(document, dict) or set(document) != {"manifest", "signature"}:
        raise ManifestError("manifest document must contain exactly manifest and signature")
    body = document.get("manifest")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        raise ManifestError("manifest document contains malformed objects")
    if set(signature) != {"algorithm", "keyId", "value"}:
        raise ManifestError("signature object contains missing or unknown fields")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise ManifestError(
            f"unsupported signature algorithm {signature.get('algorithm')!r}")
    key_id = signature.get("keyId")
    if not isinstance(key_id, str) or len(key_id) > 128:
        raise ManifestError(f"malformed signature key id {key_id!r}")
    if key_id not in trusted_keys:
        raise ManifestError(f"manifest signed by unknown key id {key_id!r}")
    public_bytes = trusted_keys[key_id]
    if not isinstance(public_bytes, bytes) or len(public_bytes) != 32:
        raise ManifestError("trusted update public key must be exactly 32 bytes")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise ManifestError("signature is not valid base64") from exc
    if len(raw_signature) != 64:
        raise ManifestError("Ed25519 signature must be exactly 64 bytes")

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(
            raw_signature, canonical_bytes(body))
    except (InvalidSignature, ValueError) as exc:
        raise ManifestError("manifest signature does not verify") from exc
    validate_body(body)
    return body


def load_trusted_key(hex_path: str) -> dict:
    try:
        with open(hex_path, "r", encoding="ascii") as handle:
            public_hex = handle.read().strip()
        public_bytes = bytes.fromhex(public_hex)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"cannot load update-signing public key: {exc}") from exc
    if len(public_bytes) != 32:
        raise ManifestError("update-signing public key must be exactly 32 bytes")
    return {key_id_for(public_bytes): public_bytes}


def generate_keypair() -> tuple:
    """Ephemeral helper for tests/key-ceremony tooling; never called by runtime."""
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    return private_key, public_bytes


def key_id_for(public_bytes: bytes) -> str:
    if not isinstance(public_bytes, bytes) or len(public_bytes) != 32:
        raise ManifestError("public key must be exactly 32 bytes")
    return hashlib.sha256(public_bytes).hexdigest()[:16]
