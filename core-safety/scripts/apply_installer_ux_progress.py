#!/usr/bin/env python3
from pathlib import Path

INSTALLER = Path("electrumx-ravencoin-install.py")
TESTS = Path("core-safety/scripts/test_installer.py")


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(INSTALLER, "import tempfile\nimport urllib.error\n", "import tempfile\nimport textwrap\nimport time\nimport urllib.error\n")
replace_once(INSTALLER, 'VERSION = "0.3.0"\n', 'VERSION = "0.4.0"\n')

replace_once(
    INSTALLER,
    '''class InstallError(RuntimeError):\n    """Fatal fail-closed installation error."""\n\n\n# ---------------------------------------------------------------------------\n# CLI and host checks\n# ---------------------------------------------------------------------------\n''',
    '''class InstallError(RuntimeError):\n    """Fatal fail-closed installation error."""\n\n\ndef _ui_width() -> int:\n    """Return a bounded width that stays readable on narrow and wide terminals."""\n    columns = shutil.get_terminal_size(fallback=(88, 24)).columns\n    return max(56, min(columns, 100))\n\n\ndef _ui_wrap(text: str, *, initial: str = "", subsequent: str = "") -> str:\n    return textwrap.fill(\n        text, width=_ui_width(), initial_indent=initial, subsequent_indent=subsequent,\n        break_long_words=False, break_on_hyphens=False)\n\n\ndef print_installer_banner() -> None:\n    width = _ui_width()\n    print()\n    print("=" * width)\n    print("ELECTRUMX RAVENCOIN".center(width))\n    print("Verified Node Installer".center(width))\n    print("=" * width)\n    print()\n\n\ndef ui_section(title: str, subtitle: Optional[str] = None) -> None:\n    print()\n    print(f"[ {title} ]")\n    print("-" * _ui_width())\n    if subtitle:\n        print(_ui_wrap(subtitle))\n    print()\n\n\ndef print_installation_summary(storage_root: Optional[Path], bootstrap: str,\n                               monitor: bool, controller: bool) -> None:\n    ui_section(\n        "Installation summary",\n        "The selections below are the exact configuration the installer will activate.")\n    rows = (\n        ("Project data", str(storage_root) if storage_root else "not selected (--check-only)"),\n        ("Docker images", "existing Docker data-root (unchanged)"),\n        ("Bootstrap", "ChainStrap Fast Verified Bootstrap" if bootstrap == "chainstrap" else "Traditional Ravencoin P2P"),\n        ("Node Monitor", "enabled" if monitor else "disabled"),\n        ("Advanced controls", "enabled (root-owned helper)" if controller else "disabled"),\n    )\n    label_width = max(len(label) for label, _value in rows)\n    for label, value in rows:\n        prefix = f"  {label:<{label_width}} : "\n        print(_ui_wrap(value, initial=prefix, subsequent=" " * len(prefix)))\n    print()\n\n\n# ---------------------------------------------------------------------------\n# CLI and host checks\n# ---------------------------------------------------------------------------\n''')

replace_once(
    INSTALLER,
    '''    candidates = discover_storage_candidates()\n    print("Project data storage (Docker images remain in Docker's existing data-root):")\n    for index, item in enumerate(candidates, 1):\n        state = ""\n        if item["root"].exists():\n            state = " [existing path - cannot use for fresh install]"\n        print(\n            f"  {index}. {item['source']} mounted at {item['mountpoint']} "\n            f"({item['fstype']}, {_format_storage_bytes(item['free'])} free / "\n            f"{_format_storage_bytes(item['size'])}){state}")\n        print(f"     data directory: {item['root']}")\n    print("  C. Custom dedicated directory on another mounted filesystem")\n\n    answer = prompt("Storage choice [1]: ").strip().lower()\n''',
    '''    candidates = discover_storage_candidates()\n    ui_section(\n        "1 / 4  Project data storage",\n        "Choose the mounted filesystem that will hold the Ravencoin blockchain, "\n        "ChainStrap data, ElectrumX database and Node Monitor history. Docker images "\n        "remain in Docker's existing data-root.")\n    for index, item in enumerate(candidates, 1):\n        state = ""\n        if item["root"].exists():\n            state = " [existing path - cannot use for fresh install]"\n        description = (\n            f"{index}. {item['source']} mounted at {item['mountpoint']} "\n            f"({item['fstype']}, {_format_storage_bytes(item['free'])} free / "\n            f"{_format_storage_bytes(item['size'])}){state}")\n        print(_ui_wrap(description, initial="  ", subsequent="     "))\n        print(_ui_wrap(f"data directory: {item['root']}", initial="     ", subsequent="     "))\n        print()\n    print("  C. Custom dedicated directory on another mounted filesystem")\n    print()\n\n    answer = prompt("Storage choice [1]: ").strip().lower()\n''')
