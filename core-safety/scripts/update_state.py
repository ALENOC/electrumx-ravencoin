# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Persistent last-known-good state for the ElectrumX node self-update system.

Five fields, one file, written atomically (temp file + os.replace, which is
atomic on the same filesystem) so a crash mid-write never leaves a torn,
half-updated state file behind. This module never touches Ravencoin Core's
blockchain data or ElectrumX's own database; it only ever writes its own
small JSON state file.
"""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import tempfile
from typing import Optional

STATE_SCHEMA_VERSION = 1


@dataclasses.dataclass
class UpdateState:
    current_release: Optional[dict] = None
    last_known_good_release: Optional[dict] = None
    pending_candidate: Optional[dict] = None
    update_timestamp: Optional[str] = None
    failure_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "schemaVersion": STATE_SCHEMA_VERSION,
            "currentRelease": self.current_release,
            "lastKnownGoodRelease": self.last_known_good_release,
            "pendingCandidate": self.pending_candidate,
            "updateTimestamp": self.update_timestamp,
            "failureReason": self.failure_reason,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UpdateState":
        return cls(
            current_release=data.get("currentRelease"),
            last_known_good_release=data.get("lastKnownGoodRelease"),
            pending_candidate=data.get("pendingCandidate"),
            update_timestamp=data.get("updateTimestamp"),
            failure_reason=data.get("failureReason"),
        )


def load_state(path: str) -> UpdateState:
    if not os.path.exists(path):
        return UpdateState()
    with open(path, "r", encoding="utf-8") as handle:
        return UpdateState.from_dict(json.load(handle))


def save_state(path: str, state: UpdateState) -> None:
    """Atomic write: never leaves a partially written state file on disk."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    fd, tmp_path = tempfile.mkstemp(prefix=".update-state-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_path, path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


def record_check_result(state: UpdateState, *, pending_candidate: Optional[dict],
                        failure_reason: Optional[str] = None) -> UpdateState:
    state.pending_candidate = pending_candidate
    state.failure_reason = failure_reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_promotion(state: UpdateState, *, applied_release: dict) -> UpdateState:
    """The previously-current release becomes last-known-good; the applied
    candidate becomes current. Called only after all health gates pass.
    """
    if state.current_release is not None:
        state.last_known_good_release = state.current_release
    state.current_release = applied_release
    state.pending_candidate = None
    state.failure_reason = None
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_rollback(state: UpdateState, *, reason: str) -> UpdateState:
    """current_release reverts to last_known_good_release. Never deletes
    Core blockchain data or the ElectrumX database; this only ever tracks
    which release identity is meant to be running.
    """
    if state.last_known_good_release is not None:
        state.current_release = state.last_known_good_release
    state.pending_candidate = None
    state.failure_reason = reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state


def record_stuck(state: UpdateState, *, reason: str) -> UpdateState:
    """Health gates failed and rollbackSafe was false: neither promote nor
    roll back. current_release is left exactly as it was; an operator must
    decide. This is distinct from record_rollback on purpose.
    """
    state.failure_reason = reason
    state.update_timestamp = datetime.datetime.now(datetime.timezone.utc) \
        .replace(microsecond=0).isoformat()
    return state
