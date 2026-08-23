#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""One-time, explicit adoption path for legacy setup.sh 1.13.1 installs.

The 1.13.7 transactional updater normally requires the installer marker and
bind-backed storage introduced by the new installer. Historical 1.13.1
``setup.sh`` deployments predate both. This wrapper discovers and proves that
specific legacy shape while the old node is still serving, requires explicit
operator consent, writes a narrow schema-v1 adoption marker atomically, and
then runs the normal v2 updater with process-local storage proof functions that
preserve the existing Docker named volumes verbatim.

The ``discover`` command is strictly read-only. The ``apply`` command refuses to
write the adoption marker until the already-discovered pending 1.13.7 candidate
has been revalidated, downloaded, provenance-checked, bundle-checked and trust-
continuity checked. If this invocation creates an adoption marker but the normal
updater returns without promotion, the wrapper removes that marker again only
when the restored root still contains the exact 1.13.1 adoption marker.

Nothing here converts a Docker volume to a bind mount, derives an API from
Docker's private data-root, removes a volume, or runs ChainStrap. The wrapper
accepts only ElectrumX-RVN 1.13.1 / Ravencoin Core 4.8.0 and the fixed Compose
project namespace. Any ambiguity is refused before the old services stop.
"""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional, Sequence

import electrumx_update_cli as cli
import update_runtime as runtime

LEGACY_ELECTRUMX_VERSION = "1.13.1"
TARGET_ELECTRUMX_VERSION = "1.13.7"
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
    required = set(LEGACY_DATA_MOUNTS["ravencoin-core"]) | set(LEGACY_DATA_MOUNTS["electrumx"])
    for logical in required:
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
            "installer marker already exists; use the normal updater instead of legacy discovery")
    compose = root / runtime.BASE_COMPOSE
    if compose.is_symlink() or not compose.is_file():
        raise LegacyAdoptionError("legacy install root lacks a safe compose.yaml")
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


def _print_discovery(discovery: dict) -> None:
    print("LEGACY INSTALLATION DISCOVERED")
    print(f"  install root: {discovery['root']}")
    print(f"  ElectrumX:    {discovery['electrumxVersion']}")
    print(f"  Core:         {discovery['coreVersion']}")
    print(f"  DB height:    {discovery.get('electrumxHeight')}")
    print("  storage:      existing Docker named volumes (preserved, not converted)")
    for logical, name in discovery["storage"].items():
        print(f"    {logical}: {name}")
    print("  Node Monitor: external/separate project; not adopted into this Compose project")
    print("  ChainStrap:   disabled for this upgrade path")


def _write_adoption_marker(root: Path, *, electrumx_version: str = LEGACY_ELECTRUMX_VERSION) -> None:
    runtime._write_private_json(root / runtime.INSTALL_MARKER, {
        "schemaVersion": 1,
        "electrumxVersion": electrumx_version,
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


def _is_exact_legacy_marker(marker: dict, *, version: str) -> bool:
    legacy = marker.get("legacyAdoption") or {}
    return bool(
        marker.get("schemaVersion") == 1 and
        marker.get("electrumxVersion") == version and
        marker.get("bootstrapChoice") == "p2p" and
        marker.get("nodeMonitorEnabled") is False and
        marker.get("monitorControllerEnabled") is False and
        marker.get("storageMode") == LEGACY_STORAGE_MODE and
        legacy.get("source") == "setup.sh" and
        legacy.get("fromVersion") == LEGACY_ELECTRUMX_VERSION and
        legacy.get("storageMode") == LEGACY_STORAGE_MODE and
        legacy.get("chainstrapRerunAllowed") is False
    )


def _remove_created_adoption_marker(root: Path) -> None:
    path = root / runtime.INSTALL_MARKER
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise LegacyAdoptionError(
            "cannot roll back adoption marker because marker path is not a regular file")
    marker = runtime.read_install_marker(root)
    if not _is_exact_legacy_marker(marker, version=LEGACY_ELECTRUMX_VERSION):
        raise LegacyAdoptionError(
            "refusing to remove adoption marker because restored root is not exact legacy 1.13.1 state")
    path.unlink()
    directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
    print("UPDATER_CHECKPOINT legacy-adoption-rollback=PASS marker=REMOVED old-stack=PRESERVED")


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


def _validate_legacy_runtime_compose_files(files: Sequence[str]) -> list[str]:
    """Accept the legacy named-volume model plus safe runtime-only overlays.

    Legacy storage must remain the original plain Docker named-volume model,
    so compose.storage.yaml, ChainStrap and in-project monitor overlays are
    forbidden.  compose.tls.yaml is runtime-only and must be preserved when
    the legacy operator .env selected it.
    """
    selected = list(files)
    allowed = {runtime.BASE_COMPOSE, runtime.TLS_OVERLAY}
    if not selected or selected[0] != runtime.BASE_COMPOSE:
        raise LegacyAdoptionError(
            "legacy named-volume adoption lost the base Compose file")
    unexpected = sorted(set(selected) - allowed)
    if runtime.STORAGE_OVERLAY in unexpected:
        raise LegacyAdoptionError(
            "legacy candidate unexpectedly selected a storage overlay: "
            + runtime.STORAGE_OVERLAY)
    if unexpected:
        raise LegacyAdoptionError(
            "legacy named-volume adoption selected incompatible Compose file(s): "
            + ", ".join(unexpected))
    return selected


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
    if marker.get("storageMode") != LEGACY_STORAGE_MODE:
        raise LegacyAdoptionError("legacy running storage proof received the wrong Compose mode")
    _validate_legacy_runtime_compose_files(files)
    _rendered_named_model(root)
    expected = _expected_data_volumes()
    _prove_named_volume_objects(expected)
    runtime._verify_active_storage_mounts(root, files, marker)
    return expected


def _prove_candidate_named_storage(
        root: Path, files: Sequence[str], expected: dict[str, str]) -> None:
    _validate_legacy_runtime_compose_files(files)
    _rendered_named_model(root)
    if expected != _expected_data_volumes():
        raise LegacyAdoptionError("candidate named-volume identities differ from the running node")
    _prove_named_volume_objects(expected)
    # setup.sh may emit its own marker in the staged tree. Replace only the
    # staged marker after storage proof so the activated 1.13.7 root retains
    # the explicit legacy storage mode for rollback and future updates.
    _write_adoption_marker(root, electrumx_version=TARGET_ELECTRUMX_VERSION)


def install_runtime_compatibility_hooks() -> None:
    original_required = runtime._required_update_compose_files
    original_running = runtime.prove_running_storage_continuity
    original_candidate = runtime.prove_candidate_storage_continuity

    def required(marker: dict) -> list[str]:
        if marker.get("storageMode") == LEGACY_STORAGE_MODE:
            return _legacy_compose_files(marker)
        return original_required(marker)

    def running(root: Path, files: Sequence[str], marker: dict):
        if marker.get("storageMode") == LEGACY_STORAGE_MODE:
            return _prove_running_named_storage(root, files, marker)
        return original_running(root, files, marker)

    def candidate(root: Path, files: Sequence[str], expected, *,
                  storage_mode: Optional[str] = None):
        if storage_mode is None:
            storage_mode = (
                LEGACY_STORAGE_MODE
                if expected and all(isinstance(value, str) for value in expected.values())
                else None)
        if storage_mode == LEGACY_STORAGE_MODE:
            return _prove_candidate_named_storage(root, files, expected)
        return original_candidate(root, files, expected, storage_mode=storage_mode)

    # Docker volume-object proofs are deliberately NOT hooked.  update_runtime
    # dispatches on the persistent marker storage mode by itself, so the normal
    # updater never depends on a process-local rebinding to validate an adopted
    # named-volume installation.
    runtime._required_update_compose_files = required
    runtime.prove_running_storage_continuity = running
    runtime.prove_candidate_storage_continuity = candidate


def _confirm(discovery: dict, *, assume_yes: bool) -> None:
    _print_discovery(discovery)
    if assume_yes:
        return
    if not sys.stdin.isatty():
        raise LegacyAdoptionError(
            "legacy adoption requires a TTY or the explicit --yes-adopt-legacy flag")
    entered = input(f"Type exactly {CONSENT_TEXT!r} to continue: ")
    if entered != CONSENT_TEXT:
        raise LegacyAdoptionError("legacy adoption not confirmed")


def _preflight_pending_candidate() -> dict:
    """Fully authenticate the pending 1.13.7 candidate before marker mutation."""
    try:
        state = cli.load_state(cli.DEFAULT_STATE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise LegacyAdoptionError(
            f"cannot load updater state before legacy adoption: {exc}") from exc
    if not state.pending_candidate:
        raise LegacyAdoptionError(
            "no pending candidate; run electrumx-update check after the signed 1.13.7 release exists")

    trusted_keys = cli.load_trusted_key(cli.DEFAULT_TRUSTED_KEY_PATH)
    resolved_policy = cli.resolve_production_core_policy(state)
    _high_water_path, high_water = cli._resolve_high_water()
    manifest, artifact_url = cli._revalidate_pending_for_apply(
        state, resolved_policy, trusted_keys, high_water)
    if manifest.get("electrumxVersion") != TARGET_ELECTRUMX_VERSION:
        raise LegacyAdoptionError(
            f"legacy adoption target must be exactly ElectrumX-RVN {TARGET_ELECTRUMX_VERSION}")

    root = cli.DEFAULT_INSTALL_ROOT
    trusted_update_key = runtime._read_installed_key(
        root / runtime.UPDATE_PUBLIC_KEY_PATH, "ElectrumX release/update")
    trusted_core_key = runtime._read_installed_key(
        root / runtime.CORE_POLICY_PUBLIC_KEY_PATH, "safe-Core policy")

    cli.DEFAULT_STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
    with tempfile.TemporaryDirectory(
            prefix=".legacy-adoption-preflight-", dir=cli.DEFAULT_STATE_DIR) as temporary:
        artifact = runtime.download_verified_artifact(
            artifact_url, expected_digest=manifest["artifactDigest"],
            directory=Path(temporary))
        cli._verify_bundle_provenance(artifact, manifest)
        runtime.validate_bundle_file(
            artifact, manifest,
            trusted_update_public_key_hex=trusted_update_key,
            trusted_core_policy_public_key_hex=trusted_core_key)

    print(
        "UPDATER_CHECKPOINT legacy-candidate-preflight=PASS "
        f"version={TARGET_ELECTRUMX_VERSION} marker=UNTOUCHED old-stack=RUNNING")
    return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electrumx-update-legacy-1.13.1")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="read-only discovery; never writes the adoption marker")
    apply_parser = sub.add_parser("apply", help="preflight candidate, adopt, then apply")
    apply_parser.add_argument("--yes-adopt-legacy", action="store_true",
                              help="explicitly approve the one-time legacy adoption")
    apply_parser.add_argument("--approve-consensus-change", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = cli.DEFAULT_INSTALL_ROOT
    created_marker = False
    try:
        if args.command == "discover":
            discovery = discover_legacy_install(root)
            _print_discovery(discovery)
            print("UPDATER_CHECKPOINT legacy-discovery=PASS mutation=NONE old-stack=RUNNING")
            return 0

        # No install-root mutation is permitted before this succeeds. This
        # deliberately performs the expensive network/artifact checks twice:
        # once here to guard adoption, and again inside the normal apply path
        # immediately before the transaction.
        _preflight_pending_candidate()

        marker_path = root / runtime.INSTALL_MARKER
        if marker_path.exists() or marker_path.is_symlink():
            marker = runtime.read_install_marker(root)
            if not _is_exact_legacy_marker(marker, version=LEGACY_ELECTRUMX_VERSION):
                raise LegacyAdoptionError(
                    "an installer marker already exists but is not an exact legacy-1.13.1 adoption")
            _prove_running_named_storage(root, [runtime.BASE_COMPOSE], marker)
        else:
            discovery = discover_legacy_install(root)
            _confirm(discovery, assume_yes=args.yes_adopt_legacy)
            _write_adoption_marker(root)
            created_marker = True
            print("UPDATER_CHECKPOINT legacy-adoption=PASS old-stack=RUNNING storage=named-volumes")

        install_runtime_compatibility_hooks()
        forwarded = ["apply"]
        if args.approve_consensus_change:
            forwarded.append("--approve-consensus-change")
        result = cli.main(forwarded)
        if result != 0 and created_marker:
            _remove_created_adoption_marker(root)
        return result
    except (OSError, ValueError, json.JSONDecodeError, runtime.UpdateRuntimeError) as exc:
        if created_marker:
            try:
                _remove_created_adoption_marker(root)
            except runtime.UpdateRuntimeError as rollback_exc:
                print(
                    "legacy adoption marker rollback refused: "
                    f"{type(rollback_exc).__name__}: {rollback_exc}", file=sys.stderr)
        print(f"legacy adoption/apply refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
