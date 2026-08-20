# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from __future__ import annotations

import gzip
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import sys
import tarfile
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location("electrumx_ravencoin_install", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

import update_manifest as um  # noqa: E402

CORE_COMMIT = "c" * 40
MONITOR_COMMIT = "d" * 40


def signed_document(key_pair, *, artifact_digest="sha256:" + "a" * 64,
                    installer_digest="sha256:" + "b" * 64,
                    core_repository="RavenProject/Ravencoin"):
    private_key, public_bytes = key_pair
    body = um.build_manifest(
        electrumx_version="1.14.0",
        channel="stable",
        artifact_digest=artifact_digest,
        architecture="linux/amd64,linux/arm64",
        core_version="4.8.0",
        core_repository=core_repository,
        core_tag="v4.8.0",
        core_commit=CORE_COMMIT,
        certification_report_digest="e" * 64,
        safe_core_policy_version=3,
        required_updater_version="1.0.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest=installer_digest,
    )
    return um.sign_manifest(
        body, private_key, key_id=um.key_id_for(public_bytes)), public_bytes


def bundle_files():
    metadata = {
        "schemaVersion": 1,
        "electrumxVersion": "1.14.0",
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": "f" * 40,
        "nodeMonitor": {
            "repository": "ALENOC/ravencoin-node-monitor",
            "commit": MONITOR_COMMIT,
            "bundledPath": "vendor/ravencoin-node-monitor",
        },
    }
    return {
        "compose.yaml": (
            "RAVENCOIN_VERSION: 4.8.0\n"
            f"RAVENCOIN_SOURCE_COMMIT: {CORE_COMMIT}\n"
            "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin\n"
        ).encode(),
        "compose.chainstrap.yaml": b"network_mode: none\n",
        "compose.monitor.yaml": (
            b"services:\n  monitor:\n"
            b"    security_opt:\n      - no-new-privileges:true\n"
            b"    cap_drop:\n      - ALL\n"
            b'    ports:\n      - "127.0.0.1:8899:8899/tcp"\n'
        ),
        "setup.sh": b"#!/bin/sh\nexit 0\n",
        ".env.example": b"EXAMPLE=1\n",
        "docker/core/bootstrap-reindex.sh": (
            b"#!/bin/sh\nravend -connect=0\nravend -connect=0\n"
            b"raven-cli getbestblockhash\nraven-cli getblockhash 1\n"
            b"raven-cli listassets\nraven-cli getassetdata\n"
            b"raven-cli listaddressesbyasset\n"
        ),
        "release-install-metadata.json": (
            json.dumps(metadata, sort_keys=True) + "\n").encode(),
        "vendor/ravencoin-node-monitor/Dockerfile": b"FROM scratch\n",
        "vendor/ravencoin-node-monitor/.env.example": b"BIND_PORT=8899\n",
        "vendor/ravencoin-node-monitor/contrib/ravencoin-bandwidth-controller.py": b"#!/usr/bin/env python3\n",
        "compose.monitor-controller.yaml": (
            b"services:\n  controller:\n"
            b"    volumes:\n"
            b"      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro\n"
        ),
        "core-safety/production/update-signing-public-key.hex": b"a" * 64,
        "core-safety/production/safe-core-policy.json": b"{}\n",
        "core-safety/production/core-policy-signing-public-key.hex": b"b" * 64,
    }


def make_bundle(files=None, *, special_member=None):
    files = files if files is not None else bundle_files()
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
            if special_member is not None:
                archive.addfile(special_member)
    return raw.getvalue()


def test_source_installer_has_no_unceremonied_production_key():
    assert installer.RELEASE_PUBLIC_KEY_HEX == ""
    with pytest.raises(installer.InstallError):
        installer.require_release_public_key("")


def test_valid_signed_manifest_verifies():
    key_pair = um.generate_keypair()
    document, public_bytes = signed_document(key_pair)
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    assert body["coreRepository"] == "RavenProject/Ravencoin"
    assert body["coreCommit"] == CORE_COMMIT


def test_manifest_signed_by_other_key_is_refused():
    document, _ = signed_document(um.generate_keypair())
    _, wrong_public = um.generate_keypair()
    with pytest.raises(installer.InstallError):
        installer.verify_manifest_signature(document, wrong_public.hex())


def test_installer_digest_binds_exact_downloaded_file(tmp_path):
    path = tmp_path / "electrumx-ravencoin-install.py"
    path.write_bytes(b"exact installer bytes")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    body = {"installerDigest": digest}
    installer.verify_running_installer(body, path)
    path.write_bytes(b"tampered")
    with pytest.raises(installer.InstallError):
        installer.verify_running_installer(body, path)


def test_valid_bundle_is_bound_to_manifest_and_monitor_identity():
    data = make_bundle()
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    metadata = installer.validate_bundle(data, body, public_key_hex="a" * 64)
    assert metadata["nodeMonitor"]["commit"] == MONITOR_COMMIT


def test_bundle_with_wrong_digest_is_refused():
    data = make_bundle()
    document, public_bytes = signed_document(um.generate_keypair())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="SHA-256 mismatch"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_path_traversal_is_refused_even_when_digest_matches():
    files = bundle_files()
    files["../escape"] = b"bad"
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="unsafe bundle path"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_symlink_is_refused_even_when_digest_matches():
    member = tarfile.TarInfo("vendor/ravencoin-node-monitor/evil-link")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/passwd"
    data = make_bundle(special_member=member)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="forbidden"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_core_identity_must_match_signed_manifest():
    files = bundle_files()
    files["compose.yaml"] = files["compose.yaml"].replace(
        CORE_COMMIT.encode(), ("9" * 40).encode())
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="Core commit"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_chainstrap_exact_tip_gate_is_required_by_installer():
    files = bundle_files()
    files["docker/core/bootstrap-reindex.sh"] = b"ravend -connect=0\n"
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="offline isolation"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_chainstrap_reindex_missing_rpc_gate_is_refused():
    files = bundle_files()
    files["docker/core/bootstrap-reindex.sh"] = (
        b"#!/bin/sh\nravend -connect=0\nravend -connect=0\n"
        b"raven-cli getbestblockhash\nraven-cli getblockhash 1\n"
    )
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="lacks gate 'listassets'"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_updater_key_must_match_installer_trust_root():
    data = make_bundle()
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="trust root"):
        installer.validate_bundle(data, body, public_key_hex="c" * 64)


