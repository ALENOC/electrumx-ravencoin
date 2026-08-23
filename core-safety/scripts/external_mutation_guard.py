# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Coordinate the updater with host-side services that can recreate containers.

The Ravencoin Node Monitor bandwidth controller intentionally runs outside the
ElectrumX Compose project and may reconcile ``MAX_SESSIONS`` by issuing its own
``docker compose up`` for the ElectrumX service.  During an atomic release
switch that behavior races the updater's own Compose transaction and can remove
or replace a container while Docker Compose is still waiting on it.

This module narrowly coordinates that one known systemd unit.  If it is active,
the updater stops it before any runtime mutation and restores it only after a
successful promotion or an exact rollback.  Missing/inactive units are a no-op.
No arbitrary unit name or command is accepted from configuration or state.
"""

from __future__ import annotations

import shutil
import subprocess

CONTROLLER_SERVICE = "ravencoin-bandwidth-controller.service"


class ExternalMutationGuardError(RuntimeError):
    """The known external mutator could not be safely suspended/restored."""


def _systemctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", *args], check=False, capture_output=True, text=True,
        timeout=60)


def controller_is_active() -> bool:
    """Return whether the known host controller is currently active."""
    if shutil.which("systemctl") is None:
        return False
    try:
        completed = _systemctl("is-active", "--quiet", CONTROLLER_SERVICE)
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def suspend_if_active() -> bool:
    """Stop the known controller if active and prove it is no longer active.

    Returns ``True`` only when this invocation actually suspended the unit.
    An inactive/missing unit is deliberately a no-op.  Failure to stop an
    active unit fails closed before the updater mutates the running stack.
    """
    if not controller_is_active():
        return False
    try:
        stopped = _systemctl("stop", CONTROLLER_SERVICE)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalMutationGuardError(
            f"cannot stop {CONTROLLER_SERVICE}: {exc}") from exc
    if stopped.returncode != 0:
        detail = (stopped.stderr or stopped.stdout).strip()
        raise ExternalMutationGuardError(
            f"cannot stop {CONTROLLER_SERVICE}: {detail or 'systemctl failed'}")
    if controller_is_active():
        raise ExternalMutationGuardError(
            f"{CONTROLLER_SERVICE} remained active after stop")
    print(
        "UPDATER_CHECKPOINT external-mutator-suspend=PASS "
        f"service={CONTROLLER_SERVICE}")
    return True


def resume_if_suspended(suspended: bool) -> None:
    """Restore the controller only when this updater suspended it."""
    if not suspended:
        return
    try:
        started = _systemctl("start", CONTROLLER_SERVICE)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ExternalMutationGuardError(
            f"cannot restart {CONTROLLER_SERVICE}: {exc}") from exc
    if started.returncode != 0:
        detail = (started.stderr or started.stdout).strip()
        raise ExternalMutationGuardError(
            f"cannot restart {CONTROLLER_SERVICE}: {detail or 'systemctl failed'}")
    if not controller_is_active():
        raise ExternalMutationGuardError(
            f"{CONTROLLER_SERVICE} did not become active after start")
    print(
        "UPDATER_CHECKPOINT external-mutator-resume=PASS "
        f"service={CONTROLLER_SERVICE}")