replace_once(
    INSTALLER,
    '''    print(f"selected project data storage: {selected} ({_format_storage_bytes(usage.free)} free)")\n    return selected\n''',
    '''    print()\n    print(_ui_wrap(\n        f"Selected project data storage: {selected} ({_format_storage_bytes(usage.free)} free)"))\n    print()\n    return selected\n''')

replace_once(
    INSTALLER,
    '''def choose_bootstrap(args, interactive: bool, prompt: Callable[[str], str] = input) -> str:\n    if args.chainstrap:\n        return "chainstrap"\n    if args.p2p_bootstrap:\n        return "p2p"\n    if not interactive:\n        return "chainstrap"\n    answer = prompt(\n        "Blockchain bootstrap method:\\n"\n        "  1. Fast Verified Bootstrap using ChainStrap [recommended, default]\\n"\n        "  2. Traditional Ravencoin P2P synchronization\\n"\n        "Choice [1]: ").strip()\n    if answer in ("", "1"):\n        return "chainstrap"\n    if answer == "2":\n        return "p2p"\n    raise InstallError(f"unrecognized bootstrap choice {answer!r}")\n\n\ndef choose_monitor(args, interactive: bool, prompt: Callable[[str], str] = input) -> bool:\n    if args.with_monitor_controller:\n        return True\n    if args.with_monitor:\n        return True\n    if args.without_monitor:\n        return False\n    if not interactive:\n        return True\n    answer = prompt(\n        "Install Ravencoin Node Monitor?\\n"\n        "  Y. Yes [recommended, default]\\n"\n        "  N. No\\n"\n        "Choice [Y]: ").strip().lower()\n    if answer in ("", "y", "yes"):\n        return True\n    if answer in ("n", "no"):\n        return False\n    raise InstallError(f"unrecognized monitor choice {answer!r}")\n\n\ndef choose_monitor_controller(args, monitor: bool, interactive: bool,\n                              prompt: Callable[[str], str] = input) -> bool:\n    if not monitor:\n        return False\n    if args.with_monitor_controller:\n        return True\n    if not interactive:\n        return False\n    answer = prompt(\n        "Enable advanced host controls (bandwidth / connection limits)?\\n"\n        "  y. Yes\\n"\n        "  N. No [default]\\n"\n        "Choice [N]: ").strip().lower()\n    if answer in ("", "n", "no"):\n        return False\n    if answer in ("y", "yes"):\n        return True\n    raise InstallError(f"unrecognized advanced-control choice {answer!r}")\n''',
    '''def choose_bootstrap(args, interactive: bool, prompt: Callable[[str], str] = input) -> str:\n    if args.chainstrap:\n        return "chainstrap"\n    if args.p2p_bootstrap:\n        return "p2p"\n    if not interactive:\n        return "chainstrap"\n    ui_section(\n        "2 / 4  Blockchain bootstrap",\n        "ChainStrap downloads a vetted snapshot and then Ravencoin Core reindexes and "\n        "validates it offline. Traditional P2P synchronization remains available as an "\n        "explicit alternative.")\n    print("  1. Fast Verified Bootstrap using ChainStrap  [recommended, default]")\n    print("  2. Traditional Ravencoin P2P synchronization")\n    print()\n    answer = prompt("Choice [1]: ").strip()\n    print()\n    if answer in ("", "1"):\n        return "chainstrap"\n    if answer == "2":\n        return "p2p"\n    raise InstallError(f"unrecognized bootstrap choice {answer!r}")\n\n\ndef choose_monitor(args, interactive: bool, prompt: Callable[[str], str] = input) -> bool:\n    if args.with_monitor_controller:\n        return True\n    if args.with_monitor:\n        return True\n    if args.without_monitor:\n        return False\n    if not interactive:\n        return True\n    ui_section(\n        "3 / 4  Ravencoin Node Monitor",\n        "The monitor is isolated from ElectrumX failure and remains available to report "\n        "Core, host and network state when ElectrumX is degraded.")\n    print("  Y. Install Node Monitor  [recommended, default]")\n    print("  N. Do not install Node Monitor")\n    print()\n    answer = prompt("Choice [Y]: ").strip().lower()\n    print()\n    if answer in ("", "y", "yes"):\n        return True\n    if answer in ("n", "no"):\n        return False\n    raise InstallError(f"unrecognized monitor choice {answer!r}")\n\n\ndef choose_monitor_controller(args, monitor: bool, interactive: bool,\n                              prompt: Callable[[str], str] = input) -> bool:\n    if not monitor:\n        return False\n    if args.with_monitor_controller:\n        return True\n    if not interactive:\n        return False\n    ui_section(\n        "4 / 4  Advanced host controls",\n        "Optional. Enabling this installs a separate root-owned systemd helper and may "\n        "request sudo. It is not required for normal monitoring.")\n    print("  N. Keep advanced host controls disabled  [recommended, default]")\n    print("  Y. Enable bandwidth / connection controls (requires sudo)")\n    print()\n    answer = prompt("Choice [N]: ").strip().lower()\n    print()\n    if answer in ("", "n", "no"):\n        return False\n    if answer in ("y", "yes"):\n        return True\n    raise InstallError(f"unrecognized advanced-control choice {answer!r}")\n''')

