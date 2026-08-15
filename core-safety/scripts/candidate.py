# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Candidate identity and lifecycle states for Ravencoin Core safety certification.

A candidate is identified by repository plus commit.  A version number is
metadata: it is displayed and it classifies known-unsafe generations, but it
never grants trust.  Two repositories publishing the same version are two
different candidates and each must be certified separately.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Optional

#: Repositories a candidate may be *discovered* from.  Presence here grants the
#: right to be tested, never the right to be trusted.  Both entries are treated
#: identically by every later stage.
ALLOWED_SOURCE_REPOSITORIES = (
    "2miners/Ravencoin",
    "RavenProject/Ravencoin",
)

#: Generations known to be unsafe for mainnet regardless of any other evidence.
#: These predate the August 2026 nHeight binding fix.
KNOWN_UNSAFE_VERSIONS = (
    "4.6.0",
    "4.6.1",
    "4.6.1.1",
    "4.7.0",
)

COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
VERSION_PATTERN = re.compile(r"^\d+(\.\d+){1,3}$")
TAG_PATTERN = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")


class CandidateState(str, Enum):
    """Lifecycle of a discovered release candidate."""

    DISCOVERED = "DISCOVERED"
    PROVENANCE_VALIDATED = "PROVENANCE_VALIDATED"
    PROVENANCE_FAILED = "PROVENANCE_FAILED"
    BUILD_PASSED = "BUILD_PASSED"
    BUILD_FAILED = "BUILD_FAILED"
    CERTIFICATION_PASSED = "CERTIFICATION_PASSED"
    CERTIFICATION_FAILED = "CERTIFICATION_FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    KNOWN_SAFE = "KNOWN_SAFE"
    KNOWN_UNSAFE = "KNOWN_UNSAFE"
    REVOKED = "REVOKED"


class TestResult(str, Enum):
    """Outcome of one profile test.  Only PASS is ever good news."""

    __test__ = False  # not a pytest test class despite the name

    PASS = "PASS"
    FAIL = "FAIL"
    UNAVAILABLE = "UNAVAILABLE"
    ERROR = "ERROR"
    SKIPPED = "SKIPPED"


#: Results that must never be interpreted as a pass, and must never silently
#: disappear from an aggregate.
NON_PASSING_RESULTS = (
    TestResult.FAIL,
    TestResult.UNAVAILABLE,
    TestResult.ERROR,
    TestResult.SKIPPED,
)


class CandidateError(ValueError):
    """A candidate record is structurally invalid or not from an allowed source."""


@dataclass(frozen=True)
class Candidate:
    """An exact Ravencoin Core release candidate."""

    repository: str
    tag: str
    commit: str
    version: str
    tag_object: Optional[str] = None
    tag_verified: bool = False
    commit_verified: bool = False
    published_at: Optional[str] = None
    release_url: Optional[str] = None
    artifact_name: Optional[str] = None
    artifact_sha256: Optional[str] = None
    notes: tuple = field(default_factory=tuple)

    def __post_init__(self):
        if self.repository not in ALLOWED_SOURCE_REPOSITORIES:
            raise CandidateError(
                f"repository {self.repository!r} is not in the source allowlist"
            )
        if not COMMIT_PATTERN.match(self.commit or ""):
            raise CandidateError(f"commit {self.commit!r} is not a full 40 hex sha")
        if not TAG_PATTERN.match(self.tag or ""):
            raise CandidateError(f"tag {self.tag!r} is malformed")
        if not VERSION_PATTERN.match(self.version or ""):
            raise CandidateError(f"version {self.version!r} is malformed")
        if self.tag_object is not None and not COMMIT_PATTERN.match(self.tag_object):
            raise CandidateError("tag object sha is malformed")
        if self.artifact_sha256 is not None and not re.fullmatch(
                r"[0-9a-f]{64}", self.artifact_sha256):
            raise CandidateError("artifact sha256 is malformed")

    @property
    def identity(self) -> str:
        """The certification key.  Repository plus commit, never the version."""
        return f"{self.repository}@{self.commit}"

    @property
    def identity_digest(self) -> str:
        return hashlib.sha256(self.identity.encode()).hexdigest()

    @property
    def is_known_unsafe_version(self) -> bool:
        return self.version in KNOWN_UNSAFE_VERSIONS

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["notes"] = list(self.notes)
        payload["identity"] = self.identity
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "Candidate":
        allowed = {
            "repository", "tag", "commit", "version", "tag_object", "tag_verified",
            "commit_verified", "published_at", "release_url", "artifact_name",
            "artifact_sha256", "notes",
        }
        unknown = set(payload) - allowed - {"identity"}
        if unknown:
            raise CandidateError(f"unknown candidate fields: {sorted(unknown)}")
        data = {key: payload[key] for key in allowed if key in payload}
        if "notes" in data:
            data["notes"] = tuple(data["notes"])
        return cls(**data)


def aggregate_state(results: dict, *, required_ids, triggers_flagged=False) -> CandidateState:
    """Reduce individual test results to a candidate state.

    Fails closed in every direction: a missing required result is as bad as a
    failing one, an error never becomes a pass, and a flagged review trigger
    outranks a clean sweep of passes.
    """
    missing = [test_id for test_id in required_ids if test_id not in results]
    if any(results.get(test_id) is TestResult.FAIL for test_id in required_ids):
        return CandidateState.CERTIFICATION_FAILED
    if missing:
        return CandidateState.REVIEW_REQUIRED
    if any(results[test_id] in NON_PASSING_RESULTS for test_id in required_ids):
        return CandidateState.REVIEW_REQUIRED
    if triggers_flagged:
        return CandidateState.REVIEW_REQUIRED
    return CandidateState.CERTIFICATION_PASSED


def load_profile(path) -> dict:
    """Read a safety profile and check the parts other code relies on."""
    with open(path, "r", encoding="utf-8") as handle:
        profile = json.load(handle)
    for key in ("profileId", "tests", "candidateSources", "promotion"):
        if key not in profile:
            raise CandidateError(f"safety profile is missing {key!r}")
    allowed = tuple(profile["candidateSources"]["allowed"])
    if allowed != ALLOWED_SOURCE_REPOSITORIES:
        raise CandidateError(
            "safety profile source allowlist disagrees with the code allowlist; "
            "both must be changed deliberately"
        )
    return profile


def required_test_ids(profile: dict, *, have_chain_data: bool,
                      artifact_pinned: bool) -> tuple:
    """Which tests must pass for this run to be promotable.

    Data-dependent tests are not silently dropped when the data is absent: they
    stay required, which forces REVIEW_REQUIRED instead of an easy pass.
    """
    required = []
    for test in profile["tests"]:
        klass = test["class"]
        if klass == "mandatory":
            required.append(test["id"])
        elif klass == "mandatory-with-chain-data":
            required.append(test["id"])
        elif klass == "mandatory-when-artifact-pinned" and artifact_pinned:
            required.append(test["id"])
    return tuple(required)
