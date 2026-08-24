# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Production I/O for the explicitly approved ElectrumX updater.

Nothing in this module performs discovery or grants trust. The caller supplies
an already signature-verified manifest and an artifact whose SHA-256 was
verified again immediately before use. This module then stages the release,
builds it while the current node is still running, stops the old stack, swaps
the installation directory with same-filesystem renames, starts the new stack,
and evaluates the concrete runtime health gates.

Blockchain, ElectrumX DB, monitor data and Core configuration are held through
four Docker local-driver volumes whose ``device`` is an installer-selected host
bind directory. The updater proves those existing bind mappings before it can
stop the running node, proves the candidate Compose model resolves to the same
four host directories, and rechecks the mapping before starting the new stack.
Operator configuration and host-side secrets are copied only from a small
allowlist of known mutable paths. ChainStrap is deliberately *not* re-run during
a software update.

A normal software update is also forbidden from rotating either production
trust root. The candidate bundle must carry the same ElectrumX release/update
public key and the same safe-Core policy public key as the currently installed
release. The bundled safe-Core policy is re-verified under that already trusted
Core-policy key and must certify the exact manifest Core identity/report. Key
rotation therefore needs a separate, explicitly reviewed mechanism rather than
being smuggled inside an otherwise ordinary signed software update.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath
from typing import Callable, Optional, Sequence

import policy as core_policy
from update_decision import HealthGateResult

REPOSITORY = "ALENOC/electrumx-ravencoin"
CORE_REPOSITORY = "RavenProject/Ravencoin"
#: Pinned explicitly on every Compose invocation (GLM53-RVN-008): an exported
#: COMPOSE_PROJECT_NAME in the operator environment must not detach the
#: updater from the project namespace it validates and switches.
COMPOSE_PROJECT_NAME = "electrumx-ravencoin"
INSTALL_MARKER = ".electrumx-ravencoin-installed.json"
BASE_COMPOSE = "compose.yaml"
STORAGE_OVERLAY = "compose.storage.yaml"
MONITOR_OVERLAY = "compose.monitor.yaml"
MONITOR_CONTROLLER_OVERLAY = "compose.monitor-controller.yaml"
CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
TLS_OVERLAY = "compose.tls.yaml"
EXISTING_CORE_COMPOSE = "compose.existing-core.yaml"
BUNDLE_METADATA = "release-install-metadata.json"

#: Every Compose file a signed release ships. A COMPOSE_FILE selection in the
#: operator's .env may only name files from this set: anything else is a
#: host-local overlay that no release carries, so it cannot survive a release
#: switch and it silently detaches the model the operator's stack runs from the
#: model this updater proves.
RELEASE_COMPOSE_FILES = frozenset({
    BASE_COMPOSE,
    STORAGE_OVERLAY,
    MONITOR_OVERLAY,
    MONITOR_CONTROLLER_OVERLAY,
    CHAINSTRAP_OVERLAY,
    TLS_OVERLAY,
    EXISTING_CORE_COMPOSE,
})

#: Compose reads these from the process environment and from .env. The updater
#: pins project name and file set explicitly on every call it makes, and the
#: staged setup.sh must be equally independent of the invoking environment.
COMPOSE_ENV_OVERRIDES = ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PROJECT_NAME",
                         "COMPOSE_PATH_SEPARATOR")
MONITOR_PATH = "vendor/ravencoin-node-monitor"
UPDATE_PUBLIC_KEY_PATH = "core-safety/production/update-signing-public-key.hex"
CORE_POLICY_PATH = "core-safety/production/safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY_PATH = "core-safety/production/core-policy-signing-public-key.hex"

STORAGE_BIND_ENV = {
    "ravencoin-data": "RAVENCOIN_DATA_HOST_DIR",
    "ravencoin-config": "RAVENCOIN_CONFIG_HOST_DIR",
    "electrumx-data": "ELECTRUMX_DATA_HOST_DIR",
    "monitor-data": "MONITOR_DATA_HOST_DIR",
}
LEGACY_NAMED_VOLUME_STORAGE_MODE = "named-volumes"
BIND_BACKED_STORAGE_MODE = "bind-backed"

ACTIVE_STORAGE_MOUNTS = {
    "ravencoin-core": {
        "ravencoin-data": "/var/lib/ravencoin",
        "ravencoin-config": "/var/lib/ravencoin-config",
    },
    "electrumx": {
        "electrumx-data": "/var/lib/electrumx/db",
    },
    "monitor": {
        "monitor-data": "/data",
    },
}

CHECKPOINT_HEIGHT = 4_487_775
CHECKPOINT_HASH = "000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd"

MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
MAX_BUNDLE_FILES = 8192
MAX_BUNDLE_FILE_BYTES = 256 * 1024 * 1024
MAX_BUNDLE_EXTRACTED_BYTES = 768 * 1024 * 1024
SHA256_RE = re.compile(r"^sha256:([0-9a-f]{64})$")
RAW_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

REQUIRED_BUNDLE_PATHS = frozenset({
    BASE_COMPOSE,
    STORAGE_OVERLAY,
    CHAINSTRAP_OVERLAY,
    MONITOR_OVERLAY,
    MONITOR_CONTROLLER_OVERLAY,
    TLS_OVERLAY,
    EXISTING_CORE_COMPOSE,
    "setup.sh",
    ".env.example",
    "docker/core/bootstrap-reindex.sh",
    BUNDLE_METADATA,
    UPDATE_PUBLIC_KEY_PATH,
    CORE_POLICY_PATH,
    CORE_POLICY_PUBLIC_KEY_PATH,
    f"{MONITOR_PATH}/Dockerfile",
    f"{MONITOR_PATH}/.env.example",
})

# Only operator-owned mutable state may cross a release-directory boundary.
# No source file, Compose file, executable or signing material is copied from
# the old release into the new one.
PERSISTENT_PATHS = (
    ".env",
    ".secrets",
    "certs",
    "contrib/electrumx.env",
    f"{MONITOR_PATH}/.env",
)


class UpdateRuntimeError(RuntimeError):
    pass