replace_once(
    INSTALLER,
    '''def verify_monitor_host_publish(root: Path, files: Sequence[str]) -> None:\n''',
    '''def _compose_output_tail(handle, limit: int = 80) -> str:\n    handle.flush()\n    handle.seek(0)\n    return "\\n".join(handle.read().splitlines()[-limit:])\n\n\ndef _wait_for_compose_container(root: Path, base: Sequence[str], service: str,\n                                parent, output_handle, timeout: float = 90.0) -> str:\n    deadline = time.monotonic() + timeout\n    while time.monotonic() < deadline:\n        completed = subprocess.run(\n            list(base) + ["ps", "-a", "-q", service], cwd=root, check=False,\n            capture_output=True, text=True)\n        if completed.returncode == 0 and completed.stdout.strip():\n            return completed.stdout.strip().splitlines()[-1]\n        if parent.poll() is not None:\n            tail = _compose_output_tail(output_handle)\n            detail = f"\\n{tail}" if tail else ""\n            raise InstallError(\n                f"Compose activation exited before {service} was created{detail}")\n        time.sleep(0.25)\n    raise InstallError(f"timed out waiting for Compose service {service}")\n\n\ndef _compose_container_result(container_id: str) -> tuple[str, int]:\n    completed = subprocess.run(\n        ["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}",\n         container_id], check=False, capture_output=True, text=True)\n    if completed.returncode != 0:\n        raise InstallError("cannot inspect completed bootstrap container state")\n    fields = completed.stdout.strip().split()\n    if len(fields) != 2:\n        raise InstallError("Docker returned malformed bootstrap container state")\n    try:\n        exit_code = int(fields[1])\n    except ValueError as exc:\n        raise InstallError("Docker returned a malformed bootstrap exit code") from exc\n    return fields[0], exit_code\n\n\ndef _stream_compose_one_shot(root: Path, base: Sequence[str], service: str,\n                             title: str, subtitle: str, parent, output_handle) -> None:\n    ui_section(title, subtitle)\n    container_id = _wait_for_compose_container(\n        root, base, service, parent, output_handle)\n    print("Live progress follows. Leave this terminal running.\\n")\n    logs = subprocess.run(\n        list(base) + ["logs", "--no-color", "--follow", service],\n        cwd=root, check=False)\n    if logs.returncode != 0:\n        print(\n            "Warning: the live log follower ended unexpectedly; "\n            "the service exit status will still be verified.",\n            file=sys.stderr)\n    status, exit_code = _compose_container_result(container_id)\n    if status != "exited" or exit_code != 0:\n        raise InstallError(\n            f"{service} did not complete successfully: status={status}, exit={exit_code}")\n    print()\n    print(f"[OK] {title}")\n    print()\n\n\ndef run_chainstrap_activation_with_live_logs(root: Path, base: Sequence[str]) -> None:\n    """Activate Compose while streaming the two long one-shot bootstrap phases."""\n    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as compose_output:\n        parent = subprocess.Popen(\n            list(base) + ["up", "-d", "--no-build"], cwd=root,\n            stdout=compose_output, stderr=subprocess.STDOUT, text=True)\n        try:\n            _stream_compose_one_shot(\n                root, base, "chainstrap-bootstrap",\n                "ChainStrap verified bootstrap",\n                "Downloading and verifying the vetted snapshot. Progress includes part and "\n                "snapshot percentage, bytes, transfer rate and ETA.",\n                parent, compose_output)\n            _stream_compose_one_shot(\n                root, base, "ravencoin-bootstrap-reindex",\n                "Offline Ravencoin Core validation",\n                "Ravencoin Core is reindexing the downloaded raw blocks with networking "\n                "disabled and will verify the exact snapshot tip and asset indexes.",\n                parent, compose_output)\n            ui_section(\n                "Starting node services",\n                "Bootstrap validation succeeded. Starting Ravencoin Core, ElectrumX and the "\n                "selected optional services.")\n            returncode = parent.wait()\n            if returncode != 0:\n                tail = _compose_output_tail(compose_output)\n                if tail:\n                    print(tail, file=sys.stderr)\n                raise InstallError(\n                    f"docker compose activation failed with exit code {returncode}")\n            print("[OK] Docker services started")\n            print()\n        except BaseException:\n            if parent.poll() is None:\n                parent.terminate()\n                try:\n                    parent.wait(timeout=5)\n                except subprocess.TimeoutExpired:\n                    parent.kill()\n                    parent.wait()\n            raise\n\n\ndef activate_compose(root: Path, base: Sequence[str], bootstrap: str) -> None:\n    if bootstrap == "chainstrap":\n        run_chainstrap_activation_with_live_logs(root, base)\n        return\n    if bootstrap != "p2p":\n        raise InstallError(f"unknown bootstrap choice {bootstrap!r}")\n    ui_section(\n        "Starting node services",\n        "Traditional P2P synchronization selected. Starting Ravencoin Core, ElectrumX and "\n        "the selected optional services.")\n    run_checked(list(base) + ["up", "-d", "--no-build"], cwd=root)\n\n\ndef verify_monitor_host_publish(root: Path, files: Sequence[str]) -> None:\n''')

