# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for updater preservation of installer-selected bind storage."""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_runtime as runtime  # noqa: E402

INSTALLER_SPEC = importlib.util.spec_from_file_location(
    "storage_compose_installer_source", ROOT / "electrumx-ravencoin-install.py")
installer = importlib.util.module_from_spec(INSTALLER_SPEC)
sys.modules[INSTALLER_SPEC.name] = installer
INSTALLER_SPEC.loader.exec_module(installer)


def _marker(*, monitor=False, controller=False):
    return {
        "schemaVersion": 1,
        "bootstrapChoice": "chainstrap",
        "nodeMonitorEnabled": monitor,
        "monitorControllerEnabled": controller,
    }


def _write_files(root: pathlib.Path, names):
    for name in names:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# test\n", encoding="utf-8")


@pytest.mark.parametrize("bootstrap", ["chainstrap", "p2p"])
@pytest.mark.parametrize("monitor,controller", [(False, False), (True, False), (True, True)])
def test_updater_f_set_covers_every_installer_runtime_compose_file(
        tmp_path, bootstrap, monitor, controller):
    """Omitting compose.storage.yaml (or any live overlay) must fail this test."""
    installer_files = installer.compose_files(bootstrap, monitor, controller)
    # ChainStrap is a completed one-shot bootstrap, not part of a running-node
    # software update. Every other file written/selected by the installer is a
    # runtime dependency and must remain in the updater's explicit -f set.
    expected_runtime = [
        name for name in installer_files if name != installer.CHAINSTRAP_OVERLAY
    ]
    _write_files(tmp_path, expected_runtime)

    marker = _marker(monitor=monitor, controller=controller)
    actual = runtime.update_compose_files(marker, tmp_path)

    assert runtime.STORAGE_OVERLAY == installer.STORAGE_OVERLAY
    assert runtime.STORAGE_OVERLAY in actual
    assert actual == expected_runtime


def test_updater_refuses_missing_storage_overlay_before_any_switch(tmp_path):
    _write_files(tmp_path, [runtime.BASE_COMPOSE])
    with pytest.raises(runtime.UpdateRuntimeError, match="compose.storage.yaml"):
        runtime.update_compose_files(_marker(), tmp_path)


def _storage_env(tmp_path: pathlib.Path):
    values = {}
    lines = []
    for logical, key in runtime.STORAGE_BIND_ENV.items():
        directory = tmp_path / logical
        directory.mkdir()
        values[logical] = directory.resolve()
        lines.append(f"{key}={directory}\n")
    (tmp_path / ".env").write_text("".join(lines), encoding="utf-8")
    return values


def test_storage_env_requires_all_four_existing_bind_directories(tmp_path):
    expected = _storage_env(tmp_path)
    assert runtime._read_storage_env(tmp_path) == expected

    lines = (tmp_path / ".env").read_text(encoding="utf-8").splitlines()
    (tmp_path / ".env").write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(runtime.UpdateRuntimeError, match="missing storage binding"):
        runtime._read_storage_env(tmp_path)


def test_storage_env_rejects_symlinked_bind_directory(tmp_path):
    expected = _storage_env(tmp_path)
    victim = expected["ravencoin-data"]
    victim.rmdir()
    real = tmp_path / "real-ravencoin-data"
    real.mkdir()
    os.symlink(real, victim)
    with pytest.raises(runtime.UpdateRuntimeError, match="non-symlink directory"):
        runtime._read_storage_env(tmp_path)


def test_candidate_storage_must_resolve_to_exact_existing_paths(monkeypatch, tmp_path):
    expected = _storage_env(tmp_path)
    files = [runtime.BASE_COMPOSE, runtime.STORAGE_OVERLAY]
    _write_files(tmp_path, files)
    monkeypatch.setattr(runtime, "_compose_storage_bindings", lambda root, selected: expected)
    runtime.prove_candidate_storage_continuity(tmp_path, files, expected)

    changed = dict(expected)
    changed["electrumx-data"] = tmp_path / "wrong-electrumx-data"
    changed["electrumx-data"].mkdir()
    monkeypatch.setattr(runtime, "_compose_storage_bindings", lambda root, selected: changed)
    with pytest.raises(runtime.UpdateRuntimeError, match="would not preserve"):
        runtime.prove_candidate_storage_continuity(tmp_path, files, expected)


def test_running_storage_preflight_proves_volume_objects_and_active_mounts(
        monkeypatch, tmp_path):
    expected = _storage_env(tmp_path)
    files = [runtime.BASE_COMPOSE, runtime.STORAGE_OVERLAY]
    _write_files(tmp_path, files)
    calls = []

    monkeypatch.setattr(runtime, "_compose_storage_bindings", lambda root, selected: expected)
    monkeypatch.setattr(
        runtime, "_docker_volume_bindings",
        lambda bindings: calls.append(("volumes", dict(bindings))))
    monkeypatch.setattr(
        runtime, "_verify_active_storage_mounts",
        lambda root, selected, marker: calls.append(("mounts", tuple(selected))))

    assert runtime.prove_running_storage_continuity(
        tmp_path, files, _marker()) == expected
    assert calls == [
        ("volumes", expected),
        ("mounts", tuple(files)),
    ]
