# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for release/update signing trust migration.

The checked-in v1 public key and its attestation are retained only as historical
evidence. The attestation must continue to verify under its original v1 domain,
but it is not authority for the 1.13.3 replacement key. Manifest-v2 tests use
the current v2 domain and strict schema.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402

PUBLIC_KEY_PATH = ROOT / "core-safety" / "production" / \
    "update-signing-public-key.hex"
ATTESTATION_PATH = ROOT / "core-safety" / "production" / \
    "update-signing-key-attestation.json"
HISTORICAL_V1_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1\x00"


def _published_public_bytes() -> bytes:
    return bytes.fromhex(PUBLIC_KEY_PATH.read_text(encoding="ascii").strip())


def _attestation() -> dict:
    return json.loads(ATTESTATION_PATH.read_text(encoding="utf-8"))


def _attestation_message(statement: dict) -> bytes:
    return HISTORICAL_V1_DOMAIN + json.dumps(
        statement, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def test_retired_public_key_is_well_formed_and_explicitly_denied_by_v2_renderer():
    public_bytes = _published_public_bytes()
    assert len(public_bytes) == 32
    assert public_bytes.hex() == render_installer_v2.RETIRED_UPDATE_PUBLIC_KEY_HEX
    assert update_manifest.key_id_for(public_bytes) == render_installer_v2.RETIRED_UPDATE_KEY_ID


def test_historical_v1_attestation_still_verifies_under_its_original_domain():
    document = _attestation()
    statement = document["statement"]
    public_bytes = _published_public_bytes()
    assert statement["publicKey"] == public_bytes.hex()
    assert statement["keyId"] == update_manifest.key_id_for(public_bytes)
    assert document["signature"]["keyId"] == statement["keyId"]
    assert document["signature"]["algorithm"] == "ed25519"
    assert statement["domain"] == "ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1"
    Ed25519PublicKey.from_public_bytes(public_bytes).verify(
        bytes.fromhex(document["signature"]["value"]),
        _attestation_message(statement))


def test_historical_attestation_records_why_ci_key_is_retired():
    statement = _attestation()["statement"]
    assert statement["secretName"] == "ELECTRUMX_UPDATE_SIGNING_KEY"
    assert statement["secretEnvironment"] == "electrumx-release-signing"
    assert statement["operator"] == "ALENOC"


def test_historical_attestation_fails_under_foreign_key():
    document = _attestation()
    message = _attestation_message(document["statement"])
    signature = bytes.fromhex(document["signature"]["value"])
    foreign_public = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(InvalidSignature):
        foreign_public.verify(signature, message)


def test_historical_attestation_does_not_verify_under_v2_domain():
    document = _attestation()
    v2_message = update_manifest.SIGNATURE_DOMAIN + json.dumps(
        document["statement"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(_published_public_bytes()).verify(
            bytes.fromhex(document["signature"]["value"]), v2_message)


def _sample_body():
    return {
        "schemaVersion": update_manifest.SCHEMA_VERSION,
        "electrumxVersion": "1.13.3",
        "artifact_revision": 0,
        "channel": "stable",
        "releaseTimestamp": "2026-08-22T00:00:00Z",
        "artifactDigest": "sha256:" + "a" * 64,
        "provenanceDigest": "sha256:" + "d" * 64,
        "architecture": "linux/arm64",
        "coreVersion": "4.8.0",
        "coreRepository": "RavenProject/Ravencoin",
        "coreTag": "v4.8.0",
        "coreCommit": "22549129888d02e0e08fcdb9f96f3c699167e774",
        "certificationReportDigest": "b" * 64,
        "safeCorePolicyVersion": 3,
        "requiredUpdaterVersion": "2.0.0",
        "configCompatibility": {"breakingChanges": []},
        "dbCompatibility": {"schemaVersion": 1},
        "rollbackSafe": True,
        "consensusImpact": False,
        "autoUpdateEligible": True,
        "installerFilename": "electrumx-ravencoin-install.py",
        "installerDigest": "sha256:" + "c" * 64,
    }


def test_manifest_signed_by_a_foreign_key_is_rejected():
    trusted = update_manifest.load_trusted_key(str(PUBLIC_KEY_PATH))
    foreign_private, foreign_public = update_manifest.generate_keypair()
    body = _sample_body()
    document = update_manifest.sign_manifest(
        body, foreign_private,
        key_id=update_manifest.key_id_for(foreign_public))
    with pytest.raises(update_manifest.ManifestError):
        update_manifest.verify_manifest(document, trusted)


def test_release_timestamp_accepts_the_utc_designator():
    for stamp in ("2026-08-22T00:00:00Z", "2026-08-22T00:00:00+00:00",
                  "2026-08-22T02:00:00+02:00"):
        body = _sample_body()
        body["releaseTimestamp"] = stamp
        update_manifest.validate_body(body)


def test_release_timestamp_without_a_timezone_is_rejected():
    body = _sample_body()
    body["releaseTimestamp"] = "2026-08-22T00:00:00"
    with pytest.raises(update_manifest.ManifestError):
        update_manifest.validate_body(body)
