import importlib.util
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).parents[1]
SCRIPTS = ROOT / "core-safety/scripts"
sys.path.insert(0, str(SCRIPTS))

MODULE = SCRIPTS / "artifact_revision.py"
SPEC = importlib.util.spec_from_file_location("artifact_revision", MODULE)
ar = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ar
SPEC.loader.exec_module(ar)

import update_decision as ud  # noqa: E402


def manifest(version="1.13.3", revision=0,
             digest="sha256:" + "a" * 64,
             provenance="sha256:" + "b" * 64):
    return {
        "electrumxVersion": version,
        "artifact_revision": revision,
        "artifactDigest": digest,
        "provenanceDigest": provenance,
        "releaseTimestamp": "2026-08-22T00:00:00Z",
    }


def host_from(value):
    return ud.HostFacts(
        architecture="linux/amd64",
        installed_updater_version="2.0.0",
        current_electrumx_version=value.get("electrumxVersion"),
        current_artifact_revision=value.get("artifact_revision"),
        current_artifact_digest=value.get("artifactDigest"),
        current_provenance_digest=value.get("provenanceDigest"),
    )


def eligibility(current, candidate):
    return ud.evaluate_eligibility(
        auto_update_mode="stable",
        channel="stable",
        host=host_from(current),
        candidate_version=candidate.get("electrumxVersion"),
        candidate_is_prerelease=False,
        candidate_revision=candidate.get("artifact_revision"),
        candidate_artifact_digest=candidate.get("artifactDigest"),
        candidate_provenance_digest=candidate.get("provenanceDigest"),
    )


@pytest.mark.parametrize("current,candidate,expected", [
    (manifest(version="1.13.3"), manifest(version="1.13.2"),
     ar.EligibilityVerdict.REFUSED_OLDER_VERSION),
    (manifest(version="1.13.3"), manifest(version="1.13.4"),
     ar.EligibilityVerdict.ELIGIBLE),
    ({**manifest(), "artifact_revision": None}, manifest(),
     ar.EligibilityVerdict.REFUSED_MISSING_REVISION_DATA),
    (manifest(), {**manifest(), "artifact_revision": None},
     ar.EligibilityVerdict.REFUSED_MISSING_REVISION_DATA),
    ({**manifest(), "artifact_revision": "0"}, manifest(),
     ar.EligibilityVerdict.REFUSED_MALFORMED_REVISION_DATA),
    (manifest(), {**manifest(), "artifact_revision": True},
     ar.EligibilityVerdict.REFUSED_MALFORMED_REVISION_DATA),
    (manifest(revision=2), manifest(revision=1),
     ar.EligibilityVerdict.REFUSED_OLDER_REVISION),
    (manifest(revision=1), manifest(revision=2),
     ar.EligibilityVerdict.ELIGIBLE),
    ({**manifest(), "artifactDigest": None}, manifest(),
     ar.EligibilityVerdict.REFUSED_MISSING_DIGEST_DATA),
    (manifest(), {**manifest(), "artifactDigest": None},
     ar.EligibilityVerdict.REFUSED_MISSING_DIGEST_DATA),
    ({**manifest(), "artifactDigest": "bad"}, manifest(),
     ar.EligibilityVerdict.REFUSED_MALFORMED_DIGEST_DATA),
    (manifest(), {**manifest(), "provenanceDigest": None},
     ar.EligibilityVerdict.REFUSED_MISSING_DIGEST_DATA),
    (manifest(), manifest(digest="sha256:" + "c" * 64),
     ar.EligibilityVerdict.REFUSED_ARTIFACT_EQUIVOCATION),
    (manifest(), manifest(provenance="sha256:" + "c" * 64),
     ar.EligibilityVerdict.REFUSED_ARTIFACT_EQUIVOCATION),
    (manifest(), manifest(), ar.EligibilityVerdict.IGNORED_SAME_ARTIFACT),
    ({**manifest(), "electrumxVersion": "not a version"}, manifest(),
     ar.EligibilityVerdict.REFUSED_MALFORMED_VERSION_DATA),
])
def test_ordering_entry_points_agree_on_full_matrix(current, candidate, expected):
    direct = ar.compare_revision(current, candidate)
    via_eligibility = eligibility(current, candidate)
    assert direct.verdict is expected
    assert via_eligibility.verdict is direct.verdict
    assert via_eligibility.reason == direct.reason


def high_water(version="1.13.3", revision=3,
               digest="sha256:" + "a" * 64,
               provenance="sha256:" + "b" * 64):
    return {
        "schemaVersion": ar.STATE_SCHEMA,
        "highestAcceptedVersion": version,
        "releases": {version: {
            "artifact_revision": revision,
            "artifactDigest": digest,
            "provenanceDigest": provenance,
            "releaseTimestamp": "2026-08-22T00:00:00Z",
        }},
    }


