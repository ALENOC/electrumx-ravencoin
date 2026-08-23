# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for the adopted named-volume storage model at runtime.

Hardware qualification of the normal 1.13.4 -> 1.13.5 update on a Raspberry Pi
5 failed on a node adopted from legacy 1.13.1.  Its marker carries::

    "storageMode": "named-volumes"

Preflight passed, the release switch completed, and then the update failed
with::

    UpdateRuntimeError: existing installer volume
    electrumx-ravencoin_ravencoin-data is not local bind-backed storage

and the automatic rollback failed with the identical message, leaving the node
stopped in HealthVerdict.STUCK_NO_BLIND_ROLLBACK.

Cause: ``prove_running_storage_continuity`` branches on the marker, but
``start_services``, ``run_health_checks`` and ``rollback_to`` called the
bind-backed primitive ``_docker_volume_bindings`` directly.  The legacy
wrapper hid this because it monkeypatched that primitive in-process; the
normal CLI never installs those hooks.

These tests drive the real post-switch code paths against a fake ``docker``
that returns what a plain Docker named volume actually returns.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_runtime as runtime  # noqa: E402

PROJECT = runtime.COMPOSE_PROJECT_NAME
DATA_VOLUMES = ("ravencoin-data", "ravencoin-config", "electrumx-data")
SECRET_VOLUME = f"{PROJECT}_rpc-secrets"

# The real marker read off the qualification node.
PI_MARKER = {
    "bootstrapChoice": "p2p",
    "electrumxVersion": "1.13.4",
    "legacyAdoption": {
        "chainstrapRerunAllowed": False,
        "fromVersion": "1.13.1",
        "source": "setup.sh",
        "storageMode": "named-volumes",
    },
    "monitorControllerEnabled": False,
    "nodeMonitorEnabled": False,
    "schemaVersion": 1,
    "storageMode": "named-volumes",
}


