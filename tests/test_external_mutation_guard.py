# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from __future__ import annotations

import pathlib
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import external_mutation_guard as guard  # noqa: E402
import update_apply  # noqa: E402


def _cp(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def _state(*, current=None, version="1.13.4", revision=0, rollback_safe=True):
    return SimpleNamespace(
        current_release=current,
        pending_candidate={
            "_eligibilityVerdict": update_apply.EligibilityVerdict.ELIGIBLE.value,
            "_verificationVerdict": update_apply.VerificationVerdict.VERIFIED.value,
            "manifest": {
                "electrumxVersion": version,
                "artifact_revision": revision,
                "rollbackSafe": rollback_safe,
            },
        },
        failure_reason=None,
    )


def _allowed(monkeypatch):
    monkeypatch.setattr(
        update_apply, "evaluate_apply",
        lambda **kwargs: SimpleNamespace(verdict=update_apply.ApplyVerdict.ALLOWED, reason=""))


def test_guard_inactive_unit_is_noop(monkeypatch):
    monkeypatch.setattr(guard.shutil, "which", lambda name: "/usr/bin/systemctl")
    calls = []

    def run(*args):
        calls.append(args)
        return _cp(3)

    monkeypatch.setattr(guard, "_systemctl", run)
    assert guard.suspend_if_active() is False
    assert calls == [("is-active", "--quiet", guard.CONTROLLER_SERVICE)]


def test_guard_stops_only_fixed_active_controller(monkeypatch):
    monkeypatch.setattr(guard.shutil, "which", lambda name: "/usr/bin/systemctl")
    calls = []
    responses = iter([_cp(0), _cp(0), _cp(3)])

    def run(*args):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(guard, "_systemctl", run)
    assert guard.suspend_if_active() is True
    assert calls == [
        ("is-active", "--quiet", guard.CONTROLLER_SERVICE),
        ("stop", guard.CONTROLLER_SERVICE),
        ("is-active", "--quiet", guard.CONTROLLER_SERVICE),
    ]


def test_guard_resumes_only_when_it_suspended(monkeypatch):
    monkeypatch.setattr(guard.shutil, "which", lambda name: "/usr/bin/systemctl")
    calls = []
    responses = iter([_cp(0), _cp(0)])

    def run(*args):
        calls.append(args)
        return next(responses)

    monkeypatch.setattr(guard, "_systemctl", run)
    guard.resume_if_suspended(False)
    assert calls == []
    guard.resume_if_suspended(True)
    assert calls == [
        ("start", guard.CONTROLLER_SERVICE),
        ("is-active", "--quiet", guard.CONTROLLER_SERVICE),
    ]


def test_guard_refuses_mutation_when_active_controller_cannot_stop(monkeypatch):
    monkeypatch.setattr(guard.shutil, "which", lambda name: "/usr/bin/systemctl")
    responses = iter([_cp(0), _cp(1, stderr="permission denied")])
    monkeypatch.setattr(guard, "_systemctl", lambda *args: next(responses))
    with pytest.raises(guard.ExternalMutationGuardError, match="permission denied"):
        guard.suspend_if_active()


def test_version_change_suspends_before_docker_hooks_and_resumes_after_health(monkeypatch):
    _allowed(monkeypatch)
    events = []
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "suspend_if_active",
        lambda: events.append("suspend") or True)
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "resume_if_suspended",
        lambda suspended: events.append(("resume", suspended)))
    monkeypatch.setattr(
        update_apply, "evaluate_health",
        lambda health, rollback_safe: SimpleNamespace(
            verdict=update_apply.HealthVerdict.PROMOTE_TO_CURRENT, reason="healthy"))
    monkeypatch.setattr(
        update_apply, "record_promotion",
        lambda state, applied_release: events.append("promotion"))

    hooks = update_apply.ApplyHooks(
        stop_services=lambda: events.append("stop"),
        switch_atomically=lambda manifest: events.append("switch"),
        start_services=lambda: events.append("start"),
        run_health_checks=lambda manifest: events.append("health") or object(),
        rollback_to=lambda previous: events.append("rollback"),
    )
    result = update_apply.apply_pending_candidate(
        _state(), hooks, approve_consensus_change=False)

    assert result.verdict == update_apply.HealthVerdict.PROMOTE_TO_CURRENT
    assert events == [
        "suspend", "stop", "switch", "start", "health",
        ("resume", True), "promotion",
    ]


