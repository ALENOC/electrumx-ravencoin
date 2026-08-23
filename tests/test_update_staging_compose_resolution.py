# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for Compose file resolution in a staged release tree.

Hardware qualification of the legacy 1.13.1 -> 1.13.2 upgrade failed during
staging with::

    UpdateRuntimeError: command failed (1): sh ./setup.sh --bundled-core
    stat .../compose.local-core-identity.yaml: no such file or directory

The named overlay is host-local: no release ships it. The staged tree receives
the operator's .env, and setup.sh validated the Compose model with a bare
``docker compose config``, which resolves its file set implicitly from
COMPOSE_FILE in the environment or in .env. These tests reproduce both
resolution channels with a stub ``docker`` that implements only that rule, so
they run without a Docker daemon.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_runtime as runtime  # noqa: E402
import legacy_1_13_1_apply as legacy  # noqa: E402

HOST_LOCAL_OVERLAY = "compose.local-core-identity.yaml"

STUB_DOCKER = '''#!/usr/bin/env python3
"""Minimal ``docker`` stub implementing Compose file resolution only."""
import os
import pathlib
import sys

argv = sys.argv[1:]
log = os.environ.get("STUB_DOCKER_LOG")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(argv) + "\\n")

if not argv or argv[0] != "compose":
    # info, version, network ls/inspect: nothing this stub needs to model.
    if argv[:2] == ["network", "ls"]:
        sys.exit(0)
    if argv[:2] == ["network", "inspect"]:
        print("[]")
        sys.exit(0)
    sys.exit(0)

rest = argv[1:]
if rest[:1] == ["version"]:
    sys.exit(0)

selected = []
index = 0
while index < len(rest):
    item = rest[index]
    if item == "-f":
        selected.append(rest[index + 1])
        index += 2
        continue
    if item == "-p":
        index += 2
        continue
    index += 1

if not selected:
    # Same precedence Compose itself applies.
    value = os.environ.get("COMPOSE_FILE")
    if value is None:
        env_file = pathlib.Path(".env")
        if env_file.is_file():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("COMPOSE_FILE="):
                    value = line.split("=", 1)[1].strip()
    if value:
        selected = [entry for entry in value.split(":") if entry]
    else:
        selected = ["compose.yaml"]

for entry in selected:
    path = pathlib.Path(entry)
    if not path.is_file():
        sys.stderr.write(
            "stat %s: no such file or directory\\n" % (pathlib.Path.cwd() / entry))
        sys.exit(1)
sys.exit(0)
'''


@pytest.fixture()
def stub_docker(tmp_path):
    bin_dir = tmp_path / "stub-bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text(STUB_DOCKER, encoding="utf-8")
    docker.chmod(0o755)
    return bin_dir


def _staging_tree(tmp_path, *, setup_source: str, compose_file: str | None) -> pathlib.Path:
    """A staged release tree with the release Compose set and an operator .env."""
    staging = tmp_path / "staging"
    (staging / "core-safety" / "scripts").mkdir(parents=True)
    (staging / ".secrets").mkdir()
    (staging / "setup.sh").write_text(setup_source, encoding="utf-8")
    shutil.copy(SCRIPTS / "configure_monitor_admin_network.py",
                staging / "core-safety" / "scripts" / "configure_monitor_admin_network.py")
    for name in sorted(runtime.RELEASE_COMPOSE_FILES):
        shutil.copy(ROOT / name, staging / name)
    shutil.copy(ROOT / ".env.example", staging / ".env.example")
    env_lines = (ROOT / ".env.example").read_text(encoding="utf-8")
    if compose_file is not None:
        env_lines += f"\nCOMPOSE_FILE={compose_file}\n"
    (staging / ".env").write_text(env_lines, encoding="utf-8")
    (staging / ".env").chmod(0o600)
    return staging


def _run_setup(staging: pathlib.Path, bin_dir: pathlib.Path, *, environment=None):
    child = dict(os.environ)
    child["PATH"] = f"{bin_dir}{os.pathsep}{child['PATH']}"
    child["STUB_DOCKER_LOG"] = str(staging.parent / "docker.log")
    child.pop("COMPOSE_FILE", None)
    if environment:
        child.update(environment)
    return subprocess.run(["sh", "./setup.sh", "--bundled-core"], cwd=staging,
                          capture_output=True, text=True, check=False, env=child)


def _pre_fix_setup_source() -> str:
    """setup.sh as qualified, with the implicit Compose validation restored."""
    source = (ROOT / "setup.sh").read_text(encoding="utf-8")
    return source.replace("docker compose -f compose.yaml config --quiet",
                          "docker compose config --quiet")


