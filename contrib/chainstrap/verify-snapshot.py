#!/usr/bin/env python3
"""Independent ChainStrap verifier with raw-block-only mixed-content policy.

Current upstream RVN snapshots can contain derived Ravencoin datadir material
alongside raw block files.  This verifier authenticates each part archive by
size/SHA-256, ignores safe foreign members outside the security-sensitive
``blocks/`` namespace, streams every allowlisted raw block to EOF for ZIP
integrity, and retains the original contiguous global block-sequence gate.
No ignored member is opened, trusted or represented as verified block state.
"""
from __future__ import annotations

import stat
import zipfile
from pathlib import PurePosixPath

import verify_snapshot_base as _base
from verify_snapshot_base import *  # noqa: F401,F403 - compatibility surface


def __getattr__(name: str):
    try:
        return getattr(_base, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__():
    return sorted(set(globals()) | set(dir(_base)))


def _safe_foreign_member(member: zipfile.ZipInfo) -> bool:
    if member.is_dir():
        return True
    mode = (member.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return kind in (0, stat.S_IFREG) and not (member.flag_bits & 0x1)


def verify_part(archive_path, part: dict, claimed_indexes: set[int]) -> dict:
    info = archive_path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise VerificationError(f"archive {archive_path.name} must be a regular file")
    if info.st_size != part["bytes"]:
        raise VerificationError(f"archive {archive_path.name} byte count mismatch")
    digest = _base._sha256_file(archive_path)
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
            if name in member_names:
                raise VerificationError(f"duplicate ZIP member {name!r}")
            member_names.add(name)

            match = BLOCK_RE.fullmatch(name)
            if match is None:
                if name == "blocks" or name.startswith("blocks/"):
                    raise VerificationError(f"non-allowlisted ZIP member {name!r}")
                if not _safe_foreign_member(member):
                    raise VerificationError(f"unsafe ZIP member type {name!r}")
                continue

            index = int(match.group(1))
            if index in claimed_indexes or index in indexes:
                raise VerificationError(f"duplicate block index across parts: {index}")
            if not _base._regular_zip_member(member) or (member.flag_bits & 0x1):
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

    if not indexes:
        raise VerificationError(
            f"archive {archive_path.name} contains no allowlisted raw block members")
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


_base.verify_part = verify_part


def verify_snapshot(*args, **kwargs):
    _base.verify_part = verify_part
    return _base.verify_snapshot(*args, **kwargs)


def main() -> int:
    _base.verify_part = verify_part
    return _base.main()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (VerificationError, OSError, zipfile.BadZipFile) as exc:
        import os
        print(f"verify-snapshot: REFUSED: {exc}", file=os.sys.stderr)
        raise SystemExit(1) from exc
