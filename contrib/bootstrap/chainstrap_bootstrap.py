#!/usr/bin/env python3
"""Fetch a vetted Ravencoin ChainStrap snapshot and keep only raw block files.

This is intentionally not a generic ChainStrap client.  The manifest is shipped
with this repository, only RVN mainnet is accepted, and extraction is limited
to ``blocks/blk*.dat``.  Ravencoin Core must reindex and validate those files
before the normal ElectrumX stack is allowed to start.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import zlib
import zipfile
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

CHUNK = 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 5.0
RATE_WARMUP_BYTES = 32 * 1024 * 1024
RATE_WARMUP_SECONDS = 15.0
GATEWAY_RETRIES = 3
RETRY_BACKOFF_SECONDS = 1.0
GATEWAY_COOLDOWN_SECONDS = 5 * 60
MAX_PART_BYTES = 4 * 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 80 * 1024 * 1024 * 1024
MAX_BLOCK_FILE_BYTES = 128 * 1024 * 1024
BLOCK_RE = re.compile(r"^blocks/blk([0-9]{5,8})\.dat$")
CID_RE = re.compile(r"^[A-Za-z0-9]{20,120}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
BLOCKHASH_RE = re.compile(r"^[0-9a-f]{64}$")
CONTENT_RANGE_RE = re.compile(r"^bytes ([0-9]+)-([0-9]+)/([0-9]+)$")
DEFAULT_GATEWAYS = (
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://gateway.pinata.cloud/ipfs/",
)
ALLOWED_GATEWAY_HOSTS = frozenset(
    ("ipfs.io", "dweb.link", "w3s.link", "gateway.pinata.cloud")
)
SUBDOMAIN_GATEWAY_HOSTS = frozenset(("dweb.link", "w3s.link"))
BASE58BTC_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
BASE58BTC_INDEX = {char: index for index, char in enumerate(BASE58BTC_ALPHABET)}
BLOCKS_MARKER = ".chainstrap-blocks-ready.json"
PROGRESS_MARKER = ".chainstrap-bootstrap-progress.json"
REINDEX_MARKER = ".chainstrap-reindex-complete"


def log(message: str = "") -> None:
    print(message, flush=True)


def format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if abs(amount) < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{amount:.0f} {unit}"
            return f"{amount:.1f} {unit}"
        amount /= 1024.0
    raise AssertionError("unreachable")


def format_duration(seconds: float) -> str:
    value = max(0, int(seconds))
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_rate(bytes_per_second: float) -> str:
    if bytes_per_second <= 0:
        return "--"
    return f"{format_bytes(bytes_per_second)}/s"


def format_progress_rate_eta(
    transferred: int, elapsed: float, remaining: int, complete: bool = False
) -> str:
    """Hide unstable startup ETA until the transfer sample is meaningful."""
    elapsed = max(elapsed, 0.001)
    if (
        not complete
        and transferred < RATE_WARMUP_BYTES
        and elapsed < RATE_WARMUP_SECONDS
    ):
        return "measuring speed..."
    speed = transferred / elapsed
    if speed <= 0:
        return "measuring speed..."
    eta = remaining / speed
    return f"{format_rate(speed)} | ETA {format_duration(eta)}"


class DownloadIntegrityError(RuntimeError):
    """The local partial can no longer be trusted for safe resume."""


class ResumeProtocolError(RuntimeError):
    """A gateway did not honor a resumable HTTP Range request safely."""


class GatewayEndpoint(NamedTuple):
    """One release-allowlisted transport endpoint for a specific IPFS CID."""

    base: str
    url: str
    family_host: str
    display_host: str


class GatewayPool:
    """Prefer proven gateways and temporarily defer zero-progress failures."""

    def __init__(self, override: Optional[str] = None) -> None:
        raw = (override,) if override else DEFAULT_GATEWAYS
        self._bases = tuple(validate_gateway(item) for item in raw if item)
        self._preferred: Optional[str] = None
        self._cooldown_until: dict[str, float] = {}

    @property
    def has_alternatives(self) -> bool:
        return len(self._bases) > 1

    def is_preferred(self, endpoint: GatewayEndpoint) -> bool:
        return endpoint.base == self._preferred

    def endpoints(self, cid: str) -> list[GatewayEndpoint]:
        now = time.monotonic()
        endpoints = [_gateway_endpoint(base, cid) for base in self._bases]

        def rank(endpoint: GatewayEndpoint) -> tuple[int, int]:
            cooling = self._cooldown_until.get(endpoint.base, 0.0) > now
            preferred = endpoint.base == self._preferred
            if preferred and not cooling:
                return (0, self._bases.index(endpoint.base))
            if not cooling:
                return (1, self._bases.index(endpoint.base))
            return (2, self._bases.index(endpoint.base))

        return sorted(endpoints, key=rank)

    def record_zero_progress_failure(self, endpoint: GatewayEndpoint) -> None:
        if len(self._bases) <= 1:
            return
        until = time.monotonic() + GATEWAY_COOLDOWN_SECONDS
        self._cooldown_until[endpoint.base] = until
        if self._preferred == endpoint.base:
            self._preferred = None
        log(
            "    no payload bytes received; circuit open for "
            f"{format_duration(GATEWAY_COOLDOWN_SECONDS)}"
        )

    def record_success(self, endpoint: GatewayEndpoint) -> None:
        previous = self._preferred
        self._cooldown_until.pop(endpoint.base, None)
        self._preferred = endpoint.base
        if previous != endpoint.base and len(self._bases) > 1:
            log(f"    {endpoint.display_host} promoted to preferred gateway")


def _validated_resume_range(value: Optional[str], have: int, expected: int) -> None:
    if not value:
        raise ResumeProtocolError("206 response is missing Content-Range")
    match = CONTENT_RANGE_RE.fullmatch(value.strip())
    if not match:
        raise ResumeProtocolError(f"invalid Content-Range: {value!r}")
    start, end, total = (int(item) for item in match.groups())
    if start != have:
        raise ResumeProtocolError(
            f"Content-Range starts at {start}, expected resume offset {have}"
        )
    if end < start or end >= expected:
        raise ResumeProtocolError(
            f"Content-Range end {end} is outside the vetted part size {expected}"
        )
    if total != expected:
        raise ResumeProtocolError(
            f"Content-Range total {total} does not match vetted part size {expected}"
        )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_manifest(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid manifest JSON: {exc}") from exc
    validate_manifest(manifest)
    return manifest, sha256_bytes(raw)


def validate_manifest(manifest: dict) -> None:
    if manifest.get("chain") != "RVN" or manifest.get("mode") != "mainnet":
        raise ValueError("only the vetted RVN mainnet bootstrap is supported")

    height = manifest.get("blocks")
    if not isinstance(height, int) or height <= 0:
        raise ValueError("manifest blocks must be a positive integer")
    blockhash = manifest.get("blockhash")
    if not isinstance(blockhash, str) or not BLOCKHASH_RE.fullmatch(blockhash):
        raise ValueError("manifest blockhash must be 64 lowercase hex characters")

    source = manifest.get("source")
    if not isinstance(source, dict):
        raise ValueError("manifest source metadata is required")
    if source.get("repository") != "chainstrap/chainstrap.github.io":
        raise ValueError("unexpected ChainStrap source repository")
    commit = source.get("commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("source commit must be a full Git commit SHA")
    if source.get("path") != "RVN/RVN-mainnet.json":
        raise ValueError("unexpected ChainStrap source path")

    parts = manifest.get("parts")
    if not isinstance(parts, list) or not parts or len(parts) > 64:
        raise ValueError("manifest must contain between 1 and 64 parts")

    total = 0
    seen = set()
    for index, part in enumerate(parts, 1):
        if not isinstance(part, dict):
            raise ValueError(f"part {index} is not an object")
        cid = part.get("cid")
        expected = part.get("bytes")
        digest = part.get("sha256")
        if not isinstance(cid, str) or not CID_RE.fullmatch(cid) or "/" in cid:
            raise ValueError(f"part {index} has an invalid CID")
        if cid in seen:
            raise ValueError(f"duplicate CID in part {index}")
        seen.add(cid)
        if not isinstance(expected, int) or not 0 < expected <= MAX_PART_BYTES:
            raise ValueError(f"part {index} has an invalid byte count")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            raise ValueError(f"part {index} has an invalid SHA-256")
        total += expected

    declared = manifest.get("bytes")
    if not isinstance(declared, int) or declared != total:
        raise ValueError("manifest total byte count does not match its parts")
    if total > MAX_TOTAL_BYTES:
        raise ValueError("manifest exceeds the bootstrap download safety limit")


def marker_payload(manifest: dict, manifest_sha256: str) -> dict:
    source = manifest["source"]
    return {
        "schema": 1,
        "chain": "RVN",
        "mode": "mainnet",
        "height": manifest["blocks"],
        "blockhash": manifest["blockhash"],
        "manifest_sha256": manifest_sha256,
        "source_repository": source["repository"],
        "source_commit": source["commit"],
        "source_path": source["path"],
    }


def write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".new")
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _read_json_object(path: Path, label: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must contain a JSON object")
    return value


def _datadir_has_payload(datadir: Path) -> bool:
    """Return True when a fresh bootstrap would overwrite meaningful data."""
    for entry in datadir.iterdir():
        if entry.name == PROGRESS_MARKER:
            continue
        if entry.is_dir() and not entry.is_symlink():
            try:
                next(entry.iterdir())
            except StopIteration:
                continue
        return True
    return False


def check_existing_state(
    datadir: Path, expected_marker: dict, expected_cids: set[str]
) -> tuple[bool, set[str], bool]:
    marker = datadir / BLOCKS_MARKER
    progress_marker = datadir / PROGRESS_MARKER
    reindex_marker = datadir / REINDEX_MARKER
    block_files = sorted((datadir / "blocks").glob("blk*.dat"))

    if marker.exists():
        actual = _read_json_object(marker, "existing bootstrap marker")
        if reindex_marker.exists():
            done_hash = reindex_marker.read_text(encoding="utf-8").strip()
            if not SHA256_RE.fullmatch(done_hash) or done_hash != sha256_file(marker):
                raise RuntimeError(
                    "the completed-reindex marker does not match the block bootstrap marker"
                )
            progress_marker.unlink(missing_ok=True)
            log(
                "A prior Fast Verified Bootstrap already completed full Core validation; "
                "the current release manifest is only relevant to fresh nodes."
            )
            return True, set(), False
        if actual != expected_marker:
            raise RuntimeError(
                "the data volume contains a different unfinished ChainStrap bootstrap marker; "
                "refusing to mix snapshots"
            )
        if not block_files:
            raise RuntimeError("bootstrap marker exists but no block files were found")
        validate_contiguous_blocks(datadir)
        progress_marker.unlink(missing_ok=True)
        log("Vetted ChainStrap block bootstrap is already present; nothing to download.")
        return True, set(), False

    if reindex_marker.exists():
        raise RuntimeError("reindex marker exists without a matching block bootstrap marker")

    if progress_marker.exists():
        progress = _read_json_object(progress_marker, "bootstrap progress marker")
        if progress.get("marker") != expected_marker:
            raise RuntimeError(
                "the data volume contains progress for a different ChainStrap snapshot"
            )
        completed = progress.get("completed_cids")
        if not isinstance(completed, list) or len(completed) != len(set(completed)):
            raise RuntimeError("bootstrap progress marker has an invalid completed CID list")
        completed_set = set(completed)
        if not completed_set.issubset(expected_cids):
            raise RuntimeError("bootstrap progress marker names a CID outside the vetted manifest")
        log(f"Resuming vetted ChainStrap bootstrap after {len(completed_set)} part(s).")
        return False, completed_set, True

    if _datadir_has_payload(datadir):
        raise RuntimeError(
            "the Ravencoin data volume is not empty and has no matching ChainStrap marker; "
            "fast bootstrap is only supported for a fresh data volume"
        )
    return False, set(), False


def write_progress(
    path: Path, expected_marker: dict, completed_cids: set[str], parts: list
) -> None:
    ordered = [part["cid"] for part in parts if part["cid"] in completed_cids]
    write_json_atomic(
        path,
        {
            "schema": 1,
            "marker": expected_marker,
            "completed_cids": ordered,
        },
    )


def check_disk_space(datadir: Path, manifest: dict) -> None:
    usage = shutil.disk_usage(datadir)
    total = manifest["bytes"]
    largest = max(part["bytes"] for part in manifest["parts"])
    # Keep conservative headroom for expanded raw blocks plus Core's indexes.
    # Parts are deleted after extraction, so only one archive needs extra peak space.
    required = int(total * 2.60) + largest
    if usage.free < required:
        gib = 1024 ** 3
        raise RuntimeError(
            f"not enough free space for verified bootstrap: have {usage.free / gib:.1f} GiB, "
            f"require at least {required / gib:.1f} GiB"
        )


def validate_gateway(gateway: str) -> str:
    parsed = urlsplit(gateway)
    if parsed.scheme != "https":
        raise ValueError("IPFS gateways must use HTTPS")
    if parsed.hostname not in ALLOWED_GATEWAY_HOSTS:
        raise ValueError("IPFS gateway host is not in the release allowlist")
    if parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError("IPFS gateway credentials or non-standard ports are not allowed")
    if not parsed.path.rstrip("/").endswith("/ipfs"):
        raise ValueError("IPFS gateway path must end in /ipfs/")
    return gateway if gateway.endswith("/") else gateway + "/"


def _base58btc_decode(value: str) -> bytes:
    number = 0
    for char in value:
        try:
            digit = BASE58BTC_INDEX[char]
        except KeyError as exc:
            raise ValueError("CIDv0 contains a non-base58btc character") from exc
        number = number * 58 + digit
    raw = b"" if number == 0 else number.to_bytes((number.bit_length() + 7) // 8, "big")
    leading = len(value) - len(value.lstrip("1"))
    return b"\x00" * leading + raw


def _cid_dns_label(cid: str) -> Optional[str]:
    """Convert a sha2-256 dag-pb CIDv0 to the DNS-safe CIDv1 base32 form."""
    if cid.startswith("b") and re.fullmatch(r"b[a-z2-7]+", cid):
        return cid
    if not cid.startswith("Qm"):
        return None
    try:
        multihash = _base58btc_decode(cid)
    except ValueError:
        return None
    if len(multihash) != 34 or multihash[:2] != b"\x12\x20":
        return None
    cidv1 = b"\x01\x70" + multihash
    encoded = base64.b32encode(cidv1).decode("ascii").lower().rstrip("=")
    label = "b" + encoded
    if len(label) > 63:
        return None
    return label


def _gateway_endpoint(base: str, cid: str) -> GatewayEndpoint:
    validated = validate_gateway(base)
    host = urlsplit(validated).hostname
    assert host is not None
    dns_label = _cid_dns_label(cid)
    if host in SUBDOMAIN_GATEWAY_HOSTS and dns_label:
        url = f"https://{dns_label}.ipfs.{host}/"
    else:
        url = validated + cid
    return GatewayEndpoint(
        base=validated,
        url=url,
        family_host=host,
        display_host=host,
    )


def gateway_urls(cid: str, override: Optional[str]) -> list[str]:
    gateways = (override,) if override else DEFAULT_GATEWAYS
    return [_gateway_endpoint(gateway, cid).url for gateway in gateways if gateway]


def _validate_final_gateway_url(final_url: str, family_host: str) -> None:
    final = urlsplit(final_url)
    hostname = final.hostname or ""
    same_family = hostname == family_host
    if family_host in SUBDOMAIN_GATEWAY_HOSTS:
        same_family = same_family or hostname.endswith(f".ipfs.{family_host}")
    if (
        final.scheme != "https"
        or not same_family
        or final.username
        or final.password
        or final.port not in (None, 443)
    ):
        raise RuntimeError(
            "IPFS gateway redirected outside its release-allowlisted HTTPS family"
        )


def _download_once(
    url: str,
    partial: Path,
    expected: int,
    overall_before: int,
    overall_total: int,
    family_host: Optional[str] = None,
) -> None:
    have = partial.stat().st_size if partial.exists() else 0
    if have > expected:
        partial.unlink()
        have = 0
    if have == expected:
        return

    headers = {"User-Agent": "electrumx-ravencoin-chainstrap-bootstrap/1"}
    if have:
        headers["Range"] = f"bytes={have}-"
        log(f"    resuming partial download at {format_bytes(have)}")
    request = Request(url, headers=headers)
    attempt_started = time.monotonic()
    session_start = have
    last_report = attempt_started

    with urlopen(request, timeout=120) as response:
        final_url = response.geturl()
        expected_family = family_host or (urlsplit(url).hostname or "")
        _validate_final_gateway_url(final_url, expected_family)

        status = getattr(response, "status", response.getcode())
        if have:
            if status == 200:
                raise ResumeProtocolError(
                    "gateway ignored the Range request; preserving the partial for another gateway"
                )
            if status != 206:
                raise ResumeProtocolError(
                    f"gateway returned HTTP {status} for a Range request"
                )
            _validated_resume_range(response.headers.get("Content-Range"), have, expected)
        elif status not in (200, 206):
            raise RuntimeError(f"unexpected HTTP status {status}")
        elif status == 206:
            _validated_resume_range(response.headers.get("Content-Range"), 0, expected)

        mode = "ab" if have else "wb"
        with partial.open(mode) as out:
            try:
                while True:
                    chunk = response.read(CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
                    current = out.tell()
                    if current > expected:
                        raise DownloadIntegrityError(
                            "gateway sent more bytes than the vetted manifest allows"
                        )

                    now = time.monotonic()
                    if now - last_report >= PROGRESS_INTERVAL_SECONDS or current == expected:
                        elapsed = max(now - attempt_started, 0.001)
                        transferred = max(current - session_start, 0)
                        remaining = max(expected - current, 0)
                        rate_eta = format_progress_rate_eta(
                            transferred,
                            elapsed,
                            remaining,
                            complete=current == expected,
                        )
                        part_pct = 100.0 * current / expected
                        overall_pct = 100.0 * (overall_before + current) / overall_total
                        log(
                            f"    progress {part_pct:5.1f}% | "
                            f"{format_bytes(current)} / {format_bytes(expected)} | "
                            f"{rate_eta} | snapshot {overall_pct:5.1f}%"
                        )
                        last_report = now
            finally:
                # Persist bytes already received even when the peer drops the connection.
                # A later allowlisted gateway can continue from this exact offset.
                out.flush()
                os.fsync(out.fileno())

    elapsed = max(time.monotonic() - attempt_started, 0.001)
    transferred = max(partial.stat().st_size - session_start, 0)
    log(
        f"    download complete in {format_duration(elapsed)} "
        f"(average {format_rate(transferred / elapsed)})"
    )


def _verify_partial_if_complete(
    partial: Path, complete: Path, expected: int, wanted_sha: str
) -> bool:
    if not partial.exists() or partial.stat().st_size != expected:
        return False
    log("  complete partial found; verifying SHA-256 before any network request")
    if sha256_file(partial) != wanted_sha:
        log("  complete partial failed SHA-256; discarding it and restarting safely")
        partial.unlink()
        return False
    os.replace(partial, complete)
    log("  complete partial matches the vetted SHA-256")
    return True


def download_verified(
    part: dict,
    cache_dir: Path,
    gateway: Optional[str],
    overall_before: int,
    overall_total: int,
    gateway_pool: Optional[GatewayPool] = None,
) -> Path:
    cid = part["cid"]
    expected = part["bytes"]
    wanted_sha = part["sha256"]
    complete = cache_dir / f"{cid}.zip"
    partial = cache_dir / f"{cid}.zip.part"

    if complete.exists():
        if complete.stat().st_size == expected and sha256_file(complete) == wanted_sha:
            log("  cached archive already matches size and SHA-256")
            return complete
        complete.unlink()

    if _verify_partial_if_complete(partial, complete, expected, wanted_sha):
        return complete

    pool = gateway_pool
    if pool is not None:
        endpoints = pool.endpoints(cid)
    else:
        endpoints = []
        for url in gateway_urls(cid, gateway):
            host = urlsplit(url).hostname or "unknown"
            family = host
            for candidate in SUBDOMAIN_GATEWAY_HOSTS:
                if host.endswith(f".ipfs.{candidate}"):
                    family = candidate
                    break
            endpoints.append(
                GatewayEndpoint(base=url, url=url, family_host=family, display_host=family)
            )

    errors = []
    for gateway_index, endpoint in enumerate(endpoints, 1):
        url = endpoint.url
        for retry in range(1, GATEWAY_RETRIES + 1):
            before_size = partial.stat().st_size if partial.exists() else 0
            attempt_started = time.monotonic()
            try:
                preferred = (
                    " [preferred]"
                    if pool is not None and pool.is_preferred(endpoint)
                    else ""
                )
                suffix = f" | try {retry}/{GATEWAY_RETRIES}" if GATEWAY_RETRIES > 1 else ""
                log(
                    f"  gateway {gateway_index}/{len(endpoints)}: "
                    f"{endpoint.display_host}{preferred}{suffix}"
                )
                _download_once(
                    url,
                    partial,
                    expected,
                    overall_before,
                    overall_total,
                    family_host=endpoint.family_host,
                )
                if partial.stat().st_size != expected:
                    raise RuntimeError(
                        f"short download: expected {expected}, got {partial.stat().st_size}; "
                        "partial preserved for resume"
                    )
                log("    verifying SHA-256...")
                verify_started = time.monotonic()
                if sha256_file(partial) != wanted_sha:
                    raise DownloadIntegrityError("SHA-256 mismatch")
                log(
                    "    SHA-256 verified "
                    f"in {format_duration(time.monotonic() - verify_started)}"
                )
                os.replace(partial, complete)
                if pool is not None:
                    pool.record_success(endpoint)
                return complete
            except (HTTPError, URLError, OSError, RuntimeError) as exc:
                errors.append(f"{url} try {retry}/{GATEWAY_RETRIES}: {exc}")
                after_size = partial.stat().st_size if partial.exists() else 0
                progress_made = after_size > before_size
                log(
                    f"    gateway attempt failed after "
                    f"{format_duration(time.monotonic() - attempt_started)}: {exc}"
                )

                if isinstance(exc, DownloadIntegrityError):
                    partial.unlink(missing_ok=True)
                    log("    unsafe/corrupt partial discarded; next attempt starts from zero")
                elif partial.exists():
                    log(
                        f"    partial preserved at {format_bytes(partial.stat().st_size)} "
                        "for safe HTTP Range resume"
                    )

                protocol_failure = isinstance(exc, ResumeProtocolError)
                integrity_failure = isinstance(exc, DownloadIntegrityError)
                zero_progress_transport = (
                    not progress_made and not protocol_failure and not integrity_failure
                )
                if (
                    zero_progress_transport
                    and pool is not None
                    and pool.has_alternatives
                ):
                    pool.record_zero_progress_failure(endpoint)

                retry_same_gateway = (
                    retry < GATEWAY_RETRIES
                    and (
                        progress_made
                        or (pool is not None and not pool.has_alternatives)
                    )
                    and not protocol_failure
                    and not integrity_failure
                )
                if retry_same_gateway:
                    delay = RETRY_BACKOFF_SECONDS * retry
                    reason = (
                        "payload progressed before failure"
                        if progress_made
                        else "single configured gateway has no fallback"
                    )
                    log(
                        f"    {reason}; retrying this gateway in "
                        f"{format_duration(delay)}"
                    )
                    time.sleep(delay)
                    continue

                if protocol_failure:
                    log(
                        "    resume protocol is incompatible; "
                        "skipping retries for this gateway"
                    )
                elif zero_progress_transport:
                    log(
                        "    zero-progress transport failure; "
                        "skipping redundant retries for this gateway"
                    )
                if gateway_index < len(endpoints):
                    log("    falling back to the next allowlisted HTTPS gateway")
                break

    raise RuntimeError(
        "all IPFS gateway attempts failed; the partial was preserved when safe:\n  "
        + "\n  ".join(errors)
    )


def _is_symlink(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(mode)


def _crc32_file(path: Path) -> int:
    value = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            value = zlib.crc32(block, value)
    return value & 0xFFFFFFFF


def extract_block_files(
    archive_path: Path, datadir: Path, show_progress: bool = False
) -> list[Path]:
    extracted = []
    seen_names = set()
    with zipfile.ZipFile(archive_path) as archive:
        vetted = []
        for info in archive.infolist():
            name = info.filename.replace("\\", "/")
            match = BLOCK_RE.fullmatch(name)
            if not match:
                continue
            if name in seen_names:
                raise RuntimeError(f"duplicate block entry inside archive: {name}")
            seen_names.add(name)
            if info.is_dir() or _is_symlink(info):
                raise RuntimeError(f"unsafe block archive entry: {name}")
            # Ravencoin Core MAX_BLOCKFILE_SIZE is 128 MiB.  Refuse an archive
            # that tries to turn a valid-looking blk filename into a ZIP bomb.
            if info.file_size > MAX_BLOCK_FILE_BYTES:
                raise RuntimeError(f"oversized raw block file in archive: {name}")
            vetted.append((name, info))

        total_uncompressed = sum(info.file_size for _, info in vetted)
        if show_progress:
            log(
                f"  extracting {len(vetted)} vetted raw block file(s) "
                f"({format_bytes(total_uncompressed)} uncompressed)"
            )

        progress_step = max(1, len(vetted) // 4)
        completed_bytes = 0
        for position, (name, info) in enumerate(vetted, 1):
            target = datadir / "blocks" / Path(name).name
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.stat().st_size == info.file_size:
                if _crc32_file(target) == info.CRC:
                    extracted.append(target)
                    completed_bytes += info.file_size
                    if show_progress and (
                        position == len(vetted) or position % progress_step == 0
                    ):
                        log(
                            f"    extraction {position}/{len(vetted)} files | "
                            f"{format_bytes(completed_bytes)} / "
                            f"{format_bytes(total_uncompressed)}"
                        )
                    continue

            temp = target.with_name(target.name + ".chainstrap.part")
            with archive.open(info, "r") as source, temp.open("wb") as out:
                shutil.copyfileobj(source, out, length=CHUNK)
                out.flush()
                os.fsync(out.fileno())
            if temp.stat().st_size != info.file_size or _crc32_file(temp) != info.CRC:
                temp.unlink(missing_ok=True)
                raise RuntimeError(f"ZIP integrity check failed for {name}")
            os.replace(temp, target)
            extracted.append(target)
            completed_bytes += info.file_size
            if show_progress and (position == len(vetted) or position % progress_step == 0):
                log(
                    f"    extraction {position}/{len(vetted)} files | "
                    f"{format_bytes(completed_bytes)} / {format_bytes(total_uncompressed)}"
                )
    return extracted


def validate_contiguous_blocks(datadir: Path) -> list[Path]:
    candidates = sorted((datadir / "blocks").glob("blk*.dat"))
    if not candidates:
        raise RuntimeError("snapshot produced no blk*.dat files")

    by_index = {}
    for path in candidates:
        match = re.fullmatch(r"blk([0-9]{5,8})\.dat", path.name)
        if not match:
            raise RuntimeError(f"unexpected block-like filename: {path.name}")
        index = int(match.group(1))
        if index in by_index:
            raise RuntimeError(f"duplicate block-file index: {index}")
        by_index[index] = path

    highest = max(by_index)
    expected = set(range(highest + 1))
    actual = set(by_index)
    if 0 not in actual:
        raise RuntimeError("snapshot is missing blk00000.dat")
    if actual != expected:
        missing = sorted(expected - actual)
        raise RuntimeError(f"snapshot has a block-file gap before reindex: {missing[:5]}")
    return [by_index[index] for index in range(highest + 1)]


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--datadir", default=Path("/var/lib/ravencoin"), type=Path)
    parser.add_argument("--gateway", help="use one HTTPS IPFS gateway only")
    parser.add_argument("--skip-disk-check", action="store_true", help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    manifest, manifest_sha = load_manifest(args.manifest)
    expected_marker = marker_payload(manifest, manifest_sha)

    args.datadir.mkdir(parents=True, exist_ok=True)
    expected_cids = {part["cid"] for part in manifest["parts"]}
    ready, completed_cids, resuming = check_existing_state(
        args.datadir, expected_marker, expected_cids
    )
    if ready:
        return 0
    if not args.skip_disk_check and not resuming:
        check_disk_space(args.datadir, manifest)

    progress_path = args.datadir / PROGRESS_MARKER
    if not progress_path.exists():
        write_progress(progress_path, expected_marker, completed_cids, manifest["parts"])

    source = manifest["source"]
    free_space = shutil.disk_usage(args.datadir).free
    total_parts = len(manifest["parts"])
    total_bytes = manifest["bytes"]
    completed_bytes = sum(
        part["bytes"] for part in manifest["parts"] if part["cid"] in completed_cids
    )

    log("Fast Verified Bootstrap for Ravencoin mainnet")
    log(f"  ChainStrap source: {source['repository']}@{source['commit']}")
    log(f"  Snapshot height:  {manifest['blocks']}")
    log(f"  Snapshot hash:    {manifest['blockhash']}")
    log(f"  Download size:    {format_bytes(total_bytes)} across {total_parts} parts")
    log(f"  Free space:       {format_bytes(free_space)}")
    log("  Trust model:      raw blk*.dat only; Core performs a full local reindex")
    log(
        "  Docker status:    Compose may show this one-shot service as 'Waiting'; "
        "progress below means it is active."
    )

    cache_dir = args.datadir / ".chainstrap-cache"
    cache_dir.mkdir(mode=0o700, exist_ok=True)
    gateway_pool = GatewayPool(args.gateway)
    total_extracted = 0
    started = time.monotonic()
    try:
        for index, part in enumerate(manifest["parts"], 1):
            cid = part["cid"]
            log("")
            log(
                f"Part {index}/{total_parts} | {format_bytes(part['bytes'])} compressed | "
                f"CID {cid}"
            )
            if cid in completed_cids:
                log(
                    "  already extracted in this vetted bootstrap | "
                    f"snapshot {100.0 * completed_bytes / total_bytes:.1f}%"
                )
                continue
            archive = download_verified(
                part,
                cache_dir,
                args.gateway,
                completed_bytes,
                total_bytes,
                gateway_pool=gateway_pool,
            )
            files = extract_block_files(archive, args.datadir, show_progress=True)
            total_extracted += len(files)
            archive.unlink()
            completed_cids.add(cid)
            completed_bytes += part["bytes"]
            write_progress(progress_path, expected_marker, completed_cids, manifest["parts"])
            log(
                f"  part accepted: {len(files)} raw block file(s) | "
                f"snapshot {100.0 * completed_bytes / total_bytes:.1f}% "
                f"({index}/{total_parts})"
            )
        block_files = validate_contiguous_blocks(args.datadir)
        write_json_atomic(args.datadir / BLOCKS_MARKER, expected_marker)
        progress_path.unlink(missing_ok=True)
    finally:
        try:
            cache_dir.rmdir()
        except OSError:
            pass

    elapsed = int(time.monotonic() - started)
    log("")
    log(
        f"ChainStrap stage complete: {len(block_files)} contiguous raw block files "
        f"({total_extracted} archive entries) in {format_duration(elapsed)}."
    )
    log("Next phase: pinned Ravencoin Core 4.8.0 performs the enforced offline full reindex.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, OSError, zipfile.BadZipFile) as exc:
        print(f"chainstrap-bootstrap: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
