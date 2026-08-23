import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_manifest  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "offline_sign_release", SCRIPTS / "offline_sign_release.py")
offline = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(offline)


def _candidate(tmp_path: Path):
    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    ).hex()
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()

    key_path = tmp_path / "release-key.hex"
    key_path.write_text(private_hex + "\n", encoding="ascii")
    os.chmod(key_path, 0o600)

    (tmp_path / offline.BUNDLE_NAME).write_bytes(b"bundle-v1")
    (tmp_path / offline.INSTALLER_NAME).write_bytes(b"installer-v1")
    (tmp_path / offline.PROVENANCE_NAME).write_bytes(b"{\"provenance\":1}\n")

    body = update_manifest.build_manifest(
        electrumx_version="1.13.3",
        artifact_revision=0,
        channel="stable",
        artifact_digest="sha256:" + offline.sha256(tmp_path / offline.BUNDLE_NAME),
        provenance_digest="sha256:" + offline.sha256(tmp_path / offline.PROVENANCE_NAME),
        architecture="linux/amd64,linux/arm64",
        core_version="4.8.0",
        core_repository="RavenProject/Ravencoin",
        core_tag="v4.8.0",
        core_commit="22549129888d02e0e08fcdb9f96f3c699167e774",
        certification_report_digest="a" * 64,
        safe_core_policy_version=3,
        required_updater_version="2.0.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename=offline.INSTALLER_NAME,
        installer_digest="sha256:" + offline.sha256(tmp_path / offline.INSTALLER_NAME),
        release_timestamp="2026-08-22T15:00:00+00:00",
    )
    unsigned = tmp_path / offline.UNSIGNED_NAME
    unsigned.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    key_id = update_manifest.key_id_for(bytes.fromhex(public_hex))
    inputs = {
        "schemaVersion": 1,
        "tag": "v1.13.3",
        "electrumxVersion": "1.13.3",
        "artifact_revision": 0,
        "expectedPublicKeyHex": public_hex,
        "expectedKeyId": key_id,
        "retiredKeyIdForbidden": offline.render_installer_v2.RETIRED_UPDATE_KEY_ID,
        "unsignedManifestSha256": offline.sha256(unsigned),
        "artifactDigest": body["artifactDigest"],
        "installerDigest": body["installerDigest"],
        "provenanceDigest": body["provenanceDigest"],
    }
    (tmp_path / offline.INPUTS_NAME).write_text(
        json.dumps(inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return key_path, public_hex


def test_offline_sign_then_verify_only(tmp_path):
    key_path, public_hex = _candidate(tmp_path)
    signed = offline.sign(tmp_path, key_path, public_hex)
    assert signed == tmp_path / offline.SIGNED_NAME

    result = offline.verify_signed(tmp_path, public_hex)
    assert result["version"] == "1.13.3"
    assert result["artifactRevision"] == 0
    assert result["keyId"] == update_manifest.key_id_for(bytes.fromhex(public_hex))
    assert (tmp_path / offline.CHECKSUMS_NAME).is_file()


def test_verify_only_refuses_post_sign_tamper(tmp_path):
    key_path, public_hex = _candidate(tmp_path)
    offline.sign(tmp_path, key_path, public_hex)
    (tmp_path / offline.BUNDLE_NAME).write_bytes(b"tampered")

    with pytest.raises(offline.OfflineSigningError, match="bundle digest differs"):
        offline.verify_signed(tmp_path, public_hex)
