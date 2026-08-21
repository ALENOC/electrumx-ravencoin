# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for the production ElectrumX release/update trust root.

The private half of this key exists only in the protected release-signing
environment, so these tests never see it.  What they do prove is that the
public key published in the repository is the one the ceremony actually
generated (via a signature that only the protected private key could have
produced), that the key is bound to the ElectrumX update-manifest domain,
and that a manifest signed by any other key fails closed.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_manifest  # noqa: E402

PUBLIC_KEY_PATH = ROOT / "core-safety" / "production" / \
    "update-signing-public-key.hex"
ATTESTATION_PATH = ROOT / "core-safety" / "production" / \
    "update-signing-key-attestation.json"


def _published_public_bytes() -> bytes:
    return bytes.fromhex(PUBLIC_KEY_PATH.read_text(encoding="ascii").strip())


def _attestation() -> dict:
    return json.loads(ATTESTATION_PATH.read_text(encoding="utf-8"))


def _attestation_message(statement: dict) -> bytes:
    return update_manifest.SIGNATURE_DOMAIN + json.dumps(
        statement, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def test_published_public_key_is_well_formed():
    public_bytes = _published_public_bytes()
    assert len(public_bytes) == 32
    trusted = update_manifest.load_trusted_key(str(PUBLIC_KEY_PATH))
    assert list(trusted) == [update_manifest.key_id_for(public_bytes)]


def test_attestation_proves_possession_of_the_protected_private_key():
    """Only the holder of the protected private key can produce this."""
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


def test_attestation_records_the_protected_secret_location():
    statement = _attestation()["statement"]
    assert statement["secretName"] == "ELECTRUMX_UPDATE_SIGNING_KEY"
    assert statement["secretEnvironment"] == "electrumx-release-signing"
    assert statement["operator"] == "ALENOC"


def test_attestation_fails_closed_under_any_other_key():
    document = _attestation()
    message = _attestation_message(document["statement"])
    signature = bytes.fromhex(document["signature"]["value"])
    foreign_public = Ed25519PrivateKey.generate().public_key()
    with pytest.raises(InvalidSignature):
        foreign_public.verify(signature, message)


def test_attestation_signature_is_domain_bound():
    """The same bytes without the update-manifest domain must not verify."""
    document = _attestation()
    undomained = json.dumps(
        document["statement"], sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")
    with pytest.raises(InvalidSignature):
        Ed25519PublicKey.from_public_bytes(_published_public_bytes()).verify(
            bytes.fromhex(document["signature"]["value"]), undomained)


def test_manifest_signed_by_a_foreign_key_is_rejected():
    trusted = update_manifest.load_trusted_key(str(PUBLIC_KEY_PATH))
    foreign_private, foreign_public = update_manifest.generate_keypair()
    body = {
        "schemaVersion": update_manifest.SCHEMA_VERSION,
        "electrumxVersion": "1.13.1",
        "channel": "stable",
        "releaseTimestamp": "2026-08-21T00:00:00Z",
        "artifactDigest": "sha256:" + "a" * 64,
        "architecture": "linux/arm64",
        "coreVersion": "4.8.0",
        "coreRepository": "RavenProject/Ravencoin",
        "coreTag": "v4.8.0",
        "coreCommit": "22549129888d02e0e08fcdb9f96f3c699167e774",
        "certificationReportDigest": "b" * 64,
        "safeCorePolicyVersion": 3,
        "requiredUpdaterVersion": "1.13.1",
        "configCompatibility": {"breakingChanges": []},
        "dbCompatibility": {"schemaVersion": 1},
        "rollbackSafe": True,
        "consensusImpact": False,
        "autoUpdateEligible": True,
        "installerFilename": "electrumx-ravencoin-install.py",
        "installerDigest": "sha256:" + "c" * 64,
    }
    document = update_manifest.sign_manifest(
        body, foreign_private,
        key_id=update_manifest.key_id_for(foreign_public))
    with pytest.raises(update_manifest.ManifestError):
        update_manifest.verify_manifest(document, trusted)
