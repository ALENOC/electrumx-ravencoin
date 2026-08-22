#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""One-time, explicit adoption path for legacy setup.sh 1.13.1 installs.

The 1.13.2 transactional updater normally requires the installer marker and
bind-backed storage introduced by the new installer.  Historical 1.13.1
``setup.sh`` deployments predate both.  This wrapper discovers and proves that
specific legacy shape while the old node is still serving, requires explicit
operator consent, writes a narrow schema-v1 adoption marker atomically, and
then runs the normal v2 updater with process-local storage proof functions that
preserve the existing Docker named volumes verbatim.

Nothing here converts a Docker volume to a bind mount, derives an API from
Docker's private data-root, removes a volume, or runs ChainStrap.  The wrapper
accepts only ElectrumX-RVN 1.13.1 / Ravencoin Core 4.8.0 and the fixed Compose
project namespace.  Any ambiguity is refused before the old services stop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

import electrumx_update_cli as cli
import update_runtime as runtime

LEGACY_ELECTRUMX_VERSION = "1.13.1"
LEGACY_CORE_VERSION = "4.8.0"
CONSENT_TEXT = "ADOPT LEGACY 1.13.1"
LEGACY_STORAGE_MODE = "named-volumes"
LEGACY_DATA_MOUNTS = {
    "ravencoin-core": {
        "ravencoin-data": "/var/lib/ravencoin",
        "ravencoin-config": "/var/lib/ravencoin-config",
    },
    "electrumx": {
        "electrumx-data": "/var/lib/electrumx/db",
    },
}
LEGACY_REQUIRED_VOLUMES = (
    "ravencoin-data",
    "ravencoin-config",
    "electrumx-data",
    "rpc-secrets",
    "raven-secrets",
)


class LegacyAdoptionError(runtime.UpdateRuntimeError):
    pass


def _run(argv: Sequence[str], *, cwd: Path, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(argv), cwd=cwd, check=False, capture_output=True, text=True,
        timeout=timeout)


def _compose(root: Path, *args: str) -> subprocess.CompletedProcess:
    return _run(
        ["docker", "compose", "-p", runtime.COMPOSE_PROJECT_NAME,
         "-f", runtime.BASE_COMPOSE, *args], cwd=root)


def _container_id(root: Path, service: str) -> str:
    completed = _compose(root, "ps", "-q", service)
    value = completed.stdout.strip()
    if completed.returncode != 0 or not value or "\n" in value:
        raise LegacyAdoptionError(
            f"legacy discovery cannot identify exactly one running {service} container")
    return value


def _inspect_mounts(container_id: str) -> list[dict]:
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{json .Mounts}}", container_id],
        check=False, capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise LegacyAdoptionError("legacy discovery cannot inspect container mounts")
    try:
        mounts = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAdoptionError("legacy discovery received invalid Docker mount JSON") from exc
    if not isinstance(mounts, list):
        raise LegacyAdoptionError("legacy discovery received a non-list Docker mount set")
    return mounts


def _inspect_named_volume(volume_name: str) -> dict:
    completed = subprocess.run(
        ["docker", "volume", "inspect", volume_name], check=False,
        capture_output=True, text=True, timeout=60)
    if completed.returncode != 0:
        raise LegacyAdoptionError(f"required legacy volume {volume_name} is missing")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAdoptionError(f"invalid Docker metadata for {volume_name}") from exc
    if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
        raise LegacyAdoptionError(f"unexpected Docker metadata for {volume_name}")
    volume = payload[0]
    options = volume.get("Options")
    if volume.get("Driver") != "local" or options not in (None, {}):
        raise LegacyAdoptionError(
            f"legacy volume {volume_name} is not a plain local named volume")
    return volume


def _rendered_named_model(root: Path) -> dict:
    completed = _compose(root, "config", "--format", "json")
    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr)[-2000:]
        raise LegacyAdoptionError(f"cannot render legacy Compose model:\n{detail}")
    try:
        model = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAdoptionError("legacy Compose config did not return JSON") from exc
    volumes = model.get("volumes")
    if not isinstance(volumes, dict):
        raise LegacyAdoptionError("legacy Compose model has no volumes object")
    for logical in LEGACY_DATA_MOUNTS["ravencoin-core"] | LEGACY_DATA_MOUNTS["electrumx"]:
        definition = volumes.get(logical)
        if definition is None:
            raise LegacyAdoptionError(f"legacy Compose model lost volume {logical}")
        if not isinstance(definition, dict):
            raise LegacyAdoptionError(f"legacy Compose volume {logical} is malformed")
        options = definition.get("driver_opts")
        if options not in (None, {}):
            raise LegacyAdoptionError(
                f"legacy Compose volume {logical} unexpectedly has driver options")
    return model


