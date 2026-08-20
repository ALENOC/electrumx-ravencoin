#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Build a NON-PRODUCTION, ephemeral-key-signed release bundle for validating
electrumx-ravencoin-install.py end to end, before any real signed release
exists.

This script never touches RELEASE_PUBLIC_KEY_HEX, never writes into the
repository, and generates a brand-new Ed25519 keypair every run. The private
key lives only under the output directory (default: a tempdir outside the
repository) and is never written to the repository or reused across runs.

Usage::

    python3 core-safety/scripts/build_local_release_validation_bundle.py \\
        --monitor-repo /path/to/ravencoin-node-monitor

Then run the installer against the produced directory::

    python3 electrumx-ravencoin-install.py \\
        --local-release-validation-dir <printed output dir> \\
        --install-dir /path/to/fresh/install
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import update_manifest as um  # noqa: E402

INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"
ELECTRUMX_INIT = ROOT / "electrumx" / "__init__.py"
SAFE_CORE_POLICY = ROOT / "core-safety" / "production" / "safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY = ROOT / "core-safety" / "production" / "core-policy-signing-public-key.hex"

UPDATE_PUBLIC_KEY_BUNDLE_PATH = "core-safety/production/update-signing-public-key.hex"
CORE_POLICY_BUNDLE_PATH = "core-safety/production/safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY_BUNDLE_PATH = "core-safety/production/core-policy-signing-public-key.hex"
BUNDLE_METADATA_NAME = "release-install-metadata.json"
MONITOR_BUNDLE_PATH = "vendor/ravencoin-node-monitor"


def electrumx_version() -> str:
    text = ELECTRUMX_INIT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version = "):
            return line.split("ElectrumX-RVN ")[1].strip().strip("'")
    raise SystemExit("could not determine ElectrumX version from electrumx/__init__.py")


def git_head(repo: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True).stdout.strip()


def core_commit_and_version() -> tuple[str, str]:
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    commit = version = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("RAVENCOIN_SOURCE_COMMIT:") and commit is None:
            commit = line.split(":", 1)[1].strip()
        if line.startswith("RAVENCOIN_VERSION:") and version is None:
            version = line.split(":", 1)[1].strip()
    if not commit or not version:
        raise SystemExit("could not read RAVENCOIN_SOURCE_COMMIT/RAVENCOIN_VERSION from compose.yaml")
    return commit, version


def certification_report_digest() -> str:
    policy = json.loads(SAFE_CORE_POLICY.read_text(encoding="utf-8"))
    releases = policy["policy"]["releases"]
    return releases[0]["reportDigest"]


def safe_core_policy_version() -> int:
    policy = json.loads(SAFE_CORE_POLICY.read_text(encoding="utf-8"))
    return policy["policy"]["policyVersion"]


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    archive.addfile(info, io.BytesIO(data))


def add_file(archive: tarfile.TarFile, arcname: str, path: Path) -> None:
    data = path.read_bytes()
    mode = 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644
    add_bytes(archive, arcname, data, mode=mode)


def build_bundle(monitor_repo: Path, update_public_key_hex: str,
                 core_commit: str, ex_version: str) -> tuple[bytes, dict]:
    monitor_commit = git_head(monitor_repo)
    metadata = {
        "schemaVersion": 1,
        "electrumxVersion": ex_version,
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": git_head(ROOT),
        "nodeMonitor": {
            "repository": "ALENOC/ravencoin-node-monitor",
            "commit": monitor_commit,
            "bundledPath": MONITOR_BUNDLE_PATH,
        },
    }

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in ("compose.yaml", "compose.chainstrap.yaml", "compose.monitor.yaml",
                    "compose.monitor-controller.yaml", "setup.sh", ".env.example",
                    "docker/core/bootstrap-reindex.sh"):
            add_file(archive, name, ROOT / name)

        add_bytes(archive, BUNDLE_METADATA_NAME,
                 json.dumps(metadata, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        add_bytes(archive, UPDATE_PUBLIC_KEY_BUNDLE_PATH,
                 (update_public_key_hex + "\n").encode("utf-8"))
        add_file(archive, CORE_POLICY_BUNDLE_PATH, SAFE_CORE_POLICY)
        add_file(archive, CORE_POLICY_PUBLIC_KEY_BUNDLE_PATH, CORE_POLICY_PUBLIC_KEY)

        for name in ("Dockerfile", ".env.example", "contrib/ravencoin-bandwidth-controller.py"):
            add_file(archive, f"{MONITOR_BUNDLE_PATH}/{name}", monitor_repo / name)

    return buffer.getvalue(), metadata


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-repo", type=Path,
                        default=ROOT.parent / "ravencoin-node-monitor",
                        help="path to a clean ravencoin-node-monitor checkout")
    parser.add_argument("--out-dir", type=Path, default=None,
                        help="output directory (default: a fresh tempdir outside the repo)")
    args = parser.parse_args()

    if not (args.monitor_repo / "Dockerfile").is_file():
        raise SystemExit(f"--monitor-repo {args.monitor_repo} has no Dockerfile; not a valid checkout")

    out_dir = args.out_dir or Path(tempfile.mkdtemp(prefix="electrumx-local-release-validation-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)

    private_key, public_bytes = um.generate_keypair()
    public_key_hex = public_bytes.hex()

    core_commit, core_version = core_commit_and_version()
    ex_version = electrumx_version()

    bundle_bytes, metadata = build_bundle(
        args.monitor_repo, public_key_hex, core_commit, ex_version)

    installer_digest = sha256_hex(INSTALLER_PATH.read_bytes())

    body = um.build_manifest(
        electrumx_version=ex_version,
        channel="stable",
        artifact_digest="sha256:" + sha256_hex(bundle_bytes),
        architecture="linux/amd64,linux/arm64",
        core_version=core_version,
        core_repository="RavenProject/Ravencoin",
        core_tag=f"v{core_version}",
        core_commit=core_commit,
        certification_report_digest=certification_report_digest(),
        safe_core_policy_version=safe_core_policy_version(),
        required_updater_version="0.3.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + installer_digest,
    )
    document = um.sign_manifest(body, private_key, key_id=um.key_id_for(public_bytes))

    (out_dir / "manifest.json").write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (out_dir / "manifest.json").chmod(0o600)
    (out_dir / "bundle.tar.gz").write_bytes(bundle_bytes)
    (out_dir / "bundle.tar.gz").chmod(0o600)
    (out_dir / "public-key.hex").write_text(public_key_hex + "\n", encoding="utf-8")
    (out_dir / "public-key.hex").chmod(0o600)

    private_key_path = out_dir / "PRIVATE-KEY-DESTROY-AFTER-USE.hex"
    private_key_path.write_text(
        private_key.private_bytes_raw().hex() + "\n", encoding="utf-8")
    private_key_path.chmod(0o600)

    print(f"NON-PRODUCTION local release validation bundle written to: {out_dir}")
    print(f"electrumxVersion={ex_version} coreVersion={core_version} coreCommit={core_commit[:12]}")
    print(f"Node Monitor commit={metadata['nodeMonitor']['commit'][:12]} (from {args.monitor_repo})")
    print()
    print("Run the installer against this directory, e.g.:")
    print(f"  python3 electrumx-ravencoin-install.py \\")
    print(f"      --local-release-validation-dir {out_dir} \\")
    print(f"      --install-dir /path/to/fresh/install")
    print()
    print(f"Destroy {out_dir} (including the ephemeral private key) once validation is done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