def _safe_member_name(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise UpdateRuntimeError(f"unsafe bundle path {name!r}")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise UpdateRuntimeError(f"unsafe bundle path {name!r}")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_release_asset_url(url: str, *, expected_filename: Optional[str] = None) -> str:
    """Accept only one concrete tagged release asset in our own repository.

    The URL is not a trust anchor; the signed digest is. Restricting the URL
    still prevents the updater from becoming a generic authenticated downloader
    if its local state file is tampered with.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise UpdateRuntimeError("release asset URL is malformed") from exc
    if parsed.scheme != "https" or parsed.hostname != "github.com" or \
            parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise UpdateRuntimeError("release asset URL is outside the approved HTTPS GitHub namespace")
    prefix = f"/{REPOSITORY}/releases/download/"
    if not parsed.path.startswith(prefix):
        raise UpdateRuntimeError("release asset URL is outside the approved repository")
    remainder = parsed.path[len(prefix):]
    parts = remainder.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise UpdateRuntimeError("release asset URL must name one concrete tag and file")
    tag, filename = parts
    if tag in ("latest", "latest/download") or any(value in (".", "..") for value in parts):
        raise UpdateRuntimeError("mutable or traversal release URL refused")
    if "/" in tag or "\\" in tag or "/" in filename or "\\" in filename:
        raise UpdateRuntimeError("release asset URL contains an invalid path component")
    if expected_filename is not None and filename != expected_filename:
        raise UpdateRuntimeError(
            f"release asset filename {filename!r} does not equal {expected_filename!r}")
    return tag


def fetch_small_release_asset(url: str, *, max_bytes: int = 2 * 1024 * 1024,
                              timeout: int = 30) -> bytes:
    validate_release_asset_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "electrumx-update"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    if int(declared) > max_bytes:
                        raise UpdateRuntimeError("release metadata exceeds size limit")
                except ValueError as exc:
                    raise UpdateRuntimeError("invalid Content-Length") from exc
            data = response.read(max_bytes + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateRuntimeError(f"release metadata download failed: {exc}") from exc
    if len(data) > max_bytes:
        raise UpdateRuntimeError("release metadata exceeds size limit")
    return data


def download_verified_artifact(url: str, *, expected_digest: str,
                               directory: Path, timeout: int = 180) -> Path:
    validate_release_asset_url(url, expected_filename="electrumx-ravencoin-bundle.tar.gz")
    match = SHA256_RE.fullmatch(expected_digest or "")
    if match is None:
        raise UpdateRuntimeError("signed artifact digest is malformed")
    directory.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".electrumx-update-artifact-", dir=directory)
    path = Path(temporary_name)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb") as output:
            request = urllib.request.Request(url, headers={"User-Agent": "electrumx-update"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    declared = response.headers.get("Content-Length")
                    if declared is not None:
                        try:
                            if int(declared) > MAX_ARTIFACT_BYTES:
                                raise UpdateRuntimeError("release artifact exceeds size limit")
                        except ValueError as exc:
                            raise UpdateRuntimeError("invalid Content-Length") from exc
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > MAX_ARTIFACT_BYTES:
                            raise UpdateRuntimeError("release artifact exceeds size limit")
                        digest.update(chunk)
                        output.write(chunk)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise UpdateRuntimeError(f"release artifact download failed: {exc}") from exc
            output.flush()
            os.fsync(output.fileno())
        if digest.hexdigest() != match.group(1):
            raise UpdateRuntimeError("release artifact SHA-256 mismatch")
        os.chmod(path, 0o600)
        return path
    except BaseException:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        raise


def _read_tar_text(archive: tarfile.TarFile, name: str) -> str:
    member = archive.getmember(name)
    handle = archive.extractfile(member)
    if handle is None:
        raise UpdateRuntimeError(f"cannot read {name!r} from release bundle")
    try:
        return handle.read().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise UpdateRuntimeError(f"bundle file {name!r} is not UTF-8") from exc


def _read_installed_key(path: Path, label: str) -> str:
    try:
        value = path.read_text(encoding="ascii").strip().lower()
    except OSError as exc:
        raise UpdateRuntimeError(
            f"cannot read installed {label} trust root {path}: {exc}") from exc
    if not RAW_SHA256_RE.fullmatch(value):
        raise UpdateRuntimeError(f"installed {label} trust root is malformed")
    return value


def _verify_bundle_trust_continuity(
        archive: tarfile.TarFile, manifest: dict, *,
        trusted_update_public_key_hex: str,
        trusted_core_policy_public_key_hex: str) -> None:
    """Keep both trust roots stable across ordinary software updates."""
    update_key = (trusted_update_public_key_hex or "").strip().lower()
    core_key = (trusted_core_policy_public_key_hex or "").strip().lower()
    if not RAW_SHA256_RE.fullmatch(update_key):
        raise UpdateRuntimeError("trusted ElectrumX update public key is malformed")
    if not RAW_SHA256_RE.fullmatch(core_key):
        raise UpdateRuntimeError("trusted safe-Core policy public key is malformed")

    bundled_update_key = _read_tar_text(archive, UPDATE_PUBLIC_KEY_PATH).strip().lower()
    bundled_core_key = _read_tar_text(archive, CORE_POLICY_PUBLIC_KEY_PATH).strip().lower()
    if bundled_update_key != update_key:
        raise UpdateRuntimeError(
            "ordinary software update attempted to rotate the ElectrumX release/update trust root")
    if bundled_core_key != core_key:
        raise UpdateRuntimeError(
            "ordinary software update attempted to rotate the safe-Core policy trust root")

    try:
        document = json.loads(_read_tar_text(archive, CORE_POLICY_PATH))
    except json.JSONDecodeError as exc:
        raise UpdateRuntimeError("bundle safe-Core policy is invalid JSON") from exc
    public_bytes = bytes.fromhex(core_key)
    trusted = {core_policy.key_id_for(public_bytes): public_bytes}
    try:
        body = core_policy.verify_policy(
            document, trusted,
            minimum_policy_version=int(manifest.get("safeCorePolicyVersion", 0)))
    except (core_policy.PolicyError, TypeError, ValueError) as exc:
        raise UpdateRuntimeError(f"bundle safe-Core policy does not verify: {exc}") from exc

    if body.get("policyVersion") != manifest.get("safeCorePolicyVersion"):
        raise UpdateRuntimeError(
            "bundle safe-Core policy version disagrees with signed release manifest")
    entry = core_policy.lookup_release(
        body, str(manifest.get("coreRepository", "")), str(manifest.get("coreCommit", "")))
    if entry is None or entry.get("status") != "KNOWN_SAFE":
        raise UpdateRuntimeError(
            "bundle safe-Core policy does not certify the exact manifest Core identity")
    if entry.get("version") != manifest.get("coreVersion") or \
            entry.get("tag") != manifest.get("coreTag"):
        raise UpdateRuntimeError(
            "bundle safe-Core policy Core version/tag disagrees with signed release manifest")
    if entry.get("reportDigest") != manifest.get("certificationReportDigest"):
        raise UpdateRuntimeError(
            "bundle safe-Core certification report digest disagrees with signed release manifest")
    if (entry.get("certification") or {}).get("result") != "PASS":
        raise UpdateRuntimeError(
            "bundle safe-Core policy entry lacks passing certification evidence")


def validate_bundle_file(
        path: Path, manifest: dict, *,
        trusted_update_public_key_hex: Optional[str] = None,
        trusted_core_policy_public_key_hex: Optional[str] = None) -> dict:
    expected = manifest.get("artifactDigest")
    match = SHA256_RE.fullmatch(expected or "")
    if match is None or _sha256_file(path) != match.group(1):
        raise UpdateRuntimeError("release bundle digest no longer matches signed manifest")
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise UpdateRuntimeError("release bundle exceeds size limit")
    try:
        archive = tarfile.open(path, mode="r:gz")
    except tarfile.TarError as exc:
        raise UpdateRuntimeError("release artifact is not a valid gzip tar archive") from exc

    with archive:
        members = archive.getmembers()
        if len(members) > MAX_BUNDLE_FILES:
            raise UpdateRuntimeError("release bundle contains too many entries")
        names = set()
        total = 0
        for member in members:
            normalized = _safe_member_name(member.name).as_posix()
            if normalized in names:
                raise UpdateRuntimeError(f"duplicate bundle path {normalized!r}")
            names.add(normalized)
            if not (member.isfile() or member.isdir()):
                raise UpdateRuntimeError(
                    f"bundle contains forbidden link/device/special entry {normalized!r}")
            if member.isfile():
                if member.size < 0 or member.size > MAX_BUNDLE_FILE_BYTES:
                    raise UpdateRuntimeError(f"bundle file {normalized!r} has unsafe size")
                total += member.size
                if total > MAX_BUNDLE_EXTRACTED_BYTES:
                    raise UpdateRuntimeError("release bundle expands beyond the size limit")

        missing = REQUIRED_BUNDLE_PATHS - names
        if missing:
            raise UpdateRuntimeError(
                "release bundle is incomplete: " + ", ".join(sorted(missing)))

        try:
            metadata = json.loads(_read_tar_text(archive, BUNDLE_METADATA))
        except json.JSONDecodeError as exc:
            raise UpdateRuntimeError("release install metadata is invalid JSON") from exc
        if not isinstance(metadata, dict) or metadata.get("schemaVersion") != 1:
            raise UpdateRuntimeError("release install metadata has unsupported schema")
        if metadata.get("electrumxVersion") != manifest.get("electrumxVersion"):
            raise UpdateRuntimeError("bundle ElectrumX version disagrees with signed manifest")
        if metadata.get("sourceRepository") != REPOSITORY:
            raise UpdateRuntimeError("bundle source repository is unexpected")
        if not COMMIT_RE.fullmatch(str(metadata.get("sourceCommit", ""))):
            raise UpdateRuntimeError("bundle source commit is malformed")
        monitor = metadata.get("nodeMonitor") or {}
        if monitor.get("repository") != "ALENOC/ravencoin-node-monitor" or \
                not COMMIT_RE.fullmatch(str(monitor.get("commit", ""))) or \
                monitor.get("bundledPath") != MONITOR_PATH:
            raise UpdateRuntimeError("bundle Node Monitor pin is malformed")

        compose = _read_tar_text(archive, BASE_COMPOSE)
        if f"RAVENCOIN_SOURCE_COMMIT: {manifest.get('coreCommit')}" not in compose:
            raise UpdateRuntimeError("bundle Core commit disagrees with signed manifest")
        if "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin" not in compose:
            raise UpdateRuntimeError("bundle Core source is not RavenProject/Ravencoin")
        if f"RAVENCOIN_VERSION: {manifest.get('coreVersion')}" not in compose:
            raise UpdateRuntimeError("bundle Core version disagrees with signed manifest")

        chainstrap = _read_tar_text(archive, CHAINSTRAP_OVERLAY)
        reindex = _read_tar_text(archive, "docker/core/bootstrap-reindex.sh")
        if "network_mode: none" not in chainstrap:
            raise UpdateRuntimeError("ChainStrap validation lost Docker network isolation")
        if reindex.count("-connect=0") < 2:
            raise UpdateRuntimeError("ChainStrap import/probe lost explicit peer suppression")
        for required in ("getbestblockhash", "getblockhash", "listassets",
                         "getassetdata", "listaddressesbyasset"):
            if required not in reindex:
                raise UpdateRuntimeError(
                    f"ChainStrap post-reindex gate lost required check {required!r}")

        monitor_compose = _read_tar_text(archive, MONITOR_OVERLAY)
        for required in ("no-new-privileges:true", "cap_drop:", "- ALL",
                         '"127.0.0.1:8899:8899/tcp"'):
            if required not in monitor_compose:
                raise UpdateRuntimeError(
                    f"Node Monitor isolation lost required invariant {required!r}")

        if (trusted_update_public_key_hex is None) != \
                (trusted_core_policy_public_key_hex is None):
            raise UpdateRuntimeError(
                "both updater trust roots must be supplied together for continuity validation")
        if trusted_update_public_key_hex is not None:
            _verify_bundle_trust_continuity(
                archive, manifest,
                trusted_update_public_key_hex=trusted_update_public_key_hex,
                trusted_core_policy_public_key_hex=trusted_core_policy_public_key_hex)
        return metadata


def extract_bundle_file(path: Path, destination: Path) -> None:
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            relative = _safe_member_name(member.name)
            target = destination.joinpath(*relative.parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = archive.extractfile(member)
            if source is None:
                raise UpdateRuntimeError(f"cannot extract {member.name!r}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(target, flags, 0o600)
            try:
                with os.fdopen(fd, "wb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
            except BaseException:
                try:
                    target.unlink()
                except FileNotFoundError:
                    pass
                raise
            mode = 0o755 if member.mode & stat.S_IXUSR else 0o644
            os.chmod(target, mode)


def _preserve_ownership(source_stat: os.stat_result, destination: Path) -> None:
    """Keep operator state owned by the operator across a release switch.

    The updater runs as root, so a plain copy hands every preserved file to
    root:root.  Permission bits alone are not enough: a sidecar that
    bind-mounts this state under an unprivileged uid (the Node Monitor reads
    .secrets as 1000:1000) then loses access and crash-loops after an
    otherwise healthy update.  Ownership is part of the operator state and is
    carried over unchanged.
    """
    current = destination.stat()
    if (current.st_uid, current.st_gid) == (source_stat.st_uid, source_stat.st_gid):
        return
    try:
        os.chown(destination, source_stat.st_uid, source_stat.st_gid)
    except OSError as exc:
        raise UpdateRuntimeError(
            f"cannot preserve operator ownership of {destination}; "
            "run the updater with the privileges required to restore "
            f"uid {source_stat.st_uid} gid {source_stat.st_gid}") from exc


def _copy_mutable_path(source: Path, destination: Path) -> None:
    if source.is_symlink():
        raise UpdateRuntimeError(f"refusing symlink in persistent operator state: {source}")
    if source.is_dir():
        source_stat = source.stat()
        destination.mkdir(parents=True, exist_ok=True)
        _preserve_ownership(source_stat, destination)
        os.chmod(destination, 0o700)
        for child in source.iterdir():
            _copy_mutable_path(child, destination / child.name)
        return
    if not source.is_file():
        raise UpdateRuntimeError(f"persistent operator state is not a regular file: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    source_stat = source.stat()
    shutil.copyfile(source, destination)
    _preserve_ownership(source_stat, destination)
    os.chmod(destination, source_stat.st_mode & 0o777)


def copy_persistent_state(old_root: Path, new_root: Path) -> None:
    for relative in PERSISTENT_PATHS:
        source = old_root / relative
        if source.exists() or source.is_symlink():
            _copy_mutable_path(source, new_root / relative)


def read_install_marker(root: Path) -> dict:
    path = root / INSTALL_MARKER
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise UpdateRuntimeError(
            f"cannot read verified installer marker {path}; refusing production update") from exc
    if not isinstance(marker, dict) or marker.get("schemaVersion") != 1:
        raise UpdateRuntimeError("unsupported installer marker schema")
    if marker.get("bootstrapChoice") not in ("chainstrap", "p2p"):
        raise UpdateRuntimeError("installer marker has unknown bootstrap choice")
    if not isinstance(marker.get("nodeMonitorEnabled"), bool):
        raise UpdateRuntimeError("installer marker has invalid Node Monitor setting")
    if marker.get("monitorControllerEnabled") not in (None, False, True):
        raise UpdateRuntimeError("installer marker has invalid monitor-controller setting")
    return marker


def _required_update_compose_files(marker: dict) -> list[str]:
    # ChainStrap is a completed one-shot bootstrap and intentionally does not
    # run during software updates.
    #
    # Modern installs use compose.storage.yaml for bind-backed named volumes.
    # A node adopted from legacy 1.13.1 deliberately preserves Docker-managed
    # named volumes instead.  That storage mode is persistent installation
    # state and must survive every later normal update without another legacy
    # adoption prompt.
    if marker.get("storageMode") == LEGACY_NAMED_VOLUME_STORAGE_MODE:
        if marker.get("nodeMonitorEnabled") or marker.get("monitorControllerEnabled"):
            raise UpdateRuntimeError(
                "named-volume legacy storage cannot implicitly absorb "
                "an in-project monitor/controller")
        files = [BASE_COMPOSE]
    else:
        files = [BASE_COMPOSE, STORAGE_OVERLAY]

    if marker.get("nodeMonitorEnabled"):
        files.append(MONITOR_OVERLAY)
    if marker.get("monitorControllerEnabled"):
        files.append(MONITOR_CONTROLLER_OVERLAY)
    return files


def update_compose_files(marker: dict, root: Path) -> list[str]:
    # Start with the files implied by the signed installer marker, then carry
    # forward any release-owned overlays explicitly selected by COMPOSE_FILE
    # in the operator .env.  The .env is persistent across the atomic release
    # switch, so silently dropping one of its release overlays would recreate
    # a different runtime model after promotion (for example, losing
    # compose.tls.yaml and therefore the public ElectrumX TLS listener).
    #
    # ChainStrap is deliberately excluded: it is a completed one-shot
    # bootstrap and must never be re-run by a software update.
    files = list(_required_update_compose_files(marker))
    for filename in prove_env_compose_selection(root):
        if filename == CHAINSTRAP_OVERLAY:
            continue
        if filename not in files:
            files.append(filename)

    for filename in files:
        path = root / filename
        if path.is_symlink() or not path.is_file():
            raise UpdateRuntimeError(
                f"running node depends on Compose file {filename!r}, but it is absent or unsafe")
    return files


def _env_compose_selection(root: Path) -> list[str]:
    """Return the COMPOSE_FILE entries declared in the installed .env."""
    env_path = root / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        raise UpdateRuntimeError("installer .env is missing or is a symlink")
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise UpdateRuntimeError(f"cannot read installer .env: {exc}") from exc
    raw: Optional[str] = None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != "COMPOSE_FILE":
            continue
        if raw is not None:
            raise UpdateRuntimeError("installer .env contains duplicate COMPOSE_FILE")
        raw = value.strip()
    if raw is None:
        return []
    # COMPOSE_PATH_SEPARATOR defaults to ":" on POSIX, so ":" is the only
    # separator this code has to honour.  A comma-only value is still split
    # so the entries are named in the refusal message instead of appearing
    # as one unreadable blob; either way the check stays fail-closed,
    # because an unsplit value cannot match a release Compose file name.
    separator = "," if ("," in raw and ":" not in raw) else ":"
    return [entry.strip() for entry in raw.split(separator) if entry.strip()]


def prove_env_compose_selection(root: Path) -> list[str]:
    """Refuse to update a node whose .env selects non-release Compose files.

    A COMPOSE_FILE naming a host-local overlay breaks two invariants at once.
    The overlay is in no release, so any implicit ``docker compose`` resolution
    in the staged release tree stats a file that does not exist there, and the
    running stack's real model differs from the model this updater proves with
    its own explicit ``-f`` selection. Both are caught here, before the update
    mutates anything.
    """
    entries = _env_compose_selection(root)
    unknown = [entry for entry in entries if entry not in RELEASE_COMPOSE_FILES]
    if unknown:
        raise UpdateRuntimeError(
            "installer .env selects Compose file(s) that no release ships: "
            + ", ".join(sorted(unknown))
            + "; these cannot survive a release switch. Remove them from "
              "COMPOSE_FILE in .env (and fold any required setting into the "
              "release Compose model) before updating")
    missing = [entry for entry in entries
               if (root / entry).is_symlink() or not (root / entry).is_file()]
    if missing:
        raise UpdateRuntimeError(
            "installer .env selects Compose file(s) absent or unsafe in the "
            "installed release: " + ", ".join(sorted(missing)))
    return entries


def _read_storage_env(root: Path) -> dict[str, Path]:
    env_path = root / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        raise UpdateRuntimeError("installer .env is missing or is a symlink")
    values: dict[str, str] = {}
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        raise UpdateRuntimeError(f"cannot read installer .env: {exc}") from exc
    wanted = set(STORAGE_BIND_ENV.values())
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in wanted:
            continue
        if key in values:
            raise UpdateRuntimeError(f"installer .env contains duplicate {key}")
        values[key] = value.strip()
    missing = sorted(wanted - set(values))
    if missing:
        raise UpdateRuntimeError(
            "installer .env is missing storage binding(s): " + ", ".join(missing))

    result = {}
    for logical, key in STORAGE_BIND_ENV.items():
        raw = values[key]
        if not raw or "${" in raw or "\x00" in raw:
            raise UpdateRuntimeError(f"installer storage binding {key} is malformed")
        path = Path(raw)
        if not path.is_absolute():
            raise UpdateRuntimeError(f"installer storage binding {key} must be absolute")
        try:
            info = path.lstat()
        except OSError as exc:
            raise UpdateRuntimeError(
                f"installer storage binding {key} does not exist: {path}: {exc}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise UpdateRuntimeError(
                f"installer storage binding {key} must be an existing non-symlink directory")
        result[logical] = path.resolve()
    if len(set(result.values())) != len(result):
        raise UpdateRuntimeError("two installer storage volumes resolve to the same host directory")
    return result


def _child_environment() -> dict[str, str]:
    # Compose honours COMPOSE_FILE and friends from the inherited environment.
    # Nothing the updater runs may take its file set, profiles or project
    # namespace from whatever shell or unit invoked the update.
    environment = dict(os.environ)
    for name in COMPOSE_ENV_OVERRIDES:
        environment.pop(name, None)
    return environment


def _run(argv: Sequence[str], *, cwd: Path, timeout: int = 1800,
         check: bool = False) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        list(argv), cwd=cwd, check=False, capture_output=True, text=True,
        timeout=timeout, env=_child_environment())
    if check and completed.returncode != 0:
        tail = (completed.stdout + "\n" + completed.stderr)[-2000:]
        raise UpdateRuntimeError(
            f"command failed ({completed.returncode}): {' '.join(argv)}\n{tail}")
    return completed


def _compose_prefix(root: Path, files: Sequence[str]) -> list[str]:
    del root
    args = ["docker", "compose", "-p", COMPOSE_PROJECT_NAME]
    for filename in files:
        args += ["-f", filename]
    return args


def _compose_storage_bindings(root: Path, files: Sequence[str]) -> dict[str, Path]:
    completed = _run(
        _compose_prefix(root, files) + ["config", "--format", "json"],
        cwd=root, timeout=120)
    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr)[-2000:]
        raise UpdateRuntimeError(f"cannot render Compose storage model:\n{detail}")
    try:
        config = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UpdateRuntimeError("docker compose config did not return valid JSON") from exc
    volumes = config.get("volumes")
    if not isinstance(volumes, dict):
        raise UpdateRuntimeError("rendered Compose model has no volumes object")

    result = {}
    for logical in STORAGE_BIND_ENV:
        definition = volumes.get(logical)
        if not isinstance(definition, dict):
            raise UpdateRuntimeError(f"rendered Compose model lost volume {logical}")
        driver = definition.get("driver")
        options = definition.get("driver_opts")
        if driver != "local" or not isinstance(options, dict):
            raise UpdateRuntimeError(
                f"rendered Compose volume {logical} is not a local bind-backed volume")
        option_type = str(options.get("type", ""))
        option_o = str(options.get("o", ""))
        option_device = str(options.get("device", ""))
        if option_type != "none" or "bind" not in {item.strip() for item in option_o.split(",")}:
            raise UpdateRuntimeError(
                f"rendered Compose volume {logical} lost type=none/o=bind")
        device = Path(option_device)
        if not device.is_absolute():
            raise UpdateRuntimeError(
                f"rendered Compose volume {logical} has non-absolute bind device")
        result[logical] = device.resolve()
    return result


def _docker_volume_bindings(expected: dict[str, Path]) -> None:
    for logical, wanted in expected.items():
        volume_name = f"{COMPOSE_PROJECT_NAME}_{logical}"
        completed = subprocess.run(
            ["docker", "volume", "inspect", volume_name], check=False,
            capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise UpdateRuntimeError(
                f"existing installer volume {volume_name} is missing; refusing update")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise UpdateRuntimeError(
                f"docker volume inspect returned invalid JSON for {volume_name}") from exc
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise UpdateRuntimeError(
                f"docker volume inspect returned unexpected data for {volume_name}")
        volume = payload[0]
        options = volume.get("Options")
        if volume.get("Driver") != "local" or not isinstance(options, dict):
            raise UpdateRuntimeError(
                f"existing installer volume {volume_name} is not local bind-backed storage")
        option_type = str(options.get("type", ""))
        option_o = str(options.get("o", ""))
        option_device = str(options.get("device", ""))
        if option_type != "none" or "bind" not in {item.strip() for item in option_o.split(",")}:
            raise UpdateRuntimeError(
                f"existing installer volume {volume_name} lost type=none/o=bind")
        if Path(option_device).resolve() != wanted:
            raise UpdateRuntimeError(
                f"existing installer volume {volume_name} points to {option_device}, expected {wanted}")


def _verify_active_storage_mounts(root: Path, files: Sequence[str], marker: dict) -> None:
    services = ["ravencoin-core", "electrumx"]
    if marker.get("nodeMonitorEnabled"):
        services.append("monitor")
    for service in services:
        completed = _run(
            _compose_prefix(root, files) + ["ps", "-q", service], cwd=root, timeout=60)
        container_id = completed.stdout.strip()
        if completed.returncode != 0 or not container_id:
            raise UpdateRuntimeError(
                f"cannot prove active storage mounts because service {service} is not running")
        inspected = subprocess.run(
            ["docker", "inspect", "--format", "{{json .Mounts}}", container_id],
            check=False, capture_output=True, text=True, timeout=60)
        if inspected.returncode != 0:
            raise UpdateRuntimeError(f"cannot inspect active mounts for service {service}")
        try:
            mounts = json.loads(inspected.stdout)
        except json.JSONDecodeError as exc:
            raise UpdateRuntimeError(
                f"docker inspect returned invalid mount JSON for service {service}") from exc
        if not isinstance(mounts, list):
            raise UpdateRuntimeError(f"docker inspect returned invalid mounts for {service}")
        for logical, destination in ACTIVE_STORAGE_MOUNTS[service].items():
            volume_name = f"{COMPOSE_PROJECT_NAME}_{logical}"
            matches = [
                item for item in mounts
                if isinstance(item, dict)
                and item.get("Type") == "volume"
                and item.get("Name") == volume_name
                and item.get("Destination") == destination
            ]
            if len(matches) != 1:
                raise UpdateRuntimeError(
                    f"running {service} is not attached to expected installer volume "
                    f"{volume_name} at {destination}")


def _expected_named_volume_storage() -> dict[str, str]:
    return {
        logical: f"{COMPOSE_PROJECT_NAME}_{logical}"
        for logical in ("ravencoin-data", "ravencoin-config", "electrumx-data")
    }


def _prove_named_volume_objects(expected: dict[str, str]) -> None:
    for logical, volume_name in expected.items():
        wanted = f"{COMPOSE_PROJECT_NAME}_{logical}"
        if volume_name != wanted:
            raise UpdateRuntimeError(
                f"named-volume identity changed for {logical}: {volume_name!r}")
        completed = subprocess.run(
            ["docker", "volume", "inspect", volume_name],
            check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            raise UpdateRuntimeError(
                f"existing named volume {volume_name} is missing; refusing update")
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise UpdateRuntimeError(
                f"docker volume inspect returned invalid JSON for {volume_name}") from exc
        if not isinstance(payload, list) or len(payload) != 1 or not isinstance(payload[0], dict):
            raise UpdateRuntimeError(
                f"docker volume inspect returned unexpected data for {volume_name}")
        volume = payload[0]
        options = volume.get("Options")
        # An adopted legacy installation keeps plain Docker-managed named
        # volumes.  Docker reports those with no driver options at all, so a
        # volume that suddenly carries bind driver_opts is not the object this
        # installation was adopted with and must not be accepted silently.
        if volume.get("Driver") != "local" or options not in (None, {}):
            raise UpdateRuntimeError(
                f"existing named volume {volume_name} is not a plain local named volume")


def _prove_candidate_named_volume_model(
        root: Path, files: Sequence[str], expected: dict[str, str]) -> None:
    if STORAGE_OVERLAY in files:
        raise UpdateRuntimeError(
            "named-volume installation unexpectedly selected compose.storage.yaml")
    if BASE_COMPOSE not in files:
        raise UpdateRuntimeError(
            "named-volume installation lost compose.yaml")
    if expected != _expected_named_volume_storage():
        raise UpdateRuntimeError(
            "candidate named-volume identities differ from running installation")

    completed = _run(
        _compose_prefix(root, files) + ["config", "--format", "json"],
        cwd=root, timeout=120)
    if completed.returncode != 0:
        detail = (completed.stdout + "\n" + completed.stderr)[-2000:]
        raise UpdateRuntimeError(
            f"cannot render candidate named-volume Compose model:\n{detail}")
    try:
        config = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise UpdateRuntimeError(
            "docker compose config did not return valid JSON") from exc

    volumes = config.get("volumes")
    if not isinstance(volumes, dict):
        raise UpdateRuntimeError("candidate Compose model has no volumes object")

    for logical, wanted in expected.items():
        definition = volumes.get(logical)
        if not isinstance(definition, dict):
            raise UpdateRuntimeError(
                f"candidate Compose model lost named volume {logical}")
        rendered_name = definition.get("name")
        if rendered_name is not None and rendered_name != wanted:
            raise UpdateRuntimeError(
                f"candidate named volume {logical} resolves to "
                f"{rendered_name!r}, expected {wanted!r}")
        options = definition.get("driver_opts")
        if options:
            raise UpdateRuntimeError(
                f"candidate named volume {logical} unexpectedly has driver_opts")


def storage_mode_of(marker: dict) -> str:
    """Return the persistent storage model of an installation.

    ``named-volumes`` is durable installation state written once by the legacy
    1.13.1 adoption.  Every later normal update must read it from the marker
    and validate storage natively, without any process-local hook from
    legacy_1_13_1_apply.
    """
    if marker.get("storageMode") == LEGACY_NAMED_VOLUME_STORAGE_MODE:
        return LEGACY_NAMED_VOLUME_STORAGE_MODE
    return BIND_BACKED_STORAGE_MODE


def prove_storage_volume_objects(expected, *, storage_mode: str) -> None:
    """Prove the Docker volume objects for the installation's storage model.

    Every runtime phase after the release switch (candidate start, health
    gate, rollback) must go through here.  Calling the bind-backed primitive
    directly is what made an adopted named-volume node fail post-switch while
    its preflight passed.
    """
    if storage_mode == LEGACY_NAMED_VOLUME_STORAGE_MODE:
        _prove_named_volume_objects(expected)
        return
    _docker_volume_bindings(expected)


def prove_running_storage_continuity(
        root: Path, files: Sequence[str], marker: dict):
    if storage_mode_of(marker) == LEGACY_NAMED_VOLUME_STORAGE_MODE:
        expected = _expected_named_volume_storage()
        _prove_named_volume_objects(expected)
        _verify_active_storage_mounts(root, files, marker)
        return expected

    expected = _read_storage_env(root)
    rendered = _compose_storage_bindings(root, files)
    if rendered != expected:
        raise UpdateRuntimeError(
            "running Compose storage model does not match the four installer host paths")
    _docker_volume_bindings(expected)
    _verify_active_storage_mounts(root, files, marker)
    return expected


def prove_candidate_storage_continuity(
        root: Path, files: Sequence[str], expected, *,
        storage_mode: Optional[str] = None) -> None:
    if storage_mode is None:
        storage_mode = (
            LEGACY_NAMED_VOLUME_STORAGE_MODE
            if expected and all(isinstance(value, str) for value in expected.values())
            else BIND_BACKED_STORAGE_MODE)
    if storage_mode == LEGACY_NAMED_VOLUME_STORAGE_MODE:
        _prove_candidate_named_volume_model(root, files, expected)
        return

    candidate = _read_storage_env(root)
    if candidate != expected:
        raise UpdateRuntimeError(
            "candidate .env does not preserve the four existing installer host paths")
    rendered = _compose_storage_bindings(root, files)
    if rendered != expected:
        raise UpdateRuntimeError(
            "candidate Compose model would not preserve the four existing bind mounts")


def _write_private_json(path: Path, payload: dict) -> None:
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
        if temporary.exists():
            temporary.unlink()


class TransactionalComposeSwitch:
    """One same-filesystem release-directory transaction."""

    def __init__(self, *, install_root: Path, artifact_path: Path, manifest: dict,
                 health_timeout: int = 1800):
        self.root = install_root.resolve()
        self.parent = self.root.parent
        self.artifact_path = artifact_path.resolve()
        self.manifest = manifest
        self.health_timeout = health_timeout
        self.marker = read_install_marker(self.root)
        # Persistent installation state, read once from the verified marker and
        # used by every storage proof in this transaction, including rollback.
        self.storage_mode = storage_mode_of(self.marker)
        self.old_files = update_compose_files(self.marker, self.root)
        # The operator's .env crosses into the staged release, so its Compose
        # selection must name release files only. Proven before any staging so
        # a host-local overlay aborts the update instead of failing an apply.
        prove_env_compose_selection(self.root)
        # This is the first mutating-upgrade gate. It runs while the old stack
        # is still serving and refuses any lost/changed bind volume or mount.
        self.storage_bindings = prove_running_storage_continuity(
            self.root, self.old_files, self.marker)
        if self.storage_mode == LEGACY_NAMED_VOLUME_STORAGE_MODE:
            print(
                "UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING "
                f"storage-model=named-volumes volume-objects={len(self.storage_bindings)} "
                "active-mounts=PASS")
        else:
            print(
                "UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING "
                "bind-paths=4 volume-objects=4 active-mounts=PASS")
        self.trusted_update_public_key_hex = _read_installed_key(
            self.root / UPDATE_PUBLIC_KEY_PATH, "ElectrumX release/update")
        self.trusted_core_policy_public_key_hex = _read_installed_key(
            self.root / CORE_POLICY_PUBLIC_KEY_PATH, "safe-Core policy")
        self.staging: Optional[Path] = None
        self.backup: Optional[Path] = None
        self.failed: Optional[Path] = None
        self.switched = False
        self.journal = self.parent / f".{self.root.name}.update-transaction.json"
        if self.journal.exists():
            raise UpdateRuntimeError(
                f"unfinished updater transaction exists at {self.journal}; recover it before updating")

    def _journal(self, phase: str) -> None:
        _write_private_json(self.journal, {
            "schemaVersion": 1,
            "phase": phase,
            "installRoot": str(self.root),
            "staging": str(self.staging) if self.staging else None,
            "backup": str(self.backup) if self.backup else None,
            "failed": str(self.failed) if self.failed else None,
            "candidateVersion": self.manifest.get("electrumxVersion"),
            "artifactDigest": self.manifest.get("artifactDigest"),
            "storageMode": self.storage_mode,
            "storageBindings": {
                name: str(path) for name, path in self.storage_bindings.items()
            },
        })

    def prepare(self) -> None:
        if self.staging is not None:
            return
        validate_bundle_file(
            self.artifact_path, self.manifest,
            trusted_update_public_key_hex=self.trusted_update_public_key_hex,
            trusted_core_policy_public_key_hex=self.trusted_core_policy_public_key_hex)
        staging = Path(tempfile.mkdtemp(
            prefix=f".{self.root.name}.release-staging-", dir=self.parent))
        self.staging = staging
        self._journal("STAGING")
        try:
            extract_bundle_file(self.artifact_path, staging)
            copy_persistent_state(self.root, staging)
            _run(["sh", "./setup.sh", "--bundled-core"], cwd=staging, check=True)

            # Installation identity is persistent operator state.  Preserve the
            # verified marker across the release-directory switch and update
            # only the release version.  This keeps storageMode=named-volumes
            # durable after the one-time legacy adoption, so subsequent
            # 1.13.x -> 1.13.y updates use the normal updater with no new
            # legacy consent step.
            marker = dict(self.marker)
            marker["electrumxVersion"] = self.manifest["electrumxVersion"]
            _write_private_json(staging / INSTALL_MARKER, marker)

            files = update_compose_files(marker, staging)
            prove_candidate_storage_continuity(
                staging, files, self.storage_bindings, storage_mode=self.storage_mode)
            if self.storage_mode == LEGACY_NAMED_VOLUME_STORAGE_MODE:
                print(
                    "UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING "
                    "compose-model=PASS storage-model=named-volumes "
                    f"volume-objects={len(self.storage_bindings)}")
            else:
                print(
                    "UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING "
                    "compose-model=PASS bind-paths=4")
            prefix = _compose_prefix(staging, files)
            _run(prefix + ["config", "--quiet"], cwd=staging, check=True)
            # Build before activation while the old containers and their proven
            # bind-backed storage remain untouched and online.
            _run(prefix + ["build"], cwd=staging, timeout=7200, check=True)
            self._journal("STAGED_AND_BUILT")
        except BaseException:
            self.cleanup_unactivated()
            raise

    def stop_services(self) -> None:
        self.prepare()
        prefix = _compose_prefix(self.root, self.old_files)
        _run(prefix + ["stop"], cwd=self.root, timeout=1800, check=True)
        self._journal("OLD_STACK_STOPPED")

    def switch_atomically(self, manifest: dict) -> None:
        if manifest != self.manifest:
            raise UpdateRuntimeError("apply manifest changed after staging")
        if self.staging is None:
            raise UpdateRuntimeError("release was not staged")
        backup = self.parent / (
            f".{self.root.name}.last-known-good-"
            f"{str(self.marker.get('electrumxVersion', 'unknown')).replace('/', '_')}-"
            f"{int(time.time())}")
        if backup.exists():
            raise UpdateRuntimeError(f"backup destination already exists: {backup}")
        self.backup = backup
        self._journal("SWITCH_BEGIN")
        os.replace(self.root, backup)
        try:
            os.replace(self.staging, self.root)
        except BaseException:
            os.replace(backup, self.root)
            self.backup = None
            self._journal("SWITCH_RESTORED_OLD_ROOT")
            raise
        self.staging = None
        self.switched = True
        self._journal("NEW_ROOT_ACTIVE")
        print(
            "UPDATER_CHECKPOINT release-switch=PASS "
            "same-filesystem-renames=COMPLETE new-root=ACTIVE")

    def start_services(self) -> None:
        files = update_compose_files(self.marker, self.root)
        # Recheck the candidate tree after the atomic rename and before Docker
        # can create/attach anything. A missing storage overlay therefore can
        # never silently fall through to fresh named volumes.
        prove_candidate_storage_continuity(
            self.root, files, self.storage_bindings, storage_mode=self.storage_mode)
        prove_storage_volume_objects(self.storage_bindings, storage_mode=self.storage_mode)
        prefix = _compose_prefix(self.root, files)
        _run(prefix + ["up", "-d", "--no-build"], cwd=self.root,
             timeout=1800, check=True)
        self._journal("NEW_STACK_STARTED")

    def _container_id(self, service: str) -> Optional[str]:
        files = update_compose_files(self.marker, self.root)
        completed = _run(
            _compose_prefix(self.root, files) + ["ps", "-q", service],
            cwd=self.root, timeout=60)
        value = completed.stdout.strip()
        return value if completed.returncode == 0 and value else None

    @staticmethod
    def _inspect(container_id: str, template: str) -> Optional[str]:
        completed = subprocess.run(
            ["docker", "inspect", "-f", template, container_id],
            check=False, capture_output=True, text=True, timeout=60)
        if completed.returncode != 0:
            return None
        return completed.stdout.strip()

    def _compose_exec(self, service: str, *args: str, timeout: int = 120) -> subprocess.CompletedProcess:
        files = update_compose_files(self.marker, self.root)
        return _run(_compose_prefix(self.root, files) + ["exec", "-T", service, *args],
                    cwd=self.root, timeout=timeout)

    def _core_rpc(self, *args: str) -> subprocess.CompletedProcess:
        return self._compose_exec(
            "ravencoin-core", "raven-cli", "-datadir=/var/lib/ravencoin",
            "-conf=/var/lib/ravencoin-config/raven.conf", *args)

    def _dynamic_health(self) -> tuple[Optional[dict], Optional[dict]]:
        chain = None
        electrumx = None
        core = self._core_rpc("getblockchaininfo")
        if core.returncode == 0:
            try:
                chain = json.loads(core.stdout)
            except json.JSONDecodeError:
                chain = None
        info = self._compose_exec("electrumx", "electrumx_rpc", "getinfo")
        if info.returncode == 0:
            try:
                electrumx = json.loads(info.stdout)
            except json.JSONDecodeError:
                electrumx = None
        return chain, electrumx

    def run_health_checks(self, manifest: dict) -> HealthGateResult:
        if manifest != self.manifest:
            raise UpdateRuntimeError("health-check manifest changed after switch")

        # The new services must still be attached to the exact same volume
        # objects, in this installation's own storage model, before a release
        # can be promoted to current.
        prove_storage_volume_objects(self.storage_bindings, storage_mode=self.storage_mode)
        _verify_active_storage_mounts(
            self.root, update_compose_files(self.marker, self.root), self.marker)

        core_id = self._container_id("ravencoin-core")
        electrumx_id = self._container_id("electrumx")
        core_source = core_revision = core_version_label = None
        restart_count = None
        if core_id:
            core_source = self._inspect(
                core_id, '{{ index .Config.Labels "org.opencontainers.image.source" }}')
            core_revision = self._inspect(
                core_id, '{{ index .Config.Labels "org.opencontainers.image.revision" }}')
            core_version_label = self._inspect(
                core_id, '{{ index .Config.Labels "org.opencontainers.image.version" }}')
            restart_count = self._inspect(core_id, "{{ .RestartCount }}")

        expected_core_version = str(manifest.get("coreVersion"))
        core_binary = self._compose_exec("ravencoin-core", "ravend", "--version") \
            if core_id else None
        expected_core_version_ok = bool(
            core_version_label == expected_core_version and core_binary is not None and
            core_binary.returncode == 0 and expected_core_version in core_binary.stdout)
        core_source_identity_ok = core_source == f"https://github.com/{CORE_REPOSITORY}"
        expected_core_commit_ok = core_revision == manifest.get("coreCommit")
        core_not_crash_looping = restart_count == "0"

        deadline = time.monotonic() + self.health_timeout
        chain = electrumx_info = None
        while time.monotonic() < deadline:
            chain, electrumx_info = self._dynamic_health()
            if chain is not None and electrumx_info is not None:
                core_height = chain.get("blocks")
                db_height = electrumx_info.get("db height")
                daemon_height = electrumx_info.get("daemon height")
                if isinstance(core_height, int) and db_height == core_height and \
                        daemon_height == core_height:
                    break
            time.sleep(15)

        core_rpc_healthy = chain is not None
        correct_mainnet = bool(chain and chain.get("chain") == "main")
        checkpoint_verified = False
        if chain and isinstance(chain.get("blocks"), int) and \
                chain["blocks"] >= CHECKPOINT_HEIGHT:
            checkpoint = self._core_rpc("getblockhash", str(CHECKPOINT_HEIGHT))
            checkpoint_verified = (
                checkpoint.returncode == 0 and checkpoint.stdout.strip() == CHECKPOINT_HASH)

        electrumx_service_responds = electrumx_info is not None
        electrumx_db_opens = bool(
            electrumx_info and isinstance(electrumx_info.get("db height"), int) and
            electrumx_info["db height"] >= 0)
        electrumx_db_tip_matches_core = bool(
            chain and electrumx_info and
            electrumx_info.get("db height") == chain.get("blocks"))
        expected_electrumx_version_ok = bool(
            electrumx_info and isinstance(electrumx_info.get("version"), str) and
            electrumx_info["version"].endswith(str(manifest.get("electrumxVersion"))))
        no_startup_safety_policy_rejection = bool(
            chain and electrumx_info and
            electrumx_info.get("daemon height") == chain.get("blocks"))

        if self.marker.get("nodeMonitorEnabled"):
            monitor_id = self._container_id("monitor")
            monitor_running = bool(
                monitor_id and self._inspect(monitor_id, "{{ .State.Running }}") == "true")
            # An operator who selected the monitor gets an all-or-nothing update:
            # do not promote a release that silently dropped the optional service.
            electrumx_service_responds = electrumx_service_responds and monitor_running

        result = HealthGateResult(
            expected_electrumx_version_ok=expected_electrumx_version_ok,
            expected_core_version_ok=expected_core_version_ok,
            core_source_identity_ok=core_source_identity_ok,
            expected_core_commit_ok=expected_core_commit_ok,
            core_rpc_healthy=core_rpc_healthy,
            correct_mainnet=correct_mainnet,
            checkpoint_verified=checkpoint_verified,
            core_not_crash_looping=core_not_crash_looping,
            electrumx_db_opens=electrumx_db_opens,
            electrumx_db_tip_matches_core=electrumx_db_tip_matches_core,
            electrumx_service_responds=electrumx_service_responds,
            no_startup_safety_policy_rejection=no_startup_safety_policy_rejection,
        )
        self._journal("HEALTH_PASSED" if result.all_pass() else "HEALTH_FAILED")
        return result

    def rollback_to(self, previous: Optional[dict]) -> None:
        # previous is intentionally not used as a source of files. The exact old
        # release directory captured before the switch is the rollback payload.
        del previous
        if not self.switched or self.backup is None:
            if self.root.exists():
                prefix = _compose_prefix(self.root, self.old_files)
                _run(prefix + ["up", "-d", "--no-build"], cwd=self.root,
                     timeout=1800, check=True)
            if self.journal.exists():
                self.journal.unlink()
            return

        new_files = update_compose_files(self.marker, self.root)
        _run(_compose_prefix(self.root, new_files) + ["stop"], cwd=self.root,
             timeout=1800, check=False)
        failed = self.parent / f".{self.root.name}.failed-update-{int(time.time())}"
        if failed.exists():
            raise UpdateRuntimeError(f"failed-update destination already exists: {failed}")
        os.replace(self.root, failed)
        self.failed = failed
        os.replace(self.backup, self.root)
        self.backup = None
        self.switched = False
        self._journal("ROLLED_BACK")
        prefix = _compose_prefix(self.root, self.old_files)
        # The restored release directory still carries the previously proven
        # storage overlay and .env. Recheck the Docker volume objects before
        # bringing the old stack back.
        prove_candidate_storage_continuity(
            self.root, self.old_files, self.storage_bindings, storage_mode=self.storage_mode)
        prove_storage_volume_objects(self.storage_bindings, storage_mode=self.storage_mode)
        _run(prefix + ["up", "-d", "--no-build"], cwd=self.root,
             timeout=1800, check=True)
        self.journal.unlink(missing_ok=True)

    def finalize_success(self) -> None:
        if not self.switched:
            raise UpdateRuntimeError("cannot finalize an update that never switched")
        if self.backup and self.backup.exists():
            shutil.rmtree(self.backup)
        self.backup = None
        self.journal.unlink(missing_ok=True)

    def cleanup_unactivated(self) -> None:
        if self.staging and self.staging.exists():
            shutil.rmtree(self.staging, ignore_errors=True)
        self.staging = None
        if not self.switched:
            self.journal.unlink(missing_ok=True)
