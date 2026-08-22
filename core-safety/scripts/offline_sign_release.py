#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Sign or verify a reviewed release-manifest v2 on an offline machine.

This program performs no network I/O. Signing accepts the candidate handoff
created by ``build_production_release.py``, verifies every bound digest again,
verifies that the private key derives the independently authenticated
replacement public key, rejects the retired CI-held key, and writes the signed
manifest plus final checksums.

``--verify-only`` never opens a private key. It verifies the signed manifest
under the independently supplied public key, requires the signed body to equal
the reviewed unsigned body byte-for-meaning, rechecks bundle/installer/
provenance digests, and requires ``SHA256SUMS`` to exactly match the files that
would be published.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import re
import stat
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402

BUNDLE_NAME = "electrumx-ravencoin-bundle.tar.gz"
INSTALLER_NAME = "electrumx-ravencoin-install.py"
PROVENANCE_NAME = "release-provenance.json"
UNSIGNED_NAME = "unsigned-release-manifest.json"
INPUTS_NAME = "offline-signing-inputs.json"
SIGNED_NAME = "release-manifest.json"
CHECKSUMS_NAME = "SHA256SUMS"
HEX_RE = re.compile(r"^[0-9a-f]{64}$")


class OfflineSigningError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_public_key(value: str) -> str:
    normalized = value.strip().lower()
    if not HEX_RE.fullmatch(normalized):
        raise OfflineSigningError("expected replacement public key is malformed")
    return normalized


def _regular_private_file(path: pathlib.Path) -> None:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise OfflineSigningError("private key must be a regular non-symlink file")
    if info.st_uid != os.geteuid():
        raise OfflineSigningError("private key must be owned by the signing uid")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise OfflineSigningError("private key file mode must be 0600")


def load_private_key(path: pathlib.Path) -> tuple[Ed25519PrivateKey, str]:
    _regular_private_file(path)
    value = path.read_text(encoding="ascii").strip().lower()
    if not HEX_RE.fullmatch(value):
        raise OfflineSigningError("private key file must contain exactly 32 raw bytes as hex")
    private = Ed25519PrivateKey.from_private_bytes(bytes.fromhex(value))
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ).hex()
    return private, public


