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
import urllib.error
import urllib.request
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

SUPPORTED_ARCHITECTURES = ("amd64", "arm64")
MIN_PYTHON = (3, 9)

NODE_MONITOR_REPOSITORY = "https://github.com/ALENOC/ravencoin-node-monitor"
NODE_MONITOR_DIR = "ravencoin-node-monitor"
NODE_MONITOR_ENV_FILE = f"{NODE_MONITOR_DIR}/.env"
NODE_MONITOR_COMPOSE_OVERLAY = "compose.monitor.yaml"
MONITOR_DASHBOARD_BIND = "127.0.0.1"
MONITOR_DASHBOARD_PORT = 8899

# The monitor's own compose.yml has no published image (comment: "image:
# ghcr.io/OWNER/..." is deliberately commented out), so it must be built
# from a real checkout of its source, never invented or embedded here.
# Its ElectrumX admin RPC integration only works if the monitor container
# shares the electrumx container's network namespace (see that project's
# docker-compose.electrumx.example.yml); the target container name below
# is deterministic because this repository pins the Compose project name
# to "electrumx-ravencoin" (compose.yaml's top-level ``name:``) and the
# electrumx service declares no ``container_name`` override, so Compose's
# own naming rule (<project>-<service>-1) is stable across hosts.
ELECTRUMX_CONTAINER_NAME = "electrumx-ravencoin-electrumx-1"
CORE_RPC_INTERNAL_HOST = "ravencoin-core"
CORE_RPC_INTERNAL_PORT = 8766
ELECTRUMX_ADMIN_RPC_HOST = "127.0.0.1"
ELECTRUMX_ADMIN_RPC_PORT = 8000
RPC_SECRETS_MOUNT = "/run/raven-secrets"

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


def detect_git(which: Callable[[str], Optional[str]] = shutil.which) -> Optional[str]:
    return which("git")


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