def test_incomplete_bundle_is_refused():
    files = bundle_files()
    del files["core-safety/production/safe-core-policy.json"]
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="release bundle is incomplete"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_safe_extract_writes_no_path_outside_destination(tmp_path):
    data = make_bundle()
    destination = tmp_path / "install"
    destination.mkdir()
    installer.extract_bundle(data, destination)
    assert (destination / "compose.yaml").is_file()
    assert (destination / "vendor/ravencoin-node-monitor/Dockerfile").is_file()


def test_fresh_defaults_are_chainstrap_and_monitor():
    args = installer.parse_args([])
    assert installer.choose_bootstrap(args, interactive=False) == "chainstrap"
    assert installer.choose_monitor(args, interactive=False) is True


def test_p2p_opt_out_drops_only_chainstrap_overlay():
    assert installer.compose_files("chainstrap", False) == [
        "compose.yaml", "compose.chainstrap.yaml"]
    assert installer.compose_files("p2p", False) == ["compose.yaml"]


def test_monitor_choice_adds_hardened_overlay():
    assert installer.compose_files("chainstrap", True) == [
        "compose.yaml", "compose.chainstrap.yaml", "compose.monitor.yaml"]


def test_monitor_password_file_is_created_0600(tmp_path):
    monitor = tmp_path / "vendor" / "ravencoin-node-monitor"
    monitor.mkdir(parents=True)
    installer.write_monitor_env(tmp_path)
    path = monitor / ".env"
    assert path.is_file()
    assert (path.stat().st_mode & 0o777) == 0o600
    text = path.read_text(encoding="utf-8")
    assert "HISTORY_STORAGE=memory" in text
    assert "MONITOR_PASSWORD=" in text
    assert "PRICE_FEED_ENABLED=true" in text


def test_existing_destination_is_never_overwritten(tmp_path):
    target = tmp_path / "electrumx-ravencoin"
    target.mkdir()
    with pytest.raises(installer.InstallError, match="refusing to overwrite"):
        installer.install_fresh(
            target, b"not-used", body={}, metadata={},
            bootstrap="chainstrap", monitor=False, controller=False)


def test_architecture_detection():
    assert installer.detect_architecture("x86_64") == "amd64"
    assert installer.detect_architecture("aarch64") == "arm64"
    with pytest.raises(installer.InstallError):
        installer.detect_architecture("mips64")


def test_cli_conflicting_choices_fail():
    with pytest.raises(SystemExit):
        installer.parse_args(["--chainstrap", "--p2p-bootstrap"])
    with pytest.raises(SystemExit):
        installer.parse_args(["--with-monitor", "--without-monitor"])


# ---------------------------------------------------------------------------
# NON-PRODUCTION local release validation: separation from the production
# trust root. These tests prove the validation mechanism can never be
# mistaken for, or substitute for, a real signed release.
# ---------------------------------------------------------------------------

