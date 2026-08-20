#!/usr/bin/env python3
"""One-shot deterministic migration for installer-selectable project storage.

This file is removed by the migration workflow after it has applied the change.
It uses exact anchors and refuses to modify a tree that no longer matches the
reviewed source, so it cannot silently patch a different installer revision.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "electrumx-ravencoin-install.py"
MONITOR_COMPOSE = ROOT / "compose.monitor.yaml"
CI = ROOT / ".github" / "workflows" / "ci.yml"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def patch_installer() -> None:
    text = INSTALLER.read_text(encoding="utf-8")

    text = replace_once(
        text,
        'CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"\nBASE_COMPOSE = "compose.yaml"\n',
        'CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"\n'
        'STORAGE_OVERLAY = "compose.storage.yaml"\n'
        'BASE_COMPOSE = "compose.yaml"\n'
        'STORAGE_ROOT_DIRNAME = "electrumx-ravencoin-storage"\n'
        'STORAGE_SUBDIRS = ("ravencoin-data", "ravencoin-config", "electrumx-data", "monitor-data")\n'
        'SAFE_STORAGE_PATH_RE = re.compile(r"^[A-Za-z0-9_./ +@-]+$")\n',
        "installer storage constants",
    )

    text = replace_once(
        text,
        'REQUIRED_BUNDLE_PATHS = frozenset({\n    BASE_COMPOSE,\n',
        'REQUIRED_BUNDLE_PATHS = frozenset({\n    BASE_COMPOSE,\n    STORAGE_OVERLAY,\n',
        "required storage overlay",
    )

    text = replace_once(
        text,
        '    return parser\n\n\ndef parse_args',
        '    parser.add_argument(\n'
        '        "--storage-root", default=None, metavar="DIR",\n'
        '        help="store Ravencoin/ChainStrap, ElectrumX and Node Monitor persistent "\n'
        '             "data under DIR; interactive installs offer mounted disks/filesystems")\n'
        '    return parser\n\n\ndef parse_args',
        "storage-root argument",
    )

    storage_functions = r'''

def _format_storage_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} {unit}"
        amount /= 1024.0
    raise AssertionError("unreachable")


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def validate_storage_root_path(path: Path) -> Path:
    raw = str(path.expanduser())
    if not raw or not SAFE_STORAGE_PATH_RE.fullmatch(raw) or any(ch in raw for ch in ":'$\n\r"):
        raise InstallError(
            "storage path contains unsupported characters; use letters, digits, spaces, "
            "and common path characters only")
    resolved = path.expanduser().resolve(strict=False)
    home = Path.home().resolve()
    if resolved in (Path("/"), home):
        raise InstallError("storage root must be a dedicated child directory, not / or $HOME")
    if resolved.exists():
        raise InstallError(
            f"fresh install storage root already exists: {resolved}; preserve or remove it "
            "explicitly before retrying")
    parent = _nearest_existing_parent(resolved.parent)
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise InstallError(f"storage parent is not writable by the current user: {parent}")
    try:
        mount = Path(subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "--target", str(parent)],
            check=False, capture_output=True, text=True).stdout.strip() or "/").resolve()
    except OSError:
        mount = Path("/")
    if resolved == mount:
        raise InstallError("storage root must be a child directory, not the filesystem mountpoint")
    return resolved


def _storage_candidate_root(mountpoint: Path) -> Optional[Path]:
    try:
        home = Path.home().resolve()
        if os.stat(home).st_dev == os.stat(mountpoint).st_dev:
            return home / STORAGE_ROOT_DIRNAME
    except OSError:
        pass
    if os.access(mountpoint, os.W_OK | os.X_OK):
        return mountpoint / STORAGE_ROOT_DIRNAME
    return None


def discover_storage_candidates() -> list[dict]:
    """Return writable mounted block filesystems without changing the host."""
    lsblk = shutil.which("lsblk")
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if lsblk is not None:
        completed = subprocess.run(
            [lsblk, "--json", "--bytes", "--output",
             "NAME,PATH,TYPE,FSTYPE,SIZE,MOUNTPOINTS,RO"],
            check=False, capture_output=True, text=True)
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                payload = {}

            def walk(nodes) -> None:
                for node in nodes or []:
                    mountpoints = node.get("mountpoints") or []
                    if isinstance(mountpoints, str):
                        mountpoints = [mountpoints]
                    source = str(node.get("path") or node.get("name") or "unknown")
                    if node.get("fstype") and not node.get("ro"):
                        for raw_mount in mountpoints:
                            if not raw_mount:
                                continue
                            mountpoint = Path(raw_mount).resolve()
                            if not mountpoint.is_dir():
                                continue
                            suggested = _storage_candidate_root(mountpoint)
                            if suggested is None:
                                continue
                            key = (source, str(mountpoint))
                            if key in seen:
                                continue
                            seen.add(key)
                            try:
                                usage = shutil.disk_usage(mountpoint)
                            except OSError:
                                continue
                            candidates.append({
                                "source": source,
                                "mountpoint": mountpoint,
                                "fstype": str(node.get("fstype")),
                                "size": int(node.get("size") or usage.total),
                                "free": int(usage.free),
                                "root": suggested,
                            })
                    walk(node.get("children"))

            walk(payload.get("blockdevices"))

    if not candidates:
        home = Path.home().resolve()
        usage = shutil.disk_usage(home)
        candidates.append({
            "source": "current-home-filesystem",
            "mountpoint": home,
            "fstype": "unknown",
            "size": usage.total,
            "free": usage.free,
            "root": home / STORAGE_ROOT_DIRNAME,
        })
    return sorted(candidates, key=lambda item: (str(item["mountpoint"]) != "/", str(item["mountpoint"])))


def choose_storage_root(args, interactive: bool,
                        prompt: Callable[[str], str] = input) -> Path:
    if args.storage_root:
        selected = validate_storage_root_path(Path(args.storage_root))
        usage = shutil.disk_usage(_nearest_existing_parent(selected.parent))
        print(f"project data storage: {selected} ({_format_storage_bytes(usage.free)} free)")
        return selected
    if not interactive:
        raise InstallError("--storage-root is required for a non-interactive fresh install")

    candidates = discover_storage_candidates()
    print("Project data storage (Docker images remain in Docker's existing data-root):")
    for index, item in enumerate(candidates, 1):
        state = ""
        if item["root"].exists():
            state = " [existing path - cannot use for fresh install]"
        print(
            f"  {index}. {item['source']} mounted at {item['mountpoint']} "
            f"({item['fstype']}, {_format_storage_bytes(item['free'])} free / "
            f"{_format_storage_bytes(item['size'])}){state}")
        print(f"     data directory: {item['root']}")
    print("  C. Custom dedicated directory on another mounted filesystem")

    answer = prompt("Storage choice [1]: ").strip().lower()
    if answer in ("c", "custom"):
        custom = prompt("Dedicated storage directory: ").strip()
        if not custom:
            raise InstallError("no custom storage directory supplied")
        selected = validate_storage_root_path(Path(custom))
    else:
        if answer == "":
            answer = "1"
        try:
            index = int(answer)
        except ValueError as exc:
            raise InstallError(f"unrecognized storage choice {answer!r}") from exc
        if index < 1 or index > len(candidates):
            raise InstallError(f"storage choice {index} is outside the displayed range")
        selected = validate_storage_root_path(candidates[index - 1]["root"])

    usage = shutil.disk_usage(_nearest_existing_parent(selected.parent))
    print(f"selected project data storage: {selected} ({_format_storage_bytes(usage.free)} free)")
    return selected


def require_clean_storage_root(storage_root: Path) -> None:
    validate_storage_root_path(storage_root)


def prepare_storage_layout(storage_root: Path) -> None:
    require_clean_storage_root(storage_root)
    created = False
    try:
        storage_root.mkdir(mode=0o755, parents=False, exist_ok=False)
        created = True
        for name in STORAGE_SUBDIRS:
            (storage_root / name).mkdir(mode=0o755)
    except BaseException:
        if created:
            shutil.rmtree(storage_root, ignore_errors=True)
        raise


def _storage_env_value(path: Path) -> str:
    value = str(path)
    if not SAFE_STORAGE_PATH_RE.fullmatch(value) or any(ch in value for ch in ":'$\n\r"):
        raise InstallError(f"storage path cannot be represented safely in Compose: {path}")
    return value


def write_storage_env(root: Path, storage_root: Path) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        raise InstallError("setup.sh did not create .env before storage configuration")
    mapping = {
        "RAVENCOIN_DATA_HOST_DIR": storage_root / "ravencoin-data",
        "RAVENCOIN_CONFIG_HOST_DIR": storage_root / "ravencoin-config",
        "ELECTRUMX_DATA_HOST_DIR": storage_root / "electrumx-data",
        "MONITOR_DATA_HOST_DIR": storage_root / "monitor-data",
    }
    existing = env_path.read_text(encoding="utf-8")
    if any(f"{key}=" in existing for key in mapping):
        raise InstallError("refusing to overwrite pre-existing storage path configuration")
    with env_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# Selected by the verified installer; project data only, not Docker images.\n")
        for key, path in mapping.items():
            handle.write(f"{key}={_storage_env_value(path)}\n")


def initialize_storage_permissions(storage_root: Path, monitor: bool) -> None:
    raven_mounts = [
        (storage_root / "ravencoin-data", "/storage/ravencoin-data"),
        (storage_root / "ravencoin-config", "/storage/ravencoin-config"),
    ]
    if monitor:
        raven_mounts.append((storage_root / "monitor-data", "/storage/monitor-data"))
    argv = ["docker", "run", "--rm", "--network", "none", "--user", "0:0",
            "--entrypoint", "/bin/sh"]
    for host, container in raven_mounts:
        argv += ["-v", f"{host}:{container}"]
    targets = " ".join(container for _host, container in raven_mounts)
    argv += ["alenoc/ravencoin-core:4.8.0", "-ec",
             f"chown -R 10001:10001 {targets}; chmod 0750 {targets}"]
    run_checked(argv)

    electrumx_dir = storage_root / "electrumx-data"
    run_checked([
        "docker", "run", "--rm", "--network", "none", "--user", "0:0",
        "--entrypoint", "/bin/sh", "-v", f"{electrumx_dir}:/storage/electrumx-data",
        "alenoc/electrumx-ravencoin:1.13.0", "-ec",
        "uid=$(id -u electrumx); gid=$(id -g electrumx); "
        "chown -R \"$uid:$gid\" /storage/electrumx-data; chmod 0750 /storage/electrumx-data",
    ])


def cleanup_storage_layout_best_effort(storage_root: Path) -> None:
    if not storage_root.exists():
        return
    # Container UIDs own the data subdirectories. Use the already-built Core
    # image only to return ownership to the invoking host user before rmtree.
    try:
        subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--user", "0:0",
            "--entrypoint", "/bin/sh", "-v", f"{storage_root}:/storage",
            "alenoc/ravencoin-core:4.8.0", "-ec",
            f"chown -R {os.getuid()}:{os.getgid()} /storage",
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    shutil.rmtree(storage_root, ignore_errors=True)
'''

    marker = (
        '# ---------------------------------------------------------------------------\n'
        '# Operator choices / generated configuration\n'
        '# ---------------------------------------------------------------------------\n'
    )
    text = replace_once(text, marker, storage_functions + "\n\n" + marker,
                        "storage function insertion")

    text = replace_once(
        text,
        '    files = [BASE_COMPOSE]\n',
        '    files = [BASE_COMPOSE, STORAGE_OVERLAY]\n',
        "storage compose overlay",
    )

    text = replace_once(
        text,
        '        "HISTORY_ENABLED=true\\nHISTORY_STORAGE=memory\\n"\n',
        '        "HISTORY_ENABLED=true\\nHISTORY_STORAGE=memory\\n"\n'
        '        "HISTORY_DB_PATH=/data/history.db\\n"\n'
        '        "EXTRA_DISK_PATHS=Project storage=/data\\n"\n',
        "monitor storage environment",
    )

    text = replace_once(
        text,
        'def write_install_marker(root: Path, *, body: dict, metadata: dict,\n'
        '                         bootstrap: str, monitor: bool, controller: bool) -> None:\n',
        'def write_install_marker(root: Path, *, body: dict, metadata: dict,\n'
        '                         bootstrap: str, monitor: bool, controller: bool,\n'
        '                         storage_root: Path) -> None:\n',
        "install marker signature",
    )

    text = replace_once(
        text,
        '        "installerVersion": VERSION,\n',
        '        "installerVersion": VERSION,\n        "storageRoot": str(storage_root),\n',
        "install marker storage root",
    )

    text = replace_once(
        text,
        'def install_fresh(target: Path, data: bytes, *, body: dict, metadata: dict,\n'
        '                  bootstrap: str, monitor: bool, controller: bool) -> None:\n',
        'def install_fresh(target: Path, data: bytes, *, body: dict, metadata: dict,\n'
        '                  bootstrap: str, monitor: bool, controller: bool,\n'
        '                  storage_root: Path) -> None:\n',
        "install_fresh signature",
    )

    text = replace_once(
        text,
        '    require_clean_docker_project_runtime()\n\n    parent = target.parent.resolve()\n',
        '    require_clean_docker_project_runtime()\n'
        '    require_clean_storage_root(storage_root)\n\n'
        '    parent = target.parent.resolve()\n',
        "fresh storage preflight",
    )

    text = replace_once(
        text,
        '    moved = False\n    controller_installed = False\n',
        '    moved = False\n    controller_installed = False\n    storage_prepared = False\n',
        "storage prepared flag",
    )

    text = replace_once(
        text,
        '        run_checked(["sh", "./setup.sh", "--bundled-core"], cwd=staging)\n'
        '        if monitor:\n',
        '        run_checked(["sh", "./setup.sh", "--bundled-core"], cwd=staging)\n'
        '        write_storage_env(staging, storage_root)\n'
        '        if monitor:\n',
        "write storage env",
    )

    text = replace_once(
        text,
        '        run_checked(base + ["build"], cwd=staging)\n\n        os.replace(staging, target)\n',
        '        run_checked(base + ["build"], cwd=staging)\n'
        '        prepare_storage_layout(storage_root)\n'
        '        storage_prepared = True\n'
        '        initialize_storage_permissions(storage_root, monitor)\n\n'
        '        os.replace(staging, target)\n',
        "prepare selected storage after build",
    )

    text = replace_once(
        text,
        '            target, body=body, metadata=metadata, bootstrap=bootstrap,\n'
        '            monitor=monitor, controller=controller)\n',
        '            target, body=body, metadata=metadata, bootstrap=bootstrap,\n'
        '            monitor=monitor, controller=controller, storage_root=storage_root)\n',
        "marker call storage root",
    )

    text = replace_once(
        text,
        '        if moved and target.exists():\n'
        '            shutil.rmtree(target, ignore_errors=True)\n'
        '        raise\n',
        '        if moved and target.exists():\n'
        '            shutil.rmtree(target, ignore_errors=True)\n'
        '        if storage_prepared:\n'
        '            cleanup_storage_layout_best_effort(storage_root)\n'
        '        raise\n',
        "failed-run host storage cleanup",
    )

    text = replace_once(
        text,
        '        interactive = sys.stdin.isatty()\n'
        '        # Resolve choices even in --check-only so explicit unsupported controller\n',
        '        interactive = sys.stdin.isatty()\n'
        '        storage_root = None\n'
        '        if not args.check_only or args.storage_root:\n'
        '            storage_root = choose_storage_root(args, interactive)\n'
        '        # Resolve choices even in --check-only so explicit unsupported controller\n',
        "storage selection in main",
    )

    text = replace_once(
        text,
        '        install_fresh(\n'
        '            target, bundle, body=body, metadata=metadata,\n'
        '            bootstrap=bootstrap, monitor=monitor, controller=controller)\n',
        '        if storage_root is None:\n'
        '            raise InstallError("fresh install requires a selected project storage root")\n'
        '        install_fresh(\n'
        '            target, bundle, body=body, metadata=metadata,\n'
        '            bootstrap=bootstrap, monitor=monitor, controller=controller,\n'
        '            storage_root=storage_root)\n',
        "install_fresh main call",
    )

    text = replace_once(
        text,
        '        print(f"installation complete in {target}")\n'
        '        print(f"bootstrap: {bootstrap}")\n',
        '        print(f"installation complete in {target}")\n'
        '        print(f"project data storage: {storage_root}")\n'
        '        print("Docker images remain in the daemon existing DockerRootDir")\n'
        '        print(f"bootstrap: {bootstrap}")\n',
        "installation storage summary",
    )

    INSTALLER.write_text(text, encoding="utf-8")


def patch_monitor_compose() -> None:
    text = MONITOR_COMPOSE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '    volumes:\n      - rpc-secrets:/run/raven-secrets:ro\n',
        '    volumes:\n      - rpc-secrets:/run/raven-secrets:ro\n      - monitor-data:/data\n',
        "monitor data mount",
    )
    if not text.endswith("\n"):
        text += "\n"
    text += "\nvolumes:\n  monitor-data:\n"
    MONITOR_COMPOSE.write_text(text, encoding="utf-8")


def patch_ci() -> None:
    text = CI.read_text(encoding="utf-8")
    anchor = '          docker compose -f compose.yaml -f compose.chainstrap.yaml config --quiet\n'
    addition = (
        '          STORAGE_TEST_ROOT="$RUNNER_TEMP/electrumx-storage"\n'
        '          mkdir -p "$STORAGE_TEST_ROOT"/{ravencoin-data,ravencoin-config,electrumx-data,monitor-data}\n'
        '          cat >> .env <<EOF\n'
        '          RAVENCOIN_DATA_HOST_DIR=$STORAGE_TEST_ROOT/ravencoin-data\n'
        '          RAVENCOIN_CONFIG_HOST_DIR=$STORAGE_TEST_ROOT/ravencoin-config\n'
        '          ELECTRUMX_DATA_HOST_DIR=$STORAGE_TEST_ROOT/electrumx-data\n'
        '          MONITOR_DATA_HOST_DIR=$STORAGE_TEST_ROOT/monitor-data\n'
        '          EOF\n'
        '          docker compose -f compose.yaml -f compose.storage.yaml config --quiet\n'
        '          docker compose -f compose.yaml -f compose.storage.yaml -f compose.chainstrap.yaml -f compose.monitor.yaml config --quiet\n'
    )
    text = replace_once(text, anchor, anchor + addition, "CI storage compose validation")
    CI.write_text(text, encoding="utf-8")


def create_new_files() -> None:
    storage_compose = '''# Project data lives on the operator-selected filesystem while Docker images\n# and writable image layers remain under the daemon's existing DockerRootDir.\n# setup/installer writes the four absolute host paths into .env.\nvolumes:\n  ravencoin-data:\n    driver: local\n    driver_opts:\n      type: none\n      o: bind\n      device: "${RAVENCOIN_DATA_HOST_DIR:?RAVENCOIN_DATA_HOST_DIR is required}"\n  ravencoin-config:\n    driver: local\n    driver_opts:\n      type: none\n      o: bind\n      device: "${RAVENCOIN_CONFIG_HOST_DIR:?RAVENCOIN_CONFIG_HOST_DIR is required}"\n  electrumx-data:\n    driver: local\n    driver_opts:\n      type: none\n      o: bind\n      device: "${ELECTRUMX_DATA_HOST_DIR:?ELECTRUMX_DATA_HOST_DIR is required}"\n  monitor-data:\n    driver: local\n    driver_opts:\n      type: none\n      o: bind\n      device: "${MONITOR_DATA_HOST_DIR:?MONITOR_DATA_HOST_DIR is required}"\n'''
    (ROOT / "compose.storage.yaml").write_text(storage_compose, encoding="utf-8")

    tests = r'''from __future__ import annotations

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
'''
    (ROOT / "tests" / "test_installer_storage.py").write_text(tests, encoding="utf-8")

    docs = '''# Selecting the node data disk\n\nThe verified installer separates **project data placement** from Docker's\nglobal image store. On an interactive fresh install it lists writable mounted\nblock filesystems, their mount points, filesystem types and free space, then\nasks which one should hold the node data.\n\nThe selected dedicated directory contains:\n\n- `ravencoin-data/`: Ravencoin Core blockchain data and the ChainStrap bootstrap;\n- `ravencoin-config/`: generated Core configuration state;\n- `electrumx-data/`: the ElectrumX database;\n- `monitor-data/`: Node Monitor persistent-data mount. History remains RAM-only\n  by default; `/data/history.db` is only used if the operator later opts into\n  SQLite history.\n\nDocker images and writable image layers are deliberately **not moved**. They\nremain under the Docker daemon's existing `DockerRootDir`; changing that is a\nglobal Docker-host administration operation and is outside this installer.\n\nFor automation, pass a dedicated, not-yet-existing directory with\n`--storage-root /mount/path/electrumx-ravencoin-storage`. Non-interactive fresh\ninstalls fail closed if `--storage-root` is omitted. The installer refuses `/`,\n`$HOME`, a filesystem mountpoint itself, an existing data root, or a path whose\nparent is not writable by the invoking operator.\n\nCompose continues to use named volumes, but `compose.storage.yaml` configures\nthem as local-driver bind volumes whose real bytes live under the selected\nfilesystem. This preserves the existing Core/ChainStrap sharing model while\nkeeping the large blockchain and index data off the Docker image disk.\n\nIf a fresh install fails after storage activation, the installer removes the\nCompose volumes and returns ownership of the dedicated storage tree before\ndeleting it. It never falls back from ChainStrap to P2P silently.\n'''
    (ROOT / "docs" / "storage-selection.md").write_text(docs, encoding="utf-8")


def main() -> int:
    patch_installer()
    patch_monitor_compose()
    patch_ci()
    create_new_files()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
