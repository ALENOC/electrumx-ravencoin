# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Every RavenProject/Ravencoin source-commit pin must agree with the one the
GitHub tag actually resolves to.

This guards against exactly the defect found and fixed on 2026-08-20: the
bundled Core build pinned ``b60f50e04f1fba425b28804e61be2694faaf3469``, a
tree-identical merge parent of the real ``v4.8.0`` tag target, instead of the
tag's own commit.  ``repository + commit`` is the certification identity key
(see core-safety/scripts/candidate.py); a stale pin here would certify one
commit while the build actually fetches another.

The expected value below is not invented: it is the full 40-character commit
that ``GET /repos/RavenProject/Ravencoin/git/refs/tags/v4.8.0`` resolved to,
independently re-verified against the GitHub compare API and both source
archive digests.  See core-safety/production/provenance-v4.8.0.md for the
full evidence trail.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The commit RavenProject/Ravencoin's v4.8.0 tag resolves to, live-verified.
EXPECTED_TAG_COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"

# (file, regex) pairs; each regex's first group must equal EXPECTED_TAG_COMMIT.
PIN_LOCATIONS = (
    ("docker/core/Dockerfile",
     re.compile(r"ARG RAVENCOIN_SOURCE_COMMIT=([0-9a-f]{40})")),
    ("compose.yaml",
     re.compile(r"RAVENCOIN_SOURCE_COMMIT:\s*([0-9a-f]{40})")),
    ("compose.chainstrap.yaml",
     re.compile(r"RAVENCOIN_SOURCE_COMMIT:\s*([0-9a-f]{40})")),
    (".github/workflows/ci.yml",
     re.compile(r"RAVENCOIN_SOURCE_COMMIT=([0-9a-f]{40})")),
)


def test_every_dockerfile_and_compose_pin_matches_the_resolved_tag_commit():
    for relative_path, pattern in PIN_LOCATIONS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        found = pattern.findall(text)
        assert found, f"{relative_path} has no RAVENCOIN_SOURCE_COMMIT pin to check"
        for commit in found:
            assert commit == EXPECTED_TAG_COMMIT, (
                f"{relative_path} pins {commit}, which is not the commit "
                f"RavenProject/Ravencoin's v4.8.0 tag resolves to "
                f"({EXPECTED_TAG_COMMIT}); repository+commit is the "
                f"certification identity key, so a stale pin here silently "
                f"certifies the wrong candidate")


def test_ci_core_artifact_smoke_invocation_matches_the_resolved_tag_commit():
    text = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    match = re.search(
        r'core-artifact-smoke\.sh.*?\n\s*"[^"]+"\s*\\\n\s*"[^"]+"\s*\\\n\s*"([0-9a-f]{40})"',
        text, re.DOTALL)
    assert match, "could not find the core-artifact-smoke.sh commit argument"
    assert match.group(1) == EXPECTED_TAG_COMMIT


def test_known_zero_diff_merge_parent_is_never_used_as_the_source_pin():
    # b60f50e04f1fba425b28804e61be2694faaf3469 is a real, tree-identical
    # commit (one of the tag commit's two merge parents), which makes it an
    # easy commit to paste back in by habit.  It must never be the pin: the
    # certification identity is repository+commit, not repository+tree.
    stale_commit = "b60f50e04f1fba425b28804e61be2694faaf3469"
    for relative_path, pattern in PIN_LOCATIONS:
        text = (ROOT / relative_path).read_text(encoding="utf-8")
        for commit in pattern.findall(text):
            assert commit != stale_commit, (
                f"{relative_path} pins the zero-diff merge parent instead of "
                f"the official v4.8.0 tag commit")
