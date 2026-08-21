# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

import importlib.util
import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

spec = importlib.util.spec_from_file_location(
    "discover_releases_stable_test", SCRIPTS / "discover_releases.py")
discover = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discover)


def _fetch_for(release):
    def fetch(url, token=None, timeout=30):
        if url.endswith("/releases?per_page=10"):
            return [release]
        raise AssertionError(f"prerelease/draft must be filtered before tag lookup: {url}")
    return fetch


def test_prerelease_is_not_a_stable_core_candidate():
    release = {
        "tag_name": "v4.9.0-rc1",
        "draft": False,
        "prerelease": True,
    }
    assert discover.discover_repository(
        "RavenProject/Ravencoin", _fetch_for(release)) == []


def test_draft_is_not_a_stable_core_candidate():
    release = {
        "tag_name": "v4.9.0",
        "draft": True,
        "prerelease": False,
    }
    assert discover.discover_repository(
        "RavenProject/Ravencoin", _fetch_for(release)) == []
