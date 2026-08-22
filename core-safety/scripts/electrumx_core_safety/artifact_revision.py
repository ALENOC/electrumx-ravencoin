#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Artifact ordering and host-wide anti-rollback state.

The locator is deliberately root-owned and outside any installation directory.
It selects exactly one host namespace. A non-root installer can use an
administrator-provisioned locator for its uid, but can never create or replace
that locator itself. Root never follows an unprivileged namespace.

Release ordering is centralized here. Callers must not reimplement version,
revision, digest, or equivocation ordering independently.
"""
from __future__ import annotations

import enum
import json
import os
import pathlib
import re
import stat
from dataclasses import dataclass
from typing import Mapping, Optional

from packaging.version import InvalidVersion, Version

LOCATOR_PATH = pathlib.Path("/var/lib/electrumx-ravencoin/security-state.locator")
ROOT_STATE_PATH = pathlib.Path("/var/lib/electrumx-ravencoin/security-state.json")
STATE_BASENAME = pathlib.Path("electrumx-ravencoin/security-state.json")
LOCATOR_SCHEMA = 1
STATE_SCHEMA = 2
MAX_LOCATOR_BYTES = 16 * 1024
MAX_STATE_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class RevisionSecurityError(RuntimeError):
    """An artifact revision or host anti-rollback invariant failed."""


class EligibilityVerdict(enum.Enum):
    ELIGIBLE = "ELIGIBLE"
    IGNORED_SAME_ARTIFACT = "IGNORED_SAME_ARTIFACT"
    REFUSED_OLDER_VERSION = "REFUSED_OLDER_VERSION"
    REFUSED_OLDER_REVISION = "REFUSED_OLDER_REVISION"
    REFUSED_ARTIFACT_EQUIVOCATION = "REFUSED_ARTIFACT_EQUIVOCATION"
    REFUSED_MISSING_REVISION_DATA = "REFUSED_MISSING_REVISION_DATA"
    REFUSED_MALFORMED_REVISION_DATA = "REFUSED_MALFORMED_REVISION_DATA"
    REFUSED_MISSING_DIGEST_DATA = "REFUSED_MISSING_DIGEST_DATA"
    REFUSED_MALFORMED_DIGEST_DATA = "REFUSED_MALFORMED_DIGEST_DATA"
    REFUSED_MALFORMED_VERSION_DATA = "REFUSED_MALFORMED_VERSION_DATA"
    REFUSED_PRERELEASE_ON_STABLE_CHANNEL = "REFUSED_PRERELEASE_ON_STABLE_CHANNEL"
    REFUSED_WRONG_CHANNEL = "REFUSED_WRONG_CHANNEL"
    REFUSED_AUTO_UPDATE_OFF = "REFUSED_AUTO_UPDATE_OFF"


@dataclass(frozen=True)
class OrderingDecision:
    verdict: EligibilityVerdict
    reason: str = ""


def _parse_version(value: object, label: str) -> tuple[Optional[Version], Optional[OrderingDecision]]:
    if not isinstance(value, str) or not value:
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MALFORMED_VERSION_DATA,
            f"{label} must be a non-empty semantic version string")
    try:
        return Version(value), None
    except InvalidVersion as exc:
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MALFORMED_VERSION_DATA,
            f"{label} is unparseable: {exc}")


def _revision_for_order(value: object, label: str) -> tuple[Optional[int], Optional[OrderingDecision]]:
    if value is None:
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MISSING_REVISION_DATA,
            f"{label} is missing")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MALFORMED_REVISION_DATA,
            f"{label} must be a non-negative integer")
    return value, None


def validate_revision(value: object) -> int:
    revision, refusal = _revision_for_order(value, "artifact_revision")
    if refusal is not None:
        raise RevisionSecurityError(refusal.reason)
    return revision


def _digest_for_order(value: object, label: str) -> tuple[Optional[str], Optional[OrderingDecision]]:
    if value is None or value == "":
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MISSING_DIGEST_DATA,
            f"{label} is missing")
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        return None, OrderingDecision(
            EligibilityVerdict.REFUSED_MALFORMED_DIGEST_DATA,
            f"{label} must be sha256:<64 lowercase hex>")
    return value, None


def classify_release_order(current: Mapping, candidate: Mapping) -> OrderingDecision:
    """Return the one canonical version/revision/digest ordering decision."""
    current_version, refusal = _parse_version(
        current.get("electrumxVersion"), "current electrumxVersion")
    if refusal is not None:
        return refusal
    candidate_version, refusal = _parse_version(
        candidate.get("electrumxVersion"), "candidate electrumxVersion")
    if refusal is not None:
        return refusal

    if candidate_version < current_version:
        return OrderingDecision(EligibilityVerdict.REFUSED_OLDER_VERSION)
    if candidate_version > current_version:
        return OrderingDecision(EligibilityVerdict.ELIGIBLE)

    current_revision, refusal = _revision_for_order(
        current.get("artifact_revision"), "current artifact_revision")
    if refusal is not None:
        return refusal
    candidate_revision, refusal = _revision_for_order(
        candidate.get("artifact_revision"), "candidate artifact_revision")
    if refusal is not None:
        return refusal

    if candidate_revision < current_revision:
        return OrderingDecision(EligibilityVerdict.REFUSED_OLDER_REVISION)
    if candidate_revision > current_revision:
        return OrderingDecision(EligibilityVerdict.ELIGIBLE)

    current_artifact, refusal = _digest_for_order(
        current.get("artifactDigest"), "current artifactDigest")
    if refusal is not None:
        return refusal
    candidate_artifact, refusal = _digest_for_order(
        candidate.get("artifactDigest"), "candidate artifactDigest")
    if refusal is not None:
        return refusal
    current_provenance, refusal = _digest_for_order(
        current.get("provenanceDigest"), "current provenanceDigest")
    if refusal is not None:
        return refusal
    candidate_provenance, refusal = _digest_for_order(
        candidate.get("provenanceDigest"), "candidate provenanceDigest")
    if refusal is not None:
        return refusal

    if current_artifact != candidate_artifact or current_provenance != candidate_provenance:
        return OrderingDecision(
            EligibilityVerdict.REFUSED_ARTIFACT_EQUIVOCATION,
            "same version/revision is bound to different artifact or provenance digest")
    return OrderingDecision(EligibilityVerdict.IGNORED_SAME_ARTIFACT)


def compare_revision(current: Mapping, candidate: Mapping) -> OrderingDecision:
    """Compatibility entry point; delegates to the canonical ordering function."""
    return classify_release_order(current, candidate)


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


def _validate_stat(info: os.stat_result, *, owner_uid: int,
                   modes: tuple[int, ...], label: str) -> None:
    if not stat.S_ISREG(info.st_mode):
        raise RevisionSecurityError(f"{label} must be a regular non-symlink file")
    if info.st_uid != owner_uid:
        raise RevisionSecurityError(
            f"{label} owner uid {info.st_uid} does not equal required uid {owner_uid}")
    mode = stat.S_IMODE(info.st_mode)
    if mode not in modes:
        expected = "/".join(f"{item:04o}" for item in modes)
        raise RevisionSecurityError(f"{label} mode {mode:04o} is not {expected}")


def _read_json_from_verified_fd(path: pathlib.Path, *, owner_uid: int,
                                modes: tuple[int, ...], label: str,
                                max_bytes: int, missing_ok: bool = False):
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise RevisionSecurityError("O_NOFOLLOW is required for security-state reads")
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise RevisionSecurityError(f"{label} is missing: {path}")
    except OSError as exc:
        raise RevisionSecurityError(f"cannot open {label} {path}: {exc}") from exc
    try:
        info = os.fstat(fd)
        _validate_stat(info, owner_uid=owner_uid, modes=modes, label=label)
        if info.st_size < 0 or info.st_size > max_bytes:
            raise RevisionSecurityError(f"{label} exceeds size limit")
        chunks = []
        remaining = max_bytes + 1
        while remaining > 0:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > max_bytes:
            raise RevisionSecurityError(f"{label} exceeds size limit")
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RevisionSecurityError(f"cannot decode {label}: {exc}") from exc
    finally:
        os.close(fd)


def _validate_locator_data(data: object) -> dict:
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


def _read_locator(locator: pathlib.Path, *, missing_ok: bool = False):
    data = _read_json_from_verified_fd(
        locator, owner_uid=0, modes=(0o644,), label="security-state locator",
        max_bytes=MAX_LOCATOR_BYTES, missing_ok=missing_ok)
    if data is None:
        return None
    return _validate_locator_data(data)


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

    locator = _read_locator(locator_path, missing_ok=True)
    if locator is None:
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
    return locator["path"]


def _validate_high_water_state(data: object) -> dict:
    if not isinstance(data, dict) or set(data) != {
            "schemaVersion", "highestAcceptedVersion", "releases"}:
        raise RevisionSecurityError("security-state target has unexpected schema")
    if data["schemaVersion"] != STATE_SCHEMA:
        raise RevisionSecurityError("security-state target has unsupported schema")
    highest = data["highestAcceptedVersion"]
    if highest is not None:
        _, refusal = _parse_version(highest, "highestAcceptedVersion")
        if refusal is not None:
            raise RevisionSecurityError(refusal.reason)
    releases = data["releases"]
    if not isinstance(releases, dict):
        raise RevisionSecurityError("security-state releases must be an object")
    for version, record in releases.items():
        _, refusal = _parse_version(version, "release state version")
        if refusal is not None:
            raise RevisionSecurityError(refusal.reason)
        if not isinstance(record, dict) or set(record) != {
                "artifact_revision", "artifactDigest", "provenanceDigest", "releaseTimestamp"}:
            raise RevisionSecurityError("security-state release record has unexpected schema")
        validate_revision(record["artifact_revision"])
        for field in ("artifactDigest", "provenanceDigest"):
            _, digest_refusal = _digest_for_order(record.get(field), f"release state {field}")
            if digest_refusal is not None:
                raise RevisionSecurityError(digest_refusal.reason)
        if not isinstance(record["releaseTimestamp"], str) or not record["releaseTimestamp"]:
            raise RevisionSecurityError("security-state releaseTimestamp is malformed")
    return data


def load_high_water(path: pathlib.Path) -> dict:
    data = _read_json_from_verified_fd(
        path, owner_uid=os.geteuid(), modes=(0o600,), label="security-state target",
        max_bytes=MAX_STATE_BYTES, missing_ok=True)
    if data is None:
        return {
            "schemaVersion": STATE_SCHEMA,
            "highestAcceptedVersion": None,
            "releases": {},
        }
    return _validate_high_water_state(data)


def _manifest_identity(manifest: Mapping) -> dict:
    return {
        "electrumxVersion": manifest.get("electrumxVersion"),
        "artifact_revision": manifest.get("artifact_revision"),
        "artifactDigest": manifest.get("artifactDigest"),
        "provenanceDigest": manifest.get("provenanceDigest"),
    }


def enforce_high_water(state: Mapping, manifest: Mapping) -> None:
    state = _validate_high_water_state(dict(state))
    candidate = _manifest_identity(manifest)
    candidate_version, refusal = _parse_version(
        candidate["electrumxVersion"], "candidate electrumxVersion")
    if refusal is not None:
        raise RevisionSecurityError(refusal.reason)
    validate_revision(candidate["artifact_revision"])
    for field in ("artifactDigest", "provenanceDigest"):
        _, digest_refusal = _digest_for_order(candidate.get(field), f"candidate {field}")
        if digest_refusal is not None:
            raise RevisionSecurityError(digest_refusal.reason)

    highest = state["highestAcceptedVersion"]
    if highest is not None:
        highest_version = Version(highest)
        if candidate_version < highest_version:
            raise RevisionSecurityError("release version is below persisted high-water")
        if candidate_version == highest_version and str(candidate_version) not in state["releases"]:
            raise RevisionSecurityError(
                "persisted highest version has no corresponding release record")

    previous = state["releases"].get(str(candidate_version))
    if previous is None:
        return
    current = {
        "electrumxVersion": str(candidate_version),
        **previous,
    }
    decision = classify_release_order(current, candidate)
    if decision.verdict in (
            EligibilityVerdict.ELIGIBLE,
            EligibilityVerdict.IGNORED_SAME_ARTIFACT):
        return
    raise RevisionSecurityError(decision.reason or decision.verdict.value)


def advance_high_water(path: pathlib.Path, manifest: Mapping) -> None:
    """Advance only after successful install/promotion; never during discovery."""
    state = load_high_water(path)
    enforce_high_water(state, manifest)
    candidate = _manifest_identity(manifest)
    candidate_version = Version(str(candidate["electrumxVersion"]))
    version = str(candidate_version)
    revision = validate_revision(candidate["artifact_revision"])
    previous = state["releases"].get(version)
    changed = False
    if previous is None or revision > previous["artifact_revision"]:
        state["releases"][version] = {
            "artifact_revision": revision,
            "artifactDigest": str(candidate["artifactDigest"]),
            "provenanceDigest": str(candidate["provenanceDigest"]),
            "releaseTimestamp": str(manifest["releaseTimestamp"]),
        }
        changed = True
    highest = state["highestAcceptedVersion"]
    if highest is None or candidate_version > Version(highest):
        state["highestAcceptedVersion"] = version
        changed = True
    if changed:
        _atomic_json(path, state, 0o600)
