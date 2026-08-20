#!/usr/bin/env python3
"""Update pre-storage installer regression fixtures to the new storage contract."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
TEST = ROOT / "core-safety" / "scripts" / "test_installer.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = TEST.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '        "compose.chainstrap.yaml": b"network_mode: none\\n",\n',
        '        "compose.storage.yaml": (\n'
        '            b"volumes:\\n"\n'
        '            b"  ravencoin-data:\\n    driver: local\\n"\n'
        '            b"  ravencoin-config:\\n    driver: local\\n"\n'
        '            b"  electrumx-data:\\n    driver: local\\n"\n'
        '            b"  monitor-data:\\n    driver: local\\n"\n'
        '        ),\n'
        '        "compose.chainstrap.yaml": b"network_mode: none\\n",\n',
        "synthetic bundle storage overlay",
    )

    text = replace_once(
        text,
        'def test_p2p_opt_out_drops_only_chainstrap_overlay():\n'
        '    assert installer.compose_files("chainstrap", False) == [\n'
        '        "compose.yaml", "compose.chainstrap.yaml"]\n'
        '    assert installer.compose_files("p2p", False) == ["compose.yaml"]\n',
        'def test_p2p_opt_out_drops_only_chainstrap_overlay():\n'
        '    assert installer.compose_files("chainstrap", False) == [\n'
        '        "compose.yaml", "compose.storage.yaml", "compose.chainstrap.yaml"]\n'
        '    assert installer.compose_files("p2p", False) == [\n'
        '        "compose.yaml", "compose.storage.yaml"]\n',
        "compose files without monitor",
    )

    text = replace_once(
        text,
        'def test_monitor_choice_adds_hardened_overlay():\n'
        '    assert installer.compose_files("chainstrap", True) == [\n'
        '        "compose.yaml", "compose.chainstrap.yaml", "compose.monitor.yaml"]\n',
        'def test_monitor_choice_adds_hardened_overlay():\n'
        '    assert installer.compose_files("chainstrap", True) == [\n'
        '        "compose.yaml", "compose.storage.yaml",\n'
        '        "compose.chainstrap.yaml", "compose.monitor.yaml"]\n',
        "compose files with monitor",
    )

    text = replace_once(
        text,
        '        installer.install_fresh(\n'
        '            target, b"not-used", body={}, metadata={},\n'
        '            bootstrap="chainstrap", monitor=False, controller=False)\n',
        '        installer.install_fresh(\n'
        '            target, b"not-used", body={}, metadata={},\n'
        '            bootstrap="chainstrap", monitor=False, controller=False,\n'
        '            storage_root=tmp_path / "storage")\n',
        "existing destination call",
    )

    text = replace_once(
        text,
        '    monkeypatch.setattr(installer, "require_clean_docker_project_runtime", lambda: None)\n'
        '    monkeypatch.setattr(installer, "extract_bundle", lambda _data, _dest: None)\n',
        '    monkeypatch.setattr(installer, "require_clean_docker_project_runtime", lambda: None)\n'
        '    monkeypatch.setattr(installer, "extract_bundle", lambda _data, _dest: None)\n'
        '    # This test isolates activation/rollback. Storage-path parsing and\n'
        '    # layout have dedicated tests in tests/test_installer_storage.py.\n'
        '    monkeypatch.setattr(installer, "write_storage_env", lambda _root, _storage: None)\n',
        "failed ChainStrap storage fixture",
    )

    text = replace_once(
        text,
        '        installer.install_fresh(\n'
        '            target, b"unused", body={}, metadata={},\n'
        '            bootstrap="chainstrap", monitor=False, controller=False)\n\n'
        '    assert not target.exists()\n',
        '        installer.install_fresh(\n'
        '            target, b"unused", body={}, metadata={},\n'
        '            bootstrap="chainstrap", monitor=False, controller=False,\n'
        '            storage_root=tmp_path / "storage")\n\n'
        '    assert not target.exists()\n'
        '    assert not (tmp_path / "storage").exists()\n',
        "failed ChainStrap call and storage cleanup",
    )

    TEST.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
