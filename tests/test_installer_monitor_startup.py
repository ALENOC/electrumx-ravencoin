"""Monitor boot recovery is automatic, signed and rolled back with installation."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("startup_installer", ROOT / "electrumx-ravencoin-install.py")
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def test_startup_helper_is_required_in_authenticated_bundle():
    assert installer.MONITOR_STARTUP_SCRIPT in installer.REQUIRED_BUNDLE_PATHS


def test_unit_preserves_identity_project_and_overlays(monkeypatch, tmp_path):
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(installer.os, "getegid", lambda: 1000)
    files = installer.compose_files("chainstrap", True, True)
    unit = installer.monitor_startup_unit_body(tmp_path / 'node $cash% "quoted"', files)
    assert "User=1000\nGroup=1000" in unit
    assert "ExecStart=:" in unit
    assert '"--project-name" "electrumx-ravencoin"' in unit
    for filename in files:
        assert filename in unit
    assert str(installer.TRUSTED_STARTUP_PATH) in unit
    assert "vendor/ravencoin-node-monitor" not in unit
    assert "%%" in unit
    assert '\\"quoted\\"' in unit
    assert "Restart=on-failure" in unit
    assert "RestartSec=10" in unit
    assert "PartOf=docker.service" in unit
    assert "WantedBy=multi-user.target docker.service" in unit


def test_preflight_preserves_existing_startup_installation(monkeypatch, tmp_path):
    existing = tmp_path / "existing.service"
    existing.write_text("preserve me")
    monkeypatch.setattr(installer, "MONITOR_STARTUP_UNIT_PATH", existing)
    monkeypatch.setattr(installer, "require_command", lambda name: name)
    with pytest.raises(installer.InstallError, match="refusing to overwrite"):
        installer.monitor_startup_prerequisites()
    assert existing.read_text() == "preserve me"


def test_tampered_privileged_copy_never_enables_service(monkeypatch, tmp_path):
    trusted = tmp_path / "trusted" / "monitor-startup.py"
    trusted.parent.mkdir()
    monkeypatch.setattr(installer, "TRUSTED_STARTUP_PATH", trusted)
    monkeypatch.setattr(installer, "_sudo_prefix", lambda: [])
    commands = []

    def fake_run(command):
        commands.append(command)
        if command[0] == "install" and command[1] != "-d":
            Path(command[-1]).write_bytes(b"tampered")

    monkeypatch.setattr(installer, "run_checked", fake_run)
    with pytest.raises(installer.InstallError, match="SHA-256 mismatch"):
        installer.install_monitor_startup(tmp_path, ["compose.yaml"], hashlib.sha256(b"signed").hexdigest())
    assert not any("enable" in command or command[0] == "mv" for command in commands)
    assert not trusted.exists()


@pytest.fixture
def fresh_install(monkeypatch, tmp_path):
    events = []
    for name in ("monitor_startup_prerequisites", "require_clean_docker_project_runtime"):
        monkeypatch.setattr(installer, name, lambda: None)
    for name in ("require_clean_storage_root", "prepare_storage_layout",
                 "write_monitor_env", "write_initial_update_state"):
        monkeypatch.setattr(installer, name, lambda *args: None)
    for name in ("extract_bundle", "write_storage_env", "initialize_storage_permissions"):
        monkeypatch.setattr(installer, name, lambda *args: None)
    monkeypatch.setattr(installer, "_bundle_member_sha256", lambda *args: "a" * 64)
    monkeypatch.setattr(installer, "run_checked", lambda *args, **kwargs: None)
    monkeypatch.setattr(installer, "activate_compose", lambda *args: None)
    monkeypatch.setattr(installer, "state_dir_for", lambda root: tmp_path / "state")
    monkeypatch.setattr(installer, "verify_monitor_host_publish", lambda *args: events.append("verify"))
    monkeypatch.setattr(installer, "install_monitor_startup", lambda *args: events.append("startup"))
    monkeypatch.setattr(installer, "write_install_marker", lambda *args, **kwargs: events.append("marker"))
    monkeypatch.setattr(installer, "uninstall_monitor_startup_best_effort", lambda: events.append("cleanup"))
    return tmp_path, events


def test_fresh_monitor_deploy_automatically_enables_boot_recovery(fresh_install):
    root, events = fresh_install
    installer.install_fresh(root / "node", b"bundle", body={}, metadata={},
                            bootstrap="p2p", monitor=True, controller=False, storage_root=root / "storage")
    assert events == ["verify", "startup", "marker"]


def test_deploy_without_monitor_does_not_install_boot_service(fresh_install):
    root, events = fresh_install
    installer.install_fresh(root / "node", b"bundle", body={}, metadata={},
                            bootstrap="p2p", monitor=False, controller=False, storage_root=root / "storage")
    assert events == ["marker"]


def test_partial_service_install_is_removed_on_rollback(monkeypatch, fresh_install):
    root, events = fresh_install

    def fail(*args):
        raise installer.InstallError("simulated enable failure")

    monkeypatch.setattr(installer, "install_monitor_startup", fail)
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: None)
    with pytest.raises(installer.InstallError, match="simulated enable failure"):
        installer.install_fresh(root / "node", b"bundle", body={}, metadata={},
                                bootstrap="p2p", monitor=True, controller=False, storage_root=root / "storage")
    assert events == ["verify", "cleanup"]
    assert not (root / "node").exists()


def test_service_is_enabled_only_after_trusted_copy_verification(monkeypatch, tmp_path):
    from types import SimpleNamespace

    trusted = tmp_path / "trusted" / "monitor-startup.py"
    unit_path = tmp_path / "startup.service"
    expected = "a" * 64
    monkeypatch.setattr(installer, "TRUSTED_STARTUP_PATH", trusted)
    monkeypatch.setattr(installer, "MONITOR_STARTUP_UNIT_PATH", unit_path)
    monkeypatch.setattr(installer, "_sudo_prefix", lambda: [])
    events = []
    monkeypatch.setattr(installer, "run_checked", lambda command: events.append(command))

    def digest(path):
        events.append(["verify-digest", str(path)])
        return expected

    monkeypatch.setattr(installer, "_file_sha256", digest)
    original_stat = Path.stat

    def fake_stat(path, *args, **kwargs):
        if path in (trusted, trusted.parent):
            return SimpleNamespace(st_uid=0, st_gid=0, st_mode=0o755)
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    installer.install_monitor_startup(tmp_path, ["compose.yaml", "compose.monitor.yaml"], expected)
    enabled = events.index(["systemctl", "enable", installer.MONITOR_STARTUP_UNIT])
    verified = events.index(["verify-digest", str(trusted)])
    assert verified < enabled
    assert not any("--now" in command for command in events)
    assert ["systemctl", "daemon-reload"] in events
