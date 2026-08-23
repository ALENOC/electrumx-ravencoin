#!/usr/bin/env python3
"""Runtime ChainStrap policy shim for mixed-content upstream snapshots.

The reviewed resolver/downloader implementation lives in
``chainstrap_runtime_base``.  Current ChainStrap RVN archives may also contain
derived Ravencoin datadir material (for example ``assets/*.ldb`` or
``blocks/index/*.ldb``).  This module keeps the production trust boundary
unchanged: only allowlisted raw ``blocks/blk*.dat`` members are accepted for
extraction; safe foreign members are ignored and never written to the datadir,
regardless of where they sit inside the archive.

The split keeps the original reviewed implementation byte-for-byte available
while making the archive-selection policy explicit and independently testable.
"""
from __future__ import annotations

import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.request import urlopen

import chainstrap_runtime_base as _base
from chainstrap_runtime_base import *  # noqa: F401,F403 - compatibility surface


def __getattr__(name: str):
    """Preserve the complete legacy module surface, including private helpers.

    Existing regression tests and maintenance tooling intentionally exercise a
    few underscored helpers.  Star-import does not re-export those names, so
    delegate unknown attributes to the byte-for-byte reviewed base module.
    """
    try:
        return getattr(_base, name)
    except AttributeError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc


def __dir__():
    return sorted(set(globals()) | set(dir(_base)))


def _foreign_member_is_safe_to_ignore(info: zipfile.ZipInfo) -> bool:
    """Return True only for inert foreign members that will never be extracted."""
    if info.is_dir():
        return True
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return kind in (0, stat.S_IFREG) and not (info.flag_bits & 0x1)


def preflight_archive(archive_path: Path, *,
                      already_claimed: set[str]) -> list[zipfile.ZipInfo]:
    """Validate a ChainStrap ZIP and select raw block members only.

    Path safety is global.  Members are classified structurally: an
    allowlisted raw ``blocks/blk*.dat`` member is eligible for extraction, any
    other safe regular member is ignored without being decompressed or counted
    toward raw block extraction limits, and unsafe/special entries fail closed
    wherever they appear.  Ignored members are never written to disk.
    """
    try:
        archive = zipfile.ZipFile(archive_path)
    except zipfile.BadZipFile as exc:
        raise RuntimeBootstrapError(
            "downloaded ChainStrap part is not a valid ZIP archive") from exc

    with archive:
        members = archive.infolist()
        if not members or len(members) > MAX_ARCHIVE_MEMBERS:
            raise RuntimeBootstrapError(
                "ChainStrap ZIP member count is outside release policy")

        vetted = []
        seen_paths = set()
        indexes = set()
        total = 0
        ignored = 0

        for info in members:
            raw_name = info.filename
            if not raw_name or "\\" in raw_name or "\x00" in raw_name:
                raise RuntimeBootstrapError(
                    f"unsafe ChainStrap ZIP member path {raw_name!r}")
            path = PurePosixPath(raw_name)
            if path.is_absolute() or any(
                    part in ("", ".", "..") for part in path.parts):
                raise RuntimeBootstrapError(
                    f"unsafe ChainStrap ZIP member path {raw_name!r}")
            if raw_name in seen_paths:
                raise RuntimeBootstrapError(
                    f"duplicate ZIP member path in ChainStrap part: {raw_name}")
            seen_paths.add(raw_name)

            match = BLOCK_RE.fullmatch(raw_name)
            if match is None:
                # Classification is structural, not location based.  Upstream
                # snapshots also carry derived datadir material inside the
                # blocks namespace (for example blocks/index/*.ldb).  Such
                # members are inert here precisely because they are never
                # extracted: only allowlisted raw blocks/blk*.dat members are
                # ever written into the Ravencoin datadir.
                if not _foreign_member_is_safe_to_ignore(info):
                    raise RuntimeBootstrapError(
                        f"unsafe ChainStrap ZIP member type: {raw_name}")
                ignored += 1
                continue

            index = int(match.group(1))
            if index in indexes:
                raise RuntimeBootstrapError(
                    f"duplicate block number in ChainStrap ZIP: {index}")
            indexes.add(index)
            basename = path.name
            if basename in already_claimed:
                raise RuntimeBootstrapError(
                    f"duplicate block file across ChainStrap parts: {basename}")
            if not _base._zip_entry_type_is_regular(info) or (info.flag_bits & 0x1):
                raise RuntimeBootstrapError(
                    f"unsafe ChainStrap ZIP member type: {raw_name}")
            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                raise RuntimeBootstrapError(
                    f"unsupported ZIP compression for {raw_name}")
            if info.file_size < 0 or info.file_size > _base.transport.MAX_BLOCK_FILE_BYTES:
                raise RuntimeBootstrapError(
                    f"raw block file exceeds size policy: {raw_name}")
            total += info.file_size
            if total > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
                raise RuntimeBootstrapError(
                    "ChainStrap ZIP expands beyond per-archive safety cap")
            vetted.append(info)

        if not vetted:
            raise RuntimeBootstrapError(
                "ChainStrap ZIP contains no allowlisted raw block members")
        if ignored:
            # Bounded output: report only a count, never attacker/upstream-
            # controlled names one by one.
            log(
                f"  preflight ignored {ignored} foreign ZIP member(s); "
                "only raw blocks/blk*.dat members are eligible for extraction")
        return vetted


def _install_policy_override() -> None:
    # Functions defined in chainstrap_runtime_base resolve globals in that base
    # module.  Install the selector there so every path through base.main(),
    # including the production floor-binding entrypoint, uses this policy.
    _base.preflight_archive = preflight_archive


def main(argv: Optional[list[str]] = None, *, opener=urlopen) -> int:
    _install_policy_override()
    return _base.main(argv, opener=opener)


# Importers may call the other public helpers directly; install immediately so
# any subsequent call into base functions observes the same archive policy.
_install_policy_override()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeBootstrapError, ValueError, RuntimeError, OSError,
            zipfile.BadZipFile) as exc:
        import sys
        print(f"chainstrap-bootstrap: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
