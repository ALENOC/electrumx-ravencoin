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
import subprocess
import sys
import tarfile
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Keep the checked-in installer bound to the historical name. Assertions that
# verify the v1 trust discontinuity continue to exercise these exact bytes.
spec = importlib.util.spec_from_file_location("electrumx_ravencoin_install", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

import policy  # noqa: E402
import render_installer_v2  # noqa: E402
import update_manifest as um  # noqa: E402

CORE_COMMIT = "c" * 40
MONITOR_COMMIT = "d" * 40
REPORT_DIGEST = "e" * 64
TEST_CORE_POLICY_PRIVATE, TEST_CORE_POLICY_PUBLIC = policy.generate_keypair()
TEST_RELEASE_PRIVATE, TEST_RELEASE_PUBLIC = um.generate_keypair()
TEST_PROVENANCE_BYTES = (
    json.dumps({
        "schemaVersion": 1,
        "artifact_revision": 0,
        "electrumxVersion": "1.14.0",
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": "f" * 40,
    }, sort_keys=True, separators=(",", ":")) + "\n"
).encode("utf-8")
TEST_PROVENANCE_DIGEST = "sha256:" + hashlib.sha256(TEST_PROVENANCE_BYTES).hexdigest()

# Manifest-v2 tests exercise the real rendered 1.13.3 installer, not a
# compatibility mode added to the historical v1 source template.
_RENDERED_DIR = pathlib.Path(tempfile.mkdtemp(prefix="electrumx-installer-v2-test-"))
RENDERED_INSTALLER_PATH = _RENDERED_DIR / "electrumx-ravencoin-install.py"
render_installer_v2.render(
    output=RENDERED_INSTALLER_PATH,
    public_key_hex=TEST_RELEASE_PUBLIC.hex(),
)
v2_spec = importlib.util.spec_from_file_location(
    "electrumx_ravencoin_install_v2", RENDERED_INSTALLER_PATH)
v2_installer = importlib.util.module_from_spec(v2_spec)
sys.modules[v2_spec.name] = v2_installer
v2_spec.loader.exec_module(v2_installer)

# Preserve the pre-existing exception assertions byte-for-byte while changing
# only the code-under-test input target from the v1 template to rendered v2.
# Installer functions resolve InstallError from module globals at call time.
v2_installer.InstallError = installer.InstallError

# Direct bundle-validation tests use a test policy key. Production source still
# contains the real independent key; only these imported test modules are patched.
installer.PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX = TEST_CORE_POLICY_PUBLIC.hex()
v2_installer.PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX = TEST_CORE_POLICY_PUBLIC.hex()


def signed_document(key_pair, *, artifact_digest="sha256:" + "a" * 64,
                    installer_digest="sha256:" + "b" * 64,
                    core_repository="RavenProject/Ravencoin",
                    core_commit=CORE_COMMIT,
                    certification_report_digest=REPORT_DIGEST,
                    safe_core_policy_version=3):
    private_key, public_bytes = key_pair
    body = um.build_manifest(
        electrumx_version="1.14.0",
        artifact_revision=0,
        channel="stable",
        artifact_digest=artifact_digest,
        provenance_digest=TEST_PROVENANCE_DIGEST,
        architecture="linux/amd64,linux/arm64",
        core_version="4.8.0",
        core_repository=core_repository,
        core_tag="v4.8.0",
        core_commit=core_commit,
        certification_report_digest=certification_report_digest,
        safe_core_policy_version=safe_core_policy_version,
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


def signed_core_policy(*, private_key=TEST_CORE_POLICY_PRIVATE,
                       public_bytes=TEST_CORE_POLICY_PUBLIC,
                       core_commit=CORE_COMMIT,
                       report_digest=REPORT_DIGEST,
                       policy_version=3,
                       extra_releases=None):
    releases = [{
        "repository": "RavenProject/Ravencoin",
        "tag": "v4.8.0",
        "version": "4.8.0",
        "commit": core_commit,
        "status": "KNOWN_SAFE",
        "publishedAt": "2026-08-19T00:16:44Z",
        "reportDigest": report_digest,
        "certification": {
            "result": "PASS",
            "profile": "rvn-consensus-2026-08-v1",
        },
    }]
    releases.extend(extra_releases or [])
    body = policy.build_policy(
        policy_version=policy_version,
        safety_profile="rvn-consensus-2026-08-v1",
        releases=releases,
        valid_for_days=3650,
    )
    return policy.sign_policy(
        body, private_key, key_id=policy.key_id_for(public_bytes))


def bundle_files(*, core_policy_document=None,
                 core_policy_public_hex=None):
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
    core_policy_document = core_policy_document or signed_core_policy()
    core_policy_public_hex = core_policy_public_hex or TEST_CORE_POLICY_PUBLIC.hex()
    return {
        "compose.yaml": (
            "RAVENCOIN_VERSION: 4.8.0\n"
            f"RAVENCOIN_SOURCE_COMMIT: {CORE_COMMIT}\n"
            "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin\n"
        ).encode(),
        "compose.storage.yaml": (
            b"volumes:\n"
            b"  ravencoin-data:\n    driver: local\n"
            b"  ravencoin-config:\n    driver: local\n"
            b"  electrumx-data:\n    driver: local\n"
            b"  monitor-data:\n    driver: local\n"
        ),
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
        "release-provenance.json": TEST_PROVENANCE_BYTES,
        "vendor/ravencoin-node-monitor/Dockerfile": b"FROM scratch\n",
        "vendor/ravencoin-node-monitor/.env.example": b"BIND_PORT=8899\n",
        "vendor/ravencoin-node-monitor/contrib/ravencoin-bandwidth-controller.py": b"#!/usr/bin/env python3\n",
        "vendor/ravencoin-node-monitor/contrib/verify-published-port.py": b"#!/usr/bin/env python3\n",
        "core-safety/scripts/configure_monitor_admin_network.py": b"#!/usr/bin/env python3\n",
        "compose.monitor-controller.yaml": (
            b"services:\n  controller:\n"
            b"    volumes:\n"
            b"      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro\n"
        ),
        "core-safety/production/update-signing-public-key.hex": b"a" * 64,
        "core-safety/production/safe-core-policy.json": (
            json.dumps(core_policy_document, sort_keys=True) + "\n").encode(),
        "core-safety/production/core-policy-signing-public-key.hex": (
            core_policy_public_hex.encode()),
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


def manifest_for_bundle(data, **kwargs):
    document, public_bytes = signed_document(
        um.generate_keypair(),
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        **kwargs)
    return v2_installer.verify_manifest_signature(document, public_bytes.hex())


def test_source_installer_has_no_unceremonied_production_release_key():
    assert installer.RELEASE_PUBLIC_KEY_HEX == ""
    with pytest.raises(installer.InstallError):
        installer.require_release_public_key("")


def test_source_installer_pins_independent_core_policy_key():
    source = INSTALLER_PATH.read_text(encoding="utf-8")
    assert "PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX" in source
    assert "9fc91edbe763513490248a23ae97575a6b963101b644e01493a3860b99e35648" in source


def test_source_installer_refuses_schema_v2_manifest():
    document, _ = signed_document(um.generate_keypair())
    with pytest.raises(installer.InstallError, match="unsupported release manifest body/schema"):
        installer.validate_manifest_body(document["manifest"])


def test_valid_signed_manifest_verifies():
    key_pair = um.generate_keypair()
    document, public_bytes = signed_document(key_pair)
    body = v2_installer.verify_manifest_signature(document, public_bytes.hex())
    assert body["coreRepository"] == "RavenProject/Ravencoin"
    assert body["coreCommit"] == CORE_COMMIT


def test_manifest_signed_by_other_key_is_refused():
    document, _ = signed_document(um.generate_keypair())
    _, wrong_public = um.generate_keypair()
    with pytest.raises(installer.InstallError):
        v2_installer.verify_manifest_signature(document, wrong_public.hex())


def test_installer_digest_binds_exact_downloaded_file(tmp_path):
    path = tmp_path / "electrumx-ravencoin-install.py"
    path.write_bytes(b"exact installer bytes")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    body = {"installerDigest": digest}
    installer.verify_running_installer(body, path)
    path.write_bytes(b"tampered")
    with pytest.raises(installer.InstallError):
        installer.verify_running_installer(body, path)


def test_valid_bundle_is_bound_to_manifest_policy_and_monitor_identity():
    data = make_bundle()
    body = manifest_for_bundle(data)
    metadata = v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)
    assert metadata["nodeMonitor"]["commit"] == MONITOR_COMMIT


def test_bundle_with_wrong_digest_is_refused():
    data = make_bundle()
    document, public_bytes = signed_document(um.generate_keypair())
    body = v2_installer.verify_manifest_signature(document, public_bytes.hex())
    with pytest.raises(installer.InstallError, match="SHA-256 mismatch"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_path_traversal_is_refused_even_when_digest_matches():
    files = bundle_files()
    files["../escape"] = b"bad"
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="unsafe bundle path"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_symlink_is_refused_even_when_digest_matches():
    member = tarfile.TarInfo("vendor/ravencoin-node-monitor/evil-link")
    member.type = tarfile.SYMTYPE
    member.linkname = "/etc/passwd"
    data = make_bundle(special_member=member)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="forbidden"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_core_identity_must_match_signed_manifest():
    files = bundle_files()
    files["compose.yaml"] = files["compose.yaml"].replace(
        CORE_COMMIT.encode(), ("9" * 40).encode())
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="Core commit"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_core_policy_signature_is_load_bearing():
    policy_document = signed_core_policy()
    policy_document["policy"]["releases"][0]["reportDigest"] = "f" * 64
    files = bundle_files(core_policy_document=policy_document)
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="safe-Core policy signature"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_core_policy_key_must_match_independent_pin():
    _, other_public = policy.generate_keypair()
    files = bundle_files(core_policy_public_hex=other_public.hex())
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="differs from pinned trust root"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_core_policy_report_digest_must_match_manifest():
    data = make_bundle()
    body = manifest_for_bundle(data, certification_report_digest="f" * 64)
    with pytest.raises(installer.InstallError, match="report digest disagrees"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_core_policy_version_must_match_manifest():
    data = make_bundle()
    body = manifest_for_bundle(data, safe_core_policy_version=4)
    with pytest.raises(installer.InstallError, match="policy versions disagree"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_core_policy_must_certify_exact_manifest_commit():
    data = make_bundle()
    body = manifest_for_bundle(data, core_commit="9" * 40)
    with pytest.raises(installer.InstallError, match="does not uniquely certify"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_chainstrap_exact_tip_gate_is_required_by_installer():
    files = bundle_files()
    files["docker/core/bootstrap-reindex.sh"] = b"ravend -connect=0\n"
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="offline isolation"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_chainstrap_reindex_missing_rpc_gate_is_refused():
    files = bundle_files()
    files["docker/core/bootstrap-reindex.sh"] = (
        b"#!/bin/sh\nravend -connect=0\nravend -connect=0\n"
        b"raven-cli getbestblockhash\nraven-cli getblockhash 1\n"
    )
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="lacks gate 'listassets'"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_bundle_updater_key_must_match_installer_trust_root():
    data = make_bundle()
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="trust root"):
        v2_installer.validate_bundle(data, body, public_key_hex="c" * 64)


def test_incomplete_bundle_is_refused():
    files = bundle_files()
    del files["core-safety/production/safe-core-policy.json"]
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="release bundle is incomplete"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


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
        "compose.yaml", "compose.storage.yaml", "compose.chainstrap.yaml"]
    assert installer.compose_files("p2p", False) == [
        "compose.yaml", "compose.storage.yaml"]


def test_monitor_choice_adds_hardened_overlay():
    assert installer.compose_files("chainstrap", True) == [
        "compose.yaml", "compose.storage.yaml",
        "compose.chainstrap.yaml", "compose.monitor.yaml"]


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
            bootstrap="chainstrap", monitor=False, controller=False,
            storage_root=tmp_path / "storage")


def test_fresh_install_refuses_preexisting_project_runtime(monkeypatch):
    monkeypatch.setattr(
        installer, "_docker_project_resources",
        lambda: {
            "containers": [],
            "volumes": ["electrumx-ravencoin_ravencoin-data"],
            "networks": [],
        })
    with pytest.raises(installer.InstallError, match="refuses existing Docker resources"):
        installer.require_clean_docker_project_runtime()


def test_clean_project_runtime_is_accepted(monkeypatch):
    monkeypatch.setattr(
        installer, "_docker_project_resources",
        lambda: {"containers": [], "volumes": [], "networks": []})
    installer.require_clean_docker_project_runtime()


def test_failed_chainstrap_run_is_torn_down_with_volumes(monkeypatch, tmp_path):
    target = tmp_path / "fresh-node"
    recorded_subprocess = []

    monkeypatch.setattr(installer, "require_clean_docker_project_runtime", lambda: None)
    monkeypatch.setattr(installer, "extract_bundle", lambda _data, _dest: None)
    # This test isolates activation/rollback. Storage-path parsing and
    # layout have dedicated tests in tests/test_installer_storage.py.
    monkeypatch.setattr(installer, "write_storage_env", lambda _root, _storage: None)

    def fake_run_checked(argv, *, cwd=None, quiet=False):
        return None

    def fake_activate(_root, _base, _bootstrap):
        raise installer.InstallError("simulated chainstrap failure")

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_subprocess_run(argv, **kwargs):
        recorded_subprocess.append(list(argv))
        return Completed()

    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(installer, "activate_compose", fake_activate)
    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)

    with pytest.raises(installer.InstallError, match="automatic P2P fallback is intentionally disabled"):
        installer.install_fresh(
            target, b"unused", body={}, metadata={},
            bootstrap="chainstrap", monitor=False, controller=False,
            storage_root=tmp_path / "storage")

    assert not target.exists()
    assert not (tmp_path / "storage").exists()
    assert any(
        "down" in command and "--volumes" in command and "--remove-orphans" in command
        for command in recorded_subprocess
    )


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
# NON-PRODUCTION local release validation: both trust roots are explicit and
# isolated from production.
# ---------------------------------------------------------------------------

def _write_local_release_validation_dir(tmp_path, *, key_pair=None,
                                        core_policy_key_pair=None):
    key_pair = key_pair or um.generate_keypair()
    core_policy_key_pair = core_policy_key_pair or policy.generate_keypair()
    _, public_bytes = key_pair
    core_private, core_public = core_policy_key_pair
    local_policy = signed_core_policy(
        private_key=core_private, public_bytes=core_public)
    files = bundle_files(
        core_policy_document=local_policy,
        core_policy_public_hex=core_public.hex())
    files["core-safety/production/update-signing-public-key.hex"] = \
        public_bytes.hex().encode()
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
    (directory / installer.LOCAL_VALIDATION_CORE_POLICY_PUBLIC_KEY_FILE).write_text(
        core_public.hex(), encoding="utf-8")
    return directory, document, data


def test_separation_a_production_path_with_empty_key_fails_closed():
    document, _ = signed_document(um.generate_keypair())

    def fetch(_url):
        return json.dumps(document).encode()

    with pytest.raises(installer.InstallError):
        installer.fetch_and_verify_release_manifest(
            public_key_hex=installer.RELEASE_PUBLIC_KEY_HEX, fetch=fetch)


def test_separation_b_production_path_with_unsigned_manifest_fails_closed():
    key_pair = um.generate_keypair()
    _, public_bytes = key_pair
    document, _ = signed_document(key_pair)
    unsigned = {"manifest": document["manifest"]}
    with pytest.raises(installer.InstallError):
        v2_installer.verify_manifest_signature(unsigned, public_bytes.hex())


def test_separation_c_production_path_with_unknown_signer_fails_closed():
    document, _ = signed_document(um.generate_keypair())
    _, trusted_public = um.generate_keypair()
    with pytest.raises(installer.InstallError):
        v2_installer.verify_manifest_signature(document, trusted_public.hex())


def test_separation_d_explicit_local_validation_with_both_matching_keys_succeeds(tmp_path):
    directory, _, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, core_policy_key_hex, manifest_fetch, bundle_fetch = \
        installer.load_local_release_validation(directory)
    body = v2_installer.fetch_and_verify_release_manifest(
        public_key_hex=public_key_hex, fetch=manifest_fetch)
    _, metadata = v2_installer.fetch_and_verify_bundle(
        body, fetch=bundle_fetch, public_key_hex=public_key_hex,
        core_policy_public_key_hex=core_policy_key_hex)
    assert metadata["nodeMonitor"]["commit"] == MONITOR_COMMIT


def test_separation_e_validation_keys_are_never_production_roots(tmp_path):
    directory, _, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, core_policy_key_hex, _, _ = \
        installer.load_local_release_validation(directory)
    assert public_key_hex != installer.RELEASE_PUBLIC_KEY_HEX
    assert core_policy_key_hex != "9fc91edbe763513490248a23ae97575a6b963101b644e01493a3860b99e35648"
    assert installer.RELEASE_PUBLIC_KEY_HEX == ""


def test_separation_f_production_installer_still_refuses_same_release_afterward(tmp_path):
    directory, document, _ = _write_local_release_validation_dir(tmp_path)
    public_key_hex, _, manifest_fetch, _ = installer.load_local_release_validation(directory)
    v2_installer.fetch_and_verify_release_manifest(
        public_key_hex=public_key_hex, fetch=manifest_fetch)

    def production_fetch(_url):
        return json.dumps(document).encode()

    with pytest.raises(installer.InstallError):
        v2_installer.fetch_and_verify_release_manifest(
            public_key_hex=v2_installer.RELEASE_PUBLIC_KEY_HEX, fetch=production_fetch)


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
    body = manifest_for_bundle(data)
    v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)


def test_monitor_controller_real_docker_socket_grant_is_still_refused():
    files = bundle_files()
    files["compose.monitor-controller.yaml"] = (
        b"services:\n  controller:\n"
        b"    volumes:\n"
        b"      - /var/run/docker.sock:/var/run/docker.sock\n"
        b"      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro\n"
    )
    data = make_bundle(files)
    body = manifest_for_bundle(data)
    with pytest.raises(installer.InstallError, match="forbidden privileges"):
        v2_installer.validate_bundle(data, body, public_key_hex="a" * 64)



def test_installer_banner_is_terminal_width_aware(monkeypatch, capsys):
    monkeypatch.setattr(
        installer.shutil, "get_terminal_size",
        lambda fallback=(88, 24): os.terminal_size((64, 24)))
    installer.print_installer_banner()
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert any(line.strip() == "ELECTRUMX RAVENCOIN" for line in lines)
    assert any(line.strip() == "Verified Node Installer" for line in lines)
    assert max(len(line) for line in lines) <= 64


def test_interactive_choices_have_distinct_spaced_sections(capsys):
    args = installer.parse_args([])
    assert installer.choose_bootstrap(args, True, prompt=lambda _message: "1") == "chainstrap"
    assert installer.choose_monitor(args, True, prompt=lambda _message: "y") is True
    assert installer.choose_monitor_controller(
        args, True, True, prompt=lambda _message: "n") is False
    output = capsys.readouterr().out
    assert "[ 2 / 4  Blockchain bootstrap ]" in output
    assert "[ 3 / 4  Ravencoin Node Monitor ]" in output
    assert "[ 4 / 4  Advanced host controls ]" in output
    assert "requires sudo" in output


def test_installation_summary_makes_advanced_controller_explicit(capsys, tmp_path):
    installer.print_installation_summary(tmp_path / "storage", "chainstrap", True, False)
    output = capsys.readouterr().out
    assert "Installation summary" in output
    assert "ChainStrap Fast Verified Bootstrap" in output
    assert "Node Monitor" in output and "enabled" in output
    assert "Advanced controls" in output and "disabled" in output
    assert "Docker images" in output and "unchanged" in output


def test_chainstrap_activation_dispatches_live_progress(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        installer, "run_chainstrap_activation_with_live_logs",
        lambda root, base: calls.append((root, list(base))))
    installer.activate_compose(tmp_path, ["docker", "compose"], "chainstrap")
    assert calls == [(tmp_path, ["docker", "compose"])]


def test_p2p_activation_keeps_normal_detached_start(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        installer, "run_checked",
        lambda argv, **kwargs: calls.append((list(argv), kwargs.get("cwd"))))
    installer.activate_compose(tmp_path, ["docker", "compose"], "p2p")
    assert calls == [(["docker", "compose", "up", "-d", "--no-build"], tmp_path)]
