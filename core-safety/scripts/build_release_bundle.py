#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Build the deterministic source bundle consumed by the single-file installer.

The bundle is not trusted by itself. Its SHA-256 becomes ``artifactDigest`` in
our separately signed ElectrumX release manifest. The optional Node Monitor is
vendored at one exact reviewed commit. For the 1.13.3 trust-root migration the
builder may replace only the bundled copy of the release/update public key; the
tracked repository trust-root file remains untouched and historical.

``release-provenance.json`` is synthetic reviewed evidence. Its exact bytes are
included in the bundle and their SHA-256 is independently signed in manifest v2.
The generated standalone installer is injected byte-for-byte into the bundle so
there is no second, stale installer implementation in the shipped artifact.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import pathlib
import re
import stat
import subprocess
import tarfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
PIN_FILE = ROOT / "release" / "install-sources.json"
UPDATE_KEY_PATH = "core-safety/production/update-signing-public-key.hex"
INSTALLER_PATH = "electrumx-ravencoin-install.py"
PROVENANCE_PATH = "release-provenance.json"
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_PROVENANCE_BYTES = 256 * 1024
RAW_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class BundleError(RuntimeError):
    pass


def run_git(cwd: pathlib.Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise BundleError(
            f"git {' '.join(args)} failed in {cwd}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def tracked_files(cwd: pathlib.Path) -> list[pathlib.Path]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=cwd, check=False, capture_output=True)
    if raw.returncode != 0:
        raise BundleError(f"git ls-files failed in {cwd}")
    result = []
    for item in raw.stdout.split(b"\0"):
        if not item:
            continue
        relative = pathlib.Path(os.fsdecode(item))
        source = cwd / relative
        if source.is_symlink():
            raise BundleError(f"refusing tracked symlink in release bundle: {relative}")
        if not source.is_file():
            raise BundleError(f"tracked path is not a regular file: {relative}")
        result.append(relative)
    return sorted(result, key=lambda path: path.as_posix())


def normalized_mode(path: pathlib.Path) -> int:
    mode = path.stat().st_mode
    return 0o755 if mode & stat.S_IXUSR else 0o644


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes, mode: int = 0o644) -> None:
    if len(data) > MAX_FILE_BYTES:
        raise BundleError(f"release-bundle file is too large: {name}")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.type = tarfile.REGTYPE
    import io
    archive.addfile(info, io.BytesIO(data))


def load_pin() -> dict:
    try:
        payload = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BundleError(f"cannot read {PIN_FILE}: {exc}") from exc
    monitor = payload.get("nodeMonitor") or {}
    if payload.get("schemaVersion") != 1:
        raise BundleError("unsupported install-sources schema")
    if monitor.get("repository") != "ALENOC/ravencoin-node-monitor":
        raise BundleError("Node Monitor repository pin is not the approved repository")
    commit = monitor.get("commit")
    if not isinstance(commit, str) or len(commit) != 40 or \
            any(char not in "0123456789abcdef" for char in commit):
        raise BundleError("Node Monitor commit pin is malformed")
    if monitor.get("bundledPath") != "vendor/ravencoin-node-monitor":
        raise BundleError("unexpected Node Monitor bundle path")
    return payload


def build_bundle(*, monitor_dir: pathlib.Path, output: pathlib.Path,
                 version: str, update_public_key_hex: str | None = None,
                 provenance_bytes: bytes | None = None,
                 installer_bytes: bytes | None = None) -> tuple[str, dict]:
    pin = load_pin()
    monitor = pin["nodeMonitor"]
    if update_public_key_hex is not None and not RAW_KEY_RE.fullmatch(update_public_key_hex):
        raise BundleError("replacement update public key is malformed")
    if provenance_bytes is None or not isinstance(provenance_bytes, bytes) or \
            not provenance_bytes or len(provenance_bytes) > MAX_PROVENANCE_BYTES:
        raise BundleError("release provenance bytes are missing or exceed the limit")
    if installer_bytes is None or not isinstance(installer_bytes, bytes) or not installer_bytes:
        raise BundleError("rendered standalone installer bytes are required")
    if len(installer_bytes) > MAX_FILE_BYTES:
        raise BundleError("rendered standalone installer exceeds bundle file limit")

    repo_head = run_git(ROOT, "rev-parse", "HEAD")
    monitor_head = run_git(monitor_dir, "rev-parse", "HEAD")
    if monitor_head != monitor["commit"]:
        raise BundleError(
            f"Node Monitor checkout is {monitor_head}, expected {monitor['commit']}")
    if run_git(ROOT, "status", "--porcelain", "--untracked-files=no"):
        raise BundleError("ElectrumX tracked worktree is dirty")
    if run_git(monitor_dir, "status", "--porcelain", "--untracked-files=no"):
        raise BundleError("Node Monitor tracked worktree is dirty")

    metadata = {
        "schemaVersion": 1,
        "electrumxVersion": version,
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": repo_head,
        "nodeMonitor": dict(monitor),
    }
    metadata_bytes = (json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")

    repo_files = tracked_files(ROOT)
    monitor_files = tracked_files(monitor_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")

    with temporary.open("wb") as raw:
        with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
                for relative in repo_files:
                    relative_name = relative.as_posix()
                    if relative_name in ("release-install-metadata.json", PROVENANCE_PATH):
                        raise BundleError(
                            f"tracked file shadows generated release evidence: {relative_name}")
                    source = ROOT / relative
                    data = source.read_bytes()
                    if relative_name == UPDATE_KEY_PATH and update_public_key_hex is not None:
                        data = (update_public_key_hex + "\n").encode("ascii")
                    elif relative_name == INSTALLER_PATH:
                        data = installer_bytes
                    add_bytes(
                        archive, relative_name, data,
                        mode=normalized_mode(source))

                prefix = monitor["bundledPath"].rstrip("/")
                for relative in monitor_files:
                    source = monitor_dir / relative
                    add_bytes(
                        archive, f"{prefix}/{relative.as_posix()}",
                        source.read_bytes(), mode=normalized_mode(source))

                add_bytes(archive, PROVENANCE_PATH, provenance_bytes, mode=0o644)
                add_bytes(
                    archive, "release-install-metadata.json", metadata_bytes,
                    mode=0o644)

    temporary.replace(output)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    return digest, metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--update-public-key-hex")
    parser.add_argument("--provenance", required=True, type=pathlib.Path)
    parser.add_argument("--installer", required=True, type=pathlib.Path)
    args = parser.parse_args()

    digest, metadata = build_bundle(
        monitor_dir=args.monitor_dir.resolve(), output=args.output.resolve(),
        version=args.version, update_public_key_hex=args.update_public_key_hex,
        provenance_bytes=args.provenance.read_bytes(),
        installer_bytes=args.installer.read_bytes())
    print(f"bundle={args.output}")
    print(f"sha256={digest}")
    print(f"sourceCommit={metadata['sourceCommit']}")
    print(f"nodeMonitorCommit={metadata['nodeMonitor']['commit']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
