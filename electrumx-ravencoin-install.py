#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""ElectrumX-Ravencoin single-file bootstrap installer.

Download it first, then run it explicitly:

    curl -fL -O https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py
    python3 electrumx-ravencoin-install.py

Never pipe this file into an interpreter (``curl ... | python3`` or
``curl ... | bash``). The whole point of a single downloadable file is that
an operator can read it before running it; piping defeats that.

Trust model: this Python file, once downloaded over HTTPS from GitHub, is the
initial bootstrap trust anchor. It cannot cryptographically authenticate
itself before you run it -- that is what "read it first" is for. Everything
this file subsequently downloads (the release manifest, the ElectrumX
artifact/image) is authenticated through the pinned Ed25519 public key
embedded below and the SHA-256 / image digests recorded in the signed
manifest. A mutable ``latest`` Docker tag is never treated as a trust
decision.

This installer never executes arbitrary remote shell fragments, never
installs updates unattended, and never overwrites an existing, populated
Core or ElectrumX datadir without an explicit operator decision.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from typing import Callable, Optional, Sequence

VERSION = "0.1.0"

# Same signature domain as core-safety/scripts/update_manifest.py, so a
# release-manifest.json signed once verifies identically whether checked by
# a running node's self-updater or by this standalone bootstrap installer.
# Duplicated deliberately: this file must remain runnable with nothing else
# from the repository present on the target host.
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

# Pinned ElectrumX RELEASE public key (Ed25519, 32 raw bytes, hex-encoded).
# This is the ONLY key material embedded in this file, and it is a PUBLIC
# key: it can verify signatures, never produce them. It is a distinct trust
# domain from the safe-Core policy signing key (POLICY_SIGNING_KEY, which
# never leaves the GitHub protected environment core-safety-signing) and
# distinct from any Core-policy key. Left empty in this development version:
# a real release build populates it at publish time, and this installer must
# refuse to run in release mode until it is populated (see
# `require_pinned_release_key`).
RELEASE_PUBLIC_KEY_HEX = ""

REPO = "ALENOC/electrumx-ravencoin"
RELEASES_LATEST_BASE = f"https://github.com/{REPO}/releases/latest/download"
MANIFEST_URL = f"{RELEASES_LATEST_BASE}/release-manifest.json"
SIGNATURE_URL = f"{RELEASES_LATEST_BASE}/release-manifest.sig"

SUPPORTED_ARCHITECTURES = ("amd64", "arm64")
MIN_PYTHON = (3, 9)

NODE_MONITOR_REPOSITORY = "https://github.com/ALENOC/ravencoin-node-monitor"
MONITOR_DASHBOARD_BIND = "127.0.0.1"
MONITOR_DASHBOARD_PORT = 8899

CHAINSTRAP_READY_MARKERS = ("chainstrap.blocks.json", "chainstrap.progress.json")
CORE_DATADIR_MARKERS = ("blocks", "chainstate", "debug.log")


class InstallError(RuntimeError):
    """Fatal, user-facing installer error. The installer always fails closed:
    every InstallError means zero further persistent changes are made."""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="electrumx-ravencoin-install.py",
        description="Install or verify an ElectrumX-Ravencoin node "
                     "(bundled Ravencoin Core, optional Node Monitor).")
    parser.add_argument("--version", action="store_true",
                        help="print the installer version and exit")
    parser.add_argument("--check-only", action="store_true",
                        help="run detection and verification only; make zero "
                             "persistent system changes")
    parser.add_argument("--chainstrap", action="store_true",
                        help="use ChainStrap Fast Verified Bootstrap "
                             "(default on a fresh bundled-Core install)")
    parser.add_argument("--p2p-bootstrap", action="store_true",
                        help="use traditional Ravencoin P2P synchronization")
    parser.add_argument("--with-monitor", action="store_true",
                        help="install Ravencoin Node Monitor")
    parser.add_argument("--without-monitor", action="store_true",
                        help="do not install Ravencoin Node Monitor")
    parser.add_argument("--with-monitor-controller", action="store_true",
                        help="enable the Node Monitor's privileged host "
                             "controller (bandwidth/connection management); "
                             "disabled by default, explicit opt-in only")
    return parser


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.chainstrap and args.p2p_bootstrap:
        parser.error("--chainstrap and --p2p-bootstrap are mutually exclusive")
    if args.with_monitor and args.without_monitor:
        parser.error("--with-monitor and --without-monitor are mutually exclusive")
    if args.with_monitor_controller and args.without_monitor:
        parser.error("--with-monitor-controller requires the monitor to be "
                     "enabled (drop --without-monitor)")
    return args


