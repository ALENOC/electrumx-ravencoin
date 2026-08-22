import importlib.util
import json
import os
import pathlib
import sys

import pytest

MODULE = pathlib.Path(__file__).parents[1] / "core-safety/scripts/artifact_revision.py"
SPEC = importlib.util.spec_from_file_location("artifact_revision", MODULE)
ar = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ar
SPEC.loader.exec_module(ar)


def manifest(version="1.13.2", revision=0, digest="sha256:" + "a" * 64):
    return {
        "electrumxVersion": version,
        "artifact_revision": revision,
        "artifactDigest": digest,
        "releaseTimestamp": "2026-08-22T00:00:00Z",
    }


def test_newer_revision_is_accepted():
    assert ar.compare_revision(manifest(revision=1), manifest(revision=2)) == "new-revision"


def test_lower_revision_is_rollback():
    assert ar.compare_revision(manifest(revision=2), manifest(revision=1)) == "rollback-revision"


def test_equal_revision_different_digest_is_equivocation():
    assert ar.compare_revision(
        manifest(), manifest(digest="sha256:" + "b" * 64)) == "equivocation"


def test_high_water_refuses_rollback():
    state = {
        "schemaVersion": 1,
        "releases": {"1.13.2": {
            "artifact_revision": 3,
            "artifactDigest": "sha256:" + "a" * 64,
        }},
    }
    with pytest.raises(ar.RevisionSecurityError, match="below persisted"):
        ar.enforce_high_water(state, manifest(revision=2))


def test_high_water_refuses_equivocation():
    state = {
        "schemaVersion": 1,
        "releases": {"1.13.2": {
            "artifact_revision": 3,
            "artifactDigest": "sha256:" + "a" * 64,
        }},
    }
    with pytest.raises(ar.RevisionSecurityError, match="equivocation"):
        ar.enforce_high_water(
            state, manifest(revision=3, digest="sha256:" + "b" * 64))


def _make_locator(tmp_path, target, *, owner_uid=0, mode=0o644, locator_uid=0):
    locator = tmp_path / "security-state.locator"
    locator.write_text(json.dumps({
        "schemaVersion": 1,
        "ownerUid": owner_uid,
        "path": str(target),
    }) + "\n", encoding="utf-8")
    os.chmod(locator, mode)
    if os.geteuid() == 0:
        os.chown(locator, locator_uid, -1)
    return locator


def test_poisoned_locator_target_path_fails_closed_for_root(tmp_path):
    expected = tmp_path / "root-state.json"
    attacker = tmp_path / "attacker-controlled.json"
    locator = _make_locator(tmp_path, attacker)
    with pytest.raises(ar.RevisionSecurityError, match="not canonical"):
        ar.resolve_host_high_water_path(
            euid=0,
            env={"HOME": "/root"},
            locator_path=locator,
            root_state_path=expected,
            provision_root_locator=False,
        )


@pytest.mark.skipif(os.geteuid() != 0, reason="ownership regression requires root test runner")
def test_wrong_owner_locator_fails_closed_for_root(tmp_path):
    expected = tmp_path / "root-state.json"
    locator = _make_locator(tmp_path, expected, locator_uid=65534)
    with pytest.raises(ar.RevisionSecurityError, match="owner uid"):
        ar.resolve_host_high_water_path(
            euid=0,
            env={"HOME": "/root"},
            locator_path=locator,
            root_state_path=expected,
            provision_root_locator=False,
        )


@pytest.mark.skipif(os.geteuid() != 0, reason="ownership regression requires root test runner")
def test_wrong_owner_target_fails_closed_for_root(tmp_path):
    expected = tmp_path / "root-state.json"
    expected.write_text("{}\n", encoding="utf-8")
    os.chmod(expected, 0o600)
    os.chown(expected, 65534, -1)
    locator = _make_locator(tmp_path, expected)
    with pytest.raises(ar.RevisionSecurityError, match="owner uid"):
        ar.resolve_host_high_water_path(
            euid=0,
            env={"HOME": "/root"},
            locator_path=locator,
            root_state_path=expected,
            provision_root_locator=False,
        )


def test_wrong_target_mode_fails_closed(tmp_path):
    expected = tmp_path / "root-state.json"
    expected.write_text("{}\n", encoding="utf-8")
    os.chmod(expected, 0o644)
    locator = _make_locator(tmp_path, expected)
    with pytest.raises(ar.RevisionSecurityError, match="mode 0644"):
        ar.resolve_host_high_water_path(
            euid=0,
            env={"HOME": "/root"},
            locator_path=locator,
            root_state_path=expected,
            provision_root_locator=False,
        )


def test_unprivileged_caller_never_creates_locator(tmp_path):
    locator = tmp_path / "missing.locator"
    with pytest.raises(ar.RevisionSecurityError, match="root-owned.*missing"):
        ar.resolve_host_high_water_path(
            euid=1000,
            env={"HOME": "/home/alice"},
            locator_path=locator,
            root_state_path=tmp_path / "root.json",
        )


def test_unprivileged_namespace_is_canonical_and_unique(tmp_path):
    state_home = tmp_path / "state-home"
    expected = state_home / "electrumx-ravencoin/security-state.json"
    locator = _make_locator(tmp_path, expected, owner_uid=1000)
    actual = ar.resolve_host_high_water_path(
        euid=1000,
        env={"HOME": "/home/alice", "XDG_STATE_HOME": str(state_home)},
        locator_path=locator,
        root_state_path=tmp_path / "root.json",
        provision_root_locator=False,
    )
    assert actual == expected


def test_root_refuses_user_namespace_even_with_root_owned_locator(tmp_path):
    user_target = tmp_path / "user-state.json"
    locator = _make_locator(tmp_path, user_target, owner_uid=1000)
    with pytest.raises(ar.RevisionSecurityError, match="belongs to uid 1000"):
        ar.resolve_host_high_water_path(
            euid=0,
            env={"HOME": "/root"},
            locator_path=locator,
            root_state_path=tmp_path / "root.json",
            provision_root_locator=False,
        )
