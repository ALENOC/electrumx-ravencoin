# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Signed safe-Core policy distribution for the ElectrumX self-updater.

Transport is deliberately not a trust root. The updater may fetch the current
policy from the project's static HTTPS distribution path, but only an Ed25519
signature under the pinned Core-policy public key can make a document usable.
A successfully verified higher policy is cached atomically; a network outage or
invalid remote response never replaces the last verified cache. The caller
supplies a monotonic minimum policy version persisted in update-state.json, so
an older still-valid signed document cannot re-enable a revoked Core identity.
"""

from __future__ import annotations

import json
import os
import pathlib
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

import policy as core_policy

DEFAULT_POLICY_URL = (
    "https://raw.githubusercontent.com/ALENOC/electrumx-ravencoin/master/"
    "core-safety/production/safe-core-policy.json"
)
MAX_POLICY_BYTES = 2 * 1024 * 1024
TRUSTED_CORE_REPOSITORY = "RavenProject/Ravencoin"


class PolicyResolutionError(RuntimeError):
    """No usable signed policy could be resolved without violating trust rules."""


@dataclass(frozen=True)
class ResolvedPolicy:
    body: dict
    source: str
    commits: frozenset
    certification_digests: dict

    @property
    def version(self) -> int:
        return self.body["policyVersion"]


def load_policy_public_key(key_path: str) -> dict:
    try:
        public_hex = pathlib.Path(key_path).read_text(encoding="ascii").strip()
        public_bytes = bytes.fromhex(public_hex)
    except (OSError, ValueError) as exc:
        raise PolicyResolutionError(
            f"cannot load Core policy public key from {key_path}: {exc}") from exc
    if len(public_bytes) != 32:
        raise PolicyResolutionError("Core policy public key must be exactly 32 bytes")
    return {core_policy.key_id_for(public_bytes): public_bytes}


def parse_policy_bytes(raw: bytes, *, source: str) -> dict:
    if not isinstance(raw, (bytes, bytearray)):
        raise PolicyResolutionError(f"{source}: policy response is not bytes")
    if len(raw) > MAX_POLICY_BYTES:
        raise PolicyResolutionError(f"{source}: policy exceeds size limit")
    try:
        document = json.loads(bytes(raw).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyResolutionError(f"{source}: policy is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise PolicyResolutionError(f"{source}: policy JSON is not an object")
    return document


def verify_policy_document(document: dict, trusted_keys: dict, *,
                           minimum_policy_version: int, source: str) -> dict:
    try:
        return core_policy.verify_policy(
            document, trusted_keys,
            minimum_policy_version=minimum_policy_version,
        )
    except core_policy.PolicyError as exc:
        raise PolicyResolutionError(f"{source}: signed Core policy rejected: {exc}") from exc


def extract_ravenproject_certifications(body: dict) -> tuple[frozenset, dict]:
    """Return only exact RavenProject KNOWN_SAFE identities from a verified body."""
    commits = set()
    digests = {}
    for release in body.get("releases", []):
        if release.get("repository") != TRUSTED_CORE_REPOSITORY:
            continue
        if release.get("status") != "KNOWN_SAFE":
            continue
        certification = release.get("certification") or {}
        if certification.get("result") != "PASS":
            raise PolicyResolutionError(
                "verified policy contains KNOWN_SAFE RavenProject release without PASS")
        commit = release.get("commit")
        report_digest = release.get("reportDigest")
        if not isinstance(commit, str) or len(commit) != 40:
            raise PolicyResolutionError(
                "verified KNOWN_SAFE RavenProject release has malformed commit identity")
        if not isinstance(report_digest, str) or len(report_digest) != 64 or \
                any(char not in "0123456789abcdef" for char in report_digest):
            raise PolicyResolutionError(
                "verified KNOWN_SAFE RavenProject release has malformed reportDigest")
        if commit in digests and digests[commit] != report_digest:
            raise PolicyResolutionError(
                "verified policy gives one Core commit conflicting certification digests")
        commits.add(commit)
        digests[commit] = report_digest
    return frozenset(commits), digests


def fetch_policy_bytes(url: str, *, timeout: float = 10.0) -> bytes:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise PolicyResolutionError("Core policy URL must use HTTPS")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "electrumx-ravencoin-update-policy"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None:
                try:
                    declared_size = int(declared)
                except ValueError as exc:
                    raise PolicyResolutionError(
                        "remote Core policy has invalid Content-Length") from exc
                if declared_size < 0 or declared_size > MAX_POLICY_BYTES:
                    raise PolicyResolutionError("remote Core policy exceeds size limit")
            chunks = []
            total = 0
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_POLICY_BYTES:
                    raise PolicyResolutionError("remote Core policy exceeds size limit")
                chunks.append(chunk)
            return b"".join(chunks)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise PolicyResolutionError(f"remote Core policy fetch failed: {exc}") from exc


def _read_document(path: str, *, source: str) -> Optional[dict]:
    file_path = pathlib.Path(path)
    if not file_path.exists():
        return None
    try:
        raw = file_path.read_bytes()
    except OSError as exc:
        raise PolicyResolutionError(f"{source}: cannot read policy file: {exc}") from exc
    return parse_policy_bytes(raw, source=source)


def _fsync_directory(directory: pathlib.Path) -> None:
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(str(directory), os.O_RDONLY | directory_flag)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def write_verified_cache(path: str, document: dict) -> None:
    """Persist only an already-verified policy document, atomically and durably."""
    target = pathlib.Path(path)
    target.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    payload = (json.dumps(document, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd, temporary_name = tempfile.mkstemp(
        prefix=".safe-core-policy-", dir=str(target.parent))
    temporary = pathlib.Path(temporary_name)
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(str(temporary), str(target))
        _fsync_directory(target.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def resolve_safe_core_policy(*, bundled_path: str, cache_path: str,
                             key_path: str, minimum_policy_version: int,
                             remote_url: str = DEFAULT_POLICY_URL,
                             fetcher: Optional[Callable[[str], bytes]] = None,
                             allow_remote: bool = True) -> ResolvedPolicy:
    """Resolve the highest valid signed policy at or above the anti-rollback floor.

    ``bundled_path`` is the immutable policy shipped with the software;
    ``cache_path`` is the last verified remote policy. Remote transport errors
    are tolerated only when one of those local sources still satisfies the
    persisted minimum version. Valid same-version policies with different
    canonical digests are treated as signer equivocation and fail closed.
    """
    if not isinstance(minimum_policy_version, int) or \
            isinstance(minimum_policy_version, bool) or minimum_policy_version < 0:
        raise PolicyResolutionError("minimum policy version must be non-negative")

    trusted_keys = load_policy_public_key(key_path)
    verified = []
    failures = []

    for source_name, path in (("bundled", bundled_path), ("cache", cache_path)):
        try:
            document = _read_document(path, source=source_name)
            if document is None:
                continue
            body = verify_policy_document(
                document, trusted_keys,
                minimum_policy_version=minimum_policy_version,
                source=source_name,
            )
            verified.append((source_name, document, body))
        except PolicyResolutionError as exc:
            failures.append(str(exc))

    remote_entry = None
    if allow_remote:
        try:
            raw = (fetcher(remote_url) if fetcher is not None
                   else fetch_policy_bytes(remote_url))
            document = parse_policy_bytes(raw, source="remote")
            body = verify_policy_document(
                document, trusted_keys,
                minimum_policy_version=minimum_policy_version,
                source="remote",
            )
            remote_entry = ("remote", document, body)
            verified.append(remote_entry)
        except PolicyResolutionError as exc:
            failures.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - injected fetch boundary
            failures.append(f"remote: policy fetch failed: {exc}")

    if not verified:
        detail = "; ".join(failures) if failures else "no policy sources are available"
        raise PolicyResolutionError(
            f"no signed safe-Core policy satisfies version floor "
            f"{minimum_policy_version}: {detail}")

    # Same signed version with a different canonical body is a serious signer
    # equivocation, not a tie that should be resolved by source preference.
    digests_by_version = {}
    for source_name, _document, body in verified:
        version = body["policyVersion"]
        digest = core_policy.policy_digest(body)
        previous = digests_by_version.get(version)
        if previous is not None and previous != digest:
            raise PolicyResolutionError(
                f"conflicting valid signed Core policies share policyVersion {version}")
        digests_by_version[version] = digest

    selected = max(verified, key=lambda item: item[2]["policyVersion"])
    source_name, selected_document, selected_body = selected

    # Cache a verified remote policy only when it is at least as new as the
    # selected local state. Never overwrite a newer verified cache with an older
    # network response.
    if remote_entry is not None:
        remote_version = remote_entry[2]["policyVersion"]
        if remote_version >= selected_body["policyVersion"]:
            write_verified_cache(cache_path, remote_entry[1])
            source_name, selected_document, selected_body = remote_entry

    commits, certification_digests = extract_ravenproject_certifications(selected_body)
    return ResolvedPolicy(
        body=selected_body,
        source=source_name,
        commits=commits,
        certification_digests=certification_digests,
    )