# --------------------------------------------------------------------------
# Host detection (pure where possible; side-effecting calls are thin and
# injectable so orchestration logic stays unit-testable without Docker).
# --------------------------------------------------------------------------

def detect_architecture(machine: Optional[str] = None) -> str:
    machine = (machine if machine is not None else platform.machine()).lower()
    if machine in ("x86_64", "amd64"):
        return "amd64"
    if machine in ("aarch64", "arm64"):
        return "arm64"
    raise InstallError(
        f"unsupported architecture {machine!r}; supported: "
        f"{', '.join(SUPPORTED_ARCHITECTURES)}")


def check_python_version(version_info: Optional[tuple] = None) -> None:
    version_info = version_info if version_info is not None else sys.version_info
    if (version_info[0], version_info[1]) < MIN_PYTHON:
        raise InstallError(
            f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ is required; found "
            f"{version_info[0]}.{version_info[1]}")


def detect_docker(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    return which("docker")


def detect_compose(
    which: Callable[[str], Optional[str]] = shutil.which,
    run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
) -> Optional[list]:
    """Return the compose invocation as an argv list, or None if unavailable.
    Prefers the ``docker compose`` plugin; falls back to standalone
    ``docker-compose`` if that is what the host has.
    """
    if which("docker") is not None:
        try:
            result = run(["docker", "compose", "version"],
                         capture_output=True, timeout=10, check=False)
            if result.returncode == 0:
                return ["docker", "compose"]
        except (OSError, subprocess.TimeoutExpired):
            pass
    if which("docker-compose") is not None:
        return ["docker-compose"]
    return None


def check_disk_space(path: str, required_bytes: int,
                     disk_usage: Callable[[str], object] = shutil.disk_usage) -> None:
    usage = disk_usage(path)
    if usage.free < required_bytes:
        raise InstallError(
            f"insufficient disk space at {path}: "
            f"{usage.free} bytes free, {required_bytes} required")


# --------------------------------------------------------------------------
# Existing-installation / existing-datadir safety
# --------------------------------------------------------------------------

def classify_datadir(path: str,
                     listdir: Callable[[str], list] = os.listdir,
                     exists: Callable[[str], bool] = os.path.exists) -> str:
    """Classify a Core datadir before any bootstrap decision is made.

    Returns one of: "empty", "core_valid", "chainstrap_validated",
    "ambiguous". Never mutates anything; callers must fail closed on
    "ambiguous" rather than guessing.
    """
    if not exists(path):
        return "empty"
    try:
        entries = set(listdir(path))
    except OSError:
        return "ambiguous"
    if not entries:
        return "empty"
    has_chainstrap_marker = any(m in entries for m in CHAINSTRAP_READY_MARKERS)
    has_core_marker = any(m in entries for m in CORE_DATADIR_MARKERS)
    if has_chainstrap_marker and has_core_marker:
        return "chainstrap_validated"
    if has_core_marker:
        return "core_valid"
    return "ambiguous"


def detect_existing_installation(
    compose_project_path: str,
    exists: Callable[[str], bool] = os.path.exists,
) -> bool:
    """True if a prior installation's declarative config is already present.
    A true result means: hand off to the updater, never re-bootstrap.
    """
    return exists(compose_project_path)


# --------------------------------------------------------------------------
# Signed release manifest verification
# --------------------------------------------------------------------------

def canonical_bytes(body: dict) -> bytes:
    return SIGNATURE_DOMAIN + json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def require_pinned_release_key(public_key_hex: str = RELEASE_PUBLIC_KEY_HEX) -> str:
    if not public_key_hex:
        raise InstallError(
            "this installer build has no pinned release public key; refusing "
            "to verify any release manifest (development build, not for use "
            "as a real release artifact)")
    return public_key_hex


def validate_manifest_body(body: dict) -> None:
    if not isinstance(body, dict):
        raise InstallError("release manifest body is not an object")
    for field in REQUIRED_MANIFEST_FIELDS:
        if field not in body:
            raise InstallError(f"release manifest missing required field {field!r}")
    if not isinstance(body["rollbackSafe"], bool):
        raise InstallError("rollbackSafe must be a boolean")
    if not isinstance(body["consensusImpact"], bool):
        raise InstallError("consensusImpact must be a boolean")
    if not isinstance(body["autoUpdateEligible"], bool):
        raise InstallError("autoUpdateEligible must be a boolean")
    if body["consensusImpact"] and body["autoUpdateEligible"]:
        raise InstallError(
            "malformed manifest: autoUpdateEligible cannot be true when "
            "consensusImpact is true")
    if not body["installerFilename"] or not body["installerDigest"]:
        raise InstallError("release manifest missing installer identity")


def verify_manifest_signature(document: dict, public_key_hex: str) -> dict:
    """Verify a signed release-manifest document and return its body dict,
    or raise InstallError. Fails closed on every malformed/untrusted input.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import (
            Ed25519PublicKey,
        )
    except ImportError as exc:
        raise InstallError(
            "the 'cryptography' Python package is required to verify the "
            "release manifest; install it first (e.g. `python3 -m pip "
            "install cryptography`) and re-run") from exc

    if not isinstance(document, dict):
        raise InstallError("release manifest document is not a JSON object")
    body = document.get("manifest")
    signature = document.get("signature")
    if not isinstance(body, dict) or not isinstance(signature, dict):
        raise InstallError(
            "release manifest document must contain 'manifest' and "
            "'signature'")
    if signature.get("algorithm") != SIGNATURE_ALGORITHM:
        raise InstallError(
            f"unsupported signature algorithm {signature.get('algorithm')!r}")
    try:
        raw_signature = base64.b64decode(signature.get("value", ""), validate=True)
    except Exception as exc:  # noqa: BLE001
        raise InstallError("release manifest signature is not valid base64") from exc

    try:
        public_bytes = bytes.fromhex(public_key_hex.strip())
    except ValueError as exc:
        raise InstallError("embedded release public key is malformed") from exc

    public_key = Ed25519PublicKey.from_public_bytes(public_bytes)
    try:
        public_key.verify(raw_signature, canonical_bytes(body))
    except InvalidSignature as exc:
        raise InstallError("release manifest signature does not verify") from exc

    validate_manifest_body(body)
    return body


def verify_architecture(body: dict, host_architecture: str) -> None:
    manifest_arch = body["architecture"]
    if host_architecture not in manifest_arch:
        raise InstallError(
            f"release manifest targets {manifest_arch!r}, host is "
            f"{host_architecture!r}")


def verify_artifact_digest(data: bytes, expected_digest: str) -> None:
    algorithm, _, expected_hex = expected_digest.partition(":")
    if algorithm != "sha256" or not expected_hex:
        raise InstallError(f"unsupported digest format {expected_digest!r}")
    actual_hex = hashlib.sha256(data).hexdigest()
    if actual_hex.lower() != expected_hex.lower():
        raise InstallError(
            "artifact digest mismatch: refusing to install an unverified "
            "artifact")


def verify_installer_digest(installer_bytes: bytes, expected_digest: str) -> None:
    verify_artifact_digest(installer_bytes, expected_digest)


# --------------------------------------------------------------------------
# Interactive choices (pure resolution logic; I/O isolated to `prompt`)
# --------------------------------------------------------------------------

def resolve_bootstrap_choice(
    *, chainstrap_flag: bool, p2p_flag: bool,
    existing_datadir_state: str, interactive: bool,
    prompt: Callable[[str], str] = input,
) -> str:
    """Returns "chainstrap", "p2p", or "preserve_existing".

    ``existing_datadir_state`` is the result of `classify_datadir`.
    """
    if chainstrap_flag and p2p_flag:
        raise InstallError("--chainstrap and --p2p-bootstrap are mutually exclusive")

    if existing_datadir_state == "core_valid":
        return "preserve_existing"
    if existing_datadir_state == "chainstrap_validated":
        return "preserve_existing"
    if existing_datadir_state == "ambiguous":
        raise InstallError(
            "existing datadir contents are not recognized as either an "
            "empty directory, a valid Core datadir, or a validated "
            "ChainStrap datadir; refusing to guess. Resolve this manually "
            "before re-running the installer")

    # existing_datadir_state == "empty": fresh bootstrap decision applies.
    if chainstrap_flag:
        return "chainstrap"
    if p2p_flag:
        return "p2p"
    if not interactive:
        return "chainstrap"

    answer = prompt(
        "Blockchain bootstrap method\n\n"
        "1. Fast Verified Bootstrap using ChainStrap [recommended, default]\n"
        "2. Traditional Ravencoin P2P synchronization\n\n"
        "Choice [1]: ").strip()
    if answer in ("", "1"):
        return "chainstrap"
    if answer == "2":
        return "p2p"
    raise InstallError(f"unrecognized bootstrap choice {answer!r}")


def resolve_monitor_choice(
    *, with_monitor_flag: bool, without_monitor_flag: bool, interactive: bool,
    prompt: Callable[[str], str] = input,
) -> bool:
    if with_monitor_flag and without_monitor_flag:
        raise InstallError("--with-monitor and --without-monitor are mutually exclusive")
    if with_monitor_flag:
        return True
    if without_monitor_flag:
        return False
    if not interactive:
        return True  # fresh interactive default is YES; non-interactive mirrors it

    answer = prompt(
        "Install Ravencoin Node Monitor?\n\n"
        "Y. Yes [recommended, default]\n"
        "N. No\n\n"
        "Choice [Y]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return True
    if answer in ("n", "no"):
        return False
    raise InstallError(f"unrecognized monitor choice {answer!r}")


def resolve_monitor_controller_choice(
    *, monitor_enabled: bool, with_controller_flag: bool, interactive: bool,
    prompt: Callable[[str], str] = input,
) -> bool:
    """The privileged host controller is opt-in only, and only ever asked
    about when the ordinary monitor itself is being installed."""
    if not monitor_enabled:
        return False
    if with_controller_flag:
        return True
    if not interactive:
        return False  # default is disabled; never enabled silently

    answer = prompt(
        "Enable advanced host controls\n"
        "(bandwidth and connection management)?\n\n"
        "y. Yes\n"
        "N. No [default]\n\n"
        "Choice [N]: ").strip().lower()
    if answer in ("y", "yes"):
        return True
    if answer in ("", "n", "no"):
        return False
    raise InstallError(f"unrecognized host-controller choice {answer!r}")


# --------------------------------------------------------------------------
# Node Monitor wiring (declarative only; never exposes Core RPC publicly)
# --------------------------------------------------------------------------

def generate_monitor_credentials(
    token_bytes: Callable[[int], bytes] = os.urandom,
) -> str:
    return base64.urlsafe_b64encode(token_bytes(32)).decode("ascii").rstrip("=")


def build_monitor_environment(*, core_network_alias: str, core_rpc_port: int,
                              electrumx_network_alias: str,
                              electrumx_rpc_port: int) -> dict:
    """Service-name based connectivity only; the operator never has to look
    up a container IP, and Core RPC is never bound to a public interface to
    make this work -- the monitor joins the same private Compose network.
    """
    return {
        "CORE_RPC_HOST": core_network_alias,
        "CORE_RPC_PORT": str(core_rpc_port),
        "ELECTRUMX_ENABLED": "true",
        "ELECTRUMX_RPC_HOST": electrumx_network_alias,
        "ELECTRUMX_RPC_PORT": str(electrumx_rpc_port),
        "MONITOR_DASHBOARD_BIND": MONITOR_DASHBOARD_BIND,
        "MONITOR_DASHBOARD_PORT": str(MONITOR_DASHBOARD_PORT),
    }


MONITOR_SECURITY_OPT = ["no-new-privileges:true"]
MONITOR_CAP_DROP = ["ALL"]


def build_monitor_service_definition(*, environment: dict,
                                     controller_enabled: bool) -> dict:
    """The ordinary monitor container is always unprivileged: no Docker
    socket, no CAP_NET_ADMIN, cap_drop ALL, no-new-privileges, read-only
    rootfs where supported, dashboard bound to loopback only. The privileged
    host controller (if enabled) is a wholly separate service/security
    domain and is never folded into this definition.
    """
    service = {
        "environment": dict(environment),
        "security_opt": list(MONITOR_SECURITY_OPT),
        "cap_drop": list(MONITOR_CAP_DROP),
        "read_only": True,
        "ports": [f"{MONITOR_DASHBOARD_BIND}:{MONITOR_DASHBOARD_PORT}:{MONITOR_DASHBOARD_PORT}"],
    }
    if controller_enabled:
        service["_controller_service_separate"] = True
    return service


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    if args.version:
        print(VERSION)
        return 0

    try:
        check_python_version()
        architecture = detect_architecture()
        docker_path = detect_docker()
        if docker_path is None:
            raise InstallError(
                "Docker was not found on this host; install Docker first "
                "(https://docs.docker.com/engine/install/) and re-run")
        compose_argv = detect_compose()
        if compose_argv is None:
            raise InstallError(
                "Docker Compose was not found on this host; install the "
                "Docker Compose plugin and re-run")

        print(f"detected architecture: {architecture}")
        print(f"docker: {docker_path}")
        print(f"compose: {' '.join(compose_argv)}")

        if args.check_only:
            print("--check-only: detection complete, no changes made")
            return 0

        raise InstallError(
            "interactive installation is not implemented in this "
            "development build; run with --check-only, or use the "
            "documented Compose files directly (see docs/DOCKER_COMPOSE.md)")
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