def test_exact_rollback_resumes_controller_only_after_rollback(monkeypatch):
    _allowed(monkeypatch)
    events = []
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "suspend_if_active",
        lambda: events.append("suspend") or True)
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "resume_if_suspended",
        lambda suspended: events.append(("resume", suspended)))
    monkeypatch.setattr(
        update_apply, "record_rollback",
        lambda state, reason, restored_release: events.append("record-rollback"))

    def fail_start():
        events.append("start")
        raise RuntimeError("compose race")

    hooks = update_apply.ApplyHooks(
        stop_services=lambda: events.append("stop"),
        switch_atomically=lambda manifest: events.append("switch"),
        start_services=fail_start,
        run_health_checks=lambda manifest: pytest.fail("health must not run"),
        rollback_to=lambda previous: events.append("rollback"),
    )
    result = update_apply.apply_pending_candidate(
        _state(), hooks, approve_consensus_change=False)

    assert result.verdict == update_apply.HealthVerdict.ROLLBACK_TO_LAST_KNOWN_GOOD
    assert events == [
        "suspend", "stop", "switch", "start", "rollback",
        ("resume", True), "record-rollback",
    ]


def test_failed_rollback_leaves_controller_suspended(monkeypatch):
    _allowed(monkeypatch)
    events = []
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "suspend_if_active", lambda: True)
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "resume_if_suspended",
        lambda suspended: pytest.fail("stuck rollback must not restart external mutator"))
    monkeypatch.setattr(update_apply, "record_stuck", lambda state, reason: events.append(reason))

    def fail_start():
        raise RuntimeError("new stack failed")

    def fail_rollback(previous):
        raise RuntimeError("rollback failed")

    hooks = update_apply.ApplyHooks(
        stop_services=lambda: None,
        switch_atomically=lambda manifest: None,
        start_services=fail_start,
        run_health_checks=lambda manifest: object(),
        rollback_to=fail_rollback,
    )
    result = update_apply.apply_pending_candidate(
        _state(), hooks, approve_consensus_change=False)

    assert result.verdict == update_apply.HealthVerdict.STUCK_NO_BLIND_ROLLBACK
    assert "external container reconciler intentionally remains suspended" in result.detail


def test_revision_only_promotion_never_touches_external_controller(monkeypatch):
    _allowed(monkeypatch)
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "suspend_if_active",
        lambda: pytest.fail("revision-only promotion must not touch systemd"))
    monkeypatch.setattr(update_apply, "record_promotion", lambda state, applied_release: None)
    current = {
        "electrumxVersion": "1.13.4",
        "artifact_revision": 0,
    }
    hooks = update_apply.ApplyHooks(
        stop_services=lambda: pytest.fail("revision-only promotion must not stop services"),
        switch_atomically=lambda manifest: None,
        start_services=lambda: None,
        run_health_checks=lambda manifest: object(),
        rollback_to=lambda previous: None,
    )
    result = update_apply.apply_pending_candidate(
        _state(current=current, version="1.13.4", revision=1),
        hooks, approve_consensus_change=False)
    assert result.verdict == update_apply.HealthVerdict.PROMOTE_TO_CURRENT


def test_controller_suspend_failure_refuses_before_runtime_hooks(monkeypatch):
    _allowed(monkeypatch)
    monkeypatch.setattr(
        update_apply.external_mutation_guard, "suspend_if_active",
        lambda: (_ for _ in ()).throw(guard.ExternalMutationGuardError("cannot stop")))
    monkeypatch.setattr(update_apply, "record_stuck", lambda state, reason: None)
    hooks = update_apply.ApplyHooks(
        stop_services=lambda: pytest.fail("runtime mutation must not begin"),
        switch_atomically=lambda manifest: None,
        start_services=lambda: None,
        run_health_checks=lambda manifest: object(),
        rollback_to=lambda previous: None,
    )
    result = update_apply.apply_pending_candidate(
        _state(), hooks, approve_consensus_change=False)
    assert result.verdict == update_apply.HealthVerdict.STUCK_NO_BLIND_ROLLBACK
    assert "could not be suspended before mutation" in result.detail
