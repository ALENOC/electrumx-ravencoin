# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""1.13.10 is the first two-digit patch version in this repository.

Any string comparison would read "1.13.10" as older than "1.13.9" and the host
would refuse the release as a rollback. Ordering must stay parsed.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# The canonical module object, never a second copy loaded by path: see
# tests/test_artifact_revision_import_identity.py.
from electrumx_core_safety import artifact_revision as ar  # noqa: E402


def _record(revision=0, suffix="a"):
    return {
        "artifact_revision": revision,
        "artifactDigest": "sha256:" + suffix * 64,
        "provenanceDigest": "sha256:" + "b" * 64,
        "releaseTimestamp": "2026-08-24T00:00:00Z",
    }


def _identity(version, revision=0, suffix="a"):
    return {
        "electrumxVersion": version,
        "artifact_revision": revision,
        "artifactDigest": "sha256:" + suffix * 64,
        "provenanceDigest": "sha256:" + "b" * 64,
    }


@pytest.mark.parametrize("current,candidate", [
    ("1.13.9", "1.13.10"),
    ("1.13.9", "1.13.11"),
    ("1.13.10", "1.13.20"),
    ("1.13.10", "1.14.0"),
])
def test_two_digit_patch_is_newer_than_single_digit(current, candidate):
    decision = ar.classify_release_order(_identity(current), _identity(candidate, suffix="c"))
    assert decision.verdict is ar.EligibilityVerdict.ELIGIBLE, decision.reason


@pytest.mark.parametrize("current,candidate", [
    ("1.13.10", "1.13.9"),
    ("1.13.11", "1.13.10"),
])
def test_going_back_from_a_two_digit_patch_is_still_a_rollback(current, candidate):
    decision = ar.classify_release_order(_identity(current), _identity(candidate, suffix="c"))
    assert decision.verdict is ar.EligibilityVerdict.REFUSED_OLDER_VERSION


def test_high_water_accepts_a_two_digit_patch_over_a_single_digit_one():
    state = {
        "schemaVersion": ar.STATE_SCHEMA,
        "highestAcceptedVersion": "1.13.9",
        "releases": {"1.13.9": _record()},
    }
    ar.enforce_high_water(state, _identity("1.13.10", suffix="c"))


def test_high_water_still_refuses_a_real_downgrade_from_a_two_digit_patch():
    state = {
        "schemaVersion": ar.STATE_SCHEMA,
        "highestAcceptedVersion": "1.13.10",
        "releases": {"1.13.10": _record()},
    }
    with pytest.raises(ar.RevisionSecurityError, match="below persisted high-water"):
        ar.enforce_high_water(state, _identity("1.13.9", suffix="c"))