def test_stub_reproduces_the_qualification_failure(tmp_path, stub_docker):
    """The pre-fix setup.sh fails exactly as the Pi apply did."""
    staging = _staging_tree(
        tmp_path, setup_source=_pre_fix_setup_source(),
        compose_file=f"compose.yaml:{HOST_LOCAL_OVERLAY}")
    completed = _run_setup(staging, stub_docker)
    assert completed.returncode != 0
    assert f"stat {staging / HOST_LOCAL_OVERLAY}: no such file or directory" in (
        completed.stdout + completed.stderr)


def test_staging_setup_ignores_host_local_overlay_in_env(tmp_path, stub_docker):
    """Fixed setup.sh validates the release model despite a stale .env entry."""
    staging = _staging_tree(
        tmp_path, setup_source=(ROOT / "setup.sh").read_text(encoding="utf-8"),
        compose_file=f"compose.yaml:{HOST_LOCAL_OVERLAY}")
    completed = _run_setup(staging, stub_docker)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert HOST_LOCAL_OVERLAY not in completed.stderr
    log = (tmp_path / "docker.log").read_text(encoding="utf-8")
    assert "compose -f compose.yaml config --quiet" in log


def test_staging_setup_ignores_ambient_compose_file(tmp_path, stub_docker):
    """The second channel: COMPOSE_FILE exported by the invoking environment."""
    staging = _staging_tree(
        tmp_path, setup_source=(ROOT / "setup.sh").read_text(encoding="utf-8"),
        compose_file=None)
    completed = _run_setup(
        staging, stub_docker,
        environment={"COMPOSE_FILE": f"compose.yaml:{HOST_LOCAL_OVERLAY}"})
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_pre_fix_setup_also_fails_through_the_ambient_channel(tmp_path, stub_docker):
    staging = _staging_tree(
        tmp_path, setup_source=_pre_fix_setup_source(), compose_file=None)
    completed = _run_setup(
        staging, stub_docker,
        environment={"COMPOSE_FILE": f"compose.yaml:{HOST_LOCAL_OVERLAY}"})
    assert completed.returncode != 0
    assert HOST_LOCAL_OVERLAY in completed.stdout + completed.stderr


def test_updater_child_environment_drops_compose_overrides(monkeypatch):
    monkeypatch.setenv("COMPOSE_FILE", f"compose.yaml:{HOST_LOCAL_OVERLAY}")
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "attacker-project")
    monkeypatch.setenv("COMPOSE_PROFILES", "surprise")
    child = runtime._child_environment()
    for name in runtime.COMPOSE_ENV_OVERRIDES:
        assert name not in child


def _install_root(tmp_path, *, compose_file: str | None, extra_files=()) -> pathlib.Path:
    root = tmp_path / "install"
    root.mkdir()
    for name in [runtime.BASE_COMPOSE, runtime.STORAGE_OVERLAY, *extra_files]:
        (root / name).write_text("services: {}\n", encoding="utf-8")
    env = "ELECTRUMX_DATA_DIR=/srv/electrumx\n"
    if compose_file is not None:
        env += f"COMPOSE_FILE={compose_file}\n"
    (root / ".env").write_text(env, encoding="utf-8")
    return root


def test_preflight_rejects_non_release_compose_selection(tmp_path):
    root = _install_root(tmp_path, compose_file=f"compose.yaml:{HOST_LOCAL_OVERLAY}")
    before = {path.name: path.read_bytes() for path in root.iterdir()}
    with pytest.raises(runtime.UpdateRuntimeError) as error:
        runtime.prove_env_compose_selection(root)
    assert HOST_LOCAL_OVERLAY in str(error.value)
    after = {path.name: path.read_bytes() for path in root.iterdir()}
    assert after == before


def test_preflight_rejects_release_file_missing_from_install(tmp_path):
    root = _install_root(
        tmp_path, compose_file=f"compose.yaml:{runtime.MONITOR_OVERLAY}")
    with pytest.raises(runtime.UpdateRuntimeError) as error:
        runtime.prove_env_compose_selection(root)
    assert runtime.MONITOR_OVERLAY in str(error.value)


def test_preflight_accepts_release_compose_selection(tmp_path):
    selection = f"{runtime.BASE_COMPOSE}:{runtime.CHAINSTRAP_OVERLAY}"
    root = _install_root(tmp_path, compose_file=selection,
                         extra_files=[runtime.CHAINSTRAP_OVERLAY])
    assert runtime.prove_env_compose_selection(root) == [
        runtime.BASE_COMPOSE, runtime.CHAINSTRAP_OVERLAY]


