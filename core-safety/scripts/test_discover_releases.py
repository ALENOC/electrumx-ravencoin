# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))

from candidate import CandidateState
from discover_releases import (
    DiscoveryError, discover_repository, new_candidates, resolve_tag_commit,
)

REPOSITORY = "RavenProject/Ravencoin"
COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"


def fake_fetch_factory(releases, *, tag_refs=None):
    tag_refs = tag_refs or {}

    def fetch(url, *, token=None, timeout=30):
        if url.endswith("/releases?per_page=10"):
            return releases
        for tag, sha in tag_refs.items():
            if url.endswith(f"/git/ref/tags/{tag}"):
                return {"object": {"type": "commit", "sha": sha}}
        raise AssertionError(f"unexpected fetch url: {url}")

    return fetch


class StableOnlyFilterTests(unittest.TestCase):

    def test_draft_release_is_never_a_candidate(self):
        releases = [{"draft": True, "tag_name": "v4.9.0", "assets": []}]
        fetch = fake_fetch_factory(releases)
        result = discover_repository(REPOSITORY, fetch, limit=10)
        self.assertEqual(result, [])

    def test_prerelease_is_never_a_candidate(self):
        releases = [{
            "draft": False, "prerelease": True, "tag_name": "v4.9.0-rc1",
            "assets": [],
        }]
        fetch = fake_fetch_factory(releases)
        result = discover_repository(REPOSITORY, fetch, limit=10)
        self.assertEqual(result, [])

    def test_stable_release_becomes_a_provenance_validated_candidate(self):
        releases = [{
            "draft": False, "prerelease": False, "tag_name": "v4.8.0",
            "target_commitish": COMMIT, "published_at": "2026-08-19T00:16:44Z",
            "html_url": "https://example.invalid/release", "assets": [],
        }]
        fetch = fake_fetch_factory(releases, tag_refs={"v4.8.0": COMMIT})
        result = discover_repository(REPOSITORY, fetch, limit=10)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["state"], CandidateState.PROVENANCE_VALIDATED.value)
        self.assertEqual(result[0]["commit"], COMMIT)
        self.assertFalse(result[0]["prerelease"])

    def test_disallowed_repository_is_refused(self):
        fetch = fake_fetch_factory([])
        with self.assertRaises(DiscoveryError):
            discover_repository("2miners/Ravencoin", fetch, limit=10)


class TagResolutionTests(unittest.TestCase):

    def test_lightweight_tag_resolves_directly_to_commit(self):
        fetch = fake_fetch_factory([], tag_refs={"v4.8.0": COMMIT})
        commit, tag_object, verified = resolve_tag_commit(REPOSITORY, "v4.8.0", fetch)
        self.assertEqual(commit, COMMIT)
        self.assertIsNone(tag_object)
        self.assertFalse(verified)


class NewCandidatesTests(unittest.TestCase):

    def test_already_processed_identity_is_not_repeated(self):
        entry = {"repository": REPOSITORY, "commit": COMMIT, "tag": "v4.8.0"}
        processed = {f"{REPOSITORY}@{COMMIT}": {"tag": "v4.8.0"}}
        self.assertEqual(new_candidates([entry], processed), [])

    def test_unprocessed_identity_is_fresh(self):
        entry = {"repository": REPOSITORY, "commit": COMMIT, "tag": "v4.8.0"}
        self.assertEqual(new_candidates([entry], {}), [entry])

    def test_same_commit_republished_under_a_different_tag_needs_review(self):
        entry = {"repository": REPOSITORY, "commit": COMMIT, "tag": "v4.9.0"}
        processed = {f"{REPOSITORY}@{COMMIT}": {"tag": "v4.8.0"}}
        result = new_candidates([entry], processed)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["state"], CandidateState.REVIEW_REQUIRED.value)


if __name__ == "__main__":
    unittest.main()
