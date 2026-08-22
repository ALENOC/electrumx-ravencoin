#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Artifact-revision ordering and host-wide anti-rollback state.

The locator is deliberately root-owned and outside any installation directory.
It selects exactly one host namespace. A non-root installer can use an
administrator-provisioned locator for its uid, but can never create or replace
that locator itself. Root never follows an unprivileged namespace.
"""
from __future__ import annotations

import json
import os
import pathlib
import stat
from typing import Mapping, Optional

LOCATOR_PATH = pathlib.Path("/var/lib/electrumx-ravencoin/security-state.locator")
ROOT_STATE_PATH = pathlib.Path("/var/lib/electrumx-ravencoin/security-state.json")
STATE_BASENAME = pathlib.Path("electrumx-ravencoin/security-state.json")
LOCATOR_SCHEMA = 1
STATE_SCHEMA = 1


class RevisionSecurityError(RuntimeError):
    """An artifact revision or host anti-rollback invariant failed."""


def validate_revision(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RevisionSecurityError("artifact_revision must be a non-negative integer")
    return value


def compare_revision(current: Mapping, candidate: Mapping) -> str:
    """Classify candidate ordering including same-version equivocation."""
    from packaging.version import Version

    current_version = Version(str(current["electrumxVersion"]))
    candidate_version = Version(str(candidate["electrumxVersion"]))
    current_revision = validate_revision(current["artifact_revision"])
    candidate_revision = validate_revision(candidate["artifact_revision"])
    if candidate_version < current_version:
        return "rollback-version"
    if candidate_version > current_version:
        return "new-version"
    if candidate_revision < current_revision:
        return "rollback-revision"
    if candidate_revision > current_revision:
        return "new-revision"
    if candidate.get("artifactDigest") != current.get("artifactDigest"):
        return "equivocation"
    return "same"


def user_state_path(env: Mapping[str, str]) -> pathlib.Path:
    xdg = env.get("XDG_STATE_HOME")
    if xdg:
        base = pathlib.Path(xdg)
    else:
        home = env.get("HOME")
        if not home:
            raise RevisionSecurityError("HOME is required when XDG_STATE_HOME is unset")
        base = pathlib.Path(home) / ".local" / "state"
    if not base.is_absolute():
        raise RevisionSecurityError("state directory must be absolute")
    return base / STATE_BASENAME


def _lstat_regular(path: pathlib.Path, *, owner_uid: int,
                   modes: tuple[int, ...], label: str) -> os.stat_result:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RevisionSecurityError(f"cannot inspect {label} {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RevisionSecurityError(f"{label} must be a regular non-symlink file")
    if info.st_uid != owner_uid:
        raise RevisionSecurityError(
            f"{label} owner uid {info.st_uid} does not equal required uid {owner_uid}")
    mode = stat.S_IMODE(info.st_mode)
    if mode not in modes:
        expected = "/".join(f"{item:04o}" for item in modes)
        raise RevisionSecurityError(f"{label} mode {mode:04o} is not {expected}")
    return info


def _read_locator(locator: pathlib.Path) -> dict:
    _lstat_regular(
        locator, owner_uid=0, modes=(0o644,), label="security-state locator")
    try:
        data = json.loads(locator.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionSecurityError(f"cannot read security-state locator: {exc}") from exc
    if not isinstance(data, dict) or set(data) != {"schemaVersion", "ownerUid", "path"}:
        raise RevisionSecurityError("security-state locator has unexpected schema")
    if data["schemaVersion"] != LOCATOR_SCHEMA:
        raise RevisionSecurityError("security-state locator schema is unsupported")
    owner_uid = data["ownerUid"]
    if not isinstance(owner_uid, int) or isinstance(owner_uid, bool) or owner_uid < 0:
        raise RevisionSecurityError("security-state locator ownerUid is malformed")
    target = pathlib.Path(str(data["path"]))
    if not target.is_absolute():
        raise RevisionSecurityError("security-state locator target must be absolute")
    return {"ownerUid": owner_uid, "path": target}


def _fsync_directory(directory: pathlib.Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_json(path: pathlib.Path, payload: dict, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".new.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def resolve_host_high_water_path(*, euid: Optional[int] = None,
                                 env: Optional[Mapping[str, str]] = None,
                                 locator_path: pathlib.Path = LOCATOR_PATH,
                                 root_state_path: pathlib.Path = ROOT_STATE_PATH,
                                 provision_root_locator: bool = True) -> pathlib.Path:
    """Resolve the one host anti-rollback namespace or fail closed.

    Root may bootstrap only the canonical root namespace. Non-root callers
    require an administrator-created root-owned locator whose ownerUid and path
    exactly match that caller and its canonical XDG/HOME state path.
    """
    uid = os.geteuid() if euid is None else euid
    environment = os.environ if env is None else env
    expected = root_state_path if uid == 0 else user_state_path(environment)

    if not locator_path.exists():
        if uid != 0 or not provision_root_locator:
            raise RevisionSecurityError(
                f"root-owned security-state locator is missing: {locator_path}")
        locator_path.parent.mkdir(parents=True, exist_ok=True)
        parent_info = locator_path.parent.stat()
        if parent_info.st_uid != 0 or stat.S_IMODE(parent_info.st_mode) & 0o022:
            raise RevisionSecurityError(
                "security-state locator parent must be root-owned and not group/world-writable")
        _atomic_json(locator_path, {
            "schemaVersion": LOCATOR_SCHEMA,
            "ownerUid": 0,
            "path": str(root_state_path),
        }, 0o644)

    locator = _read_locator(locator_path)
    if locator["ownerUid"] != uid:
        raise RevisionSecurityError(
            f"security-state namespace belongs to uid {locator['ownerUid']}, not caller uid {uid}")
    if locator["path"] != expected:
        raise RevisionSecurityError(
            f"security-state locator target {locator['path']} is not canonical {expected}")

    target = locator["path"]
    if target.exists() or target.is_symlink():
        _lstat_regular(
            target, owner_uid=uid, modes=(0o600,), label="security-state target")
    return target


def load_high_water(path: pathlib.Path) -> dict:
    if not path.exists():
        return {"schemaVersion": STATE_SCHEMA, "releases": {}}
    _lstat_regular(
        path, owner_uid=os.geteuid(), modes=(0o600,), label="security-state target")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionSecurityError(f"cannot read security-state target: {exc}") from exc
    if not isinstance(data, dict) or data.get("schemaVersion") != STATE_SCHEMA or \
            not isinstance(data.get("releases"), dict):
        raise RevisionSecurityError("security-state target has unsupported schema")
    return data


def enforce_high_water(state: Mapping, manifest: Mapping) -> None:
    version = str(manifest["electrumxVersion"])
    revision = validate_revision(manifest["artifact_revision"])
    digest = str(manifest["artifactDigest"])
    previous = state.get("releases", {}).get(version)
    if previous is None:
        return
    previous_revision = validate_revision(previous["artifact_revision"])
    if revision < previous_revision:
        raise RevisionSecurityError("artifact revision is below persisted high-water")
    if revision == previous_revision and digest != previous.get("artifactDigest"):
        raise RevisionSecurityError("artifact equivocation at persisted version/revision")


def advance_high_water(path: pathlib.Path, manifest: Mapping) -> None:
    """Advance only after successful install/promotion; never during discovery."""
    state = load_high_water(path)
    enforce_high_water(state, manifest)
    version = str(manifest["electrumxVersion"])
    revision = validate_revision(manifest["artifact_revision"])
    previous = state["releases"].get(version)
    if previous is None or revision > previous["artifact_revision"]:
        state["releases"][version] = {
            "artifact_revision": revision,
            "artifactDigest": str(manifest["artifactDigest"]),
            "releaseTimestamp": str(manifest["releaseTimestamp"]),
        }
        _atomic_json(path, state, 0o600)