def fetch_url(url: str, *, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
        return response.read()


def fetch_and_verify_release_manifest(
    *, public_key_hex: str = RELEASE_PUBLIC_KEY_HEX,
    manifest_url: str = MANIFEST_URL,
    fetch: Callable[[str], bytes] = fetch_url,
) -> dict:
    """Fetch the signed release manifest and return its verified body.

    Fails closed on every path: no pinned key, no network reachability, no
    valid JSON, no valid signature all raise InstallError, and the caller
    must never proceed to install anything without this call succeeding.
    """
    key_hex = require_pinned_release_key(public_key_hex)
    try:
        raw = fetch(manifest_url)
    except (OSError, urllib.error.URLError) as exc:
        raise InstallError(
            f"failed to fetch release manifest from {manifest_url}: {exc}") from exc
    try:
        document = json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise InstallError("release manifest is not valid JSON") from exc
    return verify_manifest_signature(document, key_hex)


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
# Node Monitor wiring against its REAL upstream contract (inspected from
# github.com/ALENOC/ravencoin-node-monitor: .env.example, Dockerfile,
# docker-compose.yml, docker-compose.electrumx.example.yml,
# docker-compose.bandwidth.yml, BANDWIDTH_CONTROL.md, SECURITY.md).
#
# That project ships no published image, so it must be built from a real
# clone. Its ElectrumX admin RPC is loopback-only inside the electrumx
# container, so the documented integration path is joining the monitor to
# electrumx's own network namespace via ``network_mode: "container:..."``,
# exactly as its docker-compose.electrumx.example.yml shows. Core RPC
# credentials are never put in plaintext env: the monitor supports
# CORE_RPC_USER_FILE / CORE_RPC_PASSWORD_FILE, so this reuses the same
# rpc-secrets named volume ravencoin-core and electrumx already read from
# in compose.yaml, instead of ever generating a second copy of that secret.
# --------------------------------------------------------------------------

def generate_monitor_credentials(
    token_bytes: Callable[[int], bytes] = os.urandom,
) -> str:
    return base64.urlsafe_b64encode(token_bytes(32)).decode("ascii").rstrip("=")


def clone_node_monitor(
    dir_path: str = NODE_MONITOR_DIR,
    git_argv: Optional[list] = None,
    *,
    run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
    exists: Callable[[str], bool] = os.path.exists,
) -> bool:
    """Clone the monitor source if it is not already present. Returns True
    if a clone happened, False if an existing checkout was left untouched
    (this installer never re-clones or resets an operator's checkout).
    """
    if exists(dir_path):
        return False
    argv = list(git_argv) if git_argv is not None else ["git"]
    cmd = argv + ["clone", "--depth", "1", NODE_MONITOR_REPOSITORY, dir_path]
    result = run(cmd, check=False)
    if result.returncode != 0:
        raise InstallError(
            f"failed to clone {NODE_MONITOR_REPOSITORY} into {dir_path} "
            f"(exit code {result.returncode})")
    return True


def build_monitor_environment() -> dict:
    """The subset of the monitor's real .env.example keys this installer
    can determine on the operator's behalf from the bundled stack's own
    compose.yaml topology. Every other key (NODE_NAME, MONITOR_PASSWORD,
    dashboard bind/port, history, thresholds, ...) keeps the monitor's own
    documented defaults and is never guessed here.
    """
    return {
        "CORE_RPC_HOST": CORE_RPC_INTERNAL_HOST,
        "CORE_RPC_PORT": str(CORE_RPC_INTERNAL_PORT),
        "CORE_RPC_USER_FILE": f"{RPC_SECRETS_MOUNT}/raven_rpc_user",
        "CORE_RPC_PASSWORD_FILE": f"{RPC_SECRETS_MOUNT}/raven_rpc_password",
        "ELECTRUMX_RPC_HOST": ELECTRUMX_ADMIN_RPC_HOST,
        "ELECTRUMX_RPC_PORT": str(ELECTRUMX_ADMIN_RPC_PORT),
    }


def write_monitor_env_file(
    path: str, *, monitor_password: str,
    os_open: Callable[[str, int, int], int] = os.open,
    fdopen: Callable = os.fdopen,
) -> None:
    """Write the monitor's own .env (its Dockerfile's ``env_file:``
    consumer), covering only the non-wiring settings this installer has an
    opinion on: a random dashboard password (bandwidth/connection-limit
    writes are refused by the monitor without one) and the documented
    RAM-only history default. RPC wiring lives in compose.monitor.yaml's
    ``environment:`` block instead, so it is never duplicated here.

    The file is created with mode 0600 from the first byte via O_CREAT
    (never a world/group-readable default mode fixed up afterwards), since
    it briefly holds ``monitor_password`` in plaintext.
    """
    content = (
        "# Generated by electrumx-ravencoin-install.py. Safe to hand-edit;\n"
        "# this installer never overwrites an existing file at this path.\n"
        "NODE_NAME=ElectrumX-Ravencoin bundled node\n"
        "MONITOR_USER=monitor\n"
        f"MONITOR_PASSWORD={monitor_password}\n"
        "HISTORY_ENABLED=true\n"
        "HISTORY_STORAGE=memory\n"
    )
    fd = os_open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(content)


def render_monitor_compose_overlay(*, controller_enabled: bool) -> str:
    """A compose.yaml-style overlay (see compose_files_for_bootstrap_choice)
    that adds the monitor service and appends its dashboard port publish to
    the existing electrumx service. Compose merges list fields like
    ``ports`` across -f layers by appending rather than replacing, which is
    what makes the second stanza below safe against compose.yaml's own
    electrumx ports list.
    """
    env = build_monitor_environment()
    lines = [
        "services:",
        "  monitor:",
        f"    build: ./{NODE_MONITOR_DIR}",
        "    container_name: ravencoin-node-monitor",
        "    restart: unless-stopped",
        "    depends_on:",
        "      electrumx:",
        "        condition: service_healthy",
        f'    network_mode: "container:{ELECTRUMX_CONTAINER_NAME}"',
        "    env_file:",
        f"      - {NODE_MONITOR_ENV_FILE}",
        "    environment:",
    ]
    for key, value in env.items():
        lines.append(f'      {key}: "{value}"')
    if controller_enabled:
        lines += [
            '      BANDWIDTH_CONTROL_ENABLED: "true"',
            '      BANDWIDTH_CONTROL_SOCKET: /run/ravencoin-bandwidth/control.sock',
        ]
    lines += [
        "    volumes:",
        "      - rpc-secrets:/run/raven-secrets:ro",
    ]
    if controller_enabled:
        lines.append(
            "      - /run/ravencoin-bandwidth:/run/ravencoin-bandwidth:ro")
    lines += [
        "    read_only: true",
        "    security_opt:",
        "      - no-new-privileges:true",
        "    cap_drop:",
        "      - ALL",
        "    tmpfs:",
        "      - /tmp",
        "    ports: []",
        "",
        "  electrumx:",
        "    ports:",
        f'      - "{MONITOR_DASHBOARD_BIND}:{MONITOR_DASHBOARD_PORT}:{MONITOR_DASHBOARD_PORT}"',
        "",
    ]
    return "\n".join(lines) + "\n"


def write_monitor_compose_overlay(
    path: str, *, controller_enabled: bool,
    open_func: Callable = open,
) -> None:
    """This overlay is installer-owned, not operator-edited, and is safe to
    regenerate on every run (unlike the monitor's own .env)."""
    with open_func(path, "w", encoding="utf-8") as handle:
        handle.write(render_monitor_compose_overlay(
            controller_enabled=controller_enabled))


BANDWIDTH_CONTROLLER_SETUP_GUIDANCE = """\
Advanced host controls selected. This installs a ROOT-OWNED systemd service
on THIS HOST (not inside any container) that can apply Linux `tc` bandwidth
shaping and recreate the Core/ElectrumX containers to change connection
limits. This installer never runs these commands for you; review them and
run them yourself:

    cd {monitor_dir}
    sudo cp contrib/ravencoin-bandwidth-controller.service.example \\
        /etc/systemd/system/ravencoin-bandwidth-controller.service
    sudo systemctl daemon-reload
    sudo systemctl enable --now ravencoin-bandwidth-controller.service

The example unit targets these container names, which already match this
deployment:

    electrumx-ravencoin-ravencoin-core-1
    electrumx-ravencoin-electrumx-1

See {monitor_dir}/BANDWIDTH_CONTROL.md and {monitor_dir}/CONNECTION_CONTROL.md
for the full security model before enabling it.
"""


# --------------------------------------------------------------------------
# Compose orchestration
# --------------------------------------------------------------------------

DEFAULT_COMPOSE_BASE = "compose.yaml"
DEFAULT_COMPOSE_CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
DEFAULT_DATADIR = os.environ.get(
    "ELECTRUMX_RAVENCOIN_DATADIR", os.path.join(os.getcwd(), "ravencoin-data"))
DEFAULT_INSTALL_MARKER = ".electrumx-ravencoin-installed.json"


def compose_files_for_bootstrap_choice(choice: str) -> list:
    """Fresh ChainStrap installs layer the ChainStrap overlay, which is where
    all of the transport-only security invariants (network_mode: none for
    the reindex step, pinned build args, cap_drop, no-new-privileges) live
    already in compose.chainstrap.yaml. Traditional P2P and "preserve an
    existing datadir" both just run the base stack.
    """
    if choice == "chainstrap":
        return [DEFAULT_COMPOSE_BASE, DEFAULT_COMPOSE_CHAINSTRAP_OVERLAY]
    if choice in ("p2p", "preserve_existing"):
        return [DEFAULT_COMPOSE_BASE]
    raise InstallError(f"unknown bootstrap choice {choice!r}")


def run_compose_up(
    compose_argv: list, compose_files: list, *,
    run: Callable[..., "subprocess.CompletedProcess"] = subprocess.run,
    cwd: Optional[str] = None,
) -> None:
    cmd = list(compose_argv)
    for compose_file in compose_files:
        cmd += ["-f", compose_file]
    cmd += ["up", "-d"]
    result = run(cmd, cwd=cwd, check=False)
    if result.returncode != 0:
        raise InstallError(f"docker compose up failed (exit code {result.returncode})")


def write_install_marker(
    path: str, *, bootstrap_choice: str, monitor_enabled: bool,
    controller_enabled: bool,
    open_func: Callable = open,
) -> None:
    """Records the installer's own decisions, not secrets, so a later run
    can tell `detect_existing_installation` "this host is already set up"
    and hand off to the updater instead of re-bootstrapping.
    """
    payload = {
        "bootstrapChoice": bootstrap_choice,
        "monitorEnabled": monitor_enabled,
        "monitorControllerEnabled": controller_enabled,
        "installerVersion": VERSION,
    }
    with open_func(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")


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

        manifest_body = fetch_and_verify_release_manifest()
        verify_architecture(manifest_body, architecture)
        print(f"release manifest verified: electrumx {manifest_body['electrumxVersion']} "
             f"(core {manifest_body['coreVersion']})")

        if args.check_only:
            print("--check-only: detection complete, release trust metadata "
                 "validated, no changes made")
            return 0

        if detect_existing_installation(DEFAULT_INSTALL_MARKER):
            print(
                f"an existing installation was detected ({DEFAULT_INSTALL_MARKER}); "
                "this installer never re-bootstraps an existing install. Use "
                "electrumx-update check / status / show / apply instead.")
            return 0

        interactive = sys.stdin.isatty()
        datadir_state = classify_datadir(DEFAULT_DATADIR)
        bootstrap_choice = resolve_bootstrap_choice(
            chainstrap_flag=args.chainstrap, p2p_flag=args.p2p_bootstrap,
            existing_datadir_state=datadir_state, interactive=interactive)
        monitor_enabled = resolve_monitor_choice(
            with_monitor_flag=args.with_monitor,
            without_monitor_flag=args.without_monitor, interactive=interactive)
        controller_enabled = resolve_monitor_controller_choice(
            monitor_enabled=monitor_enabled,
            with_controller_flag=args.with_monitor_controller,
            interactive=interactive)

        print(f"bootstrap method: {bootstrap_choice}")
        print(f"node monitor: {'enabled' if monitor_enabled else 'disabled'}")
        if monitor_enabled:
            print(f"node monitor host controller: "
                 f"{'enabled' if controller_enabled else 'disabled'}")

        compose_files = compose_files_for_bootstrap_choice(bootstrap_choice)

        if monitor_enabled:
            git_path = detect_git()
            if git_path is None:
                raise InstallError(
                    "git was not found on this host; install git first "
                    "or re-run with --without-monitor")
            cloned = clone_node_monitor(NODE_MONITOR_DIR)
            if cloned:
                print(f"cloned {NODE_MONITOR_REPOSITORY} into {NODE_MONITOR_DIR}")
            if not os.path.exists(NODE_MONITOR_ENV_FILE):
                monitor_password = generate_monitor_credentials()
                write_monitor_env_file(
                    NODE_MONITOR_ENV_FILE, monitor_password=monitor_password)
                print(f"generated {NODE_MONITOR_ENV_FILE} with a random "
                     "dashboard password")
            write_monitor_compose_overlay(
                NODE_MONITOR_COMPOSE_OVERLAY, controller_enabled=controller_enabled)
            compose_files = compose_files + [NODE_MONITOR_COMPOSE_OVERLAY]

        run_compose_up(compose_argv, compose_files)
        write_install_marker(
            DEFAULT_INSTALL_MARKER, bootstrap_choice=bootstrap_choice,
            monitor_enabled=monitor_enabled, controller_enabled=controller_enabled)

        if monitor_enabled:
            print(
                f"Node Monitor deployed from {NODE_MONITOR_REPOSITORY}, "
                f"sharing network namespace with {ELECTRUMX_CONTAINER_NAME}. "
                f"Its dashboard binds to "
                f"{MONITOR_DASHBOARD_BIND}:{MONITOR_DASHBOARD_PORT} only; "
                f"it is never exposed publicly by default. Credentials are "
                f"in {NODE_MONITOR_ENV_FILE}.")
            if controller_enabled:
                print(BANDWIDTH_CONTROLLER_SETUP_GUIDANCE.format(
                    monitor_dir=NODE_MONITOR_DIR))

        return 0
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
