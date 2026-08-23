# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from __future__ import annotations

import importlib.util
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

SPEC = importlib.util.spec_from_file_location(
    "legacy_1_13_1_apply", SCRIPTS / "legacy_1_13_1_apply.py")
legacy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(legacy)


def _cp(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _discovery(tmp_path):
    return {
        "root": tmp_path,
        "electrumxVersion": "1.13.1",
        "coreVersion": "4.8.0",
        "storage": {
            "ravencoin-data": "electrumx-ravencoin_ravencoin-data",
            "ravencoin-config": "electrumx-ravencoin_ravencoin-config",
            "electrumx-data": "electrumx-ravencoin_electrumx-data",
            "rpc-secrets": "electrumx-ravencoin_rpc-secrets",
            "raven-secrets": "electrumx-ravencoin_raven-secrets",
        },
        "electrumxHeight": 123,
    }


def test_target_release_is_1_13_8():
    assert legacy.TARGET_ELECTRUMX_VERSION == "1.13.8"


def test_legacy_compose_selection_preserves_named_volumes_without_storage_overlay():
    marker = {
        "schemaVersion": 1,
        "bootstrapChoice": "p2p",
        "nodeMonitorEnabled": False,
        "monitorControllerEnabled": False,
        "storageMode": legacy.LEGACY_STORAGE_MODE,
    }
    assert legacy._legacy_compose_files(marker) == [legacy.runtime.BASE_COMPOSE]
    assert legacy.runtime.STORAGE_OVERLAY not in legacy._legacy_compose_files(marker)


def test_legacy_compose_selection_refuses_implicit_in_project_monitor():
    marker = {
        "storageMode": legacy.LEGACY_STORAGE_MODE,
        "nodeMonitorEnabled": True,
        "monitorControllerEnabled": False,
    }
    with pytest.raises(legacy.LegacyAdoptionError, match="cannot absorb"):
        legacy._legacy_compose_files(marker)


def test_named_volume_identity_is_project_bound(monkeypatch):
    inspected = []
    monkeypatch.setattr(legacy, "_inspect_named_volume", lambda name: inspected.append(name) or {})
    expected = legacy._expected_data_volumes()
    legacy._prove_named_volume_objects(expected)
    assert inspected == list(expected.values())

    changed = dict(expected)
    changed["electrumx-data"] = "other-project_electrumx-data"
    with pytest.raises(legacy.LegacyAdoptionError, match="identity changed"):
        legacy._prove_named_volume_objects(changed)


def test_candidate_proof_never_requires_bind_host_dirs(monkeypatch, tmp_path):
    expected = legacy._expected_data_volumes()
    monkeypatch.setattr(legacy, "_rendered_named_model", lambda root: {})
    monkeypatch.setattr(legacy, "_prove_named_volume_objects", lambda value: None)
    legacy._prove_candidate_named_storage(tmp_path, [legacy.runtime.BASE_COMPOSE], expected)


def test_candidate_proof_refuses_storage_overlay(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy, "_rendered_named_model", lambda root: {})
    with pytest.raises(legacy.LegacyAdoptionError, match="storage overlay"):
        legacy._prove_candidate_named_storage(
            tmp_path,
            [legacy.runtime.BASE_COMPOSE, legacy.runtime.STORAGE_OVERLAY],
            legacy._expected_data_volumes(),
        )


def test_adoption_marker_is_atomic_private_and_disables_chainstrap(tmp_path):
    legacy._write_adoption_marker(tmp_path)
    marker_path = tmp_path / legacy.runtime.INSTALL_MARKER
    marker = legacy.runtime.read_install_marker(tmp_path)
    assert marker["bootstrapChoice"] == "p2p"
    assert marker["nodeMonitorEnabled"] is False
    assert marker["storageMode"] == legacy.LEGACY_STORAGE_MODE
    assert marker["legacyAdoption"]["chainstrapRerunAllowed"] is False
    assert marker_path.stat().st_mode & 0o777 == 0o600


def test_discovery_rejects_wrong_electrumx_version(monkeypatch, tmp_path):
    (tmp_path / legacy.runtime.BASE_COMPOSE).write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(legacy, "_rendered_named_model", lambda root: {})
    monkeypatch.setattr(legacy, "_container_id", lambda root, service: service + "-id")

    def fake_compose(root, *args):
        if "electrumx_rpc" in args:
            return _cp('{"version":"ElectrumX-RVN 1.13.0","db height":1}')
        return _cp("Raven Core Daemon version v4.8.0\n")

    monkeypatch.setattr(legacy, "_compose", fake_compose)
    with pytest.raises(legacy.LegacyAdoptionError, match="supports only ElectrumX-RVN 1.13.1"):
        legacy.discover_legacy_install(tmp_path)


def test_discovery_refuses_bind_backed_volume(monkeypatch):
    monkeypatch.setattr(legacy.subprocess, "run", lambda *a, **k: _cp(
        '[{"Driver":"local","Options":{"type":"none","o":"bind","device":"/srv/data"}}]'))
    with pytest.raises(legacy.LegacyAdoptionError, match="plain local named volume"):
        legacy._inspect_named_volume("electrumx-ravencoin_ravencoin-data")


def test_noninteractive_adoption_defaults_to_refusal(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy.sys.stdin, "isatty", lambda: False)
    with pytest.raises(legacy.LegacyAdoptionError, match="requires a TTY"):
        legacy._confirm(_discovery(tmp_path), assume_yes=False)


def test_runtime_hooks_route_string_storage_identity_to_named_volume_proofs(monkeypatch, tmp_path):
    calls = []
    # install_runtime_compatibility_hooks() rebinds module-level attributes of
    # update_runtime for the rest of the process. Record the originals through
    # monkeypatch first so pytest restores them at teardown; leaking them made
    # later tests exercise the hooks instead of the native updater code, which
    # is exactly what hid the named-volume post-switch failure.
    for name in ("_required_update_compose_files",
                 "prove_running_storage_continuity",
                 "prove_candidate_storage_continuity",
                 "_docker_volume_bindings"):
        monkeypatch.setattr(legacy.runtime, name, getattr(legacy.runtime, name))
    monkeypatch.setattr(legacy, "_prove_candidate_named_storage",
                        lambda root, files, expected: calls.append((tuple(files), dict(expected))))
    legacy.install_runtime_compatibility_hooks()
    legacy.runtime.prove_candidate_storage_continuity(
        tmp_path, [legacy.runtime.BASE_COMPOSE], legacy._expected_data_volumes())
    assert calls and calls[0][0] == (legacy.runtime.BASE_COMPOSE,)


def test_parser_requires_explicit_discover_or_apply_command():
    parser = legacy.build_parser()
    assert parser.parse_args(["discover"]).command == "discover"
    parsed = parser.parse_args(["apply", "--yes-adopt-legacy"])
    assert parsed.command == "apply"
    assert parsed.yes_adopt_legacy is True


def test_discover_command_is_read_only(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy.cli, "DEFAULT_INSTALL_ROOT", tmp_path)
    monkeypatch.setattr(legacy, "discover_legacy_install", lambda root: _discovery(tmp_path))
    monkeypatch.setattr(
        legacy, "_write_adoption_marker",
        lambda root, **kwargs: pytest.fail("discover must not write marker"))
    monkeypatch.setattr(
        legacy, "_preflight_pending_candidate",
        lambda: pytest.fail("discover must not preflight/apply candidate"))
    assert legacy.main(["discover"]) == 0
    assert not (tmp_path / legacy.runtime.INSTALL_MARKER).exists()


def test_apply_preflights_candidate_before_any_marker_mutation(monkeypatch, tmp_path):
    events = []
    monkeypatch.setattr(legacy.cli, "DEFAULT_INSTALL_ROOT", tmp_path)
    monkeypatch.setattr(
        legacy, "_preflight_pending_candidate",
        lambda: events.append("candidate-preflight") or {"electrumxVersion": "1.13.8"})
    monkeypatch.setattr(
        legacy, "discover_legacy_install",
        lambda root: events.append("discovery") or _discovery(tmp_path))
    monkeypatch.setattr(legacy, "_confirm", lambda discovery, assume_yes: events.append("confirm"))

    def write_marker(root, **kwargs):
        events.append("marker")
        legacy.runtime._write_private_json(root / legacy.runtime.INSTALL_MARKER, {
            "schemaVersion": 1,
            "electrumxVersion": "1.13.1",
            "bootstrapChoice": "p2p",
            "nodeMonitorEnabled": False,
            "monitorControllerEnabled": False,
            "storageMode": legacy.LEGACY_STORAGE_MODE,
            "legacyAdoption": {
                "source": "setup.sh",
                "fromVersion": "1.13.1",
                "storageMode": legacy.LEGACY_STORAGE_MODE,
                "chainstrapRerunAllowed": False,
            },
        })

    monkeypatch.setattr(legacy, "_write_adoption_marker", write_marker)
    monkeypatch.setattr(legacy, "install_runtime_compatibility_hooks", lambda: events.append("hooks"))
    monkeypatch.setattr(legacy.cli, "main", lambda args: events.append("apply") or 0)

    assert legacy.main(["apply", "--yes-adopt-legacy"]) == 0
    assert events == ["candidate-preflight", "discovery", "confirm", "marker", "hooks", "apply"]


def test_missing_candidate_refuses_before_marker_write(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy.cli, "DEFAULT_INSTALL_ROOT", tmp_path)
    monkeypatch.setattr(
        legacy, "_preflight_pending_candidate",
        lambda: (_ for _ in ()).throw(legacy.LegacyAdoptionError("no pending candidate")))
    monkeypatch.setattr(
        legacy, "_write_adoption_marker",
        lambda root, **kwargs: pytest.fail("marker must not be written without candidate"))
    assert legacy.main(["apply", "--yes-adopt-legacy"]) == 1
    assert not (tmp_path / legacy.runtime.INSTALL_MARKER).exists()


def test_failed_apply_removes_marker_created_by_same_invocation(monkeypatch, tmp_path):
    monkeypatch.setattr(legacy.cli, "DEFAULT_INSTALL_ROOT", tmp_path)
    monkeypatch.setattr(legacy, "_preflight_pending_candidate", lambda: {"electrumxVersion": "1.13.8"})
    monkeypatch.setattr(legacy, "discover_legacy_install", lambda root: _discovery(tmp_path))
    monkeypatch.setattr(legacy, "_confirm", lambda discovery, assume_yes: None)
    monkeypatch.setattr(legacy, "install_runtime_compatibility_hooks", lambda: None)
    monkeypatch.setattr(legacy.cli, "main", lambda args: 1)

    assert legacy.main(["apply", "--yes-adopt-legacy"]) == 1
    assert not (tmp_path / legacy.runtime.INSTALL_MARKER).exists()


def test_marker_rollback_refuses_to_remove_promoted_or_unknown_marker(tmp_path):
    legacy._write_adoption_marker(tmp_path, electrumx_version=legacy.TARGET_ELECTRUMX_VERSION)
    with pytest.raises(legacy.LegacyAdoptionError, match="refusing to remove"):
        legacy._remove_created_adoption_marker(tmp_path)
    assert (tmp_path / legacy.runtime.INSTALL_MARKER).exists()