def test_update_compose_files_preserves_tls_overlay_from_env(tmp_path):
    root = _install_root(
        tmp_path,
        compose_file=f"{runtime.BASE_COMPOSE}:{runtime.TLS_OVERLAY}",
        extra_files=[runtime.TLS_OVERLAY],
    )
    marker = {
        "schemaVersion": 1,
        "bootstrapChoice": "p2p",
        "nodeMonitorEnabled": False,
        "monitorControllerEnabled": False,
    }

    assert runtime.update_compose_files(marker, root) == [
        runtime.BASE_COMPOSE,
        runtime.STORAGE_OVERLAY,
        runtime.TLS_OVERLAY,
    ]


def test_update_compose_files_never_reactivates_chainstrap(tmp_path):
    root = _install_root(
        tmp_path,
        compose_file=(
            f"{runtime.BASE_COMPOSE}:"
            f"{runtime.CHAINSTRAP_OVERLAY}:"
            f"{runtime.TLS_OVERLAY}"
        ),
        extra_files=[runtime.CHAINSTRAP_OVERLAY, runtime.TLS_OVERLAY],
    )
    marker = {
        "schemaVersion": 1,
        "bootstrapChoice": "chainstrap",
        "nodeMonitorEnabled": False,
        "monitorControllerEnabled": False,
    }

    files = runtime.update_compose_files(marker, root)

    assert runtime.TLS_OVERLAY in files
    assert runtime.CHAINSTRAP_OVERLAY not in files


def test_legacy_named_volume_runtime_accepts_tls_overlay():
    assert legacy._validate_legacy_runtime_compose_files([
        runtime.BASE_COMPOSE,
        runtime.TLS_OVERLAY,
    ]) == [
        runtime.BASE_COMPOSE,
        runtime.TLS_OVERLAY,
    ]


def test_legacy_named_volume_runtime_rejects_storage_overlay():
    with pytest.raises(legacy.LegacyAdoptionError):
        legacy._validate_legacy_runtime_compose_files([
            runtime.BASE_COMPOSE,
            runtime.STORAGE_OVERLAY,
        ])


def test_preflight_accepts_absent_compose_file(tmp_path):
    root = _install_root(tmp_path, compose_file=None)
    assert runtime.prove_env_compose_selection(root) == []


def test_preflight_rejects_duplicate_compose_file(tmp_path):
    root = _install_root(tmp_path, compose_file=runtime.BASE_COMPOSE)
    with (root / ".env").open("a", encoding="utf-8") as handle:
        handle.write(f"COMPOSE_FILE={runtime.BASE_COMPOSE}\n")
    with pytest.raises(runtime.UpdateRuntimeError):
        runtime.prove_env_compose_selection(root)


def _every_selectable_compose_file() -> set[str]:
    selected: set[str] = set()
    for monitor in (False, True):
        for controller in (False, True):
            marker = {
                "schemaVersion": 1,
                "bootstrapChoice": "p2p",
                "nodeMonitorEnabled": monitor,
                "monitorControllerEnabled": controller,
            }
            selected.update(runtime._required_update_compose_files(marker))
            legacy_marker = dict(marker, storageMode=legacy.LEGACY_STORAGE_MODE)
            if not monitor and not controller:
                selected.update(legacy._legacy_compose_files(legacy_marker))
    return selected


def test_every_selectable_compose_file_is_required_in_the_bundle():
    missing = sorted(_every_selectable_compose_file() - runtime.REQUIRED_BUNDLE_PATHS)
    assert not missing, (
        "a released update can select these Compose files, but the bundle "
        f"validator does not require them: {missing}")


def test_release_compose_files_are_tracked_sources():
    """The bundle ships tracked files, so the release set must be tracked."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z", "--", "compose*.yaml"], cwd=ROOT,
        capture_output=True, check=True).stdout.decode().split("\0")
    tracked = {name for name in tracked if name}
    assert runtime.RELEASE_COMPOSE_FILES == tracked
    assert HOST_LOCAL_OVERLAY not in tracked


def test_required_bundle_paths_cover_the_release_compose_model():
    for name in runtime.RELEASE_COMPOSE_FILES:
        assert name in runtime.REQUIRED_BUNDLE_PATHS


def test_legacy_upgrade_keeps_its_single_compose_model():
    """Named-volume legacy continuity and disabled ChainStrap stay unchanged."""
    marker = {
        "schemaVersion": 1,
        "bootstrapChoice": "p2p",
        "nodeMonitorEnabled": False,
        "monitorControllerEnabled": False,
        "storageMode": legacy.LEGACY_STORAGE_MODE,
    }
    assert legacy._legacy_compose_files(marker) == [runtime.BASE_COMPOSE]
    assert runtime.CHAINSTRAP_OVERLAY not in legacy._legacy_compose_files(marker)
