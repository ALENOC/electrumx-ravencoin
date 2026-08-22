#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Build a NON-PRODUCTION release-validation bundle from the real source tree.

This helper exists only so the single-file installer can be exercised end to
end before the production ElectrumX release-signing ceremony is complete. It
uses the same release shape as production: the complete git-tracked ElectrumX
source tree plus the complete pinned Node Monitor source tree.

Two fresh Ed25519 trust roots are generated in memory for every run:

* an ephemeral ElectrumX release-manifest key; and
* an ephemeral safe-Core-policy key.

Only the two public keys are written to the validation directory. Private keys
are never written to disk, committed, logged or reused. The generated bundle
is explicitly NON-PRODUCTION and the normal installer path never trusts these
keys.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import policy  # noqa: E402
import update_manifest as um  # noqa: E402

INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"
ELECTRUMX_INIT = ROOT / "electrumx" / "__init__.py"
PIN_FILE = ROOT / "release" / "install-sources.json"
VALIDATION_POLICY_BODY = (
    ROOT / "core-safety" / "production" / "safe-core-policy-v3.unsigned.json"
)

UPDATE_PUBLIC_KEY_BUNDLE_PATH = "core-safety/production/update-signing-public-key.hex"
CORE_POLICY_BUNDLE_PATH = "core-safety/production/safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY_BUNDLE_PATH = (
    "core-safety/production/core-policy-signing-public-key.hex"
)
BUNDLE_METADATA_NAME = "release-install-metadata.json"
PROVENANCE_BUNDLE_PATH = "release-provenance.json"
MONITOR_BUNDLE_PATH = "vendor/ravencoin-node-monitor"
LOCAL_CORE_POLICY_PUBLIC_KEY_FILE = "core-policy-public-key.hex"
LOCAL_ARTIFACT_REVISION = 0
MAX_FILE_BYTES = 256 * 1024 * 1024


class ValidationBundleError(RuntimeError):
    pass


def run_git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ValidationBundleError(
            f"git {' '.join(args)} failed in {cwd}: {completed.stderr.strip()}")
    return completed.stdout.strip()


def git_head(repo: Path) -> str:
    return run_git(repo, "rev-parse", "HEAD")


def tracked_files(cwd: Path) -> list[Path]:
    raw = subprocess.run(
        ["git", "ls-files", "-z"], cwd=cwd, check=False, capture_output=True)
    if raw.returncode != 0:
        raise ValidationBundleError(f"git ls-files failed in {cwd}")
    result = []
    for item in raw.stdout.split(b"\0"):
        if not item:
            continue
        relative = Path(os.fsdecode(item))
        source = cwd / relative
        if source.is_symlink():
            raise ValidationBundleError(
                f"refusing tracked symlink in validation bundle: {relative}")
        if not source.is_file():
            raise ValidationBundleError(
                f"tracked path is not a regular file: {relative}")
        result.append(relative)
    return sorted(result, key=lambda path: path.as_posix())


def normalized_mode(path: Path) -> int:
    return 0o755 if path.stat().st_mode & stat.S_IXUSR else 0o644


def add_bytes(archive: tarfile.TarFile, name: str, data: bytes,
              *, mode: int = 0o644) -> None:
    if len(data) > MAX_FILE_BYTES:
        raise ValidationBundleError(f"validation-bundle file is too large: {name}")
    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    info.mode = mode
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    info.mtime = 0
    info.type = tarfile.REGTYPE
    archive.addfile(info, io.BytesIO(data))


def electrumx_version() -> str:
    text = ELECTRUMX_INIT.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("version = "):
            return line.split("ElectrumX-RVN ")[1].strip().strip("'")
    raise ValidationBundleError("could not determine ElectrumX version")


def core_commit_and_version() -> tuple[str, str]:
    text = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    commit = version = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("RAVENCOIN_SOURCE_COMMIT:") and commit is None:
            commit = stripped.split(":", 1)[1].strip()
        if stripped.startswith("RAVENCOIN_VERSION:") and version is None:
            version = stripped.split(":", 1)[1].strip()
    if not commit or not version:
        raise ValidationBundleError(
            "could not read RAVENCOIN_SOURCE_COMMIT/RAVENCOIN_VERSION from compose.yaml")
    return commit, version


