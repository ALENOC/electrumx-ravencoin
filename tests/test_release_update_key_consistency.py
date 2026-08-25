"""Production candidates must contain one release/update trust root."""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import sys
import tarfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_production_release  # noqa: E402
import build_release_bundle  # noqa: E402
import offline_sign_release  # noqa: E402
import update_manifest  # noqa: E402


def _raw_keypair(tmp_path: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    private = Ed25519PrivateKey.generate()
    private_hex = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption()).hex()
    public_hex = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw).hex()
    private_path = tmp_path / "ephemeral-test-private-key.hex"
    private_path.write_text(private_hex + "\n", encoding="ascii")
    os.chmod(private_path, 0o600)
    return private_path, public_hex, update_manifest.key_id_for(
        bytes.fromhex(public_hex))


def _json(path: pathlib.Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_signed_production_candidate_has_one_update_trust_root(
        monkeypatch, tmp_path):
    """Exercise the real production builder and offline signer end to end.

    The private key is ephemeral test data. Production private material is not
    needed or accessed.
    """
    private_path, public_hex, key_id = _raw_keypair(tmp_path)
    monitor_dir = tmp_path / "monitor"
    monitor_dir.mkdir()
    (monitor_dir / "README.md").write_text("test monitor\n", encoding="utf-8")
    monitor_pin = build_release_bundle.load_pin()["nodeMonitor"]["commit"]
    original_tracked_files = build_release_bundle.tracked_files

    def fake_run_git(cwd, *args):
        if args == ("rev-parse", "HEAD"):
            return monitor_pin if cwd.resolve() == monitor_dir.resolve() else "f" * 40
        if args == ("status", "--porcelain", "--untracked-files=no"):
            return ""
        raise AssertionError(f"unexpected git invocation: {args!r}")

    def fake_tracked_files(cwd):
        if cwd.resolve() == monitor_dir.resolve():
            return [pathlib.Path("README.md")]
        return original_tracked_files(cwd)

    monkeypatch.setattr(build_release_bundle, "run_git", fake_run_git)
    monkeypatch.setattr(build_release_bundle, "tracked_files", fake_tracked_files)

    output = tmp_path / "candidate"
    version = build_production_release.electrumx_version()
    monkeypatch.setattr(sys, "argv", [
        "build_production_release.py",
        "--monitor-dir", str(monitor_dir),
        "--output-dir", str(output),
        "--tag", f"v{version}",
        "--artifact-revision", "0",
        "--release-timestamp", "2026-08-25T00:00:00Z",
        "--update-public-key-hex", public_hex,
    ])
    assert build_production_release.main() == 0
    offline_sign_release.sign(output, private_path, public_hex)
    assert offline_sign_release.verify_signed(output, public_hex)["keyId"] == key_id

    standalone_installer = (
        output / build_production_release.INSTALLER_NAME).read_bytes()
    provenance_bytes = (
        output / build_production_release.PROVENANCE_NAME).read_bytes()
    with tarfile.open(
            output / build_production_release.BUNDLE_NAME, mode="r:gz") as archive:
        bundled_key = archive.extractfile(
            build_release_bundle.UPDATE_KEY_PATH).read().decode("ascii").strip()
        bundled_installer = archive.extractfile(
            build_release_bundle.INSTALLER_PATH).read()
        bundled_provenance = archive.extractfile(
            build_release_bundle.PROVENANCE_PATH).read()

    provenance = json.loads(provenance_bytes)
    handoff = _json(output / build_production_release.SIGNING_INPUTS_NAME)
    manifest = _json(output / offline_sign_release.SIGNED_NAME)
    assert f'RELEASE_PUBLIC_KEY_HEX = "{public_hex}"'.encode() in standalone_installer
    assert bundled_installer == standalone_installer
    assert bundled_key == public_hex
    assert bundled_provenance == provenance_bytes
    assert provenance["updateSigningPublicKey"] == public_hex
    assert provenance["updateSigningKeyId"] == key_id
    assert handoff["expectedPublicKeyHex"] == public_hex
    assert handoff["expectedKeyId"] == key_id
    assert manifest["signature"]["keyId"] == key_id
    assert update_manifest.verify_manifest(
        manifest, {key_id: bytes.fromhex(public_hex)}) == manifest["manifest"]
    assert manifest["manifest"]["provenanceDigest"] == \
        "sha256:" + hashlib.sha256(provenance_bytes).hexdigest()
