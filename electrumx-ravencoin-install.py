#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Single-file bootstrap installer for ElectrumX-Ravencoin.

Recommended use:

    curl -fL -O https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py
    python3 electrumx-ravencoin-install.py

Never pipe the download directly into Python or a shell. This file is the
initial bootstrap trust anchor and should remain inspectable before execution.
After it starts, every release-controlled byte used for installation comes from
one SHA-256-pinned bundle whose digest is covered by a dedicated Ed25519 release
manifest signature.

Fresh bundled-Core installations use ChainStrap Fast Verified Bootstrap by
default. The operator may explicitly select traditional P2P synchronization.
The optional Ravencoin Node Monitor is vendored into the same signed bundle at
one exact reviewed commit; the installer never clones a mutable branch head.

This source-tree copy intentionally contains no production release public key.
The reviewed release packaging step injects the public key created by the
separate update-signing key ceremony. Without it this development installer
fails closed before making persistent changes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Sequence

VERSION = "0.2.0"
SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1\x00"
SIGNATURE_ALGORITHM = "ed25519"

REQUIRED_MANIFEST_FIELDS = (
    "electrumxVersion",
    "channel",
    "releaseTimestamp",
    "artifactDigest",
    "architecture",
    "coreVersion",
    "coreRepository",
    "coreTag",
    "coreCommit",
    "certificationReportDigest",
    "safeCorePolicyVersion",
    "requiredUpdaterVersion",
    "configCompatibility",
    "dbCompatibility",
    "rollbackSafe",
    "consensusImpact",
    "autoUpdateEligible",
    "installerFilename",
    "installerDigest",
)

# Populated only by the reviewed release-packaging job after the dedicated
# release/update signing-key ceremony. Never put a private key in this file.
RELEASE_PUBLIC_KEY_HEX = ""

REPO = "ALENOC/electrumx-ravencoin"
RELEASE_BASE = f"https://github.com/{REPO}/releases/latest/download"
MANIFEST_URL = f"{RELEASE_BASE}/release-manifest.json"
BUNDLE_FILENAME = "electrumx-ravencoin-bundle.tar.gz"
BUNDLE_URL = f"{RELEASE_BASE}/{BUNDLE_FILENAME}"
BUNDLE_METADATA = "release-install-metadata.json"

DEFAULT_INSTALL_DIR = "electrumx-ravencoin"
INSTALL_MARKER = ".electrumx-ravencoin-installed.json"
MONITOR_PATH = "vendor/ravencoin-node-monitor"
MONITOR_ENV = f"{MONITOR_PATH}/.env"
MONITOR_OVERLAY = "compose.monitor.yaml"
CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
BASE_COMPOSE = "compose.yaml"

SUPPORTED_ARCHITECTURES = ("amd64", "arm64")
MIN_PYTHON = (3, 9)
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_FILES = 8192
MAX_BUNDLE_FILE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_EXTRACTED_BYTES = 768 * 1024 * 1024
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_BUNDLE_PATHS = frozenset({
    BASE_COMPOSE,
    CHAINSTRAP_OVERLAY,
    MONITOR_OVERLAY,
    "setup.sh",
    ".env.example",
    "docker/core/bootstrap-reindex.sh",
    BUNDLE_METADATA,
    f"{MONITOR_PATH}/Dockerfile",
    f"{MONITOR_PATH}/.env.example",
})


class InstallError(RuntimeError):
    """Fatal fail-closed installation error."""


# ---------------------------------------------------------------------------
# CLI and host checks
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="electrumx-ravencoin-install.py",
        description="Verified bootstrap installer for ElectrumX-Ravencoin.")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--check-only", action="store_true",
                        help="verify host, manifest, installer and bundle; write nothing")
    parser.add_argument("--install-dir", default=DEFAULT_INSTALL_DIR,
                        help=f"fresh install destination (default: {DEFAULT_INSTALL_DIR})")
    parser.add_argument("--chainstrap", action="store_true",
                        help="Fast Verified Bootstrap (fresh-install default)")
    parser.add_argument("--p2p-bootstrap", action="store_true",
                        help="traditional Ravencoin P2P blockchain synchronization")
    parser.add_argument("--with-monitor", action="store_true",
                        help="install the bundled Ravencoin Node Monitor")
    parser.add_argument("--without-monitor", action="store_true",
                        help="do not install the bundled Ravencoin Node Monitor")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.chainstrap and args.p2p_bootstrap:
        parser.error("--chainstrap and --p2p-bootstrap are mutually exclusive")
    if args.with_monitor and args.without_monitor:
        parser.error("--with-monitor and --without-monitor are mutually exclusive")
    return args


