from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "electrumx_ravencoin_installer_storage", ROOT / "electrumx-ravencoin-install.py")
installer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(installer)


def test_storage_overlay_is_required_release_content():
    assert installer.STORAGE_OVERLAY == "compose.storage.yaml"
    assert installer.STORAGE_OVERLAY in installer.REQUIRED_BUNDLE_PATHS


def test_compose_files_always_include_storage_overlay():
    files = installer.compose_files("chainstrap", True, False)
    assert files[0:2] == ["compose.yaml", "compose.storage.yaml"]
    assert "compose.chainstrap.yaml" in files
    assert "compose.monitor.yaml" in files


def test_storage_overlay_binds_all_persistent_project_data():
    text = (ROOT / "compose.storage.yaml").read_text(encoding="utf-8")
    for volume, variable in (
        ("ravencoin-data", "RAVENCOIN_DATA_HOST_DIR"),
        ("ravencoin-config", "RAVENCOIN_CONFIG_HOST_DIR"),
        ("electrumx-data", "ELECTRUMX_DATA_HOST_DIR"),
        ("monitor-data", "MONITOR_DATA_HOST_DIR"),
    ):
        assert f"  {volume}:" in text
        assert variable in text
    assert "DockerRootDir" in text


def test_monitor_has_selected_disk_data_mount_but_history_stays_memory():
    compose = (ROOT / "compose.monitor.yaml").read_text(encoding="utf-8")
    assert "monitor-data:/data" in compose
    env_writer = installer.write_monitor_env
    # Source-level contract: selected disk is available for future sqlite opt-in,
    # while the installer keeps the wear-safe RAM history default.
    import inspect
    source = inspect.getsource(env_writer)
    assert "HISTORY_STORAGE=memory" in source
    assert "HISTORY_DB_PATH=/data/history.db" in source


def test_validate_storage_root_rejects_existing_path(tmp_path):
    existing = tmp_path / "already-there"
    existing.mkdir()
    with pytest.raises(installer.InstallError, match="already exists"):
        installer.validate_storage_root_path(existing)


def test_validate_storage_root_rejects_filesystem_or_home(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.Path, "home", classmethod(lambda cls: tmp_path))
    with pytest.raises(installer.InstallError, match="dedicated child"):
        installer.validate_storage_root_path(tmp_path)


def test_choose_storage_root_uses_displayed_disk(monkeypatch, tmp_path):
    first_parent = tmp_path / "small"
    second_parent = tmp_path / "large"
    first_parent.mkdir()
    second_parent.mkdir()
    candidates = [
        {"source": "/dev/a1", "mountpoint": first_parent, "fstype": "ext4",
         "size": 1000, "free": 100, "root": first_parent / "electrumx-ravencoin-storage"},
        {"source": "/dev/b1", "mountpoint": second_parent, "fstype": "ext4",
         "size": 2000, "free": 1500, "root": second_parent / "electrumx-ravencoin-storage"},
    ]
    monkeypatch.setattr(installer, "discover_storage_candidates", lambda: candidates)
    selected = installer.choose_storage_root(
        SimpleNamespace(storage_root=None), True, prompt=lambda _text: "2")
    assert selected == candidates[1]["root"].resolve()


def test_noninteractive_fresh_install_requires_explicit_storage_root():
    with pytest.raises(installer.InstallError, match="--storage-root is required"):
        installer.choose_storage_root(SimpleNamespace(storage_root=None), False)


def test_write_storage_env_records_four_absolute_paths(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    (root / ".env").write_text("EXISTING=value\n", encoding="utf-8")
    storage = tmp_path / "selected-storage"
    installer.write_storage_env(root, storage)
    text = (root / ".env").read_text(encoding="utf-8")
    assert f"RAVENCOIN_DATA_HOST_DIR={storage / 'ravencoin-data'}" in text
    assert f"RAVENCOIN_CONFIG_HOST_DIR={storage / 'ravencoin-config'}" in text
    assert f"ELECTRUMX_DATA_HOST_DIR={storage / 'electrumx-data'}" in text
    assert f"MONITOR_DATA_HOST_DIR={storage / 'monitor-data'}" in text


def test_prepare_storage_layout_is_dedicated_and_complete(tmp_path):
    storage = tmp_path / "new-storage"
    installer.prepare_storage_layout(storage)
    assert storage.is_dir()
    assert {entry.name for entry in storage.iterdir()} == set(installer.STORAGE_SUBDIRS)


def test_install_marker_records_selected_storage(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    storage = tmp_path / "storage"
    body = {
        "electrumxVersion": "1.13.0", "artifactDigest": "sha256:" + "0" * 64,
        "coreRepository": "RavenProject/Ravencoin", "coreVersion": "4.8.0",
        "coreCommit": "1" * 40, "safeCorePolicyVersion": 3,
        "dbCompatibility": {"schemaVersion": 1},
    }
    metadata = {"sourceCommit": "2" * 40,
                "nodeMonitor": {"commit": "3" * 40}}
    installer.write_install_marker(
        root, body=body, metadata=metadata, bootstrap="chainstrap", monitor=True,
        controller=False, storage_root=storage)
    import json
    marker = json.loads((root / installer.INSTALL_MARKER).read_text(encoding="utf-8"))
    assert marker["storageRoot"] == str(storage)
