#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Fail closed unless a same-version artifact revision is behavior-preserving.

The verifier compares the *published previous artifacts* against the candidate,
not two fresh builds. Tar members are compared structurally (path, type, mode,
content). Compression-byte differences are therefore irrelevant. Only reviewed
ChainStrap floor evidence, generated provenance, and the sourceCommit field in
release-install-metadata may differ inside the bundle. The standalone installer
must be byte-identical.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import tarfile
from typing import Optional

DEFAULT_FLOOR_PATH = "contrib/chainstrap/reviewed-floor.json"
PROVENANCE_PATH = "release-provenance.json"
METADATA_PATH = "release-install-metadata.json"
ALLOWED_MANIFEST_BODY_DIFFERENCES = frozenset({
    "artifact_revision",
    "releaseTimestamp",
    "artifactDigest",
    "provenanceDigest",
})


class RevisionScopeError(RuntimeError):
    pass


def _json(path: pathlib.Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RevisionScopeError(f"cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RevisionScopeError(f"{path} must contain a JSON object")
    return value


def manifest_body(path: pathlib.Path) -> dict:
    document = _json(path)
    if set(document) == {"manifest", "signature"}:
        body = document["manifest"]
    else:
        body = document
    if not isinstance(body, dict):
        raise RevisionScopeError(f"{path} does not contain a manifest object")
    return body


def compare_manifest(previous: dict, candidate: dict) -> None:
    if previous.get("electrumxVersion") != candidate.get("electrumxVersion"):
        raise RevisionScopeError("artifact revision comparison requires unchanged version")
    old_revision = previous.get("artifact_revision")
    new_revision = candidate.get("artifact_revision")
    if not isinstance(old_revision, int) or isinstance(old_revision, bool) or old_revision < 0:
        raise RevisionScopeError("previous artifact_revision is malformed")
    if not isinstance(new_revision, int) or isinstance(new_revision, bool) or \
            new_revision <= old_revision:
        raise RevisionScopeError("candidate artifact_revision must strictly increase")
    keys = set(previous) | set(candidate)
    for key in keys - ALLOWED_MANIFEST_BODY_DIFFERENCES:
        if previous.get(key) != candidate.get(key):
            raise RevisionScopeError(
                f"same-version revision changed frozen manifest field {key!r}")


def _member_map(path: pathlib.Path) -> dict[str, tarfile.TarInfo]:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            result = {}
            for member in members:
                if member.name in result:
                    raise RevisionScopeError(f"duplicate tar member {member.name!r}")
                result[member.name] = member
            return result
    except tarfile.TarError as exc:
        raise RevisionScopeError(f"invalid release bundle {path}: {exc}") from exc


def _member_bytes(path: pathlib.Path, name: str) -> bytes:
    with tarfile.open(path, mode="r:gz") as archive:
        member = archive.getmember(name)
        handle = archive.extractfile(member)
        if handle is None:
            raise RevisionScopeError(f"cannot read tar member {name!r}")
        return handle.read()


def _compare_metadata(previous: bytes, candidate: bytes) -> None:
    try:
        old = json.loads(previous.decode("utf-8"))
        new = json.loads(candidate.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RevisionScopeError("release-install-metadata is malformed") from exc
    keys = set(old) | set(new)
    for key in keys - {"sourceCommit"}:
        if old.get(key) != new.get(key):
            raise RevisionScopeError(
                f"release-install-metadata changed frozen field {key!r}")
    for value in (old.get("sourceCommit"), new.get("sourceCommit")):
        if not isinstance(value, str) or len(value) != 40 or \
                any(char not in "0123456789abcdef" for char in value):
            raise RevisionScopeError("release-install-metadata sourceCommit is malformed")


def compare_bundles(previous: pathlib.Path, candidate: pathlib.Path,
                    *, floor_path: str = DEFAULT_FLOOR_PATH) -> None:
    old_members = _member_map(previous)
    new_members = _member_map(candidate)
    if set(old_members) != set(new_members):
        added = sorted(set(new_members) - set(old_members))
        removed = sorted(set(old_members) - set(new_members))
        raise RevisionScopeError(
            f"same-version bundle member set changed: added={added}, removed={removed}")

    allowed_content = {floor_path, PROVENANCE_PATH, METADATA_PATH}
    for name in sorted(old_members):
        old = old_members[name]
        new = new_members[name]
        old_kind = (old.type, old.mode, old.uid, old.gid)
        new_kind = (new.type, new.mode, new.uid, new.gid)
        if old_kind != new_kind:
            raise RevisionScopeError(f"tar metadata changed for frozen member {name!r}")
        if not old.isfile():
            if old.size != new.size:
                raise RevisionScopeError(f"non-file tar member changed {name!r}")
            continue
        old_bytes = _member_bytes(previous, name)
        new_bytes = _member_bytes(candidate, name)
        if old_bytes == new_bytes:
            continue
        if name not in allowed_content:
            raise RevisionScopeError(f"same-version revision changed frozen member {name!r}")
        if name == METADATA_PATH:
            _compare_metadata(old_bytes, new_bytes)
        elif name in (floor_path, PROVENANCE_PATH):
            try:
                json.loads(old_bytes.decode("utf-8"))
                json.loads(new_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RevisionScopeError(f"mutable evidence member {name!r} is not JSON") from exc


def same_file(previous: pathlib.Path, candidate: pathlib.Path, label: str) -> None:
    old = hashlib.sha256(previous.read_bytes()).digest()
    new = hashlib.sha256(candidate.read_bytes()).digest()
    if old != new:
        raise RevisionScopeError(f"same-version revision changed frozen {label}")


def verify(previous_dir: pathlib.Path, candidate_dir: pathlib.Path,
           *, floor_path: str = DEFAULT_FLOOR_PATH) -> None:
    previous_manifest = manifest_body(previous_dir / "release-manifest.json")
    candidate_manifest_path = candidate_dir / "release-manifest.json"
    if not candidate_manifest_path.exists():
        candidate_manifest_path = candidate_dir / "unsigned-release-manifest.json"
    candidate_manifest = manifest_body(candidate_manifest_path)
    compare_manifest(previous_manifest, candidate_manifest)
    same_file(
        previous_dir / "electrumx-ravencoin-install.py",
        candidate_dir / "electrumx-ravencoin-install.py",
        "standalone installer",
    )
    compare_bundles(
        previous_dir / "electrumx-ravencoin-bundle.tar.gz",
        candidate_dir / "electrumx-ravencoin-bundle.tar.gz",
        floor_path=floor_path,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-dir", required=True, type=pathlib.Path)
    parser.add_argument("--candidate-dir", required=True, type=pathlib.Path)
    parser.add_argument("--floor-path", default=DEFAULT_FLOOR_PATH)
    args = parser.parse_args()
    verify(args.previous_dir.resolve(), args.candidate_dir.resolve(), floor_path=args.floor_path)
    print("artifact-revision: scope verified")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RevisionScopeError as exc:
        print(f"artifact-revision: REFUSED: {exc}")
        raise SystemExit(1) from exc