def check_python_version(version_info=None) -> None:
    version_info = version_info if version_info is not None else sys.version_info
    if (version_info[0], version_info[1]) < MIN_PYTHON:
        raise InstallError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required")


def detect_architecture(machine: Optional[str] = None) -> str:
    value = (machine if machine is not None else platform.machine()).lower()
    if value in ("x86_64", "amd64"):
        return "amd64"
    if value in ("aarch64", "arm64"):
        return "arm64"
    raise InstallError(
        f"unsupported architecture {value!r}; supported: {SUPPORTED_ARCHITECTURES}")


def require_command(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise InstallError(f"required command {name!r} was not found on PATH")
    return path


def compose_command() -> list[str]:
    require_command("docker")
    result = subprocess.run(
        ["docker", "compose", "version"], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise InstallError("Docker Compose v2 is required")
    result = subprocess.run(
        ["docker", "info"], check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        raise InstallError("the current user cannot reach the Docker daemon")
    return ["docker", "compose"]


# ---------------------------------------------------------------------------
# Signed release manifest
# ---------------------------------------------------------------------------

def canonical_bytes(body: dict) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def key_id_for(public_bytes: bytes) -> str:
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def require_release_public_key(value: str = RELEASE_PUBLIC_KEY_HEX) -> bytes:
    value = value.strip()
    if not value:
        raise InstallError(
            "this development installer has no ceremonied production release public key")
    if not HEX_KEY_RE.fullmatch(value):
        raise InstallError("embedded release public key is malformed")
    return bytes.fromhex(value)


def _verify_ed25519(public_bytes: bytes, signature: bytes, message: bytes) -> None:
    """Use cryptography when installed, otherwise OpenSSL's Ed25519 verifier."""
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        openssl = shutil.which("openssl")
        if openssl is None:
            raise InstallError(
                "Ed25519 verification requires either Python cryptography or OpenSSL")
        # SubjectPublicKeyInfo DER for id-Ed25519 (OID 1.3.101.112) + raw key.
        public_der = bytes.fromhex("302a300506032b6570032100") + public_bytes
        with tempfile.TemporaryDirectory(prefix="electrumx-signature-") as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "public.der").write_bytes(public_der)
            (tmp_path / "message.bin").write_bytes(message)
            (tmp_path / "signature.bin").write_bytes(signature)
            completed = subprocess.run([
                openssl, "pkeyutl", "-verify", "-pubin",
                "-inkey", str(tmp_path / "public.der"), "-keyform", "DER",
                "-rawin", "-in", str(tmp_path / "message.bin"),
                "-sigfile", str(tmp_path / "signature.bin"),
            ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if completed.returncode != 0:
                raise InstallError("release manifest signature does not verify")
        return

    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
    except InvalidSignature as exc:
        raise InstallError("release manifest signature does not verify") from exc


def validate_manifest_body(body: dict) -> None:
    if not isinstance(body, dict):
        raise InstallError("release manifest body is not an object")
    if body.get("schemaVersion") != 1:
        raise InstallError("unsupported release manifest schema")
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in body:
            raise InstallError(f"release manifest missing {field!r}")
    if body.get("coreRepository") != "RavenProject/Ravencoin":
        raise InstallError("release manifest names a non-RavenProject Core source")
    if not COMMIT_RE.fullmatch(str(body.get("coreCommit", ""))):
        raise InstallError("release manifest Core commit is malformed")
    if body.get("channel") not in ("stable", "security"):
        raise InstallError("release manifest channel is not supported")
    if not isinstance(body.get("rollbackSafe"), bool):
        raise InstallError("rollbackSafe must be boolean")
    if not isinstance(body.get("consensusImpact"), bool):
        raise InstallError("consensusImpact must be boolean")
    if not isinstance(body.get("autoUpdateEligible"), bool):
        raise InstallError("autoUpdateEligible must be boolean")
    if body["consensusImpact"] and body["autoUpdateEligible"]:
        raise InstallError("consensus-changing release cannot be auto-update eligible")
    if body.get("installerFilename") != Path(__file__).name:
        raise InstallError("manifest installer filename does not match this file")
    if not SHA256_RE.fullmatch(str(body.get("installerDigest", ""))):
        raise InstallError("installerDigest is malformed")
    if not SHA256_RE.fullmatch(str(body.get("artifactDigest", ""))):
        raise InstallError("artifactDigest is malformed")


def verify_manifest_signature(document: dict, public_key_hex: str) -> dict:
    public_bytes = require_release_public_key(public_key_hex)
    if not isinstance(document, dict):
        raise InstallError("release manifest document is not an object")
    body = document.get("manifest")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        raise InstallError("release manifest lacks manifest/signature objects")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise InstallError("unsupported release signature algorithm")
    if signature.get("keyId") != key_id_for(public_bytes):
        raise InstallError("release manifest keyId does not match the pinned public key")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise InstallError("release manifest signature is not valid base64") from exc
    if len(raw_signature) != 64:
        raise InstallError("release manifest signature has the wrong length")
    _verify_ed25519(public_bytes, raw_signature, canonical_bytes(body))
    validate_manifest_body(body)
    return body


def fetch_bytes(url: str, *, max_bytes: int, timeout: int = 60) -> bytes:
    if not url.startswith("https://github.com/ALENOC/electrumx-ravencoin/releases/"):
        raise InstallError("refusing download outside the expected GitHub release namespace")
    request = urllib.request.Request(
        url, headers={"User-Agent": "electrumx-ravencoin-installer"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise InstallError(f"download exceeds {max_bytes} bytes")
                except ValueError as exc:
                    raise InstallError("invalid Content-Length") from exc
            chunks = []
            total = 0
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise InstallError(f"download exceeds {max_bytes} bytes")
                chunks.append(chunk)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InstallError(f"download failed: {exc}") from exc


def fetch_and_verify_release_manifest(
        *, public_key_hex: str = RELEASE_PUBLIC_KEY_HEX,
        manifest_url: str = MANIFEST_URL,
        fetch: Callable[[str], bytes] = None) -> dict:
    raw = (fetch(manifest_url) if fetch is not None
           else fetch_bytes(manifest_url, max_bytes=MAX_MANIFEST_BYTES))
    if len(raw) > MAX_MANIFEST_BYTES:
        raise InstallError("release manifest exceeds size limit")
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InstallError("release manifest is not valid UTF-8 JSON") from exc
    return verify_manifest_signature(document, public_key_hex)


def verify_digest(data: bytes, expected: str, what: str) -> None:
    match = SHA256_RE.fullmatch(expected or "")
    if match is None:
        raise InstallError(f"{what} digest format is invalid")
    observed = hashlib.sha256(data).hexdigest()
    if observed != match.group(1):
        raise InstallError(f"{what} SHA-256 mismatch")


def verify_running_installer(body: dict, installer_path: Optional[Path] = None) -> None:
    path = installer_path or Path(__file__).resolve()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InstallError(f"cannot read this installer for digest verification: {exc}") from exc
    verify_digest(data, body["installerDigest"], "installer")


def verify_architecture(body: dict, architecture: str) -> None:
    declared = body.get("architecture")
    values = []
    if isinstance(declared, str):
        values = [item.strip() for item in declared.split(",")]
    elif isinstance(declared, list):
        values = [str(item) for item in declared]
    accepted = set(values)
    if architecture not in accepted and f"linux/{architecture}" not in accepted:
        raise InstallError(
            f"release targets {declared!r}, but this host is {architecture!r}")


# ---------------------------------------------------------------------------
# Signed bundle validation / safe extraction
# ---------------------------------------------------------------------------

def _safe_member_name(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise InstallError(f"unsafe bundle path {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise InstallError(f"unsafe bundle path {name!r}")
    return path


def _validate_metadata(metadata: dict, body: dict) -> None:
    if not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1:
        raise InstallError("bundle installation metadata is malformed")
    if metadata.get("electrumxVersion") != body.get("electrumxVersion"):
        raise InstallError("bundle ElectrumX version disagrees with signed manifest")
    if metadata.get("sourceRepository") != REPO:
        raise InstallError("bundle source repository identity is unexpected")
    if not COMMIT_RE.fullmatch(str(metadata.get("sourceCommit", ""))):
        raise InstallError("bundle source commit is malformed")
    monitor = metadata.get("nodeMonitor")
    if not isinstance(monitor, dict):
        raise InstallError("bundle has no pinned Node Monitor identity")
    if monitor.get("repository") != "ALENOC/ravencoin-node-monitor":
        raise InstallError("bundle Node Monitor repository is unexpected")
    if not COMMIT_RE.fullmatch(str(monitor.get("commit", ""))):
        raise InstallError("bundle Node Monitor commit is malformed")
    if monitor.get("bundledPath") != MONITOR_PATH:
        raise InstallError("bundle Node Monitor path is unexpected")


def validate_bundle(data: bytes, body: dict) -> dict:
    verify_digest(data, body["artifactDigest"], "release bundle")
    if len(data) > MAX_BUNDLE_BYTES:
        raise InstallError("release bundle exceeds size limit")

    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
    except tarfile.TarError as exc:
        raise InstallError("release bundle is not a valid gzip tar archive") from exc

    with archive:
        members = archive.getmembers()
        if len(members) > MAX_BUNDLE_FILES:
            raise InstallError("release bundle contains too many entries")
        names = set()
        total = 0
        for member in members:
            path = _safe_member_name(member.name)
            normalized = path.as_posix()
            if normalized in names:
                raise InstallError(f"duplicate bundle path {normalized!r}")
            names.add(normalized)
            if not (member.isfile() or member.isdir()):
                raise InstallError(
                    f"bundle contains forbidden link/device/special entry {normalized!r}")
            if member.isfile():
                if member.size < 0 or member.size > MAX_BUNDLE_FILE_BYTES:
                    raise InstallError(f"bundle file {normalized!r} has unsafe size")
                total += member.size
                if total > MAX_BUNDLE_EXTRACTED_BYTES:
                    raise InstallError("release bundle expands beyond the size limit")

        missing = REQUIRED_BUNDLE_PATHS - names
        if missing:
            raise InstallError(
                "release bundle is incomplete: " + ", ".join(sorted(missing)))

        metadata_member = archive.getmember(BUNDLE_METADATA)
        handle = archive.extractfile(metadata_member)
        if handle is None:
            raise InstallError("cannot read bundle installation metadata")
        try:
            metadata = json.loads(handle.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InstallError("bundle installation metadata is invalid JSON") from exc
        _validate_metadata(metadata, body)

        # Bind the most important deployment identities/invariants directly to
        # the signed manifest before anything is written to disk.
        compose = archive.extractfile(archive.getmember(BASE_COMPOSE)).read().decode("utf-8")
        if f"RAVENCOIN_SOURCE_COMMIT: {body['coreCommit']}" not in compose:
            raise InstallError("bundle compose Core commit disagrees with signed manifest")
        if "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin" not in compose:
            raise InstallError("bundle compose does not enforce the RavenProject Core identity")
        if f"RAVENCOIN_VERSION: {body['coreVersion']}" not in compose:
            raise InstallError("bundle compose Core version disagrees with signed manifest")

        chainstrap = archive.extractfile(
            archive.getmember(CHAINSTRAP_OVERLAY)).read().decode("utf-8")
        reindex = archive.extractfile(
            archive.getmember("docker/core/bootstrap-reindex.sh")).read().decode("utf-8")
        if "network_mode: none" not in chainstrap:
            raise InstallError("ChainStrap validation lost Docker network isolation")
        if "-connect=0" not in reindex:
            raise InstallError("ChainStrap Core reindex lost explicit peer suppression")
        if "getbestblockhash" not in reindex or "getblockhash" not in reindex:
            raise InstallError("ChainStrap reindex lacks the exact-tip verification gate")

        return metadata


def extract_bundle(data: bytes, destination: Path) -> None:
    """Extract only regular files/directories after ``validate_bundle`` passed."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        for member in archive.getmembers():
            path = _safe_member_name(member.name)
            target = destination.joinpath(*path.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise InstallError(f"cannot extract {member.name!r}")
            with target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


def fetch_and_verify_bundle(body: dict, fetch: Callable[[str], bytes] = None) -> tuple[bytes, dict]:
    data = (fetch(BUNDLE_URL) if fetch is not None
            else fetch_bytes(BUNDLE_URL, max_bytes=MAX_BUNDLE_BYTES, timeout=180))
    metadata = validate_bundle(data, body)
    return data, metadata


# ---------------------------------------------------------------------------
# Installation choices / deployment
# ---------------------------------------------------------------------------

def choose_bootstrap(args, interactive: bool, prompt: Callable[[str], str] = input) -> str:
    if args.chainstrap:
        return "chainstrap"
    if args.p2p_bootstrap:
        return "p2p"
    if not interactive:
        return "chainstrap"
    answer = prompt(
        "Blockchain bootstrap method:\n"
        "  1. ChainStrap Fast Verified Bootstrap [default]\n"
        "  2. Traditional Ravencoin P2P synchronization\n"
        "Choice [1]: ").strip()
    if answer in ("", "1"):
        return "chainstrap"
    if answer == "2":
        return "p2p"
    raise InstallError(f"unrecognized bootstrap choice {answer!r}")


def choose_monitor(args, interactive: bool, prompt: Callable[[str], str] = input) -> bool:
    if args.with_monitor:
        return True
    if args.without_monitor:
        return False
    if not interactive:
        return True
    answer = prompt(
        "Install the pinned Ravencoin Node Monitor too? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    raise InstallError(f"unrecognized monitor choice {answer!r}")


def write_monitor_env(root: Path) -> None:
    path = root / MONITOR_ENV
    if path.exists():
        raise InstallError(f"refusing to overwrite existing monitor environment {path}")
    password = secrets.token_urlsafe(32)
    content = (
        "# Generated by the verified ElectrumX-Ravencoin installer.\n"
        "NODE_NAME=ElectrumX-Ravencoin bundled node\n"
        "BIND_HOST=0.0.0.0\n"
        "BIND_PORT=8899\n"
        "MONITOR_USER=monitor\n"
        f"MONITOR_PASSWORD={password}\n"
        "CORE_RPC_HOST=ravencoin-core\n"
        "CORE_RPC_PORT=8766\n"
        "CORE_RPC_USER_FILE=/run/raven-secrets/raven_rpc_user\n"
        "CORE_RPC_PASSWORD_FILE=/run/raven-secrets/raven_rpc_password\n"
        "ELECTRUMX_ENABLED=true\n"
        "ELECTRUMX_RPC_HOST=127.0.0.1\n"
        "ELECTRUMX_RPC_PORT=8000\n"
        "ELECTRUMX_SSL_HOST=127.0.0.1\n"
        "ELECTRUMX_SSL_PORT=50002\n"
        "ELECTRUMX_SSL_VERIFY=false\n"
        "HISTORY_ENABLED=true\n"
        "HISTORY_STORAGE=memory\n"
        "PRICE_FEED_ENABLED=true\n"
        "PRICE_FEED_SYMBOL=RVNUSDT\n"
        "PROMETHEUS_ENABLED=true\n"
        "MIN_SAFE_CORE_VERSION=4.8.0\n"
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def run_checked(argv: Sequence[str], *, cwd: Path) -> None:
    completed = subprocess.run(list(argv), cwd=cwd, check=False)
    if completed.returncode != 0:
        raise InstallError(
            f"command failed with exit code {completed.returncode}: {' '.join(argv)}")


def compose_files(bootstrap: str, monitor: bool) -> list[str]:
    files = [BASE_COMPOSE]
    if bootstrap == "chainstrap":
        files.append(CHAINSTRAP_OVERLAY)
    elif bootstrap != "p2p":
        raise InstallError(f"unknown bootstrap choice {bootstrap!r}")
    if monitor:
        files.append(MONITOR_OVERLAY)
    return files


def deploy(root: Path, *, bootstrap: str, monitor: bool) -> None:
    run_checked(["sh", "./setup.sh", "--bundled-core"], cwd=root)
    if monitor:
        write_monitor_env(root)

    files = compose_files(bootstrap, monitor)
    base = ["docker", "compose"]
    for filename in files:
        base += ["-f", filename]
    run_checked(base + ["config", "--quiet"], cwd=root)
    run_checked(base + ["up", "-d", "--build"], cwd=root)


def write_install_marker(root: Path, *, body: dict, metadata: dict,
                         bootstrap: str, monitor: bool) -> None:
    marker = {
        "schemaVersion": 1,
        "electrumxVersion": body["electrumxVersion"],
        "artifactDigest": body["artifactDigest"],
        "coreRepository": body["coreRepository"],
        "coreVersion": body["coreVersion"],
        "coreCommit": body["coreCommit"],
        "safeCorePolicyVersion": body["safeCorePolicyVersion"],
        "sourceCommit": metadata["sourceCommit"],
        "bootstrapChoice": bootstrap,
        "nodeMonitorEnabled": monitor,
        "nodeMonitorCommit": metadata["nodeMonitor"]["commit"] if monitor else None,
        "installerVersion": VERSION,
    }
    path = root / INSTALL_MARKER
    temporary = root / f"{INSTALL_MARKER}.new.{os.getpid()}"
    temporary.write_text(json.dumps(marker, indent=2, sort_keys=True) + "\n",
                         encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def install_fresh(target: Path, data: bytes, *, body: dict, metadata: dict,
                  bootstrap: str, monitor: bool) -> None:
    if target.exists():
        marker = target / INSTALL_MARKER
        if marker.is_file():
            raise InstallError(
                f"{target} is already installed; use electrumx-update check/status/show/apply")
        raise InstallError(f"refusing to overwrite existing path {target}")

    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".electrumx-ravencoin-install-", dir=parent))
    moved = False
    try:
        extract_bundle(data, staging)
        # Generate secrets/config in staging so an installation that fails
        # before activation leaves the requested destination untouched.
        run_checked(["sh", "./setup.sh", "--bundled-core"], cwd=staging)
        if monitor:
            write_monitor_env(staging)
        files = compose_files(bootstrap, monitor)
        base = ["docker", "compose"]
        for filename in files:
            base += ["-f", filename]
        run_checked(base + ["config", "--quiet"], cwd=staging)

        os.replace(staging, target)
        moved = True
        run_checked(base + ["up", "-d", "--build"], cwd=target)
        write_install_marker(
            target, body=body, metadata=metadata,
            bootstrap=bootstrap, monitor=monitor)
    finally:
        if not moved and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(VERSION)
        return 0

    try:
        check_python_version()
        architecture = detect_architecture()
        body = fetch_and_verify_release_manifest()
        verify_architecture(body, architecture)
        verify_running_installer(body)
        bundle, metadata = fetch_and_verify_bundle(body)

        print(
            f"verified ElectrumX {body['electrumxVersion']} release bundle; "
            f"official Core {body['coreVersion']} @ {body['coreCommit'][:12]}; "
            f"Node Monitor @ {metadata['nodeMonitor']['commit'][:12]}")

        if args.check_only:
            print("check-only complete: no persistent changes were made")
            return 0

        compose_command()
        interactive = sys.stdin.isatty()
        bootstrap = choose_bootstrap(args, interactive)
        monitor = choose_monitor(args, interactive)
        target = Path(args.install_dir).expanduser().resolve()
        install_fresh(
            target, bundle, body=body, metadata=metadata,
            bootstrap=bootstrap, monitor=monitor)

        print(f"installation complete in {target}")
        print(f"bootstrap: {bootstrap}")
        if monitor:
            print("Node Monitor: enabled at http://127.0.0.1:8899")
            print(f"Node Monitor credentials: {target / MONITOR_ENV}")
        print("updates are never applied by silence/restart: operator approval remains required")
        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
