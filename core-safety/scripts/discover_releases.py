#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Discover Ravencoin Core release candidates from the two allowed repositories.

Discovery grants nothing.  A release found here becomes a *candidate*: something
the certification harness is allowed to test.  Neither repository is trusted
because of its name, and the allowlist lives in code, not in configuration a
workflow input could change.

Only published releases and their tags are considered.  A branch head is never
treated as a release.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from candidate import (  # noqa: E402
    ALLOWED_SOURCE_REPOSITORIES, Candidate, CandidateError, CandidateState,
)

GITHUB_API = "https://api.github.com"
VERSION_FROM_TAG = re.compile(r"^v?(\d+(?:\.\d+){1,3})$")


class DiscoveryError(RuntimeError):
    """The discovery run could not complete.  Never turns into a candidate."""


def default_fetch(url: str, *, token: Optional[str] = None, timeout: int = 30) -> dict:
    """Minimal GitHub API GET.  Returns parsed JSON or raises DiscoveryError."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "electrumx-rvn-core-safety",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code in (403, 429):
            raise DiscoveryError(
                f"GitHub rate limited or refused the request ({exc.code}); the run is "
                f"abandoned rather than guessed at") from None
        raise DiscoveryError(f"GitHub returned HTTP {exc.code} for {url}") from None
    except (urllib.error.URLError, OSError) as exc:
        raise DiscoveryError(f"GitHub is unreachable: {exc}") from None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise DiscoveryError("GitHub returned a malformed response") from exc


def _expect_mapping(payload, what: str) -> dict:
    if not isinstance(payload, dict):
        raise DiscoveryError(f"{what} response was not an object")
    return payload


def resolve_tag_commit(repository: str, tag: str, fetch: Callable, *,
                       token: Optional[str] = None) -> tuple:
    """Resolve a tag to its commit, following an annotated tag object.

    Returns ``(commit, tag_object, tag_verified)``.  A tag that resolves to
    anything other than a commit is refused rather than interpreted.
    """
    reference = _expect_mapping(
        fetch(f"{GITHUB_API}/repos/{repository}/git/ref/tags/{tag}", token=token),
        "git ref")
    obj = reference.get("object") or {}
    kind = obj.get("type")
    sha = obj.get("sha")
    if not isinstance(sha, str):
        raise DiscoveryError(f"tag {tag} has no object sha")
    if kind == "commit":
        return sha, None, False
    if kind != "tag":
        raise DiscoveryError(f"tag {tag} points at a {kind!r}, which is not supported")
    tag_object = _expect_mapping(
        fetch(f"{GITHUB_API}/repos/{repository}/git/tags/{sha}", token=token),
        "tag object")
    target = tag_object.get("object") or {}
    if target.get("type") != "commit" or not isinstance(target.get("sha"), str):
        raise DiscoveryError(f"annotated tag {tag} does not point at a commit")
    verification = tag_object.get("verification") or {}
    return target["sha"], sha, bool(verification.get("verified"))


def discover_repository(repository: str, fetch: Callable, *,
                        token: Optional[str] = None, limit: int = 10) -> list:
    """Return candidates for one allowed repository."""
    if repository not in ALLOWED_SOURCE_REPOSITORIES:
        raise DiscoveryError(f"repository {repository!r} is not in the allowlist")
    releases = fetch(f"{GITHUB_API}/repos/{repository}/releases?per_page={limit}",
                     token=token)
    if not isinstance(releases, list):
        raise DiscoveryError("releases response was not a list")

    candidates = []
    for release in releases:
        if not isinstance(release, dict):
            raise DiscoveryError("a release entry was not an object")
        if release.get("draft"):
            continue
        tag = release.get("tag_name")
        if not isinstance(tag, str):
            continue
        match = VERSION_FROM_TAG.match(tag)
        if not match:
            # A release whose tag is not a plain version is not silently guessed
            # at; it is reported for human review instead.
            candidates.append({
                "state": CandidateState.REVIEW_REQUIRED.value,
                "repository": repository,
                "tag": tag,
                "reason": "tag does not look like a plain version",
            })
            continue
        version = match.group(1)
        try:
            commit, tag_object, tag_verified = resolve_tag_commit(
                repository, tag, fetch, token=token)
        except DiscoveryError as exc:
            candidates.append({
                "state": CandidateState.PROVENANCE_FAILED.value,
                "repository": repository,
                "tag": tag,
                "reason": str(exc),
            })
            continue

        target = release.get("target_commitish")
        notes = []
        if isinstance(target, str) and re.fullmatch(r"[0-9a-f]{40}", target) \
                and target != commit:
            notes.append(
                "release target_commitish disagrees with the resolved tag commit")

        artifact_name = None
        for asset in release.get("assets") or []:
            if isinstance(asset, dict) and isinstance(asset.get("name"), str) \
                    and asset["name"].endswith(".tar.gz"):
                artifact_name = asset["name"]
                break

        try:
            record = Candidate(
                repository=repository,
                tag=tag,
                commit=commit,
                version=version,
                tag_object=tag_object,
                tag_verified=tag_verified,
                commit_verified=True,
                published_at=release.get("published_at"),
                release_url=release.get("html_url"),
                artifact_name=artifact_name,
                notes=tuple(notes),
            )
        except CandidateError as exc:
            candidates.append({
                "state": CandidateState.PROVENANCE_FAILED.value,
                "repository": repository,
                "tag": tag,
                "reason": str(exc),
            })
            continue

        entry = record.to_dict()
        entry["state"] = (CandidateState.REVIEW_REQUIRED.value if notes
                          else CandidateState.PROVENANCE_VALIDATED.value)
        entry["prerelease"] = bool(release.get("prerelease"))
        candidates.append(entry)
    return candidates


def discover_all(fetch: Callable, *, token: Optional[str] = None,
                 repositories=ALLOWED_SOURCE_REPOSITORIES) -> list:
    """Discover across every allowed repository, keeping failures per repository."""
    found = []
    for repository in repositories:
        try:
            found.extend(discover_repository(repository, fetch, token=token))
        except DiscoveryError as exc:
            found.append({
                "state": CandidateState.REVIEW_REQUIRED.value,
                "repository": repository,
                "reason": f"discovery failed: {exc}",
            })
    return found


def new_candidates(discovered: list, processed: dict) -> list:
    """Filter out identities already processed, and detect changed metadata.

    Identity is repository plus commit.  The same version from two repositories,
    or the same tag re-pointed at a different commit, are different candidates.
    """
    fresh = []
    for entry in discovered:
        commit = entry.get("commit")
        if not commit:
            fresh.append(entry)
            continue
        identity = f"{entry['repository']}@{commit}"
        known = processed.get(identity)
        if known is None:
            fresh.append(entry)
            continue
        if known.get("tag") != entry.get("tag"):
            changed = dict(entry)
            changed["state"] = CandidateState.REVIEW_REQUIRED.value
            changed["reason"] = (
                f"known commit is now published under tag {entry.get('tag')!r} "
                f"instead of {known.get('tag')!r}")
            fresh.append(changed)
    return fresh


def load_state(path: pathlib.Path) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    processed = payload.get("processed")
    return processed if isinstance(processed, dict) else {}


def save_state(path: pathlib.Path, processed: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps({"processed": processed}, indent=2, sort_keys=True)
                         + "\n", encoding="utf-8")
    temporary.replace(path)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-file", default="core-safety/state/processed.json")
    parser.add_argument("--output", default="core-safety/state/new-candidates.json")
    parser.add_argument("--limit", type=int, default=10)
    arguments = parser.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or None
    state_path = pathlib.Path(arguments.state_file)
    processed = load_state(state_path)

    discovered = discover_all(default_fetch, token=token)
    fresh = new_candidates(discovered, processed)

    output = pathlib.Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(fresh, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")

    for entry in fresh:
        print(f"{entry.get('state')}: {entry.get('repository')} "
              f"{entry.get('tag')} {entry.get('commit', '')[:12]} "
              f"{entry.get('reason', '')}".rstrip())
    print(f"{len(fresh)} new candidate(s) of {len(discovered)} discovered")
    return 0


if __name__ == "__main__":
    sys.exit(main())