replace_once(
    INSTALLER,
    '''        try:\n            run_checked(base + ["up", "-d", "--no-build"], cwd=target)\n        except InstallError as exc:\n''',
    '''        try:\n            activate_compose(target, base, bootstrap)\n        except InstallError as exc:\n''')

replace_once(
    INSTALLER,
    '''    try:\n        check_python_version()\n        architecture = detect_architecture()\n''',
    '''    try:\n        print_installer_banner()\n        check_python_version()\n        architecture = detect_architecture()\n''')

replace_once(
    INSTALLER,
    '''        print(\n            f"verified ElectrumX {body['electrumxVersion']} release bundle; "\n            f"official Core {body['coreVersion']} @ {body['coreCommit'][:12]}; "\n            f"Node Monitor @ {metadata['nodeMonitor']['commit'][:12]}")\n\n        interactive = sys.stdin.isatty()\n''',
    '''        ui_section("Verified release", "All signed release and independent Core-policy checks passed.")\n        print(f"  ElectrumX    : {body['electrumxVersion']}")\n        print(f"  Ravencoin    : Core {body['coreVersion']} @ {body['coreCommit'][:12]}")\n        print(f"  Node Monitor : {metadata['nodeMonitor']['commit'][:12]}")\n        print()\n\n        interactive = sys.stdin.isatty()\n''')

replace_once(
    INSTALLER,
    '''        if controller:\n            controller_prerequisites(require_sudo=False if args.check_only else True)\n\n        if args.check_only:\n''',
    '''        if controller:\n            controller_prerequisites(require_sudo=False if args.check_only else True)\n\n        print_installation_summary(storage_root, bootstrap, monitor, controller)\n\n        if args.check_only:\n''')

# Adapt the existing rollback regression to the new activation abstraction.
replace_once(
    TESTS,
    '''    def fake_run_checked(argv, *, cwd=None, quiet=False):\n        if "up" in argv:\n            raise installer.InstallError("simulated chainstrap failure")\n\n    class Completed:\n''',
    '''    def fake_run_checked(argv, *, cwd=None, quiet=False):\n        return None\n\n    def fake_activate(_root, _base, _bootstrap):\n        raise installer.InstallError("simulated chainstrap failure")\n\n    class Completed:\n''')
replace_once(
    TESTS,
    '''    monkeypatch.setattr(installer, "run_checked", fake_run_checked)\n    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)\n''',
    '''    monkeypatch.setattr(installer, "run_checked", fake_run_checked)\n    monkeypatch.setattr(installer, "activate_compose", fake_activate)\n    monkeypatch.setattr(subprocess, "run", fake_subprocess_run)\n''')

with TESTS.open("a", encoding="utf-8") as handle:
    handle.write(r'''


def test_installer_banner_is_terminal_width_aware(monkeypatch, capsys):
    monkeypatch.setattr(
        installer.shutil, "get_terminal_size",
        lambda fallback=(88, 24): os.terminal_size((64, 24)))
    installer.print_installer_banner()
    lines = [line for line in capsys.readouterr().out.splitlines() if line]
    assert "ELECTRUMX RAVENCOIN" in lines
    assert "Verified Node Installer" in lines
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
''')

print("installer UX/progress candidate applied")
