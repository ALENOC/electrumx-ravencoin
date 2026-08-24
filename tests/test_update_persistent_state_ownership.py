# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for operator ownership of state preserved across updates.

A release switch that keeps only the permission bits of `.secrets` hands the
files to root.  Sidecars that bind-mount them under an unprivileged uid, such
as the Node Monitor, then fail to read them and crash-loop while the node
itself stays healthy.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_runtime as runtime  # noqa: E402


def _old_root(tmp_path: pathlib.Path) -> pathlib.Path:
    old = tmp_path / "old"
    secrets = old / ".secrets"
    secrets.mkdir(parents=True)
    for name in ("raven_rpc_user", "raven_rpc_password"):
        target = secrets / name
        target.write_text(f"{name}-value\n", encoding="utf-8")
        os.chmod(target, 0o600)
    (old / ".env").write_text("KEY=value\n", encoding="utf-8")
    return old


def test_secrets_keep_owner_and_mode_across_release_switch(tmp_path):
    old = _old_root(tmp_path)
    new = tmp_path / "new"
    new.mkdir()

    runtime.copy_persistent_state(old, new)

    for name in ("raven_rpc_user", "raven_rpc_password"):
        source = old / ".secrets" / name
        destination = new / ".secrets" / name
        assert destination.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        assert destination.stat().st_mode & 0o777 == 0o600
        assert (destination.stat().st_uid, destination.stat().st_gid) == \
            (source.stat().st_uid, source.stat().st_gid)


def test_preserved_directory_keeps_owner(tmp_path):
    old = _old_root(tmp_path)
    new = tmp_path / "new"
    new.mkdir()

    runtime.copy_persistent_state(old, new)

    source = old / ".secrets"
    destination = new / ".secrets"
    assert destination.is_dir()
    assert (destination.stat().st_uid, destination.stat().st_gid) == \
        (source.stat().st_uid, source.stat().st_gid)


def test_release_switch_fails_closed_when_ownership_cannot_be_restored(tmp_path, monkeypatch):
    """A silent owner change is the defect; refusing the copy is the safe end."""
    old = _old_root(tmp_path)
    new = tmp_path / "new"
    new.mkdir()

    real_stat = os.stat

    def foreign_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if str(path).startswith(str(old)):
            return os.stat_result(tuple(result)[:4] + (result.st_uid + 1, result.st_gid + 1)
                                  + tuple(result)[6:])
        return result

    def refuse_chown(*args, **kwargs):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(os, "stat", foreign_stat)
    monkeypatch.setattr(os, "chown", refuse_chown)

    with pytest.raises(runtime.UpdateRuntimeError) as excinfo:
        runtime.copy_persistent_state(old, new)
    assert "preserve operator ownership" in str(excinfo.value)


def test_every_preserved_file_and_directory_is_chowned(tmp_path, monkeypatch):
    old = _old_root(tmp_path)
    new = tmp_path / "new"
    new.mkdir()

    real_stat = os.stat

    def foreign_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if str(path).startswith(str(old)):
            return os.stat_result(tuple(result)[:4] + (4242, 4243) + tuple(result)[6:])
        return result

    calls = []

    def record_chown(path, uid, gid, *args, **kwargs):
        calls.append((pathlib.Path(path), uid, gid))

    monkeypatch.setattr(os, "stat", foreign_stat)
    monkeypatch.setattr(os, "chown", record_chown)

    runtime.copy_persistent_state(old, new)

    chowned = {path for path, _, _ in calls}
    assert new / ".secrets" in chowned
    assert new / ".secrets" / "raven_rpc_user" in chowned
    assert new / ".secrets" / "raven_rpc_password" in chowned
    assert new / ".env" in chowned
    assert all((uid, gid) == (4242, 4243) for _, uid, gid in calls)