def _load_json(path: pathlib.Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OfflineSigningError(f"cannot read {path.name}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OfflineSigningError(f"{path.name} must contain a JSON object")
    return payload


def verify_candidate(directory: pathlib.Path, expected_public_key: str) -> tuple[dict, dict]:
    expected_public_key = _normalize_public_key(expected_public_key)
    inputs = _load_json(directory / INPUTS_NAME)
    body = _load_json(directory / UNSIGNED_NAME)
    update_manifest.validate_body(body)
    if inputs.get("schemaVersion") != 1:
        raise OfflineSigningError("offline signing input schema is unsupported")
    if inputs.get("expectedPublicKeyHex") != expected_public_key:
        raise OfflineSigningError("handoff public key differs from independently supplied key")
    key_id = update_manifest.key_id_for(bytes.fromhex(expected_public_key))
    if inputs.get("expectedKeyId") != key_id:
        raise OfflineSigningError("handoff key id differs from replacement public key")
    if inputs.get("retiredKeyIdForbidden") != render_installer_v2.RETIRED_UPDATE_KEY_ID:
        raise OfflineSigningError("handoff does not bind the retired-key prohibition")
    if key_id == render_installer_v2.RETIRED_UPDATE_KEY_ID or \
            expected_public_key == render_installer_v2.RETIRED_UPDATE_PUBLIC_KEY_HEX:
        raise OfflineSigningError("retired CI-held update key is forbidden")
    if sha256(directory / UNSIGNED_NAME) != inputs.get("unsignedManifestSha256"):
        raise OfflineSigningError("unsigned manifest changed after candidate handoff")
    if "sha256:" + sha256(directory / BUNDLE_NAME) != body.get("artifactDigest"):
        raise OfflineSigningError("bundle digest differs from manifest")
    if "sha256:" + sha256(directory / INSTALLER_NAME) != body.get("installerDigest"):
        raise OfflineSigningError("installer digest differs from manifest")
    if "sha256:" + sha256(directory / PROVENANCE_NAME) != body.get("provenanceDigest"):
        raise OfflineSigningError("provenance digest differs from manifest")
    for field in ("artifactDigest", "installerDigest", "provenanceDigest"):
        if inputs.get(field) != body.get(field):
            raise OfflineSigningError(f"handoff {field} differs from manifest")
    if inputs.get("electrumxVersion") != body.get("electrumxVersion") or \
            inputs.get("artifact_revision") != body.get("artifact_revision"):
        raise OfflineSigningError("handoff release identity differs from manifest")
    return inputs, body


def _expected_checksums(directory: pathlib.Path) -> dict[str, str]:
    return {
        name: sha256(directory / name)
        for name in (BUNDLE_NAME, INSTALLER_NAME, PROVENANCE_NAME, SIGNED_NAME)
    }


def _write_checksums(directory: pathlib.Path, checksums: dict[str, str]) -> None:
    (directory / CHECKSUMS_NAME).write_text(
        "".join(f"{checksums[name]}  {name}\n" for name in (
            BUNDLE_NAME, INSTALLER_NAME, PROVENANCE_NAME, SIGNED_NAME)),
        encoding="ascii")


def _verify_checksum_file(directory: pathlib.Path, checksums: dict[str, str]) -> None:
    expected = "".join(f"{checksums[name]}  {name}\n" for name in (
        BUNDLE_NAME, INSTALLER_NAME, PROVENANCE_NAME, SIGNED_NAME))
    try:
        actual = (directory / CHECKSUMS_NAME).read_text(encoding="ascii")
    except (OSError, UnicodeDecodeError) as exc:
        raise OfflineSigningError(f"cannot read {CHECKSUMS_NAME}: {exc}") from exc
    if actual != expected:
        raise OfflineSigningError(
            "SHA256SUMS does not exactly match the signed publication files")


def sign(directory: pathlib.Path, private_key_path: pathlib.Path,
         expected_public_key: str) -> pathlib.Path:
    expected_public_key = _normalize_public_key(expected_public_key)
    private, derived_public = load_private_key(private_key_path)
    if derived_public != expected_public_key:
        raise OfflineSigningError(
            "private key does not derive the independently authenticated public key")
    inputs, body = verify_candidate(directory, expected_public_key)
    key_id = inputs["expectedKeyId"]
    document = update_manifest.sign_manifest(body, private, key_id=key_id)
    signed = directory / SIGNED_NAME
    signed.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.chmod(signed, 0o644)
    _write_checksums(directory, _expected_checksums(directory))
    return signed


def verify_signed(directory: pathlib.Path, expected_public_key: str) -> dict:
    expected_public_key = _normalize_public_key(expected_public_key)
    inputs, unsigned_body = verify_candidate(directory, expected_public_key)
    document = _load_json(directory / SIGNED_NAME)
    public_bytes = bytes.fromhex(expected_public_key)
    key_id = update_manifest.key_id_for(public_bytes)
    try:
        signed_body = update_manifest.verify_manifest(document, {key_id: public_bytes})
    except update_manifest.ManifestError as exc:
        raise OfflineSigningError(f"signed release manifest does not verify: {exc}") from exc
    if signed_body != unsigned_body:
        raise OfflineSigningError(
            "signed manifest body differs from the reviewed unsigned manifest")
    if document.get("signature", {}).get("keyId") != inputs.get("expectedKeyId"):
        raise OfflineSigningError("signed manifest keyId differs from offline handoff")
    checksums = _expected_checksums(directory)
    _verify_checksum_file(directory, checksums)
    return {
        "version": signed_body["electrumxVersion"],
        "artifactRevision": signed_body["artifact_revision"],
        "keyId": key_id,
        "bundleSha256": checksums[BUNDLE_NAME],
        "installerSha256": checksums[INSTALLER_NAME],
        "provenanceSha256": checksums[PROVENANCE_NAME],
        "manifestSha256": checksums[SIGNED_NAME],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", required=True, type=pathlib.Path)
    parser.add_argument("--private-key", type=pathlib.Path)
    parser.add_argument("--expected-public-key-hex", required=True)
    parser.add_argument(
        "--verify-only", action="store_true",
        help="verify the completed signed publication set without opening a private key")
    args = parser.parse_args()
    directory = args.candidate_dir.resolve()

    if args.verify_only:
        if args.private_key is not None:
            parser.error("--verify-only does not accept --private-key")
        result = verify_signed(directory, args.expected_public_key_hex)
        for key in (
                "version", "artifactRevision", "keyId", "bundleSha256",
                "installerSha256", "provenanceSha256", "manifestSha256"):
            print(f"{key}={result[key]}")
        print("status=VERIFIED")
        return 0

    if args.private_key is None:
        parser.error("--private-key is required unless --verify-only is used")
    signed = sign(directory, args.private_key.resolve(), args.expected_public_key_hex)
    print(f"signed={signed}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except OfflineSigningError as exc:
        print(f"offline-signing: REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