def _completed(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(
        args=["docker"], returncode=returncode, stdout=stdout, stderr=stderr)


class FakeDocker:
    """Answers exactly like Docker does for a plain named-volume install."""

    def __init__(self, *, missing=(), bind_backed=(), compose_volumes=None,
                 attached=True):
        self.calls: list[list[str]] = []
        self.missing = set(missing)
        self.bind_backed = set(bind_backed)
        self.attached = attached
        self.compose_volumes = compose_volumes
        self.compose_file_sets: list[list[str]] = []

    def _volume_inspect(self, name):
        if name in self.missing:
            return _completed(returncode=1, stderr=f"no such volume: {name}")
        if name in self.bind_backed:
            return _completed(json.dumps([{
                "Name": name,
                "Driver": "local",
                "Options": {"type": "none", "o": "bind",
                            "device": f"/srv/{name}"},
            }]))
        # A plain Docker-managed named volume reports no driver options at all.
        return _completed(json.dumps([{
            "Name": name,
            "Driver": "local",
            "Options": None,
            "Mountpoint": f"/var/lib/docker/volumes/{name}/_data",
        }]))

    def _rendered_model(self):
        if self.compose_volumes is not None:
            return self.compose_volumes
        return {logical: {} for logical in DATA_VOLUMES}

    def _mounts(self, service):
        if not self.attached:
            return []
        wanted = runtime.ACTIVE_STORAGE_MOUNTS[service]
        return [
            {"Type": "volume", "Name": f"{PROJECT}_{logical}",
             "Destination": destination}
            for logical, destination in wanted.items()
        ]

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append(argv)
        if argv[:3] == ["docker", "volume", "inspect"]:
            return self._volume_inspect(argv[3])
        if argv[:2] == ["docker", "volume"] and argv[2] in ("rm", "create", "prune"):
            raise AssertionError(f"updater must never mutate volumes: {argv}")
        if argv[:2] == ["docker", "compose"]:
            selected = [argv[i + 1] for i, item in enumerate(argv) if item == "-f"]
            rest = [item for item in argv[2:]
                    if item not in selected and item not in ("-f", "-p", PROJECT)]
            self.compose_file_sets.append(selected)
            if rest[:1] == ["config"]:
                return _completed(json.dumps({"volumes": self._rendered_model()}))
            if rest[:1] == ["ps"]:
                service = rest[-1]
                return _completed(f"cid-{service}")
            return _completed()
        if argv[:2] == ["docker", "inspect"]:
            if "{{json .Mounts}}" in argv:
                service = argv[-1].removeprefix("cid-")
                return _completed(json.dumps(self._mounts(service)))
            return _completed("")
        return _completed()


@pytest.fixture()
def fake_docker(monkeypatch):
    fake = FakeDocker()
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    return fake


def _release_root(base: pathlib.Path, name: str, *, compose_file: str) -> pathlib.Path:
    root = base / name
    root.mkdir(parents=True)
    for filename in sorted(runtime.RELEASE_COMPOSE_FILES):
        shutil.copy(ROOT / filename, root / filename)
    (root / ".env").write_text(f"COMPOSE_FILE={compose_file}\n", encoding="utf-8")
    (root / ".env").chmod(0o600)
    (root / runtime.INSTALL_MARKER).parent.mkdir(parents=True, exist_ok=True)
    (root / runtime.INSTALL_MARKER).write_text(
        json.dumps(PI_MARKER), encoding="utf-8")
    return root


def _transaction(root: pathlib.Path, *, marker=None, switched=False,
                 backup=None) -> runtime.TransactionalComposeSwitch:
    """A transaction positioned exactly as it is after release-switch=PASS."""
    marker = dict(marker or PI_MARKER)
    transaction = object.__new__(runtime.TransactionalComposeSwitch)
    transaction.root = root
    transaction.parent = root.parent
    transaction.marker = marker
    transaction.storage_mode = runtime.storage_mode_of(marker)
    transaction.storage_bindings = runtime._expected_named_volume_storage()
    transaction.old_files = runtime.update_compose_files(marker, root)
    transaction.manifest = {"electrumxVersion": "1.13.5", "coreVersion": "4.8.0"}
    transaction.health_timeout = 0
    transaction.staging = None
    transaction.backup = backup
    transaction.failed = None
    transaction.switched = switched
    transaction.journal = root.parent / f".{root.name}.update-transaction.json"
    return transaction


# --- 1. the marker alone selects the named-volume model -------------------

def test_named_volume_marker_selects_named_volume_storage_mode():
    assert runtime.storage_mode_of(PI_MARKER) == \
        runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE
    assert runtime.storage_mode_of({"storageMode": "bind"}) == \
        runtime.BIND_BACKED_STORAGE_MODE
    assert runtime.storage_mode_of({}) == runtime.BIND_BACKED_STORAGE_MODE


def test_named_volume_mode_never_runs_bind_backed_volume_validation(monkeypatch):
    def explode(expected):
        raise AssertionError("bind-backed validation reached in named-volume mode")

    monkeypatch.setattr(runtime, "_docker_volume_bindings", explode)
    fake = FakeDocker()
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    runtime.prove_storage_volume_objects(
        runtime._expected_named_volume_storage(),
        storage_mode=runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE)
    inspected = {call[3] for call in fake.calls if call[:3] == ["docker", "volume", "inspect"]}
    assert inspected == {f"{PROJECT}_{logical}" for logical in DATA_VOLUMES}


# --- 2. start_services after the release switch ---------------------------

def test_start_services_accepts_the_exact_existing_named_volume_model(
        tmp_path, fake_docker):
    """The exact hardware failure: this raised the bind-backed error."""
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root)

    transaction.start_services()

    inspected = [call[3] for call in fake_docker.calls
                 if call[:3] == ["docker", "volume", "inspect"]]
    assert sorted(inspected) == sorted(
        f"{PROJECT}_{logical}" for logical in DATA_VOLUMES)
    assert any(call[-3:] == ["up", "-d", "--no-build"] for call in fake_docker.calls)


def test_start_services_still_fails_closed_on_a_missing_named_volume(
        tmp_path, monkeypatch):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    fake = FakeDocker(missing={f"{PROJECT}_electrumx-data"})
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    transaction = _transaction(root)

    with pytest.raises(runtime.UpdateRuntimeError, match="is missing"):
        transaction.start_services()
    assert not any(call[-3:] == ["up", "-d", "--no-build"] for call in fake.calls)


def test_start_services_rejects_a_named_volume_that_became_bind_backed(
        tmp_path, monkeypatch):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    fake = FakeDocker(bind_backed={f"{PROJECT}_ravencoin-data"})
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    transaction = _transaction(root)

    with pytest.raises(runtime.UpdateRuntimeError,
                       match="is not a plain local named volume"):
        transaction.start_services()


