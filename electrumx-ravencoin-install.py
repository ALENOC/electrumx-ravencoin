#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Single-file verified bootstrap installer for ElectrumX-Ravencoin.

Recommended use::

    curl -fL -O https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py
    python3 electrumx-ravencoin-install.py

Never pipe the download directly into Python or a shell. This file is the
initial bootstrap trust anchor and remains inspectable before execution. After
it starts, every release-controlled byte used for installation comes from one
SHA-256-pinned bundle whose digest is covered by the dedicated Ed25519 release
manifest signature.

Fresh bundled-Core installations use ChainStrap Fast Verified Bootstrap by
default. Traditional P2P synchronization is an explicit alternative. The
Ravencoin Node Monitor is offered by default and remains unprivileged. Its
optional root-owned host controller is a separate explicit opt-in security
domain; the monitor never receives the Docker socket or CAP_NET_ADMIN.

The Core certification policy has an independent, pinned Ed25519 trust root.
A release manifest is accepted only when its exact RavenProject Core commit,
policy version and certification report digest are present as KNOWN_SAFE in
that signed policy. The release signer therefore cannot substitute both a Core
policy and its key inside the release bundle.

This source-tree copy intentionally contains no production release public key.
The reviewed release packaging step injects the public key created by the
separate update-signing key ceremony. Without it this development installer
fails closed before making persistent changes.
"""

from __future__ import annotations

import argparse
import base64
import datetime
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
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Sequence

VERSION = "0.4.0"
SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1\x00"
CORE_POLICY_SIGNATURE_DOMAIN = b"ALENOC-RVN-CORE-POLICY-v1\x00"
SIGNATURE_ALGORITHM = "ed25519"

REQUIRED_MANIFEST_FIELDS = (
    "electrumxVersion", "channel", "releaseTimestamp", "artifactDigest",
    "architecture", "coreVersion", "coreRepository", "coreTag", "coreCommit",
    "certificationReportDigest", "safeCorePolicyVersion",
    "requiredUpdaterVersion", "configCompatibility", "dbCompatibility",
    "rollbackSafe", "consensusImpact", "autoUpdateEligible",
    "installerFilename", "installerDigest",
)

# Injected only by the reviewed release-candidate packaging job after the
# dedicated release/update signing-key ceremony. Never place a private key here.
RELEASE_PUBLIC_KEY_HEX = ""

# Independent Core-policy trust root. This public key is already ceremonied and
# is intentionally pinned in the single-file bootstrap rather than learned from
# the release bundle. The bundle must contain the same key byte-for-byte.
PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX = (
    "9fc91edbe763513490248a23ae97575a6b963101b644e01493a3860b99e35648"
)

REPO = "ALENOC/electrumx-ravencoin"
RELEASE_BASE = f"https://github.com/{REPO}/releases/latest/download"
MANIFEST_URL = f"{RELEASE_BASE}/release-manifest.json"
BUNDLE_FILENAME = "electrumx-ravencoin-bundle.tar.gz"
BUNDLE_URL = f"{RELEASE_BASE}/{BUNDLE_FILENAME}"
BUNDLE_METADATA = "release-install-metadata.json"

DEFAULT_INSTALL_DIR = "electrumx-ravencoin"
INSTALL_MARKER = ".electrumx-ravencoin-installed.json"
COMPOSE_PROJECT_NAME = "electrumx-ravencoin"
MONITOR_PATH = "vendor/ravencoin-node-monitor"
MONITOR_ENV = f"{MONITOR_PATH}/.env"
MONITOR_OVERLAY = "compose.monitor.yaml"
MONITOR_CONTROLLER_OVERLAY = "compose.monitor-controller.yaml"
CONTROLLER_SCRIPT = f"{MONITOR_PATH}/contrib/ravencoin-bandwidth-controller.py"
MONITOR_PORT_VERIFY = f"{MONITOR_PATH}/contrib/verify-published-port.py"
NETWORK_CONFIG_HELPER = "core-safety/scripts/configure_monitor_admin_network.py"
CONTROLLER_UNIT = "electrumx-ravencoin-monitor-controller.service"
# The root systemd unit must never execute anything from the operator-owned
# install tree.  The controller script is copied, root-owned and unwritable by
# the installing user, into this fixed trusted location (GLM53-RVN-002).
TRUSTED_CONTROLLER_DIR = Path("/usr/local/lib/electrumx-ravencoin")
TRUSTED_CONTROLLER_PATH = TRUSTED_CONTROLLER_DIR / "ravencoin-bandwidth-controller.py"
CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
STORAGE_OVERLAY = "compose.storage.yaml"
BASE_COMPOSE = "compose.yaml"
STORAGE_ROOT_DIRNAME = "electrumx-ravencoin-storage"
STORAGE_SUBDIRS = ("ravencoin-data", "ravencoin-config", "electrumx-data", "monitor-data")
SAFE_STORAGE_PATH_RE = re.compile(r"^[A-Za-z0-9_./ +@-]+$")
UPDATE_PUBLIC_KEY_PATH = "core-safety/production/update-signing-public-key.hex"
CORE_POLICY_PATH = "core-safety/production/safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY_PATH = "core-safety/production/core-policy-signing-public-key.hex"

SUPPORTED_ARCHITECTURES = ("amd64", "arm64")
SUPPORTED_MANIFEST_ARCHITECTURES = ("linux/amd64", "linux/arm64")
MIN_PYTHON = (3, 9)
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BUNDLE_BYTES = 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 8192
MAX_BUNDLE_FILE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_EXTRACTED_BYTES = 768 * 1024 * 1024
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
HEX_KEY_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TAG_RE = re.compile(r"^[A-Za-z0-9._/-]{1,100}$")
VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?$")
SAFE_CONTROLLER_ROOT_RE = re.compile(r"^[A-Za-z0-9_./-]+$")

REQUIRED_BUNDLE_PATHS = frozenset({
    BASE_COMPOSE,
    STORAGE_OVERLAY,
    CHAINSTRAP_OVERLAY,
    MONITOR_OVERLAY,
    MONITOR_CONTROLLER_OVERLAY,
    "setup.sh",
    ".env.example",
    "docker/core/bootstrap-reindex.sh",
    BUNDLE_METADATA,
    UPDATE_PUBLIC_KEY_PATH,
    CORE_POLICY_PATH,
    CORE_POLICY_PUBLIC_KEY_PATH,
    f"{MONITOR_PATH}/Dockerfile",
    f"{MONITOR_PATH}/.env.example",
    CONTROLLER_SCRIPT,
    MONITOR_PORT_VERIFY,
    NETWORK_CONFIG_HELPER,
})


class InstallError(RuntimeError):
    """Fatal fail-closed installation error."""


def _ui_width() -> int:
    """Return a bounded width that stays readable on narrow and wide terminals."""
    columns = shutil.get_terminal_size(fallback=(88, 24)).columns
    return max(56, min(columns, 100))


def _ui_wrap(text: str, *, initial: str = "", subsequent: str = "") -> str:
    return textwrap.fill(
        text, width=_ui_width(), initial_indent=initial, subsequent_indent=subsequent,
        break_long_words=False, break_on_hyphens=False)


def print_installer_banner() -> None:
    width = _ui_width()
    print()
    print("=" * width)
    print("ELECTRUMX RAVENCOIN".center(width))
    print("Verified Node Installer".center(width))
    print("=" * width)
    print()


def ui_section(title: str, subtitle: Optional[str] = None) -> None:
    print()
    print(f"[ {title} ]")
    print("-" * _ui_width())
    if subtitle:
        print(_ui_wrap(subtitle))
    print()


def print_installation_summary(storage_root: Optional[Path], bootstrap: str,
                               monitor: bool, controller: bool) -> None:
    ui_section(
        "Installation summary",
        "The selections below are the exact configuration the installer will activate.")
    rows = (
        ("Project data", str(storage_root) if storage_root else "not selected (--check-only)"),
        ("Docker images", "existing Docker data-root (unchanged)"),
        ("Bootstrap", "ChainStrap Fast Verified Bootstrap" if bootstrap == "chainstrap" else "Traditional Ravencoin P2P"),
        ("Node Monitor", "enabled" if monitor else "disabled"),
        ("Advanced controls", "enabled (root-owned helper)" if controller else "disabled"),
    )
    label_width = max(len(label) for label, _value in rows)
    for label, value in rows:
        prefix = f"  {label:<{label_width}} : "
        print(_ui_wrap(value, initial=prefix, subsequent=" " * len(prefix)))
    print()


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
                        help="traditional Ravencoin P2P synchronization")
    parser.add_argument("--with-monitor", action="store_true",
                        help="install the pinned Ravencoin Node Monitor")
    parser.add_argument("--without-monitor", action="store_true",
                        help="do not install the Ravencoin Node Monitor")
    parser.add_argument("--with-monitor-controller", action="store_true",
                        help="explicitly install the root-owned bandwidth/connection controller")
    parser.add_argument("--local-release-validation-dir", default=None, metavar="DIR",
                        help="NON-PRODUCTION: verify against a local, ephemeral-key-signed "
                             "release bundle built by "
                             "core-safety/scripts/build_local_release_validation_bundle.py "
                             "instead of the real GitHub release. Never use this for a real "
                             "install; it never touches the production trust roots.")
    parser.add_argument(
        "--storage-root", default=None, metavar="DIR",
        help="store Ravencoin/ChainStrap, ElectrumX and Node Monitor persistent "
             "data under DIR; interactive installs offer mounted disks/filesystems")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.chainstrap and args.p2p_bootstrap:
        parser.error("--chainstrap and --p2p-bootstrap are mutually exclusive")
    if args.with_monitor and args.without_monitor:
        parser.error("--with-monitor and --without-monitor are mutually exclusive")
    if args.with_monitor_controller and args.without_monitor:
        parser.error("--with-monitor-controller requires the Node Monitor")
    return args


def check_python_version(version_info=None) -> None:
    version_info = version_info if version_info is not None else sys.version_info
    if (version_info[0], version_info[1]) < MIN_PYTHON:
        raise InstallError(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required")


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
    for command, error in ((["docker", "compose", "version"], "Docker Compose v2 is required"),
                           (["docker", "info"], "the current user cannot reach the Docker daemon")):
        result = subprocess.run(command, check=False, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL)
        if result.returncode != 0:
            raise InstallError(error)
    return ["docker", "compose"]


def controller_prerequisites(*, require_sudo: bool = True) -> None:
    if os.name != "posix" or platform.system().lower() != "linux":
        raise InstallError("advanced monitor controls require a Linux Docker host")
    for command in ("systemctl", "python3", "nsenter", "ip", "tc"):
        require_command(command)
    if require_sudo and os.geteuid() != 0:
        require_command("sudo")


# ---------------------------------------------------------------------------
# Signed release manifest and independent Core policy
# ---------------------------------------------------------------------------

def canonical_bytes(body: dict) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def core_policy_canonical_bytes(body: dict) -> bytes:
    return CORE_POLICY_SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True).encode("utf-8")


def key_id_for(public_bytes: bytes) -> str:
    if len(public_bytes) != 32:
        raise InstallError("public key must be exactly 32 bytes")
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def require_release_public_key(value: str = RELEASE_PUBLIC_KEY_HEX) -> bytes:
    value = value.strip()
    if not value:
        raise InstallError(
            "this development installer has no ceremonied production release public key")
    if not HEX_KEY_RE.fullmatch(value):
        raise InstallError("embedded release public key is malformed")
    return bytes.fromhex(value)


def require_core_policy_public_key(value: str) -> bytes:
    value = (value or "").strip()
    if not HEX_KEY_RE.fullmatch(value):
        raise InstallError("pinned safe-Core policy public key is malformed")
    return bytes.fromhex(value)


def _verify_ed25519(public_bytes: bytes, signature: bytes, message: bytes,
                    *, what: str = "release manifest") -> None:
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        openssl = shutil.which("openssl")
        if openssl is None:
            raise InstallError(
                "Ed25519 verification requires Python cryptography or OpenSSL")
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
                raise InstallError(f"{what} signature does not verify")
        return
    try:
        Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, message)
    except (InvalidSignature, ValueError) as exc:
        raise InstallError(f"{what} signature does not verify") from exc


def _manifest_architectures(value) -> set[str]:
    if isinstance(value, str):
        values = {item.strip() for item in value.split(",") if item.strip()}
    elif isinstance(value, list):
        if any(not isinstance(item, str) for item in value):
            raise InstallError("release architecture list is malformed")
        values = set(value)
    else:
        raise InstallError("release architecture is malformed")
    if not values or not values <= set(SUPPORTED_MANIFEST_ARCHITECTURES):
        raise InstallError("release contains unsupported architecture targets")
    return values


def validate_manifest_body(body: dict) -> None:
    if not isinstance(body, dict) or body.get("schemaVersion") != 1:
        raise InstallError("unsupported release manifest body/schema")
    missing = [field for field in REQUIRED_MANIFEST_FIELDS if field not in body]
    if missing:
        raise InstallError(f"release manifest missing fields: {missing}")
    unknown = set(body) - ({"schemaVersion"} | set(REQUIRED_MANIFEST_FIELDS))
    if unknown:
        raise InstallError(f"release manifest contains unknown fields: {sorted(unknown)}")

    for field in ("electrumxVersion", "coreVersion", "requiredUpdaterVersion"):
        if not isinstance(body[field], str) or not VERSION_RE.fullmatch(body[field]):
            raise InstallError(f"release manifest {field} is malformed")
    if body.get("channel") not in ("stable", "security"):
        raise InstallError("release manifest channel is unsupported")
    try:
        stamp = datetime.datetime.fromisoformat(body["releaseTimestamp"])
    except (TypeError, ValueError) as exc:
        raise InstallError("releaseTimestamp is malformed") from exc
    if stamp.tzinfo is None:
        raise InstallError("releaseTimestamp must include a timezone")
    if not SHA256_RE.fullmatch(str(body["artifactDigest"])):
        raise InstallError("artifactDigest is malformed")
    if not SHA256_RE.fullmatch(str(body["installerDigest"])):
        raise InstallError("installerDigest is malformed")
    if not RAW_SHA256_RE.fullmatch(str(body["certificationReportDigest"])):
        raise InstallError("certificationReportDigest is malformed")
    _manifest_architectures(body["architecture"])

    if body.get("coreRepository") != "RavenProject/Ravencoin":
        raise InstallError("release manifest names a non-RavenProject Core source")
    if not TAG_RE.fullmatch(str(body.get("coreTag", ""))):
        raise InstallError("release manifest Core tag is malformed")
    if not COMMIT_RE.fullmatch(str(body.get("coreCommit", ""))):
        raise InstallError("release manifest Core commit is malformed")
    policy_version = body.get("safeCorePolicyVersion")
    if not isinstance(policy_version, int) or isinstance(policy_version, bool) or policy_version < 1:
        raise InstallError("safeCorePolicyVersion must be a positive integer")
    if not isinstance(body.get("configCompatibility"), dict):
        raise InstallError("configCompatibility must be an object")
    db = body.get("dbCompatibility")
    if not isinstance(db, dict) or not isinstance(db.get("schemaVersion"), int) or \
            isinstance(db.get("schemaVersion"), bool) or db["schemaVersion"] < 1:
        raise InstallError("dbCompatibility.schemaVersion is malformed")
    migration = db.get("migration")
    if migration is not None:
        if not isinstance(migration, dict) or not {
                "fromSchema", "toSchema", "reversible"} <= set(migration):
            raise InstallError("dbCompatibility migration is malformed")
        if migration.get("toSchema") != db["schemaVersion"] or \
                not isinstance(migration.get("reversible"), bool):
            raise InstallError("dbCompatibility migration is inconsistent")
    for field in ("rollbackSafe", "consensusImpact", "autoUpdateEligible"):
        if not isinstance(body.get(field), bool):
            raise InstallError(f"{field} must be boolean")
    if body["consensusImpact"] and body["autoUpdateEligible"]:
        raise InstallError("consensus-changing release cannot be auto-update eligible")
    if migration is not None and migration["reversible"] is False and body["rollbackSafe"]:
        raise InstallError("irreversible DB migration cannot be rollbackSafe")
    if body.get("installerFilename") != "electrumx-ravencoin-install.py":
        raise InstallError("manifest installer filename is not canonical")


def verify_manifest_signature(document: dict, public_key_hex: str) -> dict:
    public_bytes = require_release_public_key(public_key_hex)
    if not isinstance(document, dict) or set(document) != {"manifest", "signature"}:
        raise InstallError("release manifest document is malformed")
    body = document.get("manifest")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict) or \
            set(signature) != {"algorithm", "keyId", "value"}:
        raise InstallError("release manifest signature object is malformed")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise InstallError("unsupported release signature algorithm")
    if signature.get("keyId") != key_id_for(public_bytes):
        raise InstallError("release manifest keyId does not match pinned public key")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise InstallError("release manifest signature is not valid base64") from exc
    if len(raw_signature) != 64:
        raise InstallError("release manifest signature has the wrong length")
    _verify_ed25519(public_bytes, raw_signature, canonical_bytes(body))
    validate_manifest_body(body)
    return body


def verify_safe_core_policy(document: dict, release_body: dict,
                            public_key_hex: str) -> dict:
    """Verify the independent safe-Core policy and bind it to the release.

    The policy key is supplied by the bootstrap trust root (production) or by
    the explicit local-validation directory (non-production), never learned
    from the bundle being authenticated.
    """
    public_bytes = require_core_policy_public_key(public_key_hex)
    if not isinstance(document, dict) or set(document) != {"policy", "signature"}:
        raise InstallError("safe-Core policy document is malformed")
    body = document.get("policy")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict) or \
            set(signature) != {"algorithm", "keyId", "value"}:
        raise InstallError("safe-Core policy signature object is malformed")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise InstallError("safe-Core policy signature algorithm is unsupported")
    if signature.get("keyId") != key_id_for(public_bytes):
        raise InstallError("safe-Core policy keyId does not match pinned public key")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except (ValueError, TypeError) as exc:
        raise InstallError("safe-Core policy signature is not valid base64") from exc
    if len(raw_signature) != 64:
        raise InstallError("safe-Core policy signature has the wrong length")
    _verify_ed25519(
        public_bytes, raw_signature, core_policy_canonical_bytes(body),
        what="safe-Core policy")

    if body.get("schemaVersion") != 1:
        raise InstallError("safe-Core policy schemaVersion is unsupported")
    version = body.get("policyVersion")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise InstallError("safe-Core policyVersion is malformed")
    if version != release_body.get("safeCorePolicyVersion"):
        raise InstallError("release manifest and safe-Core policy versions disagree")
    if not isinstance(body.get("safetyProfile"), str) or not body["safetyProfile"]:
        raise InstallError("safe-Core policy safetyProfile is malformed")

    expires_at = body.get("expiresAt")
    if expires_at is not None:
        try:
            expiry = datetime.datetime.fromisoformat(expires_at)
        except (TypeError, ValueError) as exc:
            raise InstallError("safe-Core policy expiresAt is malformed") from exc
        if expiry.tzinfo is None:
            raise InstallError("safe-Core policy expiresAt must include a timezone")
        if datetime.datetime.now(datetime.timezone.utc) > expiry:
            raise InstallError("safe-Core policy has expired")

    releases = body.get("releases")
    if not isinstance(releases, list):
        raise InstallError("safe-Core policy releases must be a list")
    seen = set()
    matches = []
    for entry in releases:
        if not isinstance(entry, dict):
            raise InstallError("safe-Core policy release entry is malformed")
        repository = entry.get("repository")
        commit = entry.get("commit")
        status = entry.get("status")
        if not isinstance(repository, str) or not COMMIT_RE.fullmatch(str(commit or "")):
            raise InstallError("safe-Core policy release identity is malformed")
        identity = (repository, commit)
        if identity in seen:
            raise InstallError("safe-Core policy contains duplicate release identity")
        seen.add(identity)
        if status == "KNOWN_SAFE" and repository != "RavenProject/Ravencoin":
            raise InstallError("safe-Core policy trusts a non-RavenProject Core source")
        if identity == (release_body["coreRepository"], release_body["coreCommit"]):
            matches.append(entry)

    if len(matches) != 1:
        raise InstallError("safe-Core policy does not uniquely certify the manifest Core commit")
    entry = matches[0]
    if entry.get("status") != "KNOWN_SAFE":
        raise InstallError("manifest Core commit is not KNOWN_SAFE in the signed policy")
    if entry.get("version") != release_body["coreVersion"]:
        raise InstallError("safe-Core policy Core version disagrees with manifest")
    if entry.get("tag") != release_body["coreTag"]:
        raise InstallError("safe-Core policy Core tag disagrees with manifest")
    if entry.get("reportDigest") != release_body["certificationReportDigest"]:
        raise InstallError("safe-Core certification report digest disagrees with manifest")
    if (entry.get("certification") or {}).get("result") != "PASS":
        raise InstallError("safe-Core policy entry lacks passing certification evidence")
    return body


def fetch_bytes(url: str, *, max_bytes: int, timeout: int = 60) -> bytes:
    if not url.startswith(f"https://github.com/{REPO}/releases/"):
        raise InstallError("refusing download outside expected GitHub release namespace")
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
    if hashlib.sha256(data).hexdigest() != match.group(1):
        raise InstallError(f"{what} SHA-256 mismatch")


def verify_running_installer(body: dict, installer_path: Optional[Path] = None) -> None:
    path = installer_path or Path(__file__).resolve()
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise InstallError(f"cannot read this installer for digest verification: {exc}") from exc
    verify_digest(data, body["installerDigest"], "installer")


def verify_architecture(body: dict, architecture: str) -> None:
    if f"linux/{architecture}" not in _manifest_architectures(body.get("architecture")):
        raise InstallError(
            f"release targets {body.get('architecture')!r}, but host is {architecture!r}")


# ---------------------------------------------------------------------------
# Signed bundle validation / extraction
# ---------------------------------------------------------------------------

def _safe_member_name(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise InstallError(f"unsafe bundle path {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise InstallError(f"unsafe bundle path {name!r}")
    return path


def _strip_yaml_comments(text: str) -> str:
    """Drop full-line and trailing '#' comments so invariant checks below
    only match actual directives, never explanatory prose in a comment."""
    lines = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0] if "#" in line else line
        lines.append(stripped)
    return "\n".join(lines)


def _archive_text(archive: tarfile.TarFile, name: str) -> str:
    handle = archive.extractfile(archive.getmember(name))
    if handle is None:
        raise InstallError(f"cannot read bundle member {name!r}")
    try:
        return handle.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise InstallError(f"bundle member {name!r} is not UTF-8") from exc


def _bundle_member_sha256(data: bytes, name: str) -> str:
    """Derive a member digest from the already signature-bound bundle bytes.

    This expected digest is computed before extracted files enter the
    operator-writable install tree, so it remains an independent reference for
    privileged controller installation (REAUDIT-002).
    """
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            member = archive.getmember(name)
            normalized = _safe_member_name(member.name).as_posix()
            if normalized != name or not member.isfile():
                raise InstallError(
                    f"bundle member {name!r} is not the expected regular file")
            source = archive.extractfile(member)
            if source is None:
                raise InstallError(f"cannot read bundle member {name!r}")
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
            return digest.hexdigest()
    except (tarfile.TarError, KeyError) as exc:
        raise InstallError(
            f"cannot derive trusted digest for bundle member {name!r}") from exc


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise InstallError(f"cannot hash trusted controller copy {path}: {exc}") from exc
    return digest.hexdigest()


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
    if not isinstance(monitor, dict) or \
            monitor.get("repository") != "ALENOC/ravencoin-node-monitor" or \
            not COMMIT_RE.fullmatch(str(monitor.get("commit", ""))) or \
            monitor.get("bundledPath") != MONITOR_PATH:
        raise InstallError("bundle Node Monitor identity is malformed")


def validate_bundle(data: bytes, body: dict,
                    public_key_hex: str = RELEASE_PUBLIC_KEY_HEX,
                    core_policy_public_key_hex: Optional[str] = None) -> dict:
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
            normalized = _safe_member_name(member.name).as_posix()
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
                    raise InstallError("release bundle expands beyond size limit")
        missing = REQUIRED_BUNDLE_PATHS - names
        if missing:
            raise InstallError("release bundle is incomplete: " + ", ".join(sorted(missing)))

        try:
            metadata = json.loads(_archive_text(archive, BUNDLE_METADATA))
        except json.JSONDecodeError as exc:
            raise InstallError("bundle installation metadata is invalid JSON") from exc
        _validate_metadata(metadata, body)

        # The update key embedded in this bootstrap file must be exactly the key
        # installed for future electrumx-update checks.
        embedded = require_release_public_key(public_key_hex).hex()
        bundled_key = _archive_text(archive, UPDATE_PUBLIC_KEY_PATH).strip()
        if bundled_key != embedded:
            raise InstallError("bundle updater public key differs from installer trust root")

        # The Core-policy key is a second, independent trust root. Never trust a
        # key merely because the release bundle contains it.
        trusted_core_key = (
            core_policy_public_key_hex or PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX
        ).strip()
        require_core_policy_public_key(trusted_core_key)
        bundled_core_key = _archive_text(archive, CORE_POLICY_PUBLIC_KEY_PATH).strip()
        if bundled_core_key != trusted_core_key:
            raise InstallError("bundle safe-Core policy key differs from pinned trust root")
        try:
            core_policy_document = json.loads(_archive_text(archive, CORE_POLICY_PATH))
        except json.JSONDecodeError as exc:
            raise InstallError("bundle safe-Core policy is invalid JSON") from exc
        verify_safe_core_policy(core_policy_document, body, trusted_core_key)

        compose = _archive_text(archive, BASE_COMPOSE)
        if f"RAVENCOIN_SOURCE_COMMIT: {body['coreCommit']}" not in compose:
            raise InstallError("bundle Compose Core commit disagrees with signed manifest")
        if "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin" not in compose:
            raise InstallError("bundle Compose does not enforce official Core source")
        if f"RAVENCOIN_VERSION: {body['coreVersion']}" not in compose:
            raise InstallError("bundle Compose Core version disagrees with signed manifest")

        chainstrap = _archive_text(archive, CHAINSTRAP_OVERLAY)
        reindex = _archive_text(archive, "docker/core/bootstrap-reindex.sh")
        if "network_mode: none" not in chainstrap or reindex.count("-connect=0") < 2:
            raise InstallError("ChainStrap Core validation lost offline isolation")
        for required in ("getbestblockhash", "getblockhash", "listassets",
                         "getassetdata", "listaddressesbyasset"):
            if required not in reindex:
                raise InstallError(f"ChainStrap reindex lacks gate {required!r}")

        monitor = _archive_text(archive, MONITOR_OVERLAY)
        for invariant in ("no-new-privileges:true", "cap_drop:", "- ALL",
                          '"127.0.0.1:8899:8899/tcp"'):
            if invariant not in monitor:
                raise InstallError(f"Node Monitor isolation lost {invariant!r}")
        controller = _archive_text(archive, MONITOR_CONTROLLER_OVERLAY)
        controller_directives = _strip_yaml_comments(controller)
        if "/var/run/docker.sock" in controller_directives or \
                "CAP_NET_ADMIN" in controller_directives:
            raise InstallError("monitor-controller overlay grants forbidden privileges")
        if "/run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro" not in controller:
            raise InstallError("monitor-controller overlay lacks read-only narrow socket mount")
        return metadata


def extract_bundle(data: bytes, destination: Path) -> None:
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
            fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            os.chmod(target, 0o755 if member.mode & 0o111 else 0o644)


def fetch_and_verify_bundle(body: dict, fetch: Callable[[str], bytes] = None,
                            public_key_hex: str = RELEASE_PUBLIC_KEY_HEX,
                            core_policy_public_key_hex: Optional[str] = None) -> tuple[bytes, dict]:
    data = (fetch(BUNDLE_URL) if fetch is not None
            else fetch_bytes(BUNDLE_URL, max_bytes=MAX_BUNDLE_BYTES, timeout=180))
    metadata = validate_bundle(
        data, body, public_key_hex=public_key_hex,
        core_policy_public_key_hex=core_policy_public_key_hex)
    return data, metadata


# ---------------------------------------------------------------------------
# NON-PRODUCTION local release validation
# ---------------------------------------------------------------------------

LOCAL_VALIDATION_MANIFEST_FILE = "manifest.json"
LOCAL_VALIDATION_BUNDLE_FILE = "bundle.tar.gz"
LOCAL_VALIDATION_PUBLIC_KEY_FILE = "public-key.hex"
LOCAL_VALIDATION_CORE_POLICY_PUBLIC_KEY_FILE = "core-policy-public-key.hex"


def load_local_release_validation(directory: Path) -> tuple[
        str, str, Callable[[str], bytes], Callable[[str], bytes]]:
    directory = directory.expanduser().resolve()
    manifest_path = directory / LOCAL_VALIDATION_MANIFEST_FILE
    bundle_path = directory / LOCAL_VALIDATION_BUNDLE_FILE
    key_path = directory / LOCAL_VALIDATION_PUBLIC_KEY_FILE
    core_key_path = directory / LOCAL_VALIDATION_CORE_POLICY_PUBLIC_KEY_FILE
    for path in (manifest_path, bundle_path, key_path, core_key_path):
        if not path.is_file():
            raise InstallError(
                f"local release validation directory is missing {path.name}: {path}")
    public_key_hex = key_path.read_text(encoding="utf-8").strip()
    core_policy_public_key_hex = core_key_path.read_text(encoding="utf-8").strip()
    if not HEX_KEY_RE.fullmatch(public_key_hex):
        raise InstallError("local release validation public key is malformed")
    if not HEX_KEY_RE.fullmatch(core_policy_public_key_hex):
        raise InstallError("local safe-Core policy validation key is malformed")

    def manifest_fetch(_url: str) -> bytes:
        return manifest_path.read_bytes()

    def bundle_fetch(_url: str) -> bytes:
        return bundle_path.read_bytes()

    return public_key_hex, core_policy_public_key_hex, manifest_fetch, bundle_fetch


def print_local_validation_banner(directory: Path) -> None:
    print("=" * 72, file=sys.stderr)
    print("NON-PRODUCTION LOCAL RELEASE VALIDATION MODE", file=sys.stderr)
    print(f"Trust roots: two ephemeral public keys from {directory}", file=sys.stderr)
    print("These are NOT the production release/Core-policy trust roots and", file=sys.stderr)
    print("must never be used for a real install.", file=sys.stderr)
    print("=" * 72, file=sys.stderr)




def _format_storage_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024.0 or unit == "TiB":
            return f"{amount:.1f} {unit}" if unit != "B" else f"{amount:.0f} {unit}"
        amount /= 1024.0
    raise AssertionError("unreachable")


def _nearest_existing_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def validate_storage_root_path(path: Path) -> Path:
    raw = str(path.expanduser())
    if not raw or not SAFE_STORAGE_PATH_RE.fullmatch(raw) or any(ch in raw for ch in ":'$\n\r"):
        raise InstallError(
            "storage path contains unsupported characters; use letters, digits, spaces, "
            "and common path characters only")
    resolved = path.expanduser().resolve(strict=False)
    home = Path.home().resolve()
    if resolved in (Path("/"), home):
        raise InstallError("storage root must be a dedicated child directory, not / or $HOME")
    if resolved.exists():
        raise InstallError(
            f"fresh install storage root already exists: {resolved}; preserve or remove it "
            "explicitly before retrying")
    parent = _nearest_existing_parent(resolved.parent)
    if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
        raise InstallError(f"storage parent is not writable by the current user: {parent}")
    try:
        mount = Path(subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "--target", str(parent)],
            check=False, capture_output=True, text=True).stdout.strip() or "/").resolve()
    except OSError:
        mount = Path("/")
    if resolved == mount:
        raise InstallError("storage root must be a child directory, not the filesystem mountpoint")
    return resolved


def _storage_candidate_root(mountpoint: Path) -> Optional[Path]:
    try:
        home = Path.home().resolve()
        if os.stat(home).st_dev == os.stat(mountpoint).st_dev:
            return home / STORAGE_ROOT_DIRNAME
    except OSError:
        pass
    if os.access(mountpoint, os.W_OK | os.X_OK):
        return mountpoint / STORAGE_ROOT_DIRNAME
    return None


def discover_storage_candidates() -> list[dict]:
    """Return writable mounted block filesystems without changing the host."""
    lsblk = shutil.which("lsblk")
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    if lsblk is not None:
        completed = subprocess.run(
            [lsblk, "--json", "--bytes", "--output",
             "NAME,PATH,TYPE,FSTYPE,SIZE,MOUNTPOINTS,RO"],
            check=False, capture_output=True, text=True)
        if completed.returncode == 0:
            try:
                payload = json.loads(completed.stdout)
            except json.JSONDecodeError:
                payload = {}

            def walk(nodes) -> None:
                for node in nodes or []:
                    mountpoints = node.get("mountpoints") or []
                    if isinstance(mountpoints, str):
                        mountpoints = [mountpoints]
                    source = str(node.get("path") or node.get("name") or "unknown")
                    if node.get("fstype") and not node.get("ro"):
                        for raw_mount in mountpoints:
                            if not raw_mount:
                                continue
                            mountpoint = Path(raw_mount).resolve()
                            if not mountpoint.is_dir():
                                continue
                            suggested = _storage_candidate_root(mountpoint)
                            if suggested is None:
                                continue
                            key = (source, str(mountpoint))
                            if key in seen:
                                continue
                            seen.add(key)
                            try:
                                usage = shutil.disk_usage(mountpoint)
                            except OSError:
                                continue
                            candidates.append({
                                "source": source,
                                "mountpoint": mountpoint,
                                "fstype": str(node.get("fstype")),
                                "size": int(node.get("size") or usage.total),
                                "free": int(usage.free),
                                "root": suggested,
                            })
                    walk(node.get("children"))

            walk(payload.get("blockdevices"))

    if not candidates:
        home = Path.home().resolve()
        usage = shutil.disk_usage(home)
        candidates.append({
            "source": "current-home-filesystem",
            "mountpoint": home,
            "fstype": "unknown",
            "size": usage.total,
            "free": usage.free,
            "root": home / STORAGE_ROOT_DIRNAME,
        })
    return sorted(candidates, key=lambda item: (str(item["mountpoint"]) != "/", str(item["mountpoint"])))


def choose_storage_root(args, interactive: bool,
                        prompt: Callable[[str], str] = input) -> Path:
    if args.storage_root:
        selected = validate_storage_root_path(Path(args.storage_root))
        usage = shutil.disk_usage(_nearest_existing_parent(selected.parent))
        print(f"project data storage: {selected} ({_format_storage_bytes(usage.free)} free)")
        return selected
    if not interactive:
        raise InstallError("--storage-root is required for a non-interactive fresh install")

    candidates = discover_storage_candidates()
    ui_section(
        "1 / 4  Project data storage",
        "Choose the mounted filesystem that will hold the Ravencoin blockchain, "
        "ChainStrap data, ElectrumX database and Node Monitor history. Docker images "
        "remain in Docker's existing data-root.")
    for index, item in enumerate(candidates, 1):
        state = ""
        if item["root"].exists():
            state = " [existing path - cannot use for fresh install]"
        description = (
            f"{index}. {item['source']} mounted at {item['mountpoint']} "
            f"({item['fstype']}, {_format_storage_bytes(item['free'])} free / "
            f"{_format_storage_bytes(item['size'])}){state}")
        print(_ui_wrap(description, initial="  ", subsequent="     "))
        print(_ui_wrap(f"data directory: {item['root']}", initial="     ", subsequent="     "))
        print()
    print("  C. Custom dedicated directory on another mounted filesystem")
    print()

    answer = prompt("Storage choice [1]: ").strip().lower()
    if answer in ("c", "custom"):
        custom = prompt("Dedicated storage directory: ").strip()
        if not custom:
            raise InstallError("no custom storage directory supplied")
        selected = validate_storage_root_path(Path(custom))
    else:
        if answer == "":
            answer = "1"
        try:
            index = int(answer)
        except ValueError as exc:
            raise InstallError(f"unrecognized storage choice {answer!r}") from exc
        if index < 1 or index > len(candidates):
            raise InstallError(f"storage choice {index} is outside the displayed range")
        selected = validate_storage_root_path(candidates[index - 1]["root"])

    usage = shutil.disk_usage(_nearest_existing_parent(selected.parent))
    print()
    print(_ui_wrap(
        f"Selected project data storage: {selected} ({_format_storage_bytes(usage.free)} free)"))
    print()
    return selected


def require_clean_storage_root(storage_root: Path) -> None:
    validate_storage_root_path(storage_root)


def prepare_storage_layout(storage_root: Path) -> None:
    require_clean_storage_root(storage_root)
    created = False
    try:
        storage_root.mkdir(mode=0o755, parents=False, exist_ok=False)
        created = True
        for name in STORAGE_SUBDIRS:
            (storage_root / name).mkdir(mode=0o755)
    except BaseException:
        if created:
            shutil.rmtree(storage_root, ignore_errors=True)
        raise


def _storage_env_value(path: Path) -> str:
    value = str(path)
    if not SAFE_STORAGE_PATH_RE.fullmatch(value) or any(ch in value for ch in ":'$\n\r"):
        raise InstallError(f"storage path cannot be represented safely in Compose: {path}")
    return value


def write_storage_env(root: Path, storage_root: Path) -> None:
    env_path = root / ".env"
    if not env_path.is_file():
        raise InstallError("setup.sh did not create .env before storage configuration")
    mapping = {
        "RAVENCOIN_DATA_HOST_DIR": storage_root / "ravencoin-data",
        "RAVENCOIN_CONFIG_HOST_DIR": storage_root / "ravencoin-config",
        "ELECTRUMX_DATA_HOST_DIR": storage_root / "electrumx-data",
        "MONITOR_DATA_HOST_DIR": storage_root / "monitor-data",
    }
    existing = env_path.read_text(encoding="utf-8")
    if any(f"{key}=" in existing for key in mapping):
        raise InstallError("refusing to overwrite pre-existing storage path configuration")
    with env_path.open("a", encoding="utf-8") as handle:
        handle.write("\n# Selected by the verified installer; project data only, not Docker images.\n")
        for key, path in mapping.items():
            handle.write(f"{key}={_storage_env_value(path)}\n")


def initialize_storage_permissions(storage_root: Path, monitor: bool) -> None:
    raven_mounts = [
        (storage_root / "ravencoin-data", "/storage/ravencoin-data"),
        (storage_root / "ravencoin-config", "/storage/ravencoin-config"),
    ]
    if monitor:
        raven_mounts.append((storage_root / "monitor-data", "/storage/monitor-data"))
    argv = ["docker", "run", "--rm", "--network", "none", "--user", "0:0",
            "--entrypoint", "/bin/sh"]
    for host, container in raven_mounts:
        argv += ["-v", f"{host}:{container}"]
    targets = " ".join(container for _host, container in raven_mounts)
    argv += ["alenoc/ravencoin-core:4.8.0", "-ec",
             f"chown -R 10001:10001 {targets}; chmod 0750 {targets}"]
    run_checked(argv)

    electrumx_dir = storage_root / "electrumx-data"
    run_checked([
        "docker", "run", "--rm", "--network", "none", "--user", "0:0",
        "--entrypoint", "/bin/sh", "-v", f"{electrumx_dir}:/storage/electrumx-data",
        "alenoc/electrumx-ravencoin:1.13.1", "-ec",
        "uid=$(id -u electrumx); gid=$(id -g electrumx); "
        "chown -R \"$uid:$gid\" /storage/electrumx-data; chmod 0750 /storage/electrumx-data",
    ])


def cleanup_storage_layout_best_effort(storage_root: Path) -> None:
    if not storage_root.exists():
        return
    # Container UIDs own the data subdirectories. Use the already-built Core
    # image only to return ownership to the invoking host user before rmtree.
    try:
        subprocess.run([
            "docker", "run", "--rm", "--network", "none", "--user", "0:0",
            "--entrypoint", "/bin/sh", "-v", f"{storage_root}:/storage",
            "alenoc/ravencoin-core:4.8.0", "-ec",
            f"chown -R {os.getuid()}:{os.getgid()} /storage",
        ], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    shutil.rmtree(storage_root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Operator choices / generated configuration
# ---------------------------------------------------------------------------

def choose_bootstrap(args, interactive: bool, prompt: Callable[[str], str] = input) -> str:
    if args.chainstrap:
        return "chainstrap"
    if args.p2p_bootstrap:
        return "p2p"
    if not interactive:
        return "chainstrap"
    ui_section(
        "2 / 4  Blockchain bootstrap",
        "ChainStrap downloads a vetted snapshot and then Ravencoin Core reindexes and "
        "validates it offline. Traditional P2P synchronization remains available as an "
        "explicit alternative.")
    print("  1. Fast Verified Bootstrap using ChainStrap  [recommended, default]")
    print("  2. Traditional Ravencoin P2P synchronization")
    print()
    answer = prompt("Choice [1]: ").strip()
    print()
    if answer in ("", "1"):
        return "chainstrap"
    if answer == "2":
        return "p2p"
    raise InstallError(f"unrecognized bootstrap choice {answer!r}")


def choose_monitor(args, interactive: bool, prompt: Callable[[str], str] = input) -> bool:
    if args.with_monitor_controller:
        return True
    if args.with_monitor:
        return True
    if args.without_monitor:
        return False
    if not interactive:
        return True
    ui_section(
        "3 / 4  Ravencoin Node Monitor",
        "The monitor is isolated from ElectrumX failure and remains available to report "
        "Core, host and network state when ElectrumX is degraded.")
    print("  Y. Install Node Monitor  [recommended, default]")
    print("  N. Do not install Node Monitor")
    print()
    answer = prompt("Choice [Y]: ").strip().lower()
    print()
    if answer in ("", "y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    raise InstallError(f"unrecognized monitor choice {answer!r}")


def choose_monitor_controller(args, monitor: bool, interactive: bool,
                              prompt: Callable[[str], str] = input) -> bool:
    if not monitor:
        return False
    if args.with_monitor_controller:
        return True
    if not interactive:
        return False
    ui_section(
        "4 / 4  Advanced host controls",
        "Optional. Enabling this installs a separate root-owned systemd helper and may "
        "request sudo. It is not required for normal monitoring.")
    print("  N. Keep advanced host controls disabled  [recommended, default]")
    print("  Y. Enable bandwidth / connection controls (requires sudo)")
    print()
    answer = prompt("Choice [N]: ").strip().lower()
    print()
    if answer in ("", "n", "no"):
        return False
    if answer in ("y", "yes"):
        return True
    raise InstallError(f"unrecognized advanced-control choice {answer!r}")


def write_monitor_env(root: Path) -> None:
    path = root / MONITOR_ENV
    if path.exists():
        raise InstallError(f"refusing to overwrite existing monitor environment {path}")
    password = secrets.token_urlsafe(32)
    content = (
        "# Generated by the verified ElectrumX-Ravencoin installer.\n"
        "NODE_NAME=ElectrumX-Ravencoin bundled node\n"
        "BIND_HOST=0.0.0.0\nBIND_PORT=8899\n"
        "MONITOR_USER=monitor\n"
        f"MONITOR_PASSWORD={password}\n"
        "CORE_RPC_HOST=ravencoin-core\nCORE_RPC_PORT=8766\n"
        "CORE_RPC_USER_FILE=/run/raven-secrets/raven_rpc_user\n"
        "CORE_RPC_PASSWORD_FILE=/run/raven-secrets/raven_rpc_password\n"
        "ELECTRUMX_ENABLED=true\n"
        "ELECTRUMX_SSL_VERIFY=false\n"
        "HISTORY_ENABLED=true\nHISTORY_STORAGE=memory\n"
        "HISTORY_DB_PATH=/data/history.db\n"
        "EXTRA_DISK_PATHS=Project storage=/data\n"
        "PRICE_FEED_ENABLED=true\nPRICE_FEED_SYMBOL=RVNUSDT\n"
        "PROMETHEUS_ENABLED=true\nMIN_SAFE_CORE_VERSION=4.8.0\n"
        "BANDWIDTH_CONTROL_ENABLED=false\n"
        "BANDWIDTH_CONTROL_SOCKET=/run/ravencoin-bandwidth/control.sock\n"
    )
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def run_checked(argv: Sequence[str], *, cwd: Optional[Path] = None,
                quiet: bool = False) -> None:
    completed = subprocess.run(
        list(argv), cwd=cwd, check=False,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.DEVNULL if quiet else None)
    if completed.returncode != 0:
        raise InstallError(
            f"command failed with exit code {completed.returncode}: {' '.join(argv)}")


def _compose_output_tail(handle, limit: int = 80) -> str:
    handle.flush()
    handle.seek(0)
    return "\n".join(handle.read().splitlines()[-limit:])


def _wait_for_compose_container(root: Path, base: Sequence[str], service: str,
                                parent, output_handle, timeout: float = 90.0) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        completed = subprocess.run(
            list(base) + ["ps", "-a", "-q", service], cwd=root, check=False,
            capture_output=True, text=True)
        if completed.returncode == 0 and completed.stdout.strip():
            return completed.stdout.strip().splitlines()[-1]
        if parent.poll() is not None:
            tail = _compose_output_tail(output_handle)
            detail = f"\n{tail}" if tail else ""
            raise InstallError(
                f"Compose activation exited before {service} was created{detail}")
        time.sleep(0.25)
    raise InstallError(f"timed out waiting for Compose service {service}")


def _compose_container_result(container_id: str) -> tuple[str, int]:
    completed = subprocess.run(
        ["docker", "inspect", "--format", "{{.State.Status}} {{.State.ExitCode}}",
         container_id], check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise InstallError("cannot inspect completed bootstrap container state")
    fields = completed.stdout.strip().split()
    if len(fields) != 2:
        raise InstallError("Docker returned malformed bootstrap container state")
    try:
        exit_code = int(fields[1])
    except ValueError as exc:
        raise InstallError("Docker returned a malformed bootstrap exit code") from exc
    return fields[0], exit_code


def _stream_compose_one_shot(root: Path, base: Sequence[str], service: str,
                             title: str, subtitle: str, parent, output_handle) -> None:
    ui_section(title, subtitle)
    container_id = _wait_for_compose_container(
        root, base, service, parent, output_handle)
    print("Live progress follows. Leave this terminal running.\n")
    logs = subprocess.run(
        list(base) + ["logs", "--no-color", "--follow", service],
        cwd=root, check=False)
    if logs.returncode != 0:
        print(
            "Warning: the live log follower ended unexpectedly; "
            "the service exit status will still be verified.",
            file=sys.stderr)
    status, exit_code = _compose_container_result(container_id)
    if status != "exited" or exit_code != 0:
        raise InstallError(
            f"{service} did not complete successfully: status={status}, exit={exit_code}")
    print()
    print(f"[OK] {title}")
    print()


def run_chainstrap_activation_with_live_logs(root: Path, base: Sequence[str]) -> None:
    """Activate Compose while streaming the two long one-shot bootstrap phases."""
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as compose_output:
        parent = subprocess.Popen(
            list(base) + ["up", "-d", "--no-build"], cwd=root,
            stdout=compose_output, stderr=subprocess.STDOUT, text=True)
        try:
            _stream_compose_one_shot(
                root, base, "chainstrap-bootstrap",
                "ChainStrap verified bootstrap",
                "Downloading and verifying the vetted snapshot. Progress includes part and "
                "snapshot percentage, bytes, transfer rate and ETA.",
                parent, compose_output)
            _stream_compose_one_shot(
                root, base, "ravencoin-bootstrap-reindex",
                "Offline Ravencoin Core validation",
                "Ravencoin Core is reindexing the downloaded raw blocks with networking "
                "disabled and will verify the exact snapshot tip and asset indexes.",
                parent, compose_output)
            ui_section(
                "Starting node services",
                "Bootstrap validation succeeded. Starting Ravencoin Core, ElectrumX and the "
                "selected optional services.")
            returncode = parent.wait()
            if returncode != 0:
                tail = _compose_output_tail(compose_output)
                if tail:
                    print(tail, file=sys.stderr)
                raise InstallError(
                    f"docker compose activation failed with exit code {returncode}")
            print("[OK] Docker services started")
            print()
        except BaseException:
            if parent.poll() is None:
                parent.terminate()
                try:
                    parent.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    parent.kill()
                    parent.wait()
            raise


def activate_compose(root: Path, base: Sequence[str], bootstrap: str) -> None:
    if bootstrap == "chainstrap":
        run_chainstrap_activation_with_live_logs(root, base)
        return
    if bootstrap != "p2p":
        raise InstallError(f"unknown bootstrap choice {bootstrap!r}")
    ui_section(
        "Starting node services",
        "Traditional P2P synchronization selected. Starting Ravencoin Core, ElectrumX and "
        "the selected optional services.")
    run_checked(list(base) + ["up", "-d", "--no-build"], cwd=root)


def verify_monitor_host_publish(root: Path, files: Sequence[str]) -> None:
    """Prove the Monitor is published on the host, not merely alive in-container.

    The helper performs at most one monitor-only force-recreate when Docker has
    lost the 8899 host publication after a reboot.  Failure after that single
    repair attempt aborts the fresh install; Core and ElectrumX are never
    recreated by this recovery path.
    """
    script = root / MONITOR_PORT_VERIFY
    argv = [
        sys.executable, str(script),
        "--compose-dir", str(root),
        "--container", "ravencoin-node-monitor",
        "--host", "127.0.0.1",
        "--port", "8899",
        "--repair",
    ]
    for filename in files:
        argv += ["--compose-file", filename]
    run_checked(argv, cwd=root)


def compose_files(bootstrap: str, monitor: bool, controller: bool = False) -> list[str]:
    files = [BASE_COMPOSE, STORAGE_OVERLAY]
    if bootstrap == "chainstrap":
        files.append(CHAINSTRAP_OVERLAY)
    elif bootstrap != "p2p":
        raise InstallError(f"unknown bootstrap choice {bootstrap!r}")
    if monitor:
        files.append(MONITOR_OVERLAY)
    if controller:
        if not monitor:
            raise InstallError("advanced controller cannot be enabled without monitor")
        files.append(MONITOR_CONTROLLER_OVERLAY)
    return files


def _compose_prefix(files: Sequence[str]) -> list[str]:
    # The project name is pinned explicitly (GLM53-RVN-008): an exported
    # COMPOSE_PROJECT_NAME in the operator's environment must not be able to
    # detach the installer from the project namespace its preflights and the
    # monitor/controller container names assume.
    result = ["docker", "compose", "-p", COMPOSE_PROJECT_NAME]
    for filename in files:
        result += ["-f", filename]
    return result


def _docker_project_resources() -> dict[str, list[str]]:
    """Return existing Compose-labelled runtime state for this fixed project.

    A fresh installer must never silently inherit named volumes from an aborted
    or unrelated run. We fail before activation if anything already exists.
    """
    label = f"label=com.docker.compose.project={COMPOSE_PROJECT_NAME}"
    commands = {
        "containers": ["docker", "ps", "-a", "--filter", label, "--format", "{{.Names}}"],
        "volumes": ["docker", "volume", "ls", "--filter", label, "--format", "{{.Name}}"],
        "networks": ["docker", "network", "ls", "--filter", label, "--format", "{{.Name}}"],
    }
    result = {}
    for kind, command in commands.items():
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        if completed.returncode != 0:
            raise InstallError(f"cannot inspect existing Docker {kind} for fresh install")
        result[kind] = [line for line in completed.stdout.splitlines() if line.strip()]
    return result


def require_clean_docker_project_runtime() -> None:
    resources = _docker_project_resources()
    present = [f"{kind}={','.join(values)}" for kind, values in resources.items() if values]
    if present:
        raise InstallError(
            "fresh install refuses existing Docker resources for project "
            f"{COMPOSE_PROJECT_NAME!r}: {'; '.join(present)}; remove or preserve them "
            "explicitly before retrying so no old blockchain/database state is reused")


def _sudo_prefix() -> list[str]:
    return [] if os.geteuid() == 0 else [require_command("sudo")]


def controller_unit_body(root: Path) -> str:
    root_text = str(root.resolve())
    if not SAFE_CONTROLLER_ROOT_RE.fullmatch(root_text):
        raise InstallError(
            "advanced controller requires an install path containing only letters, "
            "digits, '.', '_', '/', or '-' to keep the root systemd unit unambiguous")
    return "\n".join((
        "[Unit]",
        "Description=Ravencoin Node Monitor host controller",
        "After=docker.service",
        "Wants=docker.service",
        "",
        "[Service]",
        "Type=simple",
        "User=root",
        "Group=root",
        # GLM53-RVN-002: execute only the root-owned verified copy in the
        # trusted library path, never a script inside the operator-writable
        # install tree.
        f"ExecStart=/usr/bin/python3 {TRUSTED_CONTROLLER_PATH}",
        "Restart=on-failure",
        "RestartSec=3",
        "Environment=BANDWIDTH_SOCKET_PATH=/run/ravencoin-bandwidth/control.sock",
        "Environment=BANDWIDTH_STATE_FILE=/var/lib/ravencoin-bandwidth/limits.json",
        "Environment=BANDWIDTH_SOCKET_GID=10001",
        "Environment=RAVENCOIN_CORE_CONTAINER=electrumx-ravencoin-ravencoin-core-1",
        "Environment=ELECTRUMX_CONTAINER=electrumx-ravencoin-electrumx-1",
        "Environment=CONNECTION_RECONCILE_INTERVAL=30",
        "Environment=CONNECTION_COMPOSE_TIMEOUT=120",
        "RuntimeDirectory=ravencoin-bandwidth",
        "RuntimeDirectoryMode=0750",
        "RuntimeDirectoryPreserve=yes",
        "StateDirectory=ravencoin-bandwidth",
        "StateDirectoryMode=0700",
        "NoNewPrivileges=true",
        "PrivateTmp=true",
        "ProtectHome=read-only",
        "ProtectKernelTunables=true",
        "ProtectKernelModules=true",
        "ProtectControlGroups=true",
        "RestrictSUIDSGID=true",
        "RestrictAddressFamilies=AF_UNIX AF_NETLINK AF_INET AF_INET6",
        "",
        "[Install]",
        "WantedBy=multi-user.target",
        "",
    ))


def install_trusted_controller(source: Path, expected_sha256: str) -> None:
    """Install the controller only if the privileged copy matches the signed bundle.

    ``expected_sha256`` is derived from the already signature-bound bundle bytes,
    before extraction into an operator-writable tree.  The source may therefore
    change concurrently without being able to cross into root execution: the
    root-owned staged copy is hashed before atomic placement and the final copy is
    checked again before systemd can be enabled (REAUDIT-002).
    """
    controller_prerequisites(require_sudo=True)
    if not RAW_SHA256_RE.fullmatch(expected_sha256 or ""):
        raise InstallError("trusted controller expected SHA-256 is malformed")
    if not source.is_file():
        raise InstallError(
            f"bundle did not contain the monitor controller script at {source}")
    prefix = _sudo_prefix()
    staged = TRUSTED_CONTROLLER_PATH.with_name(
        TRUSTED_CONTROLLER_PATH.name + f".new.{secrets.token_hex(8)}")
    run_checked(prefix + ["install", "-d", "-o", "root", "-g", "root", "-m", "0755",
                          str(TRUSTED_CONTROLLER_DIR)])
    try:
        run_checked(prefix + ["install", "-o", "root", "-g", "root", "-m", "0755",
                              str(source), str(staged)])
        staged_digest = _file_sha256(staged)
        if staged_digest != expected_sha256:
            raise InstallError(
                "trusted controller SHA-256 mismatch after privileged copy; "
                "refusing root execution")
        # Same-filesystem rename under a root-owned 0755 directory: atomic, and
        # after hashing the staged object the normal user cannot modify it.
        run_checked(prefix + ["mv", "-fT", str(staged),
                              str(TRUSTED_CONTROLLER_PATH)])
    except BaseException:
        subprocess.run(prefix + ["rm", "-f", str(staged)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        raise
    verify_trusted_controller(expected_sha256)


def verify_trusted_controller(expected_sha256: Optional[str] = None) -> None:
    """Fail unless the trusted controller has safe ownership and expected bytes."""
    try:
        stat_result = TRUSTED_CONTROLLER_PATH.stat()
    except OSError as exc:
        raise InstallError(
            f"trusted controller {TRUSTED_CONTROLLER_PATH} is not installed: {exc}")
    if stat_result.st_uid != 0 or stat_result.st_gid != 0:
        raise InstallError(
            f"trusted controller {TRUSTED_CONTROLLER_PATH} must be owned by "
            f"root:root, found uid={stat_result.st_uid} gid={stat_result.st_gid}")
    if stat_result.st_mode & 0o022:
        raise InstallError(
            f"trusted controller {TRUSTED_CONTROLLER_PATH} must not be group- or "
            f"world-writable (mode {stat_result.st_mode & 0o777:o})")
    if expected_sha256 is not None:
        if not RAW_SHA256_RE.fullmatch(expected_sha256 or ""):
            raise InstallError("trusted controller expected SHA-256 is malformed")
        if _file_sha256(TRUSTED_CONTROLLER_PATH) != expected_sha256:
            raise InstallError(
                "installed trusted controller SHA-256 does not match signed bundle")
    if os.geteuid() != 0:
        # The invoking user must not be able to replace the file: the
        # containing directory must be root-owned and not user-writable.
        dir_stat = TRUSTED_CONTROLLER_DIR.stat()
        if dir_stat.st_uid != 0 or dir_stat.st_mode & 0o022:
            raise InstallError(
                f"trusted controller directory {TRUSTED_CONTROLLER_DIR} must be "
                "root-owned and not group- or world-writable")


def install_controller(root: Path, expected_sha256: str) -> None:
    controller_prerequisites(require_sudo=True)
    install_trusted_controller(root / CONTROLLER_SCRIPT, expected_sha256)
    verify_trusted_controller(expected_sha256)
    unit = controller_unit_body(root)
    with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", prefix="electrumx-controller-",
            delete=False) as handle:
        handle.write(unit)
        temporary = Path(handle.name)
    try:
        prefix = _sudo_prefix()
        run_checked(prefix + ["install", "-o", "root", "-g", "root", "-m", "0644",
                              str(temporary), f"/etc/systemd/system/{CONTROLLER_UNIT}"])
        run_checked(prefix + ["systemctl", "daemon-reload"])
        run_checked(prefix + ["systemctl", "enable", "--now", CONTROLLER_UNIT])
    finally:
        temporary.unlink(missing_ok=True)


def uninstall_controller_best_effort() -> None:
    try:
        prefix = _sudo_prefix()
        subprocess.run(prefix + ["systemctl", "disable", "--now", CONTROLLER_UNIT],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(prefix + ["rm", "-f", f"/etc/systemd/system/{CONTROLLER_UNIT}"],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(prefix + ["rm", "-f", str(TRUSTED_CONTROLLER_PATH)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(prefix + ["rmdir", str(TRUSTED_CONTROLLER_DIR)],
                       check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(prefix + ["systemctl", "daemon-reload"], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def _private_atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + f".new.{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def state_dir_for(target: Path) -> Path:
    return target.parent / f".{target.name}.state"


def write_initial_update_state(target: Path, body: dict) -> None:
    state_dir = state_dir_for(target)
    path = state_dir / "update-state.json"
    if path.exists():
        raise InstallError(f"refusing to overwrite existing updater state {path}")
    _private_atomic_json(path, {
        "schemaVersion": 2,
        "currentRelease": body,
        "lastKnownGoodRelease": None,
        "pendingCandidate": None,
        "updateTimestamp": datetime.datetime.now(datetime.timezone.utc)
            .replace(microsecond=0).isoformat(),
        "failureReason": None,
        "minimumCorePolicyVersion": body["safeCorePolicyVersion"],
    })


def write_install_marker(root: Path, *, body: dict, metadata: dict,
                         bootstrap: str, monitor: bool, controller: bool,
                         storage_root: Path) -> None:
    marker = {
        "schemaVersion": 1,
        "electrumxVersion": body["electrumxVersion"],
        "artifactDigest": body["artifactDigest"],
        "coreRepository": body["coreRepository"],
        "coreVersion": body["coreVersion"],
        "coreCommit": body["coreCommit"],
        "safeCorePolicyVersion": body["safeCorePolicyVersion"],
        "dbSchemaVersion": body["dbCompatibility"]["schemaVersion"],
        "sourceCommit": metadata["sourceCommit"],
        "bootstrapChoice": bootstrap,
        "nodeMonitorEnabled": monitor,
        "monitorControllerEnabled": controller,
        "nodeMonitorCommit": metadata["nodeMonitor"]["commit"] if monitor else None,
        "installerVersion": VERSION,
        "storageRoot": str(storage_root),
    }
    _private_atomic_json(root / INSTALL_MARKER, marker)


def install_fresh(target: Path, data: bytes, *, body: dict, metadata: dict,
                  bootstrap: str, monitor: bool, controller: bool,
                  storage_root: Path) -> None:
    if target.exists():
        marker = target / INSTALL_MARKER
        if marker.is_file():
            raise InstallError(
                f"{target} is already installed; use electrumx-update check/status/show/apply")
        raise InstallError(f"refusing to overwrite existing path {target}")
    state_dir = state_dir_for(target)
    if state_dir.exists():
        raise InstallError(
            f"refusing fresh install because updater state path already exists: {state_dir}")
    if controller:
        controller_prerequisites(require_sudo=True)
        controller_unit_body(target)
    # Derive the expected controller digest from the immutable, signed bundle
    # bytes before anything is extracted into the operator-writable tree.
    trusted_controller_sha256 = (
        _bundle_member_sha256(data, CONTROLLER_SCRIPT) if controller else None)

    # compose.yaml intentionally has a fixed project name because monitor and
    # controller isolation reference deterministic container names. Therefore a
    # fresh install must prove that no old project runtime exists before it can
    # create named volumes.
    require_clean_docker_project_runtime()
    require_clean_storage_root(storage_root)

    parent = target.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".electrumx-ravencoin-install-", dir=parent))
    moved = False
    controller_installed = False
    storage_prepared = False
    files = compose_files(bootstrap, monitor, controller)
    base = _compose_prefix(files)
    try:
        extract_bundle(data, staging)
        run_checked(["sh", "./setup.sh", "--bundled-core"], cwd=staging)
        write_storage_env(staging, storage_root)
        if monitor:
            write_monitor_env(staging)
        run_checked(base + ["config", "--quiet"], cwd=staging)

        # Build before activation; target still does not exist and no named
        # volumes have been created. This catches architecture/toolchain errors.
        run_checked(base + ["build"], cwd=staging)
        prepare_storage_layout(storage_root)
        storage_prepared = True
        initialize_storage_permissions(storage_root, monitor)

        os.replace(staging, target)
        moved = True
        if controller:
            if trusted_controller_sha256 is None:
                raise InstallError("trusted controller digest was not established")
            install_controller(target, trusted_controller_sha256)
            controller_installed = True
        try:
            activate_compose(target, base, bootstrap)
        except InstallError as exc:
            if bootstrap == "chainstrap":
                # Preserve the useful service output before the failed run is
                # torn down. Do not silently change the user's trust/transport
                # choice by falling back to P2P.
                subprocess.run(
                    base + ["logs", "--no-color", "--tail", "200", "chainstrap-bootstrap"],
                    cwd=target, check=False)
                raise InstallError(
                    "ChainStrap bootstrap failed; automatic P2P fallback is intentionally "
                    "disabled. This failed fresh run will be removed. Review the log above "
                    "and retry explicitly with --p2p-bootstrap if traditional sync is desired"
                ) from exc
            raise

        if monitor:
            verify_monitor_host_publish(target, files)

        # Marker/state are commit records and are written only after Compose
        # accepted the final release directory and the optional controller was
        # successfully installed.
        write_install_marker(
            target, body=body, metadata=metadata, bootstrap=bootstrap,
            monitor=monitor, controller=controller, storage_root=storage_root)
        write_initial_update_state(target, body)
    except BaseException:
        if moved and target.exists():
            # The preflight proved this project had no runtime resources before
            # this attempt, so volumes now carrying this project label belong to
            # the failed fresh run and are safe to remove. This prevents a retry
            # from silently inheriting a partial blockchain/ElectrumX database.
            subprocess.run(
                base + ["down", "--volumes", "--remove-orphans"], cwd=target,
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if controller_installed:
            uninstall_controller_best_effort()
        if state_dir.exists():
            shutil.rmtree(state_dir, ignore_errors=True)
        if moved and target.exists():
            shutil.rmtree(target, ignore_errors=True)
        if storage_prepared:
            cleanup_storage_layout_best_effort(storage_root)
        raise
    finally:
        if not moved and staging.exists():
            shutil.rmtree(staging, ignore_errors=True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.version:
        print(VERSION)
        return 0

    try:
        print_installer_banner()
        check_python_version()
        architecture = detect_architecture()

        if args.local_release_validation_dir:
            validation_dir = Path(args.local_release_validation_dir)
            print_local_validation_banner(validation_dir)
            public_key_hex, core_policy_public_key_hex, manifest_fetch, bundle_fetch = \
                load_local_release_validation(validation_dir)
        else:
            public_key_hex = RELEASE_PUBLIC_KEY_HEX
            core_policy_public_key_hex = PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX
            manifest_fetch = None
            bundle_fetch = None

        body = fetch_and_verify_release_manifest(
            public_key_hex=public_key_hex, fetch=manifest_fetch)
        verify_architecture(body, architecture)
        verify_running_installer(body)
        bundle, metadata = fetch_and_verify_bundle(
            body, fetch=bundle_fetch, public_key_hex=public_key_hex,
            core_policy_public_key_hex=core_policy_public_key_hex)

        ui_section("Verified release", "All signed release and independent Core-policy checks passed.")
        print(f"  ElectrumX    : {body['electrumxVersion']}")
        print(f"  Ravencoin    : Core {body['coreVersion']} @ {body['coreCommit'][:12]}")
        print(f"  Node Monitor : {metadata['nodeMonitor']['commit'][:12]}")
        print()

        interactive = sys.stdin.isatty()
        storage_root = None
        if not args.check_only or args.storage_root:
            storage_root = choose_storage_root(args, interactive)
        # Resolve choices even in --check-only so explicit unsupported controller
        # requests also have their prerequisites checked without changing state.
        bootstrap = choose_bootstrap(args, interactive)
        monitor = choose_monitor(args, interactive)
        controller = choose_monitor_controller(args, monitor, interactive)
        if controller:
            controller_prerequisites(require_sudo=False if args.check_only else True)

        print_installation_summary(storage_root, bootstrap, monitor, controller)

        if args.check_only:
            compose_command()
            print("check-only complete: no persistent changes were made")
            return 0

        compose_command()
        target = Path(args.install_dir).expanduser().resolve()
        if storage_root is None:
            raise InstallError("fresh install requires a selected project storage root")
        install_fresh(
            target, bundle, body=body, metadata=metadata,
            bootstrap=bootstrap, monitor=monitor, controller=controller,
            storage_root=storage_root)

        print(f"installation complete in {target}")
        print(f"project data storage: {storage_root}")
        print("Docker images remain in the daemon existing DockerRootDir")
        print(f"bootstrap: {bootstrap}")
        if monitor:
            print("Node Monitor: enabled at http://127.0.0.1:8899")
            print(f"Node Monitor credentials are stored privately in {target / MONITOR_ENV}")
        if controller:
            print("advanced host controls: enabled through the root-owned narrow Unix-socket helper")
        print("updates are never installed by silence/restart; explicit operator approval remains required")
        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
