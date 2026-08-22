#!/usr/bin/env python3
"""Bind the runtime ChainStrap resolver to the release-reviewed floor manifest."""
from __future__ import annotations

import hashlib
import os
import stat
import sys
import zipfile
from pathlib import Path

import chainstrap_runtime as runtime

DEFAULT_RELEASE_FLOOR = Path(
    os.environ.get(
        "CHAINSTRAP_RELEASE_FLOOR",
        "/opt/electrumx-ravencoin/bootstrap/release-floor.json",
    )
)


class FloorBindingError(RuntimeError):
    """The release-reviewed floor file disagrees with compiled runtime policy."""


def verify_floor_binding(path: Path = DEFAULT_RELEASE_FLOOR) -> tuple[dict, str]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise FloorBindingError(f"cannot stat release floor manifest {path}: {exc}") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise FloorBindingError("release floor manifest must be a regular non-symlink file")
    if info.st_size < 1 or info.st_size > runtime.MAX_METADATA_BYTES:
        raise FloorBindingError("release floor manifest exceeds metadata size policy")

    raw, floor = runtime.load_reviewed_manifest(path)
    if floor["blocks"] != runtime.RELEASE_FLOOR_HEIGHT:
        raise FloorBindingError(
            f"reviewed floor height {floor['blocks']} != compiled floor "
            f"{runtime.RELEASE_FLOOR_HEIGHT}"
        )
    if floor["blockhash"] != runtime.RELEASE_FLOOR_BLOCKHASH:
        raise FloorBindingError("reviewed floor block hash differs from compiled floor")

    digest = hashlib.sha256(raw).hexdigest()
    source = floor["source"]
    print(
        "Release floor manifest: "
        f"sha256={digest} "
        f"source={source['repository']}@{source['commit']} "
        f"height={floor['blocks']} hash={floor['blockhash']}",
        flush=True,
    )
    return floor, digest


def main(argv: list[str] | None = None) -> int:
    verify_floor_binding()
    return runtime.main(argv)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FloorBindingError,
        runtime.RuntimeBootstrapError,
        ValueError,
        RuntimeError,
        OSError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"chainstrap-bootstrap: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(1) from exc
