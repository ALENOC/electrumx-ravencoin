#!/usr/bin/env python3
"""Resolve official ChainStrap metadata at runtime and stage only raw RVN blocks.

Upstream metadata is transport input, not a consensus trust root.  This module
resolves one exact upstream Git commit (or consumes an explicit reviewed local
manifest), sanitizes it against release-embedded RVN policy, binds an in-progress
bootstrap to that exact resolution, downloads each signed-by-metadata archive
through the fixed gateway policy in ``chainstrap_bootstrap``, preflights every
ZIP member before extracting that archive, and finally hands raw ``blk*.dat``
files to the separately network-isolated Ravencoin Core reindex stage.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import chainstrap_bootstrap as transport

UPSTREAM_REPOSITORY = "chainstrap/chainstrap.github.io"
UPSTREAM_PATH = "RVN/RVN-mainnet.json"
UPSTREAM_BRANCH = "master"
UPSTREAM_API_HOST = "api.github.com"
UPSTREAM_RAW_HOST = "raw.githubusercontent.com"
UPSTREAM_COMMIT_URL = (
    "https://api.github.com/repos/chainstrap/chainstrap.github.io/commits/master"
)
UPSTREAM_RAW_PREFIX = (
    "https://raw.githubusercontent.com/chainstrap/chainstrap.github.io/"
)
RELEASE_FLOOR_HEIGHT = 4_501_329
RELEASE_FLOOR_BLOCKHASH = (
    "000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48"
)
MAX_METADATA_BYTES = 256 * 1024
MAX_COMMIT_RESPONSE_BYTES = 256 * 1024
MAX_SNAPSHOT_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_FUTURE_SKEW_SECONDS = 60 * 60
MAX_ARCHIVE_MEMBERS = 4096
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_EXTRACTED_BYTES = transport.MAX_TOTAL_BYTES
RESOLUTION_LOCK = ".chainstrap-resolution.json"
PROGRESS_MARKER = transport.PROGRESS_MARKER
BLOCKS_MARKER = transport.BLOCKS_MARKER
REINDEX_MARKER = transport.REINDEX_MARKER
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
BLOCK_RE = re.compile(r"^blocks/blk([0-9]{5,8})\.dat$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCKHASH_RE = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_TOP_LEVEL = frozenset({
    "chain", "mode", "blocks", "blockhash", "updated", "bytes", "parts",
    # Present in official ChainStrap metadata but deliberately ignored as
    # transport/trust policy. Gateways remain release-embedded below us.
    "ipfs_hashes", "baseurl",
})
PART_FIELDS = frozenset({"cid", "bytes", "sha256"})


class RuntimeBootstrapError(RuntimeError):
    """A runtime-resolution or archive-staging invariant failed."""


def log(message: str = "") -> None:
    transport.log(message)


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise RuntimeBootstrapError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _strict_json(raw: bytes, label: str) -> dict:
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeBootstrapError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeBootstrapError(f"{label} must contain a JSON object")
    return value


def _validate_final_https_url(final_url: str, *, host: str,
                              path_prefix: str) -> None:
    try:
        parsed = urlsplit(final_url)
    except ValueError as exc:
        raise RuntimeBootstrapError("upstream transport returned a malformed URL") from exc
    if parsed.scheme != "https" or parsed.hostname != host or \
            parsed.username is not None or parsed.password is not None or \
            parsed.port not in (None, 443) or parsed.query or parsed.fragment or \
            not parsed.path.startswith(path_prefix):
        raise RuntimeBootstrapError(
            "upstream transport redirected outside the fixed official HTTPS namespace")


def _fetch_bounded(url: str, *, host: str, path_prefix: str, max_bytes: int,
                   timeout: int = 30,
                   opener: Callable = urlopen) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": "electrumx-ravencoin-chainstrap-resolver/1.13.3",
            "Accept": "application/vnd.github+json" if host == UPSTREAM_API_HOST else "application/json",
        },
    )
    try:
        with opener(request, timeout=timeout) as response:
            _validate_final_https_url(
                response.geturl(), host=host, path_prefix=path_prefix)
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise RuntimeBootstrapError("upstream metadata exceeds size limit")
                except ValueError as exc:
                    raise RuntimeBootstrapError("upstream Content-Length is malformed") from exc
            raw = response.read(max_bytes + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        raise RuntimeBootstrapError(f"upstream metadata fetch failed: {exc}") from exc
    if len(raw) > max_bytes:
        raise RuntimeBootstrapError("upstream metadata exceeds size limit")
    return raw


def resolve_master_commit(*, opener: Callable = urlopen) -> str:
    raw = _fetch_bounded(
        UPSTREAM_COMMIT_URL,
        host=UPSTREAM_API_HOST,
        path_prefix="/repos/chainstrap/chainstrap.github.io/commits/master",
        max_bytes=MAX_COMMIT_RESPONSE_BYTES,
        opener=opener,
    )
    payload = _strict_json(raw, "ChainStrap commit response")
    commit = payload.get("sha")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeBootstrapError("official ChainStrap master did not resolve to a full commit SHA")
    return commit


def _raw_manifest_url(commit: str) -> str:
    if COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeBootstrapError("ChainStrap commit must be 40 lowercase hex characters")
    return f"{UPSTREAM_RAW_PREFIX}{commit}/{UPSTREAM_PATH}"


def fetch_manifest_at_commit(commit: str, *, opener: Callable = urlopen) -> tuple[bytes, dict]:
    url = _raw_manifest_url(commit)
    raw = _fetch_bounded(
        url,
        host=UPSTREAM_RAW_HOST,
        path_prefix=f"/chainstrap/chainstrap.github.io/{commit}/{UPSTREAM_PATH}",
        max_bytes=MAX_METADATA_BYTES,
        opener=opener,
    )
    return raw, _strict_json(raw, "ChainStrap RVN metadata")


def _parse_updated(value: object) -> datetime.datetime:
    if not isinstance(value, str) or not value:
        raise RuntimeBootstrapError("ChainStrap updated timestamp is required")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise RuntimeBootstrapError("ChainStrap updated timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise RuntimeBootstrapError("ChainStrap updated timestamp must include a timezone")
    return parsed.astimezone(datetime.timezone.utc)


def sanitize_upstream_manifest(payload: dict, *, commit: str,
                               require_fresh: bool,
                               now: Optional[datetime.datetime] = None) -> dict:
    if not isinstance(payload, dict):
        raise RuntimeBootstrapError("ChainStrap metadata must be an object")
    unknown = set(payload) - ALLOWED_TOP_LEVEL
    missing = {"chain", "mode", "blocks", "blockhash", "updated", "bytes", "parts"} - set(payload)
    if unknown:
        raise RuntimeBootstrapError(
            f"ChainStrap metadata contains unknown top-level fields: {sorted(unknown)}")
    if missing:
        raise RuntimeBootstrapError(
            f"ChainStrap metadata is missing required fields: {sorted(missing)}")
    if payload.get("chain") != "RVN" or payload.get("mode") != "mainnet":
        raise RuntimeBootstrapError("only official RVN mainnet metadata is accepted")
    if COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeBootstrapError("resolved ChainStrap commit is malformed")

    updated = _parse_updated(payload.get("updated"))
    clock = now or datetime.datetime.now(datetime.timezone.utc)
    if clock.tzinfo is None:
        raise RuntimeBootstrapError("resolver clock must be timezone-aware")
    clock = clock.astimezone(datetime.timezone.utc)
    age = (clock - updated).total_seconds()
    if age < -MAX_FUTURE_SKEW_SECONDS:
        raise RuntimeBootstrapError("ChainStrap metadata timestamp is implausibly in the future")
    if require_fresh and age > MAX_SNAPSHOT_AGE_SECONDS:
        raise RuntimeBootstrapError(
            "official ChainStrap master metadata is stale; use an explicitly reviewed exact "
            "commit/local manifest rather than silently falling back")

    parts = payload.get("parts")
    if not isinstance(parts, list):
        raise RuntimeBootstrapError("ChainStrap parts must be a list")
    sanitized_parts = []
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict) or set(part) != PART_FIELDS:
            raise RuntimeBootstrapError(f"ChainStrap part {index} has unexpected schema")
        sanitized_parts.append({
            "cid": part.get("cid"),
            "bytes": part.get("bytes"),
            "sha256": part.get("sha256"),
        })

    sanitized = {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": payload.get("blocks"),
        "blockhash": payload.get("blockhash"),
        "updated": payload.get("updated"),
        "bytes": payload.get("bytes"),
        "parts": sanitized_parts,
        "source": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": commit,
            "path": UPSTREAM_PATH,
        },
    }
    try:
        transport.validate_manifest(sanitized)
    except ValueError as exc:
        raise RuntimeBootstrapError(str(exc)) from exc

    height = sanitized["blocks"]
    blockhash = sanitized["blockhash"]
    if height < RELEASE_FLOOR_HEIGHT:
        raise RuntimeBootstrapError(
            f"resolved snapshot height {height} is below release floor {RELEASE_FLOOR_HEIGHT}")
    if height == RELEASE_FLOOR_HEIGHT and blockhash != RELEASE_FLOOR_BLOCKHASH:
        raise RuntimeBootstrapError("snapshot at the release-floor height has the wrong block hash")
    return sanitized


def load_reviewed_manifest(path: Path) -> tuple[bytes, dict]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeBootstrapError(f"cannot stat reviewed manifest {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeBootstrapError("reviewed manifest must be a regular non-symlink file")
    if info.st_size < 1 or info.st_size > MAX_METADATA_BYTES:
        raise RuntimeBootstrapError("reviewed manifest exceeds metadata size policy")
    raw = path.read_bytes()
    payload = _strict_json(raw, "reviewed ChainStrap manifest")
    source = payload.get("source")
    if not isinstance(source, dict) or set(source) != {"repository", "commit", "path"}:
        raise RuntimeBootstrapError("reviewed manifest source identity is missing or malformed")
    if source.get("repository") != UPSTREAM_REPOSITORY or source.get("path") != UPSTREAM_PATH:
        raise RuntimeBootstrapError("reviewed manifest source identity is not official ChainStrap RVN")
    commit = source.get("commit")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None:
        raise RuntimeBootstrapError("reviewed manifest source commit is malformed")

    # Local reviewed manifests are already sanitized release-policy documents.
    allowed = {"chain", "mode", "blocks", "blockhash", "updated", "bytes", "parts", "source"}
    if set(payload) != allowed:
        raise RuntimeBootstrapError("reviewed manifest has unexpected schema")
    try:
        transport.validate_manifest(payload)
    except ValueError as exc:
        raise RuntimeBootstrapError(str(exc)) from exc
    if payload["blocks"] < RELEASE_FLOOR_HEIGHT:
        raise RuntimeBootstrapError("reviewed snapshot is below the release floor")
    if payload["blocks"] == RELEASE_FLOOR_HEIGHT and \
            payload["blockhash"] != RELEASE_FLOOR_BLOCKHASH:
        raise RuntimeBootstrapError("reviewed snapshot conflicts with the release-floor hash")
    _parse_updated(payload.get("updated"))
    return raw, payload


def marker_payload(manifest: dict, metadata_sha256: str, *, resolution_mode: str) -> dict:
    source = manifest["source"]
    if SHA256_RE.fullmatch(metadata_sha256) is None:
        raise RuntimeBootstrapError("resolved metadata digest is malformed")
    return {
        "schema": 2,
        "chain": "RVN",
        "mode": "mainnet",
        "height": manifest["blocks"],
        "blockhash": manifest["blockhash"],
        "metadata_sha256": metadata_sha256,
        "source_repository": source["repository"],
        "source_commit": source["commit"],
        "source_path": source["path"],
        "upstream_updated": manifest["updated"],
        "resolution_mode": resolution_mode,
        "release_floor_height": RELEASE_FLOOR_HEIGHT,
        "release_floor_blockhash": RELEASE_FLOOR_BLOCKHASH,
    }


def _validate_marker(marker: object) -> dict:
    expected = {
        "schema", "chain", "mode", "height", "blockhash", "metadata_sha256",
        "source_repository", "source_commit", "source_path", "upstream_updated",
        "resolution_mode", "release_floor_height", "release_floor_blockhash",
    }
    if not isinstance(marker, dict) or set(marker) != expected:
        raise RuntimeBootstrapError("ChainStrap marker has unexpected schema")
    if marker.get("schema") != 2 or marker.get("chain") != "RVN" or marker.get("mode") != "mainnet":
        raise RuntimeBootstrapError("ChainStrap marker identity is invalid")
    height = marker.get("height")
    if not isinstance(height, int) or isinstance(height, bool) or height < RELEASE_FLOOR_HEIGHT:
        raise RuntimeBootstrapError("ChainStrap marker height is below release policy")
    for field in ("blockhash", "release_floor_blockhash"):
        if not isinstance(marker.get(field), str) or BLOCKHASH_RE.fullmatch(marker[field]) is None:
            raise RuntimeBootstrapError(f"ChainStrap marker {field} is malformed")
    if marker.get("release_floor_height") != RELEASE_FLOOR_HEIGHT or \
            marker.get("release_floor_blockhash") != RELEASE_FLOOR_BLOCKHASH:
        raise RuntimeBootstrapError("ChainStrap marker release floor differs from this release")
    if not isinstance(marker.get("metadata_sha256"), str) or \
            SHA256_RE.fullmatch(marker["metadata_sha256"]) is None:
        raise RuntimeBootstrapError("ChainStrap marker metadata digest is malformed")
    if marker.get("source_repository") != UPSTREAM_REPOSITORY or \
            marker.get("source_path") != UPSTREAM_PATH or \
            not isinstance(marker.get("source_commit"), str) or \
            COMMIT_RE.fullmatch(marker["source_commit"]) is None:
        raise RuntimeBootstrapError("ChainStrap marker source identity is invalid")
    if marker.get("resolution_mode") not in ("runtime-master", "exact-commit", "reviewed-local"):
        raise RuntimeBootstrapError("ChainStrap marker resolution mode is invalid")
    _parse_updated(marker.get("upstream_updated"))
    return marker


def _read_json_file(path: Path, label: str, *, max_bytes: int) -> dict:
    try:
        info = path.lstat()
    except OSError as exc:
        raise RuntimeBootstrapError(f"cannot stat {label}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise RuntimeBootstrapError(f"{label} must be a regular non-symlink file")
    if info.st_size < 1 or info.st_size > max_bytes:
        raise RuntimeBootstrapError(f"{label} exceeds size policy")
    return _strict_json(path.read_bytes(), label)


def _resolution_lock_payload(manifest: dict, marker: dict) -> dict:
    return {"schema": 1, "marker": marker, "manifest": manifest}


def _load_resolution_lock(path: Path) -> tuple[dict, dict]:
    value = _read_json_file(path, "ChainStrap resolution lock", max_bytes=MAX_METADATA_BYTES * 2)
    if not isinstance(value, dict) or set(value) != {"schema", "marker", "manifest"} or \
            value.get("schema") != 1:
        raise RuntimeBootstrapError("ChainStrap resolution lock has unexpected schema")
    marker = _validate_marker(value.get("marker"))
    manifest = value.get("manifest")
    if not isinstance(manifest, dict):
        raise RuntimeBootstrapError("ChainStrap resolution lock manifest is malformed")
    try:
        transport.validate_manifest(manifest)
    except ValueError as exc:
        raise RuntimeBootstrapError(str(exc)) from exc
    expected = marker_payload(
        manifest, marker["metadata_sha256"], resolution_mode=marker["resolution_mode"])
    if expected != marker:
        raise RuntimeBootstrapError("ChainStrap resolution lock marker disagrees with its manifest")
    return manifest, marker


def _completed_or_staged(datadir: Path) -> bool:
    marker_path = datadir / BLOCKS_MARKER
    done_path = datadir / REINDEX_MARKER
    if not marker_path.exists():
        if done_path.exists():
            raise RuntimeBootstrapError("reindex marker exists without a ChainStrap block marker")
        return False
    marker = _validate_marker(
        _read_json_file(marker_path, "ChainStrap block marker", max_bytes=MAX_METADATA_BYTES))
    block_files = transport.validate_contiguous_blocks(datadir)
    if done_path.exists():
        done_hash = done_path.read_text(encoding="ascii").strip()
        if SHA256_RE.fullmatch(done_hash) is None or \
                done_hash != transport.sha256_file(marker_path):
            raise RuntimeBootstrapError("completed reindex marker does not match block marker")
        log(
            "A prior Fast Verified Bootstrap already completed full Core validation; "
            "runtime upstream resolution is intentionally skipped."
        )
    else:
        log(
            "Raw block staging for this exact ChainStrap resolution is already complete; "
            "runtime upstream resolution is intentionally skipped."
        )
    log(
        f"  locked snapshot: {marker['height']}:{marker['blockhash']} | "
        f"source {marker['source_commit']} | {len(block_files)} block files"
    )
    return True


def _datadir_has_unclaimed_payload(datadir: Path) -> bool:
    ignored = {RESOLUTION_LOCK, PROGRESS_MARKER}
    for entry in datadir.iterdir():
        if entry.name in ignored:
            continue
        if entry.is_dir() and not entry.is_symlink():
            try:
                next(entry.iterdir())
            except StopIteration:
                continue
        return True
    return False


def _new_progress(marker: dict) -> dict:
    return {
        "schema": 2,
        "marker": marker,
        "completed_cids": [],
        "active_cid": None,
        "active_block_names": [],
    }


def _validate_progress(value: object, marker: dict, manifest: dict) -> dict:
    fields = {"schema", "marker", "completed_cids", "active_cid", "active_block_names"}
    if not isinstance(value, dict) or set(value) != fields or value.get("schema") != 2:
        raise RuntimeBootstrapError("ChainStrap progress marker has unexpected schema")
    if value.get("marker") != marker:
        raise RuntimeBootstrapError("ChainStrap progress marker belongs to a different resolution")
    valid_cids = [part["cid"] for part in manifest["parts"]]
    completed = value.get("completed_cids")
    if not isinstance(completed, list) or len(completed) != len(set(completed)) or \
            any(cid not in valid_cids for cid in completed):
        raise RuntimeBootstrapError("ChainStrap progress completed CID list is invalid")
    active = value.get("active_cid")
    names = value.get("active_block_names")
    if active is None:
        if names != []:
            raise RuntimeBootstrapError("inactive ChainStrap progress contains active block names")
    else:
        if active not in valid_cids or active in completed:
            raise RuntimeBootstrapError("ChainStrap progress active CID is invalid")
        if not isinstance(names, list) or not names or len(names) != len(set(names)):
            raise RuntimeBootstrapError("ChainStrap progress active block list is invalid")
        for name in names:
            if not isinstance(name, str) or re.fullmatch(r"blk[0-9]{5,8}\.dat", name) is None:
                raise RuntimeBootstrapError("ChainStrap progress contains unsafe active block name")
    return value


def _write_progress(path: Path, progress: dict) -> None:
    transport.write_json_atomic(path, progress)


def _discard_interrupted_active_part(datadir: Path, progress: dict) -> None:
    if progress["active_cid"] is None:
        return
    log(
        f"Discarding incomplete extraction state for CID {progress['active_cid']} "
        "before re-preflight."
    )
    blocks = datadir / "blocks"
    for name in progress["active_block_names"]:
        target = blocks / name
        target.unlink(missing_ok=True)
        target.with_name(target.name + ".chainstrap.part").unlink(missing_ok=True)
    progress["active_cid"] = None
    progress["active_block_names"] = []


def _zip_entry_type_is_regular(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return kind in (0, stat.S_IFREG)


def preflight_archive(archive_path: Path, *, already_claimed: set[str]) -> list[zipfile.ZipInfo]:
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise RuntimeBootstrapError("downloaded ChainStrap part is not a valid ZIP archive") from exc
    with archive:
        members = archive.infolist()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise RuntimeBootstrapError("ChainStrap ZIP member count is outside release policy")
        vetted = []
        names = set()
        indexes = set()
        total = 0
        for info in members:
            raw_name = info.filename
            if not raw_name or "\\" in raw_name or "\x00" in raw_name:
                raise RuntimeBootstrapError(f"unsafe ChainStrap ZIP member path {raw_name!r}")
            path = PurePosixPath(raw_name)
            if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
                raise RuntimeBootstrapError(f"unsafe ChainStrap ZIP member path {raw_name!r}")
            match = BLOCK_RE.fullmatch(raw_name)
            if match is None:
                raise RuntimeBootstrapError(
                    f"non-allowlisted member in ChainStrap ZIP: {raw_name!r}")
            if raw_name in names:
                raise RuntimeBootstrapError(f"duplicate block path in ChainStrap ZIP: {raw_name}")
            names.add(raw_name)
            index = int(match.group(1))
            if index in indexes:
                raise RuntimeBootstrapError(f"duplicate block number in ChainStrap ZIP: {index}")
            indexes.add(index)
            basename = path.name
            if basename in already_claimed:
                raise RuntimeBootstrapError(
                    f"duplicate block file across ChainStrap parts: {basename}")
            if not _zip_entry_type_is_regular(info) or (info.flag_bits & 0x1):
                raise RuntimeBootstrapError(f"unsafe ChainStrap ZIP member type: {raw_name}")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise RuntimeBootstrapError(f"unsupported ZIP compression for {raw_name}")
            if info.file_size < 0 or info.file_size > transport.MAX_BLOCK_FILE_BYTES:
                raise RuntimeBootstrapError(f"raw block file exceeds size policy: {raw_name}")
            total += info.file_size
            if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise RuntimeBootstrapError("ChainStrap ZIP expands beyond per-archive safety cap")
            vetted.append(info)
        return vetted


def _crc32_file(path: Path) -> int:
    return transport._crc32_file(path)  # audited transport helper; reads only the target file


def extract_preflighted_archive(archive_path: Path, datadir: Path,
                                vetted: list[zipfile.ZipInfo], *,
                                existing_uncompressed: int) -> list[Path]:
    archive_total = sum(info.file_size for info in vetted)
    if existing_uncompressed + archive_total > MAX_TOTAL_EXTRACTED_BYTES:
        raise RuntimeBootstrapError("ChainStrap raw block set exceeds total extraction cap")
    log(
        f"  preflight accepted {len(vetted)} raw block member(s), "
        f"{transport.format_bytes(archive_total)} uncompressed"
    )
    extracted = []
    with zipfile.ZipFile(archive_path) as archive:
        progress_step = max(1, len(vetted) // 4)
        completed = 0
        for position, info in enumerate(vetted, 1):
            name = info.filename
            target = datadir / "blocks" / PurePosixPath(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise RuntimeBootstrapError(
                    f"preflight/extraction race or duplicate target detected: {target.name}")
            temporary = target.with_name(target.name + ".chainstrap.part")
            temporary.unlink(missing_ok=True)
            try:
                with archive.open(info, "r") as source, temporary.open("xb") as output:
                    shutil.copyfileobj(source, output, length=transport.CHUNK)
                    output.flush()
                    os.fsync(output.fileno())
                if temporary.stat().st_size != info.file_size or \
                        _crc32_file(temporary) != info.CRC:
                    raise RuntimeBootstrapError(f"ZIP integrity check failed for {name}")
                os.replace(temporary, target)
            except BaseException:
                temporary.unlink(missing_ok=True)
                raise
            extracted.append(target)
            completed += info.file_size
            if position == len(vetted) or position % progress_step == 0:
                log(
                    f"    extraction {position}/{len(vetted)} files | "
                    f"{transport.format_bytes(completed)} / {transport.format_bytes(archive_total)}"
                )
    return extracted


def _block_bytes(datadir: Path) -> int:
    total = 0
    for path in (datadir / "blocks").glob("blk*.dat"):
        if not path.is_file() or path.is_symlink():
            raise RuntimeBootstrapError(f"unsafe existing block path {path}")
        total += path.stat().st_size
        if total > MAX_TOTAL_EXTRACTED_BYTES:
            raise RuntimeBootstrapError("existing raw block set exceeds total extraction cap")
    return total


def _resolve(args, *, opener: Callable = urlopen) -> tuple[dict, dict]:
    if args.chainstrap_manifest is not None:
        raw, manifest = load_reviewed_manifest(args.chainstrap_manifest)
        digest = hashlib.sha256(raw).hexdigest()
        return manifest, marker_payload(manifest, digest, resolution_mode="reviewed-local")

    if args.chainstrap_commit is not None:
        commit = args.chainstrap_commit
        raw, payload = fetch_manifest_at_commit(commit, opener=opener)
        manifest = sanitize_upstream_manifest(
            payload, commit=commit, require_fresh=False)
        digest = hashlib.sha256(raw).hexdigest()
        return manifest, marker_payload(manifest, digest, resolution_mode="exact-commit")

    commit = resolve_master_commit(opener=opener)
    raw, payload = fetch_manifest_at_commit(commit, opener=opener)
    manifest = sanitize_upstream_manifest(payload, commit=commit, require_fresh=True)
    digest = hashlib.sha256(raw).hexdigest()
    return manifest, marker_payload(manifest, digest, resolution_mode="runtime-master")


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--chainstrap-commit",
        help="explicit full official upstream commit; bypasses mutable master resolution")
    source.add_argument(
        "--chainstrap-manifest", type=Path,
        help="explicit reviewed local sanitized manifest; no upstream metadata fetch")
    parser.add_argument("--datadir", default=Path("/var/lib/ravencoin"), type=Path)
    parser.add_argument("--gateway", help="use one release-allowlisted HTTPS IPFS gateway only")
    parser.add_argument("--skip-disk-check", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.chainstrap_commit is not None and COMMIT_RE.fullmatch(args.chainstrap_commit) is None:
        parser.error("--chainstrap-commit must be 40 lowercase hex characters")
    return args


def main(argv: Optional[list[str]] = None, *, opener: Callable = urlopen) -> int:
    args = parse_args(argv)
    args.datadir.mkdir(parents=True, exist_ok=True)

    # Once raw blocks are staged (and especially once Core validated them), a
    # later upstream update must not trigger any resolution or rebootstrap.
    if _completed_or_staged(args.datadir):
        return 0

    lock_path = args.datadir / RESOLUTION_LOCK
    progress_path = args.datadir / PROGRESS_MARKER
    if lock_path.exists():
        manifest, marker = _load_resolution_lock(lock_path)
        log(
            "Resuming the exact previously resolved ChainStrap snapshot; "
            "mutable upstream master is intentionally not consulted."
        )
    else:
        if progress_path.exists():
            raise RuntimeBootstrapError("bootstrap progress exists without its exact resolution lock")
        if _datadir_has_unclaimed_payload(args.datadir):
            raise RuntimeBootstrapError(
                "Ravencoin data directory is not empty and has no trusted ChainStrap markers; "
                "fast bootstrap requires a fresh data volume")
        manifest, marker = _resolve(args, opener=opener)
        transport.write_json_atomic(lock_path, _resolution_lock_payload(manifest, marker))

    if progress_path.exists():
        progress = _validate_progress(
            _read_json_file(progress_path, "ChainStrap progress marker", max_bytes=MAX_METADATA_BYTES),
            marker, manifest)
    else:
        progress = _new_progress(marker)

    _discard_interrupted_active_part(args.datadir, progress)
    _write_progress(progress_path, progress)

    if not args.skip_disk_check and not progress["completed_cids"]:
        transport.check_disk_space(args.datadir, manifest)

    completed_cids = set(progress["completed_cids"])
    completed_bytes = sum(
        part["bytes"] for part in manifest["parts"] if part["cid"] in completed_cids)
    total_parts = len(manifest["parts"])
    total_bytes = manifest["bytes"]
    source = manifest["source"]
    free_space = shutil.disk_usage(args.datadir).free

    log("Fast Verified Bootstrap for Ravencoin mainnet")
    log(f"  Resolution mode:  {marker['resolution_mode']}")
    log(f"  ChainStrap source: {source['repository']}@{source['commit']}")
    log(f"  Metadata SHA-256: {marker['metadata_sha256']}")
    log(f"  Snapshot height:  {manifest['blocks']}")
    log(f"  Snapshot hash:    {manifest['blockhash']}")
    log(
        f"  Release floor:    {RELEASE_FLOOR_HEIGHT}:{RELEASE_FLOOR_BLOCKHASH}")
    log(f"  Download size:    {transport.format_bytes(total_bytes)} across {total_parts} parts")
    log(f"  Free space:       {transport.format_bytes(free_space)}")
    log("  Upstream baseurl/ipfs_hashes: ignored; release gateway allowlist is authoritative")
    log("  Trust model:      raw blk*.dat only; Core performs a full offline local reindex")

    cache_dir = args.datadir / ".chainstrap-cache"
    cache_dir.mkdir(mode=0o700, exist_ok=True)
    pool = transport.GatewayPool(args.gateway)
    started = time.monotonic()
    total_extracted = 0
    try:
        for index, part in enumerate(manifest["parts"], 1):
            cid = part["cid"]
            log("")
            log(
                f"Part {index}/{total_parts} | {transport.format_bytes(part['bytes'])} compressed | "
                f"CID {cid}")
            if cid in completed_cids:
                log(
                    "  already accepted under this exact resolution | "
                    f"snapshot {100.0 * completed_bytes / total_bytes:.1f}%")
                continue

            archive = transport.download_verified(
                part, cache_dir, args.gateway, completed_bytes, total_bytes,
                gateway_pool=pool)
            claimed = {path.name for path in (args.datadir / "blocks").glob("blk*.dat")}
            vetted = preflight_archive(archive, already_claimed=claimed)
            progress["active_cid"] = cid
            progress["active_block_names"] = [PurePosixPath(info.filename).name for info in vetted]
            _write_progress(progress_path, progress)

            existing_bytes = _block_bytes(args.datadir)
            files = extract_preflighted_archive(
                archive, args.datadir, vetted, existing_uncompressed=existing_bytes)
            total_extracted += len(files)
            archive.unlink()
            completed_cids.add(cid)
            completed_bytes += part["bytes"]
            progress["completed_cids"] = [
                item["cid"] for item in manifest["parts"] if item["cid"] in completed_cids]
            progress["active_cid"] = None
            progress["active_block_names"] = []
            _write_progress(progress_path, progress)
            log(
                f"  part accepted: {len(files)} raw block file(s) | "
                f"snapshot {100.0 * completed_bytes / total_bytes:.1f}% ({index}/{total_parts})")

        block_files = transport.validate_contiguous_blocks(args.datadir)
        _block_bytes(args.datadir)
        transport.write_json_atomic(args.datadir / BLOCKS_MARKER, marker)
        progress_path.unlink(missing_ok=True)
        lock_path.unlink(missing_ok=True)
    finally:
        try:
            cache_dir.rmdir()
        except OSError:
            pass

    log("")
    log(
        f"ChainStrap stage complete: {len(block_files)} contiguous raw block files "
        f"({total_extracted} newly extracted entries) in "
        f"{transport.format_duration(time.monotonic() - started)}.")
    log(
        "Next phase: pinned Ravencoin Core 4.8.0 performs the mandatory network-isolated "
        "full reindex and release-floor ancestry check.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeBootstrapError, ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
        print(f"chainstrap-bootstrap: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