def discover_legacy_install(root: Path) -> dict:
    root = root.resolve()
    marker = root / runtime.INSTALL_MARKER
    if marker.exists() or marker.is_symlink():
        raise LegacyAdoptionError(
            "installer marker already exists; use the normal updater instead of legacy adoption")
    compose = root / runtime.BASE_COMPOSE
    if compose.is_symlink() or not compose.is_file():
        raise LegacyAdoptionError("legacy install root lacks a safe compose.yaml")
    # A legacy setup.sh deployment did not activate the bind-storage overlay.
    _rendered_named_model(root)

    core_id = _container_id(root, "ravencoin-core")
    electrumx_id = _container_id(root, "electrumx")

    info = _compose(root, "exec", "-T", "electrumx", "electrumx_rpc", "getinfo")
    if info.returncode != 0:
        raise LegacyAdoptionError("legacy ElectrumX RPC is not healthy")
    try:
        electrumx_info = json.loads(info.stdout)
    except json.JSONDecodeError as exc:
        raise LegacyAdoptionError("legacy ElectrumX RPC returned invalid JSON") from exc
    version = electrumx_info.get("version") if isinstance(electrumx_info, dict) else None
    if not isinstance(version, str) or not version.endswith(LEGACY_ELECTRUMX_VERSION):
        raise LegacyAdoptionError(
            f"legacy adoption supports only ElectrumX-RVN {LEGACY_ELECTRUMX_VERSION}")

    core_version = _compose(root, "exec", "-T", "ravencoin-core", "ravend", "--version")
    if core_version.returncode != 0 or LEGACY_CORE_VERSION not in core_version.stdout:
        raise LegacyAdoptionError(
            f"legacy adoption supports only Ravencoin Core {LEGACY_CORE_VERSION}")

    expected = {}
    for logical in LEGACY_REQUIRED_VOLUMES:
        volume_name = f"{runtime.COMPOSE_PROJECT_NAME}_{logical}"
        _inspect_named_volume(volume_name)
        expected[logical] = volume_name

    ids = {"ravencoin-core": core_id, "electrumx": electrumx_id}
    for service, logicals in LEGACY_DATA_MOUNTS.items():
        mounts = _inspect_mounts(ids[service])
        for logical, destination in logicals.items():
            volume_name = expected[logical]
            matches = [
                item for item in mounts
                if isinstance(item, dict)
                and item.get("Type") == "volume"
                and item.get("Name") == volume_name
                and item.get("Destination") == destination
            ]
            if len(matches) != 1:
                raise LegacyAdoptionError(
                    f"running {service} does not use {volume_name} at {destination}")

    return {
        "root": root,
        "electrumxVersion": LEGACY_ELECTRUMX_VERSION,
        "coreVersion": LEGACY_CORE_VERSION,
        "storage": expected,
        "electrumxHeight": electrumx_info.get("db height"),
    }


def _write_adoption_marker(root: Path) -> None:
    runtime._write_private_json(root / runtime.INSTALL_MARKER, {
        "schemaVersion": 1,
        "electrumxVersion": LEGACY_ELECTRUMX_VERSION,
        "bootstrapChoice": "p2p",
        "nodeMonitorEnabled": False,
        "monitorControllerEnabled": False,
        "storageMode": LEGACY_STORAGE_MODE,
        "legacyAdoption": {
            "source": "setup.sh",
            "fromVersion": LEGACY_ELECTRUMX_VERSION,
            "storageMode": LEGACY_STORAGE_MODE,
            "chainstrapRerunAllowed": False,
        },
    })


def _legacy_compose_files(marker: dict) -> list[str]:
    if marker.get("storageMode") != LEGACY_STORAGE_MODE:
        return [runtime.BASE_COMPOSE, runtime.STORAGE_OVERLAY] + (
            [runtime.MONITOR_OVERLAY] if marker.get("nodeMonitorEnabled") else []) + (
            [runtime.MONITOR_CONTROLLER_OVERLAY]
            if marker.get("monitorControllerEnabled") else [])
    if marker.get("nodeMonitorEnabled") or marker.get("monitorControllerEnabled"):
        raise LegacyAdoptionError(
            "legacy named-volume adoption cannot absorb an in-project monitor implicitly")
    return [runtime.BASE_COMPOSE]


def _expected_data_volumes() -> dict[str, str]:
    return {
        logical: f"{runtime.COMPOSE_PROJECT_NAME}_{logical}"
        for logical in ("ravencoin-data", "ravencoin-config", "electrumx-data")
    }


def _prove_named_volume_objects(expected: dict[str, str]) -> None:
    for logical, volume_name in expected.items():
        wanted = f"{runtime.COMPOSE_PROJECT_NAME}_{logical}"
        if volume_name != wanted:
            raise LegacyAdoptionError(
                f"named-volume identity changed for {logical}: {volume_name!r}")
        _inspect_named_volume(volume_name)