def load_monitor_pin() -> dict:
    try:
        payload = json.loads(PIN_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationBundleError(f"cannot read {PIN_FILE}: {exc}") from exc
    monitor = payload.get("nodeMonitor") or {}
    if payload.get("schemaVersion") != 1 or \
            monitor.get("repository") != "ALENOC/ravencoin-node-monitor" or \
            monitor.get("bundledPath") != MONITOR_BUNDLE_PATH:
        raise ValidationBundleError("install-sources Node Monitor pin is malformed")
    commit = monitor.get("commit")
    if not isinstance(commit, str) or len(commit) != 40 or \
            any(char not in "0123456789abcdef" for char in commit):
        raise ValidationBundleError("Node Monitor commit pin is malformed")
    return monitor


def load_validation_policy_body(core_commit: str, core_version: str) -> tuple[dict, dict]:
    """Load the reviewed RavenProject-only v3 candidate used for local E2E.

    The file is deliberately unsigned in the repository. For local validation
    only, this helper signs that exact body with a fresh ephemeral policy key.
    Production continues to require the real, ceremonied policy signature.
    """
    try:
        body = json.loads(VALIDATION_POLICY_BODY.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationBundleError(
            f"cannot read local validation policy body: {exc}") from exc
    try:
        policy.validate_body(body)
    except policy.PolicyError as exc:
        raise ValidationBundleError(f"local validation policy body is invalid: {exc}") from exc

    matches = [
        entry for entry in body.get("releases", [])
        if entry.get("repository") == "RavenProject/Ravencoin"
        and entry.get("commit") == core_commit
    ]
    if len(matches) != 1:
        raise ValidationBundleError(
            "local validation policy must contain exactly one entry for the bundled Core commit")
    entry = matches[0]
    if entry.get("status") != "KNOWN_SAFE" or entry.get("version") != core_version or \
            (entry.get("certification") or {}).get("result") != "PASS":
        raise ValidationBundleError(
            "local validation Core entry is not the passing KNOWN_SAFE RavenProject release")
    digest = entry.get("reportDigest")
    if not isinstance(digest, str) or len(digest) != 64 or \
            any(char not in "0123456789abcdef" for char in digest):
        raise ValidationBundleError("local validation certification report digest is malformed")
    return body, entry


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_bundle(*, monitor_repo: Path, update_public_key_hex: str,
                 core_policy_document: dict, core_policy_public_key_hex: str,
                 ex_version: str) -> tuple[bytes, dict]:
    monitor_pin = load_monitor_pin()
    repo_head = git_head(ROOT)
    monitor_head = git_head(monitor_repo)
    if monitor_head != monitor_pin["commit"]:
        raise ValidationBundleError(
            f"Node Monitor checkout is {monitor_head}, expected {monitor_pin['commit']}")

    # Bind bytes to the recorded commits. Untracked files are excluded by
    # git ls-files and therefore can never leak local secrets into the bundle.
    if run_git(ROOT, "status", "--porcelain", "--untracked-files=no"):
        raise ValidationBundleError("ElectrumX tracked worktree is dirty")
    if run_git(monitor_repo, "status", "--porcelain", "--untracked-files=no"):
        raise ValidationBundleError("Node Monitor tracked worktree is dirty")

    metadata = {
        "schemaVersion": 1,
        "electrumxVersion": ex_version,
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": repo_head,
        "nodeMonitor": dict(monitor_pin),
    }
    provenance = {
        "schemaVersion": 1,
        "purpose": "NON-PRODUCTION local release validation",
        "electrumxVersion": ex_version,
        "artifact_revision": LOCAL_ARTIFACT_REVISION,
        "sourceRepository": metadata["sourceRepository"],
        "sourceCommit": repo_head,
        "nodeMonitor": dict(monitor_pin),
        "updateSigningPublicKey": update_public_key_hex,
    }
    provenance_bytes = (
        json.dumps(provenance, indent=2, sort_keys=True) + "\n").encode("utf-8")
    metadata["provenanceDigest"] = "sha256:" + sha256_hex(provenance_bytes)
    metadata_bytes = (
        json.dumps(metadata, indent=2, sort_keys=True) + "\n").encode("utf-8")
    overrides = {
        UPDATE_PUBLIC_KEY_BUNDLE_PATH: (update_public_key_hex + "\n").encode("utf-8"),
        CORE_POLICY_BUNDLE_PATH: (
            json.dumps(core_policy_document, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8"),
        CORE_POLICY_PUBLIC_KEY_BUNDLE_PATH: (
            core_policy_public_key_hex + "\n").encode("utf-8"),
    }

    repo_files = tracked_files(ROOT)
    monitor_files = tracked_files(monitor_repo)
    buffer = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=buffer, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for relative in repo_files:
                name = relative.as_posix()
                if name in (BUNDLE_METADATA_NAME, PROVENANCE_BUNDLE_PATH):
                    raise ValidationBundleError(
                        f"tracked file shadows generated release evidence: {name}")
                if name in overrides:
                    # Local validation intentionally substitutes only trust
                    # material; the source/code tree remains the exact commit.
                    continue
                source = ROOT / relative
                add_bytes(archive, name, source.read_bytes(), mode=normalized_mode(source))

            prefix = monitor_pin["bundledPath"].rstrip("/")
            for relative in monitor_files:
                source = monitor_repo / relative
                add_bytes(
                    archive, f"{prefix}/{relative.as_posix()}", source.read_bytes(),
                    mode=normalized_mode(source))

            for name, data in sorted(overrides.items()):
                add_bytes(archive, name, data)
            add_bytes(archive, PROVENANCE_BUNDLE_PATH, provenance_bytes)
            add_bytes(archive, BUNDLE_METADATA_NAME, metadata_bytes)

    return buffer.getvalue(), metadata


def write_private_mode(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--monitor-repo", type=Path, default=ROOT.parent / "ravencoin-node-monitor",
        help="path to the exact pinned, clean ravencoin-node-monitor checkout")
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help="output directory (default: a fresh tempdir outside the repository)")
    args = parser.parse_args()

    if not (args.monitor_repo / "Dockerfile").is_file():
        raise SystemExit(
            f"--monitor-repo {args.monitor_repo} has no Dockerfile; not a valid checkout")

    out_dir = args.out_dir or Path(
        tempfile.mkdtemp(prefix="electrumx-local-release-validation-"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir.chmod(0o700)

    release_private_key, release_public_bytes = um.generate_keypair()
    policy_private_key, policy_public_bytes = policy.generate_keypair()
    release_public_hex = release_public_bytes.hex()
    policy_public_hex = policy_public_bytes.hex()

    core_commit, core_version = core_commit_and_version()
    ex_version = electrumx_version()
    policy_body, core_entry = load_validation_policy_body(core_commit, core_version)
    policy_document = policy.sign_policy(
        policy_body, policy_private_key, key_id=policy.key_id_for(policy_public_bytes))

    bundle_bytes, metadata = build_bundle(
        monitor_repo=args.monitor_repo.resolve(),
        update_public_key_hex=release_public_hex,
        core_policy_document=policy_document,
        core_policy_public_key_hex=policy_public_hex,
        ex_version=ex_version,
    )

    installer_digest = sha256_hex(INSTALLER_PATH.read_bytes())
    body = um.build_manifest(
        electrumx_version=ex_version,
        artifact_revision=LOCAL_ARTIFACT_REVISION,
        channel="stable",
        artifact_digest="sha256:" + sha256_hex(bundle_bytes),
        provenance_digest=metadata["provenanceDigest"],
        architecture="linux/amd64,linux/arm64",
        core_version=core_version,
        core_repository="RavenProject/Ravencoin",
        core_tag=core_entry["tag"],
        core_commit=core_commit,
        certification_report_digest=core_entry["reportDigest"],
        safe_core_policy_version=policy_body["policyVersion"],
        required_updater_version="0.3.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + installer_digest,
    )
    manifest_document = um.sign_manifest(
        body, release_private_key, key_id=um.key_id_for(release_public_bytes))

    # Only public material and signed artifacts leave process memory. There is
    # deliberately no PRIVATE-KEY-* output file.
    write_private_mode(
        out_dir / "manifest.json",
        (json.dumps(manifest_document, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    write_private_mode(out_dir / "bundle.tar.gz", bundle_bytes)
    write_private_mode(
        out_dir / "public-key.hex", (release_public_hex + "\n").encode("utf-8"))
    write_private_mode(
        out_dir / LOCAL_CORE_POLICY_PUBLIC_KEY_FILE,
        (policy_public_hex + "\n").encode("utf-8"))

    print(f"NON-PRODUCTION local release validation bundle written to: {out_dir}")
    print(
        f"electrumxVersion={ex_version} coreVersion={core_version} "
        f"coreCommit={core_commit[:12]} policyVersion={policy_body['policyVersion']}")
    print(
        f"Node Monitor commit={metadata['nodeMonitor']['commit'][:12]} "
        f"(from {args.monitor_repo})")
    print("Both validation private keys existed only in this process and were not written to disk.")
    print()
    print("Run the installer against this directory, e.g.:")
    print("  python3 electrumx-ravencoin-install.py \\")
    print(f"      --local-release-validation-dir {out_dir} \\")
    print("      --install-dir /path/to/fresh/install")
    print()
    print(f"Destroy {out_dir} once validation is done; it contains only ephemeral public trust material.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