def _write_local_release_validation_dir(tmp_path, *, key_pair=None):
    """Build a complete, signed, self-consistent local-validation directory
    the same way core-safety/scripts/build_local_release_validation_bundle.py
    does, so tests exercise the exact on-disk shape the installer reads."""
    key_pair = key_pair or um.generate_keypair()
    _, public_bytes = key_pair
    files = bundle_files()
    files["core-safety/production/update-signing-public-key.hex"] = public_bytes.hex().encode()
    data = make_bundle(files)
    document, _ = signed_document(
        key_pair, artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    directory = tmp_path / "local-release-validation"
    directory.mkdir()
    (directory / installer.LOCAL_VALIDATION_MANIFEST_FILE).write_text(
        json.dumps(document), encoding="utf-8")
    (directory / installer.LOCAL_VALIDATION_BUNDLE_FILE).write_bytes(data)
    (directory / installer.LOCAL_VALIDATION_PUBLIC_KEY_FILE).write_text(
        public_bytes.hex(), encoding="utf-8")
    return directory, document, data


def test_separation_a_production_path_with_empty_key_fails_closed():
    document, _ = signed_document(um.generate_keypair())

    def fetch(_url):
        return json.dumps(document).encode()

    with pytest.raises(installer.InstallError):
        installer.fetch_and_verify_release_manifest(
            public_key_hex=installer.RELEASE_PUBLIC_KEY_HEX, fetch=fetch)


def test_separation_b_production_path_with_unsigned_bundle_fails_closed():
    key_pair = um.generate_keypair()
    _, public_bytes = key_pair
    document, _ = signed_document(key_pair)
    unsigned = {"manifest": document["manifest"]}  # signature stripped
    with pytest.raises(installer.InstallError):
        installer.verify_manifest_signature(unsigned, public_bytes.hex())


def test_separation_c_production_path_with_unknown_signer_fails_closed():
    document, _ = signed_document(um.generate_keypair())
    _, trusted_public = um.generate_keypair()
    with pytest.raises(installer.InstallError):
        installer.verify_manifest_signature(document, trusted_public.hex())


def test_separation_d_explicit_local_validation_with_matching_key_succeeds(tmp_path):
    directory, _, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, manifest_fetch, bundle_fetch = \
        installer.load_local_release_validation(directory)
    body = installer.fetch_and_verify_release_manifest(
        public_key_hex=public_key_hex, fetch=manifest_fetch)
    _, metadata = installer.fetch_and_verify_bundle(
        body, fetch=bundle_fetch, public_key_hex=public_key_hex)
    assert metadata["nodeMonitor"]["commit"] == MONITOR_COMMIT


def test_separation_e_validation_key_is_never_persisted_as_trust_root(tmp_path):
    directory, _, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, _, _ = installer.load_local_release_validation(directory)
    assert public_key_hex != installer.RELEASE_PUBLIC_KEY_HEX
    assert installer.RELEASE_PUBLIC_KEY_HEX == ""


def test_separation_f_production_installer_still_refuses_same_release_afterward(tmp_path):
    directory, document, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, manifest_fetch, _ = installer.load_local_release_validation(directory)
    installer.fetch_and_verify_release_manifest(
        public_key_hex=public_key_hex, fetch=manifest_fetch)

    def production_fetch(_url):
        return json.dumps(document).encode()

    with pytest.raises(installer.InstallError):
        installer.fetch_and_verify_release_manifest(
            public_key_hex=installer.RELEASE_PUBLIC_KEY_HEX, fetch=production_fetch)


def test_local_release_validation_dir_missing_file_fails_closed(tmp_path):
    directory = tmp_path / "incomplete"
    directory.mkdir()
    (directory / installer.LOCAL_VALIDATION_MANIFEST_FILE).write_text("{}", encoding="utf-8")
    with pytest.raises(installer.InstallError):
        installer.load_local_release_validation(directory)


def test_monitor_controller_comment_mentioning_forbidden_terms_is_not_a_false_positive():
    files = bundle_files()
    files["compose.monitor-controller.yaml"] = (
        b"services:\n  controller:\n"
        b"    # Intentionally no /var/run/docker.sock and no CAP_NET_ADMIN here.\n"
        b"    volumes:\n"
        b"      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro\n"
    )
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_monitor_controller_real_docker_socket_grant_is_still_refused():
    files = bundle_files()
    files["compose.monitor-controller.yaml"] = (
        b"services:\n  controller:\n"
        b"    volumes:\n"
        b"      - /var/run/docker.sock:/var/run/docker.sock\n"
        b"      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro\n"
    )
    data = make_bundle(files)
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest())
    body = installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="forbidden privileges"):
        installer.validate_bundle(data, body, public_key_hex="a" * 64)
