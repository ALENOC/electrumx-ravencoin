# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""Persistent last-known-good state for the ElectrumX node updater.

The state file is written atomically and never contains blockchain data,
ElectrumX DB content, RPC credentials, or signing keys. In addition to release
state it persists the highest verified safe-Core policy version. That monotonic
floor prevents a later local/network rollback to an older still-valid signed
policy from silently restoring trust that a newer policy revoked.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import tempfile
from typing import Optional

STATE_SCHEMA_VERSION = 2


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

    @classmethod
    def from_dict(cls, data: dict) -> "UpdateState":
        version = data.get("minimumCorePolicyVersion", 0)
        if not isinstance(version, int) or isinstance(version, bool) or version < 0:
            version = 0
        return cls(
            current_release=data.get("currentRelease"),
            last_known_good_release=data.get("lastKnownGoodRelease"),
            pending_candidate=data.get("pendingCandidate"),
            update_timestamp=data.get("updateTimestamp"),
            failure_reason=data.get("failureReason"),
            minimum_core_policy_version=version,
        )


def load_state(path: str) -> UpdateState:
    if not os.path.exists(path):
        return UpdateState()
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("update state must be a JSON object")
    return UpdateState.from_dict(payload)


def save_state(path: str, state: UpdateState) -> None:
    """Atomic write: never leaves a partially written state file on disk."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".update-state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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
    if state.current_release is not None:
        state.last_known_good_release = state.current_release
    state.current_release = applied_release
    state.pending_candidate = None
    state.failure_reason = None
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_rollback(state: UpdateState, *, reason: str) -> UpdateState:
    if state.last_known_good_release is not None:
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
