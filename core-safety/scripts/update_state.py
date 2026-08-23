# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""Persistent last-known-good state for the ElectrumX node updater.

Schema v3 records revision-aware release bodies. The security-sensitive
artifact high-water is intentionally *not* duplicated here: it lives in the
host-wide namespace selected by ``artifact_revision.py`` so moving or deleting
an installation directory cannot reset rollback protection.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import re
import tempfile
from typing import Optional

STATE_SCHEMA_VERSION = 3
PREVIOUS_STATE_SCHEMA_VERSION = 2
LEGACY_STATE_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclasses.dataclass
class UpdateState:
    current_release: Optional[dict] = None
    last_known_good_release: Optional[dict] = None
    pending_candidate: Optional[dict] = None
    update_timestamp: Optional[str] = None
    failure_reason: Optional[str] = None
    minimum_core_policy_version: int = 0

    def to_dict(self) -> dict:
        return {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "currentRelease": self.current_release,
            "lastKnownGoodRelease": self.last_known_good_release,
            "pendingCandidate": self.pending_candidate,
            "updateTimestamp": self.update_timestamp,
            "failureReason": self.failure_reason,
            "minimumCorePolicyVersion": self.minimum_core_policy_version,
        }

    @staticmethod
    def _migrate_release(value: Optional[dict], schema_version: int) -> Optional[dict]:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("release state must be an object")

        migrated = dict(value)
        required_identity = (
            "electrumxVersion",
            "artifact_revision",
            "artifactDigest",
            "provenanceDigest",
        )
        missing = [field for field in required_identity if field not in migrated]
        if missing:
            if schema_version < STATE_SCHEMA_VERSION:
                raise ValueError(
                    "legacy updater state lacks authenticated revision-aware release identity; "
                    "manual 1.13.1 -> 1.13.3 trust transition is required")
            raise ValueError(f"release state is missing identity field(s): {missing}")

        version = migrated["electrumxVersion"]
        if not isinstance(version, str) or not version:
            raise ValueError("release electrumxVersion must be a non-empty string")
        revision = migrated["artifact_revision"]
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
            raise ValueError("release artifact_revision must be a non-negative integer")
        for field in ("artifactDigest", "provenanceDigest"):
            if not isinstance(migrated[field], str) or \
                    SHA256_RE.fullmatch(migrated[field]) is None:
                raise ValueError(f"release {field} must be sha256:<64 lowercase hex>")
        return migrated

    @classmethod
    def from_dict(cls, data: dict) -> "UpdateState":
        if not isinstance(data, dict):
            raise ValueError("update state must be a JSON object")
        schema_version = data.get("schemaVersion", LEGACY_STATE_SCHEMA_VERSION)
        if schema_version not in (
                LEGACY_STATE_SCHEMA_VERSION,
                PREVIOUS_STATE_SCHEMA_VERSION,
                STATE_SCHEMA_VERSION):
            raise ValueError(f"unsupported update state schemaVersion {schema_version!r}")

        if schema_version == LEGACY_STATE_SCHEMA_VERSION:
            minimum_policy_version = 0
        else:
            if "minimumCorePolicyVersion" not in data:
                raise ValueError(
                    f"schema v{schema_version} update state is missing minimumCorePolicyVersion")
            minimum_policy_version = data["minimumCorePolicyVersion"]
            if not isinstance(minimum_policy_version, int) or \
                    isinstance(minimum_policy_version, bool) or minimum_policy_version < 0:
                raise ValueError("minimumCorePolicyVersion must be a non-negative integer")

        pending = data.get("pendingCandidate")
        if pending is not None and not isinstance(pending, dict):
            raise ValueError("pendingCandidate must be an object or null")
        if pending and pending.get("manifest") is not None:
            pending = dict(pending)
            pending["manifest"] = cls._migrate_release(
                pending["manifest"], schema_version)

        return cls(
            current_release=cls._migrate_release(
                data.get("currentRelease"), schema_version),
            last_known_good_release=cls._migrate_release(
                data.get("lastKnownGoodRelease"), schema_version),
            pending_candidate=pending,
            update_timestamp=data.get("updateTimestamp"),
            failure_reason=data.get("failureReason"),
            minimum_core_policy_version=minimum_policy_version,
        )


def load_state(path: str) -> UpdateState:
    if not os.path.exists(path):
        return UpdateState()
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return UpdateState.from_dict(payload)


def _fsync_directory(directory: str) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        directory_fd = os.open(directory, os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(directory_fd)
    except OSError:
        pass
    finally:
        os.close(directory_fd)


def save_state(path: str, state: UpdateState) -> None:
    """Durable atomic write: never leaves a torn or world-readable state."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, mode=0o700, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".update-state-", dir=directory)
    try:
        os.chmod(tmp_path, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(directory)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def effective_core_policy_floor(state: UpdateState, configured_version: int = 0) -> int:
    if not isinstance(configured_version, int) or isinstance(configured_version, bool) or \
            configured_version < 0:
        raise ValueError("configured Core policy floor must be a non-negative integer")
    return max(state.minimum_core_policy_version, configured_version)


def record_verified_core_policy(state: UpdateState, version: int) -> UpdateState:
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError("verified Core policy version must be a positive integer")
    if version < state.minimum_core_policy_version:
        raise ValueError(
            f"verified policy version {version} is below persisted anti-rollback floor "
            f"{state.minimum_core_policy_version}")
    state.minimum_core_policy_version = version
    return state


def record_check_result(state: UpdateState, *, pending_candidate: Optional[dict],
                        failure_reason: Optional[str] = None) -> UpdateState:
    state.pending_candidate = pending_candidate
    state.failure_reason = failure_reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_promotion(state: UpdateState, *, applied_release: dict) -> UpdateState:
    revision = applied_release.get("artifact_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:
        raise ValueError("promoted release lacks valid artifact_revision")
    if state.current_release is not None:
        state.last_known_good_release = state.current_release
    state.current_release = applied_release
    state.pending_candidate = None
    state.failure_reason = None
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_rollback(state: UpdateState, *, reason: str,
                    restored_release: Optional[dict] = None) -> UpdateState:
    if restored_release is not None:
        state.current_release = restored_release
    elif state.last_known_good_release is not None:
        state.current_release = state.last_known_good_release
    state.pending_candidate = None
    state.failure_reason = reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_stuck(state: UpdateState, *, reason: str) -> UpdateState:
    state.failure_reason = reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state
