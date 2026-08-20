# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys
import tarfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "build_local_release_validation_bundle",
    SCRIPTS / "build_local_release_validation_bundle.py")
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)

import policy  # noqa: E402


@pytest.fixture
def tiny_monitor_repo(tmp_path, monkeypatch):
    repo = tmp_path / "ravencoin-node-monitor"
    repo.mkdir()
    (repo / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
    (repo / ".env.example").write_text("BIND_PORT=8899\n", encoding="utf-8")
    contrib = repo / "contrib"
    contrib.mkdir()
    controller = contrib / "ravencoin-bandwidth-controller.py"
    controller.write_text("#!/usr/bin/env python3\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Bundle Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "bundle-test@example.invalid"],
                   cwd=repo, check=True)
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=repo, check=True)
    head = validation.git_head(repo)

    monkeypatch.setattr(validation, "load_monitor_pin", lambda: {
        "repository": "ALENOC/ravencoin-node-monitor",
        "commit": head,
        "bundledPath": validation.MONITOR_BUNDLE_PATH,
    })
    return repo, head


def _signed_test_policy():
    core_commit, core_version = validation.core_commit_and_version()
    body, entry = validation.load_validation_policy_body(core_commit, core_version)
    private_key, public_bytes = policy.generate_keypair()
    document = policy.sign_policy(
        body, private_key, key_id=policy.key_id_for(public_bytes))
    return document, public_bytes.hex(), entry


def test_validation_bundle_is_full_tracked_source_and_excludes_untracked_secret(
        tmp_path, tiny_monitor_repo):
    monitor_repo, monitor_head = tiny_monitor_repo
    policy_document, policy_public_hex, _ = _signed_test_policy()

    sentinel = ROOT / "LOCAL-VALIDATION-UNTRACKED-SECRET-SENTINEL.txt"
    sentinel.write_text("must never enter a release bundle\n", encoding="utf-8")
    try:
        data, metadata = validation.build_bundle(
            monitor_repo=monitor_repo,
            update_public_key_hex="a" * 64,
            core_policy_document=policy_document,
            core_policy_public_key_hex=policy_public_hex,
            ex_version=validation.electrumx_version(),
        )
    finally:
        sentinel.unlink(missing_ok=True)

    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        names = set(archive.getnames())
        # Build-critical paths that the old partial validation helper omitted.
        for required in (
                "docker/core/Dockerfile",
                "docker/core/entrypoint.sh",
                "docker/core/healthcheck.sh",
                "docker/bootstrap/Dockerfile",
                "contrib/bootstrap/chainstrap_bootstrap.py",
                "contrib/Dockerfile",
                "electrumx/__init__.py",
                "setup.py",
                "compose.yaml",
                "compose.chainstrap.yaml",
                "compose.monitor.yaml"):
            assert required in names, required

        assert sentinel.name not in names
        assert not any("PRIVATE-KEY" in name.upper() for name in names)
        assert (
            f"{validation.MONITOR_BUNDLE_PATH}/contrib/"
            "ravencoin-bandwidth-controller.py" in names)

        update_key = archive.extractfile(validation.UPDATE_PUBLIC_KEY_BUNDLE_PATH)
        assert update_key is not None
        assert update_key.read().decode().strip() == "a" * 64

        core_key = archive.extractfile(validation.CORE_POLICY_PUBLIC_KEY_BUNDLE_PATH)
        assert core_key is not None
        assert core_key.read().decode().strip() == policy_public_hex

        policy_file = archive.extractfile(validation.CORE_POLICY_BUNDLE_PATH)
        assert policy_file is not None
        bundled_policy = json.loads(policy_file.read().decode())
        assert bundled_policy == policy_document

    assert metadata["sourceCommit"] == validation.git_head(ROOT)
    assert metadata["nodeMonitor"]["commit"] == monitor_head


def test_validation_main_writes_public_material_only(
        tmp_path, tiny_monitor_repo, monkeypatch):
    monitor_repo, _ = tiny_monitor_repo
    out_dir = tmp_path / "validation-output"
    monkeypatch.setattr(
        sys, "argv",
        ["build_local_release_validation_bundle.py",
         "--monitor-repo", str(monitor_repo),
         "--out-dir", str(out_dir)])

    assert validation.main() == 0
    names = {path.name for path in out_dir.iterdir()}
    assert names == {
        "manifest.json",
        "bundle.tar.gz",
        "public-key.hex",
        "core-policy-public-key.hex",
    }
    assert all("PRIVATE" not in name.upper() for name in names)
    for path in out_dir.iterdir():
        assert path.stat().st_mode & 0o077 == 0


def test_validation_policy_binds_current_ravenproject_core():
    core_commit, core_version = validation.core_commit_and_version()
    body, entry = validation.load_validation_policy_body(core_commit, core_version)
    assert body["policyVersion"] == 3
    assert entry["repository"] == "RavenProject/Ravencoin"
    assert entry["commit"] == core_commit
    assert entry["version"] == core_version
    assert entry["status"] == "KNOWN_SAFE"
    assert entry["certification"]["result"] == "PASS"
    assert len(entry["reportDigest"]) == 64


def test_validation_helper_source_never_serializes_private_key():
    source = (SCRIPTS / "build_local_release_validation_bundle.py").read_text(
        encoding="utf-8")
    assert "PRIVATE-KEY-DESTROY-AFTER-USE" not in source
    assert ".private_bytes_raw()" not in source
