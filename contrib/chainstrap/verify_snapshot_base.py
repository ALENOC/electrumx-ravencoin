#!/usr/bin/env python3
"""Independently verify a ChainStrap RVN snapshot and emit reviewed floor evidence.

This tool performs no signing and does not trust upstream gateway/baseurl policy.
It consumes an exact upstream manifest plus locally staged part archives, verifies
part byte counts/SHA-256, strictly preflights every ZIP member, streams every raw
block member to EOF to exercise ZIP CRC/integrity, requires one contiguous global
blkNNNNN sequence starting at blk00000.dat, and only then writes a sanitized
reviewed floor manifest and evidence report.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

UPSTREAM_REPOSITORY = "chainstrap/chainstrap.github.io"
UPSTREAM_PATH = "RVN/RVN-mainnet.json"
MAX_METADATA_BYTES = 256 * 1024
MAX_PARTS = 64
MAX_PART_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_COMPRESSED = 80 * 1024 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 4096
MAX_BLOCK_FILE_BYTES = 128 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED = 4 * 1024 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 80 * 1024 * 1024 * 1024
CHUNK = 1024 * 1024
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
CID_RE = re.compile(r"^[A-Za-z0-9]{20,120}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCKHASH_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCK_RE = re.compile(r"^blocks/blk([0-9]{5,8})\.dat$")
RAW_FIELDS = frozenset({
    "chain", "mode", "blocks", "blockhash", "updated", "bytes", "parts",
    "ipfs_hashes", "baseurl",
})
REQUIRED_RAW_FIELDS = frozenset({
    "chain", "mode", "blocks", "blockhash", "updated", "bytes", "parts",
})
PART_FIELDS = frozenset({"cid", "bytes", "sha256"})


class VerificationError(RuntimeError):
    pass


def _strict_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise VerificationError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _load_json(path: Path) -> tuple[bytes, dict]:
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VerificationError(f"{path} must be a regular non-symlink file")
    if info.st_size < 1 or info.st_size > MAX_METADATA_BYTES:
        raise VerificationError("upstream manifest exceeds metadata size policy")
    raw = path.read_bytes()
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise VerificationError(f"invalid upstream manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise VerificationError("upstream manifest must be a JSON object")
    return raw, value


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise VerificationError("updated timestamp is required")
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise VerificationError("updated timestamp is not ISO-8601") from exc
    if parsed.tzinfo is None:
        raise VerificationError("updated timestamp must include a timezone")
    return value


def sanitize_manifest(raw_manifest: dict, source_commit: str) -> dict:
    if COMMIT_RE.fullmatch(source_commit) is None:
        raise VerificationError("source commit must be 40 lowercase hex characters")
    unknown = set(raw_manifest) - RAW_FIELDS
    missing = REQUIRED_RAW_FIELDS - set(raw_manifest)
    if unknown:
        raise VerificationError(f"upstream manifest has unknown fields: {sorted(unknown)}")
    if missing:
        raise VerificationError(f"upstream manifest is missing fields: {sorted(missing)}")
    if raw_manifest.get("chain") != "RVN" or raw_manifest.get("mode") != "mainnet":
        raise VerificationError("only RVN mainnet may be reviewed")
    height = raw_manifest.get("blocks")
    blockhash = raw_manifest.get("blockhash")
    if not isinstance(height, int) or isinstance(height, bool) or height <= 0:
        raise VerificationError("blocks must be a positive integer")
    if not isinstance(blockhash, str) or BLOCKHASH_RE.fullmatch(blockhash) is None:
        raise VerificationError("blockhash must be 64 lowercase hex characters")
    updated = _validate_timestamp(raw_manifest.get("updated"))
    parts = raw_manifest.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > MAX_PARTS:
        raise VerificationError("parts must contain between 1 and 64 entries")

    sanitized_parts = []
    total = 0
    seen = set()
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict) or set(part) != PART_FIELDS:
            raise VerificationError(f"part {index} has unexpected schema")
        cid = part.get("cid")
        size = part.get("bytes")
        digest = part.get("sha256")
        if not isinstance(cid, str) or CID_RE.fullmatch(cid) is None or cid in seen:
            raise VerificationError(f"part {index} has invalid/duplicate CID")
        seen.add(cid)
        if not isinstance(size, int) or isinstance(size, bool) or not 0 < size <= MAX_PART_BYTES:
            raise VerificationError(f"part {index} has invalid byte count")
        if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
            raise VerificationError(f"part {index} has invalid SHA-256")
        total += size
        sanitized_parts.append({"cid": cid, "bytes": size, "sha256": digest})
    if total > MAX_TOTAL_COMPRESSED or raw_manifest.get("bytes") != total:
        raise VerificationError("manifest total compressed bytes are invalid")

    return {
        "chain": "RVN",
        "mode": "mainnet",
        "blocks": height,
        "blockhash": blockhash,
        "updated": updated,
        "bytes": total,
        "source": {
            "repository": UPSTREAM_REPOSITORY,
            "commit": source_commit,
            "path": UPSTREAM_PATH,
        },
        "parts": sanitized_parts,
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_zip_member(info: zipfile.ZipInfo) -> bool:
    if info.is_dir():
        return False
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return kind in (0, stat.S_IFREG)


def verify_part(archive_path: Path, part: dict, claimed_indexes: set[int]) -> dict:
    info = archive_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VerificationError(f"archive {archive_path.name} must be a regular file")
    if info.st_size != part["bytes"]:
        raise VerificationError(f"archive {archive_path.name} byte count mismatch")
    digest = _sha256_file(archive_path)
    if digest != part["sha256"]:
        raise VerificationError(f"archive {archive_path.name} SHA-256 mismatch")

    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise VerificationError(f"archive {archive_path.name} is not a valid ZIP") from exc

    with archive:
        members = archive.infolist()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise VerificationError(f"archive {archive_path.name} member count violates policy")
        indexes = []
        member_names = set()
        raw_bytes = 0
        for member in members:
            name = member.filename
            if not name or "\\" in name or "\x00" in name:
                raise VerificationError(f"unsafe ZIP path {name!r}")
            posix = PurePosixPath(name)
            if posix.is_absolute() or any(item in ("", ".", "..") for item in posix.parts):
                raise VerificationError(f"unsafe ZIP path {name!r}")
            match = BLOCK_RE.fullmatch(name)
            if match is None:
                raise VerificationError(f"non-allowlisted ZIP member {name!r}")
            if name in member_names:
                raise VerificationError(f"duplicate ZIP member {name!r}")
            member_names.add(name)
            index = int(match.group(1))
            if index in claimed_indexes:
                raise VerificationError(f"duplicate block index across parts: {index}")
            if not _regular_zip_member(member) or (member.flag_bits & 0x1):
                raise VerificationError(f"unsafe ZIP member type {name!r}")
            if member.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise VerificationError(f"unsupported ZIP compression for {name!r}")
            if member.file_size < 0 or member.file_size > MAX_BLOCK_FILE_BYTES:
                raise VerificationError(f"raw block member exceeds size policy: {name!r}")
            raw_bytes += member.file_size
            if raw_bytes > MAX_ARCHIVE_UNCOMPRESSED:
                raise VerificationError("archive exceeds uncompressed safety cap")

            streamed = 0
            with archive.open(member, "r") as payload:
                while True:
                    chunk = payload.read(CHUNK)
                    if not chunk:
                        break
                    streamed += len(chunk)
                    if streamed > member.file_size:
                        raise VerificationError(f"member expanded past advertised size: {name!r}")
            if streamed != member.file_size:
                raise VerificationError(f"member size changed while reading: {name!r}")
            indexes.append(index)

    claimed_indexes.update(indexes)
    return {
        "cid": part["cid"],
        "bytes": part["bytes"],
        "sha256": digest,
        "memberCount": len(indexes),
        "rawBytes": raw_bytes,
        "firstBlock": min(indexes),
        "lastBlock": max(indexes),
    }


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def verify_snapshot(upstream_manifest: Path, source_commit: str, archives_dir: Path,
                    output_manifest: Path, evidence_report: Path) -> dict:
    raw, upstream = _load_json(upstream_manifest)
    floor = sanitize_manifest(upstream, source_commit)
    claimed: set[int] = set()
    part_evidence = []
    total_raw = 0
    for part in floor["parts"]:
        archive = archives_dir / f"{part['cid']}.zip"
        if not archive.exists():
            raise VerificationError(f"missing archive {archive}")
        evidence = verify_part(archive, part, claimed)
        part_evidence.append(evidence)
        total_raw += evidence["rawBytes"]
        if total_raw > MAX_TOTAL_UNCOMPRESSED:
            raise VerificationError("snapshot exceeds total uncompressed safety cap")

    if not claimed or min(claimed) != 0:
        raise VerificationError("snapshot block sequence does not start at blk00000.dat")
    expected = set(range(max(claimed) + 1))
    if claimed != expected:
        missing = sorted(expected - claimed)
        raise VerificationError(f"snapshot block sequence is not contiguous; first missing={missing[0]}")

    floor_bytes = (json.dumps(floor, indent=2, sort_keys=False) + "\n").encode("utf-8")
    floor_sha = hashlib.sha256(floor_bytes).hexdigest()
    evidence = {
        "schemaVersion": 1,
        "verification": "independent-local-payload",
        "sourceRepository": UPSTREAM_REPOSITORY,
        "sourceCommit": source_commit,
        "sourcePath": UPSTREAM_PATH,
        "upstreamManifestSha256": hashlib.sha256(raw).hexdigest(),
        "reviewedManifestSha256": floor_sha,
        "chain": "RVN",
        "mode": "mainnet",
        "height": floor["blocks"],
        "blockhash": floor["blockhash"],
        "partCount": len(part_evidence),
        "compressedBytes": floor["bytes"],
        "blockFileCount": len(claimed),
        "firstBlock": 0,
        "lastBlock": max(claimed),
        "rawBytes": total_raw,
        "parts": part_evidence,
    }
    _atomic_json(output_manifest, floor)
    _atomic_json(evidence_report, evidence)
    return evidence


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--upstream-manifest", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--archives-dir", required=True, type=Path)
    parser.add_argument("--output-manifest", required=True, type=Path)
    parser.add_argument("--evidence-report", required=True, type=Path)
    args = parser.parse_args()
    evidence = verify_snapshot(
        args.upstream_manifest.resolve(),
        args.source_commit,
        args.archives_dir.resolve(),
        args.output_manifest.resolve(),
        args.evidence_report.resolve(),
    )
    print(
        "VERIFIED RVN mainnet "
        f"height={evidence['height']} hash={evidence['blockhash']} "
        f"source={evidence['sourceCommit']} parts={evidence['partCount']} "
        f"blocks={evidence['blockFileCount']} "
        f"floor_sha256={evidence['reviewedManifestSha256']}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, OSError, zipfile.BadZipFile) as exc:
        print(f"verify-snapshot: REFUSED: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from exc