def _prove_running_named_storage(root: Path, files: Sequence[str], marker: dict) -> dict[str, str]:
    if list(files) != [runtime.BASE_COMPOSE] or marker.get("storageMode") != LEGACY_STORAGE_MODE:
        raise LegacyAdoptionError("legacy running storage proof received the wrong Compose mode")
    _rendered_named_model(root)
    expected = _expected_data_volumes()
    _prove_named_volume_objects(expected)
    runtime._verify_active_storage_mounts(root, files, marker)
    return expected


def _prove_candidate_named_storage(root: Path, files: Sequence[str], expected: dict[str, str]) -> None:
    if list(files) != [runtime.BASE_COMPOSE]:
        raise LegacyAdoptionError("legacy candidate unexpectedly selected a storage overlay")
    _rendered_named_model(root)
    if expected != _expected_data_volumes():
        raise LegacyAdoptionError("candidate named-volume identities differ from the running node")
    _prove_named_volume_objects(expected)


def install_runtime_compatibility_hooks() -> None:
    original_required = runtime._required_update_compose_files
    original_running = runtime.prove_running_storage_continuity
    original_candidate = runtime.prove_candidate_storage_continuity
    original_objects = runtime._docker_volume_bindings

    def required(marker: dict) -> list[str]:
        if marker.get("storageMode") == LEGACY_STORAGE_MODE:
            return _legacy_compose_files(marker)
        return original_required(marker)

    def running(root: Path, files: Sequence[str], marker: dict):
        if marker.get("storageMode") == LEGACY_STORAGE_MODE:
            return _prove_running_named_storage(root, files, marker)
        return original_running(root, files, marker)

    def candidate(root: Path, files: Sequence[str], expected):
        marker = runtime.read_install_marker(root)
        if marker.get("storageMode") == LEGACY_STORAGE_MODE:
            return _prove_candidate_named_storage(root, files, expected)
        return original_candidate(root, files, expected)

    def objects(expected):
        if expected and all(isinstance(value, str) for value in expected.values()):
            return _prove_named_volume_objects(expected)
        return original_objects(expected)

    runtime._required_update_compose_files = required
    runtime.prove_running_storage_continuity = running
    runtime.prove_candidate_storage_continuity = candidate
    runtime._docker_volume_bindings = objects


def _confirm(discovery: dict, *, assume_yes: bool) -> None:
    print("LEGACY INSTALLATION DISCOVERED")
    print(f"  install root: {discovery['root']}")
    print(f"  ElectrumX:    {discovery['electrumxVersion']}")
    print(f"  Core:         {discovery['coreVersion']}")
    print("  storage:      existing Docker named volumes (preserved, not converted)")
    for logical, name in discovery["storage"].items():
        print(f"    {logical}: {name}")
    print("  Node Monitor: external/separate project; not adopted into this Compose project")
    print("  ChainStrap:   disabled for this upgrade path")
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise LegacyAdoptionError(
            "legacy adoption requires a TTY or the explicit --yes-adopt-legacy flag")
    entered = input(f"Type exactly {CONSENT_TEXT!r} to continue: ")
    if entered != CONSENT_TEXT:
        raise LegacyAdoptionError("legacy adoption not confirmed")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electrumx-update-legacy-1.13.1")
    parser.add_argument("--yes-adopt-legacy", action="store_true",
                        help="explicitly approve the one-time legacy adoption")
    parser.add_argument("--approve-consensus-change", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = cli.DEFAULT_INSTALL_ROOT
    try:
        if (root / runtime.INSTALL_MARKER).exists():
            marker = runtime.read_install_marker(root)
            if marker.get("storageMode") != LEGACY_STORAGE_MODE or \
                    (marker.get("legacyAdoption") or {}).get("fromVersion") != LEGACY_ELECTRUMX_VERSION:
                raise LegacyAdoptionError(
                    "an installer marker already exists but is not a verified legacy-1.13.1 adoption")
            discovery = {
                "root": root,
                "electrumxVersion": LEGACY_ELECTRUMX_VERSION,
                "coreVersion": LEGACY_CORE_VERSION,
                "storage": {**_expected_data_volumes(),
                            "rpc-secrets": f"{runtime.COMPOSE_PROJECT_NAME}_rpc-secrets",
                            "raven-secrets": f"{runtime.COMPOSE_PROJECT_NAME}_raven-secrets"},
            }
            _prove_running_named_storage(root, [runtime.BASE_COMPOSE], marker)
        else:
            discovery = discover_legacy_install(root)
            _confirm(discovery, assume_yes=args.yes_adopt_legacy)
            _write_adoption_marker(root)
            print("UPDATER_CHECKPOINT legacy-adoption=PASS old-stack=RUNNING storage=named-volumes")

        install_runtime_compatibility_hooks()
        forwarded = ["apply"]
        if args.approve_consensus_change:
            forwarded.append("--approve-consensus-change")
        return cli.main(forwarded)
    except (OSError, ValueError, json.JSONDecodeError, runtime.UpdateRuntimeError) as exc:
        print(f"legacy adoption/apply refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