def test_named_volume_identity_must_be_project_bound():
    with pytest.raises(runtime.UpdateRuntimeError, match="identity changed"):
        runtime.prove_storage_volume_objects(
            {"ravencoin-data": "somebody-elses_ravencoin-data"},
            storage_mode=runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE)


# --- 3. health gate after promotion ---------------------------------------

def test_health_gate_storage_proof_accepts_the_named_volume_model(
        tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root, switched=True)

    # The gate itself fails (the fake serves no chain data); what must not
    # happen is a storage refusal on a valid named-volume installation.
    result = transaction.run_health_checks(transaction.manifest)

    assert not result.all_pass()
    inspected = [call[3] for call in fake_docker.calls
                 if call[:3] == ["docker", "volume", "inspect"]]
    assert sorted(inspected) == sorted(
        f"{PROJECT}_{logical}" for logical in DATA_VOLUMES)


def test_health_gate_still_refuses_a_detached_named_volume(tmp_path, monkeypatch):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    fake = FakeDocker(attached=False)
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    transaction = _transaction(root, switched=True)

    with pytest.raises(runtime.UpdateRuntimeError,
                       match="not attached to expected installer volume"):
        transaction.run_health_checks(transaction.manifest)


# --- 4. rollback ----------------------------------------------------------

def test_rollback_restores_the_named_volume_installation(tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    backup = _release_root(tmp_path, ".electrumx-ravencoin.last-known-good",
                           compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root, switched=True, backup=backup)

    transaction.rollback_to(None)

    assert not backup.exists()
    assert (root / ".env").read_text(encoding="utf-8").strip() == \
        "COMPOSE_FILE=compose.yaml:compose.tls.yaml"
    assert any(call[-3:] == ["up", "-d", "--no-build"] for call in fake_docker.calls)
    assert not any(call[:3] == ["docker", "volume", "rm"] for call in fake_docker.calls)


def test_rollback_does_not_require_the_storage_overlay(tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    (root / runtime.STORAGE_OVERLAY).unlink()
    backup = _release_root(tmp_path, ".electrumx-ravencoin.last-known-good",
                           compose_file="compose.yaml:compose.tls.yaml")
    (backup / runtime.STORAGE_OVERLAY).unlink()
    transaction = _transaction(root, switched=True, backup=backup)

    transaction.rollback_to(None)

    for selected in fake_docker.compose_file_sets:
        assert runtime.STORAGE_OVERLAY not in selected


def test_rollback_fails_closed_when_a_named_volume_disappeared(
        tmp_path, monkeypatch):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    backup = _release_root(tmp_path, ".electrumx-ravencoin.last-known-good",
                           compose_file="compose.yaml:compose.tls.yaml")
    fake = FakeDocker(missing={f"{PROJECT}_ravencoin-config"})
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    transaction = _transaction(root, switched=True, backup=backup)

    with pytest.raises(runtime.UpdateRuntimeError, match="is missing"):
        transaction.rollback_to(None)


# --- 5. Compose overlay selection ----------------------------------------

def test_tls_overlay_survives_promotion_and_rollback(tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    backup = _release_root(tmp_path, ".electrumx-ravencoin.last-known-good",
                           compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root, switched=True, backup=backup)
    assert transaction.old_files == [runtime.BASE_COMPOSE, runtime.TLS_OVERLAY]

    transaction.start_services()
    transaction.rollback_to(None)

    assert fake_docker.compose_file_sets
    for selected in fake_docker.compose_file_sets:
        assert selected == [runtime.BASE_COMPOSE, runtime.TLS_OVERLAY]


# --- 6. ChainStrap stays excluded ----------------------------------------

def test_named_volume_update_never_reactivates_chainstrap(tmp_path, fake_docker):
    root = _release_root(
        tmp_path, "electrumx-ravencoin",
        compose_file="compose.yaml:compose.tls.yaml:compose.chainstrap.yaml")
    transaction = _transaction(root)

    transaction.start_services()

    assert runtime.CHAINSTRAP_OVERLAY not in transaction.old_files
    for selected in fake_docker.compose_file_sets:
        assert runtime.CHAINSTRAP_OVERLAY not in selected


# --- 7. an unexpected storage overlay in named-volume mode still fails ----

def test_named_volume_mode_refuses_a_selected_storage_overlay(tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    with pytest.raises(runtime.UpdateRuntimeError,
                       match="unexpectedly selected compose.storage.yaml"):
        runtime.prove_candidate_storage_continuity(
            root, [runtime.BASE_COMPOSE, runtime.STORAGE_OVERLAY],
            runtime._expected_named_volume_storage(),
            storage_mode=runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE)


def test_named_volume_mode_refuses_bind_driver_opts_in_the_candidate_model(
        tmp_path, monkeypatch):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    fake = FakeDocker(compose_volumes={
        "ravencoin-data": {"driver_opts": {"type": "none", "o": "bind",
                                           "device": "/srv/data"}},
        "ravencoin-config": {},
        "electrumx-data": {},
    })
    monkeypatch.setattr(runtime.subprocess, "run", fake)
    with pytest.raises(runtime.UpdateRuntimeError,
                       match="unexpectedly has driver_opts"):
        runtime.prove_candidate_storage_continuity(
            root, [runtime.BASE_COMPOSE, runtime.TLS_OVERLAY],
            runtime._expected_named_volume_storage(),
            storage_mode=runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE)


# --- 8. secret volumes are never touched ---------------------------------

def test_updater_never_touches_or_converts_the_secret_volume(tmp_path, fake_docker):
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    backup = _release_root(tmp_path, ".electrumx-ravencoin.last-known-good",
                           compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root, switched=True, backup=backup)

    transaction.start_services()
    transaction.rollback_to(None)

    for call in fake_docker.calls:
        assert call[:3] != ["docker", "volume", "rm"]
        assert call[:3] != ["docker", "volume", "create"]
        assert SECRET_VOLUME not in call


# --- 9. marker continuity ------------------------------------------------

def test_promotion_keeps_storage_mode_named_volumes_in_the_marker(tmp_path):
    """Only the release version may change; storageMode is permanent state."""
    marker = dict(PI_MARKER)
    marker["electrumxVersion"] = "1.13.5"
    assert runtime.storage_mode_of(marker) == \
        runtime.LEGACY_NAMED_VOLUME_STORAGE_MODE
    assert runtime.update_compose_files.__module__ == "update_runtime"
    assert runtime._required_update_compose_files(marker) == [runtime.BASE_COMPOSE]


# --- 10. architectural guards --------------------------------------------

def test_bind_backed_primitive_has_no_callers_outside_the_storage_proofs():
    """Encode the bug class: only the two storage proofs may call it."""
    source = (SCRIPTS / "update_runtime.py").read_text(encoding="utf-8")
    callers = [
        line.strip() for line in source.splitlines()
        if "_docker_volume_bindings(" in line and not line.lstrip().startswith("def ")
    ]
    assert callers == ["_docker_volume_bindings(expected)"] * 2, callers


def test_normal_updater_sources_contain_no_legacy_adoption_consent():
    forbidden = ("ADOPT LEGACY", "CONSENT_TEXT", "yes-adopt-legacy")
    for name in ("update_runtime.py", "update_apply.py", "electrumx_update_cli.py"):
        source = (SCRIPTS / name).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{name} references {token!r}"
        # A prose reference in a comment is fine; an import is not.
        for line in source.splitlines():
            stripped = line.strip()
            assert not (stripped.startswith(("import ", "from "))
                        and "legacy_1_13_1_apply" in stripped), \
                f"{name} imports the legacy wrapper"


def test_normal_updater_import_graph_never_reaches_the_legacy_wrapper():
    completed = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.path.insert(0, %r);"
         "import electrumx_update_cli;"
         "print('legacy_1_13_1_apply' in sys.modules)" % str(SCRIPTS)],
        capture_output=True, text=True, check=True)
    assert completed.stdout.strip() == "False"


def test_named_volume_runtime_does_not_depend_on_legacy_hooks(tmp_path, fake_docker):
    """The native updater must work with no legacy module imported at all."""
    assert "legacy_1_13_1_apply" not in sys.modules or \
        runtime.prove_running_storage_continuity.__module__ == "update_runtime"
    root = _release_root(tmp_path, "electrumx-ravencoin",
                         compose_file="compose.yaml:compose.tls.yaml")
    transaction = _transaction(root)
    transaction.start_services()