def test_high_water_refuses_revision_rollback():
    with pytest.raises(ar.RevisionSecurityError, match="REFUSED_OLDER_REVISION"):
        ar.enforce_high_water(high_water(), manifest(revision=2))


def test_high_water_refuses_equivocation():
    with pytest.raises(ar.RevisionSecurityError, match="different artifact or provenance"):
        ar.enforce_high_water(
            high_water(), manifest(revision=3, digest="sha256:" + "c" * 64))


def test_high_water_refuses_version_rollback_independent_of_hostfacts():
    state = high_water(version="1.14.0", revision=0)
    with pytest.raises(ar.RevisionSecurityError, match="version is below persisted"):
        ar.enforce_high_water(state, manifest(version="1.13.9", revision=99))


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


def _force_root_owned_fstat(monkeypatch):
    real_fstat = ar.os.fstat

    def root_owned(fd):
        info = real_fstat(fd)
        values = list(info)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(ar.os, "fstat", root_owned)


def test_poisoned_locator_target_path_fails_closed_for_root(monkeypatch, tmp_path):
    _force_root_owned_fstat(monkeypatch)
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
    expected.write_text(json.dumps(high_water()) + "\n", encoding="utf-8")
    os.chmod(expected, 0o600)
    os.chown(expected, 65534, -1)
    with pytest.raises(ar.RevisionSecurityError, match="owner uid"):
        ar.load_high_water(expected)


def test_wrong_target_mode_fails_closed(tmp_path):
    expected = tmp_path / "root-state.json"
    expected.write_text(json.dumps(high_water()) + "\n", encoding="utf-8")
    os.chmod(expected, 0o644)
    with pytest.raises(ar.RevisionSecurityError, match="mode 0644"):
        ar.load_high_water(expected)


def test_unprivileged_caller_never_creates_locator(tmp_path):
    locator = tmp_path / "missing.locator"
    with pytest.raises(ar.RevisionSecurityError, match="root-owned.*missing"):
        ar.resolve_host_high_water_path(
            euid=1000,
            env={"HOME": "/home/alice"},
            locator_path=locator,
            root_state_path=tmp_path / "root.json",
        )


def test_unprivileged_namespace_is_canonical_and_unique(monkeypatch, tmp_path):
    _force_root_owned_fstat(monkeypatch)
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


def test_root_refuses_user_namespace_even_with_root_owned_locator(monkeypatch, tmp_path):
    _force_root_owned_fstat(monkeypatch)
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


def test_locator_swap_after_open_does_not_change_bytes_read(monkeypatch, tmp_path):
    _force_root_owned_fstat(monkeypatch)
    target = tmp_path / "safe-state.json"
    attacker = tmp_path / "attacker-state.json"
    locator = _make_locator(tmp_path, target)
    replacement = tmp_path / "replacement.locator"
    replacement.write_text(json.dumps({
        "schemaVersion": 1,
        "ownerUid": 0,
        "path": str(attacker),
    }) + "\n", encoding="utf-8")
    os.chmod(replacement, 0o644)

    real_open = ar.os.open
    swapped = False

    def open_then_swap(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if pathlib.Path(path) == locator and not swapped:
            os.replace(replacement, locator)
            swapped = True
        return fd

    monkeypatch.setattr(ar.os, "open", open_then_swap)
    loaded = ar._read_locator(locator)
    assert loaded["path"] == target
    assert json.loads(locator.read_text())["path"] == str(attacker)


def test_high_water_swap_after_open_does_not_change_bytes_read(monkeypatch, tmp_path):
    state_path = tmp_path / "security-state.json"
    original = high_water(version="1.14.0")
    attacker = high_water(version="1.0.0")
    state_path.write_text(json.dumps(original) + "\n", encoding="utf-8")
    os.chmod(state_path, 0o600)
    replacement = tmp_path / "replacement.json"
    replacement.write_text(json.dumps(attacker) + "\n", encoding="utf-8")
    os.chmod(replacement, 0o600)

    real_open = ar.os.open
    swapped = False

    def open_then_swap(path, flags, *args, **kwargs):
        nonlocal swapped
        fd = real_open(path, flags, *args, **kwargs)
        if pathlib.Path(path) == state_path and not swapped:
            os.replace(replacement, state_path)
            swapped = True
        return fd

    monkeypatch.setattr(ar.os, "open", open_then_swap)
    loaded = ar.load_high_water(state_path)
    assert loaded["highestAcceptedVersion"] == "1.14.0"
    assert json.loads(state_path.read_text())["highestAcceptedVersion"] == "1.0.0"
