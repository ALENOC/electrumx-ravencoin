# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Probing and controlled peer-gossip discovery.

The crawl is breadth-first with a depth limit, a candidate limit, bounded
concurrency and a per-host rate limit, because a monitor that hammers community
servers is a worse problem than the one it solves.

Everything learned from a peer is untrusted: hostnames are validated, resolved
addresses are classified, and anything not globally routable is dropped before a
connection is attempted.  Discovery never confers trust; it only produces
candidates for validation.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import time
from collections import defaultdict, deque
from struct import error as struct_error
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from electrumx.lib.coins import Ravencoin
from electrumx.lib.hash import hash_to_hex_str

from .model import (
    AssetSupport, DiscoverySource, EndpointId, Limits, ProbeResult, Transport,
)
from .netsafety import UnsafeTarget, parse_peers_response, safe_resolved_addresses

#: Sent as the client name in server.version.  Honest identification, never an
#: imitation of a wallet, so operators can tell who is probing them.
CLIENT_NAME = "Ravencoin-Electrum-Monitor/1.0"
PROTOCOL_VERSION = "1.4"

PROBE_CALLS = (
    ("server.version", [CLIENT_NAME, PROTOCOL_VERSION]),
    ("server.features", []),
    ("blockchain.headers.subscribe", []),
    ("server.ravencoin_backend", []),
    ("server.peers.subscribe", []),
    # Independent checkpoint evidence: fetched directly from this endpoint
    # rather than trusted from its own backend self-report.
    ("blockchain.block.header", [Ravencoin.INCIDENT_CHECKPOINT_HEIGHT]),
)


class RateLimiter:
    """Per-host budget so one host is never probed too often."""

    def __init__(self, limits: Optional[Limits] = None):
        self.limits = limits or Limits()
        self._events: Dict[str, deque] = defaultdict(deque)

    def allow(self, hostname: str, *, now: Optional[float] = None) -> bool:
        now = now if now is not None else time.monotonic()
        window = self._events[hostname]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= self.limits.max_probes_per_host_per_hour:
            return False
        window.append(now)
        return True


async def resolve_endpoint(endpoint: EndpointId, limits: Limits, *,
                           allow_private: bool = False,
                           resolver: Optional[Callable] = None) -> Tuple[list, list]:
    """Resolve a hostname and keep only addresses that may be connected to."""
    if endpoint.hostname.endswith(".onion"):
        # Tor names are not resolved locally; they are refused here rather than
        # leaked to the system resolver.
        raise UnsafeTarget("onion endpoints need a Tor transport, not direct DNS")
    loop = asyncio.get_running_loop()
    if resolver is not None:
        infos = await resolver(endpoint.hostname, endpoint.port)
    else:
        infos = await asyncio.wait_for(
            loop.getaddrinfo(endpoint.hostname, endpoint.port,
                             type=socket.SOCK_STREAM),
            timeout=limits.dns_timeout)
    ipv4, ipv6 = [], []
    for info in infos:
        family, address = info[0], info[4][0]
        if family == socket.AF_INET:
            ipv4.append(address)
        elif family == socket.AF_INET6:
            ipv6.append(address)
    safe_v4 = safe_resolved_addresses(ipv4, allow_private=allow_private)
    safe_v6 = safe_resolved_addresses(ipv6, allow_private=allow_private)
    if not safe_v4 and not safe_v6:
        raise UnsafeTarget(
            f"{endpoint.hostname} resolves only to addresses that must not be probed")
    return safe_v4, safe_v6


def _ravencoin_header_hash(raw_hex: str, height: int) -> Optional[str]:
    """Canonical Ravencoin block hash (real KAWPOW hashing post-fork), or
    None if the response is not a well-formed header for that height.

    This is chain evidence, not a Bitcoin-style double-SHA256 of the first
    80 bytes: post-fork Ravencoin headers are 120 bytes and the block hash
    depends on the mix hash and nonce that live past that point, so a
    truncated hash can never be cross-checked against a real chain and is
    only internally consistent between peers that make the same mistake.
    A peer that returns a wrong-length or malformed header yields no
    evidence at all, rather than a hash of the wrong thing.
    """
    try:
        header = bytes.fromhex(raw_hex)
    except ValueError:
        return None
    expected_len = Ravencoin.static_header_len(height)
    if len(header) < expected_len:
        return None
    try:
        return hash_to_hex_str(Ravencoin.header_hash(header[:expected_len]))
    except (ValueError, IndexError, struct_error):
        return None


def _certificate_summary(binary_certificate: Optional[bytes],
                         peer_certificate: Optional[dict]) -> dict:
    summary = {}
    if binary_certificate:
        summary["fingerprint"] = hashlib.sha256(binary_certificate).hexdigest()
    if peer_certificate:
        summary["not_after"] = peer_certificate.get("notAfter")
        issuer = peer_certificate.get("issuer")
        if issuer:
            flat = {}
            for part in issuer:
                for key, value in part:
                    flat[key] = value
            summary["issuer"] = flat.get("organizationName") or flat.get("commonName")
    return summary


async def probe_endpoint(endpoint: EndpointId, *, limits: Optional[Limits] = None,
                         allow_private: bool = False,
                         connector: Optional[Callable] = None,
                         calls: Optional[Sequence[tuple]] = None) -> ProbeResult:
    """Run one cheap health probe.

    Cheap on purpose: identity, features, tip, backend evidence and peers.  No
    address-history queries, which are the expensive ones for a server to answer.

    ``calls`` overrides the request list (same shape as PROBE_CALLS) so a
    caller can run the shared-height Chain Quorum challenges or the bounded
    asset capability probes over one connection.  Responses to methods not
    part of the standard probe land in ``result.extra_responses`` untrusted
    and unparsed: interpreting them is the caller's job.
    """
    limits = limits or Limits()
    started = time.monotonic()
    result = ProbeResult(endpoint=endpoint, reachable=False)

    try:
        ipv4, ipv6 = await resolve_endpoint(endpoint, limits,
                                            allow_private=allow_private)
    except UnsafeTarget as exc:
        result.error, result.error_category = str(exc), "UNSAFE_TARGET"
        return result
    except (asyncio.TimeoutError, socket.gaierror, OSError) as exc:
        result.error, result.error_category = str(exc), "DNS"
        return result

    result.resolved_ipv4, result.resolved_ipv6 = tuple(ipv4), tuple(ipv6)
    result.dns_latency_ms = (time.monotonic() - started) * 1000

    context = None
    if endpoint.transport is Transport.TLS:
        context = ssl.create_default_context()
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED

    connect_started = time.monotonic()
    try:
        if connector is not None:
            reader, writer = await connector(endpoint, context)
        else:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(
                    ipv4[0] if ipv4 else ipv6[0], endpoint.port, ssl=context,
                    server_hostname=endpoint.hostname if context else None,
                    limit=limits.max_response_bytes),
                timeout=limits.tls_timeout if context else limits.tcp_timeout)
    except ssl.SSLCertVerificationError as exc:
        result.error, result.error_category = str(exc), "TLS_INVALID"
        result.tls_valid = False
        return result
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError) as exc:
        result.error = str(exc)
        result.error_category = ("TIMEOUT" if isinstance(exc, asyncio.TimeoutError)
                                 else "TCP")
        return result

    result.connect_latency_ms = (time.monotonic() - connect_started) * 1000
    if context is not None:
        result.tls_valid = True
        transport_ssl = writer.get_extra_info("ssl_object")
        if transport_ssl is not None:
            summary = _certificate_summary(transport_ssl.getpeercert(binary_form=True),
                                           transport_ssl.getpeercert())
            result.tls_fingerprint = summary.get("fingerprint")
            result.tls_not_after = summary.get("not_after")
            result.tls_issuer = summary.get("issuer")

    rpc_started = time.monotonic()
    try:
        responses = await asyncio.wait_for(
            _speak_electrum(reader, writer, limits, calls=calls),
            timeout=limits.rpc_timeout)
    except asyncio.TimeoutError:
        result.error, result.error_category = "RPC timed out", "TIMEOUT"
        return result
    except (ValueError, OSError) as exc:
        result.error, result.error_category = str(exc), "RPC_MALFORMED"
        return result
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ssl.SSLError):
            pass

    result.rpc_latency_ms = (time.monotonic() - rpc_started) * 1000
    result.reachable = True

    version = responses.get("server.version")
    if isinstance(version, list) and version:
        result.server_version = str(version[0])[:80]
        if len(version) > 1:
            result.protocol_version = str(version[1])[:16]
    features = responses.get("server.features")
    if isinstance(features, dict):
        result.features = features
        genesis = features.get("genesis_hash")
        if isinstance(genesis, str):
            result.genesis_hash = genesis
        ravencoin = features.get("ravencoin")
        if isinstance(ravencoin, dict) and ravencoin.get("assets") is True:
            result.asset_support = AssetSupport.CAPABLE
    header = responses.get("blockchain.headers.subscribe")
    if isinstance(header, dict):
        if isinstance(header.get("height"), int):
            result.height = header["height"]
        raw = header.get("hex")
        if isinstance(raw, str) and result.height is not None:
            result.tip_hash = _ravencoin_header_hash(raw, result.height)
    checkpoint_raw = responses.get("blockchain.block.header")
    if isinstance(checkpoint_raw, str):
        result.checkpoint_hash = _ravencoin_header_hash(
            checkpoint_raw, Ravencoin.INCIDENT_CHECKPOINT_HEIGHT)
    backend = responses.get("server.ravencoin_backend")
    if isinstance(backend, dict):
        result.backend = backend
        core = backend.get("backend") or {}
        blocks = core.get("blocks")
        if isinstance(blocks, int) and not isinstance(blocks, bool):
            result.core_height = blocks
    peers = responses.get("server.peers.subscribe")
    if peers is not None:
        try:
            result.peers = tuple(parse_peers_response(peers, limits))
        except UnsafeTarget:
            result.peers = ()
    result.extra_responses = {
        method: payload for method, payload in responses.items()
        if method not in {name for name, _ in PROBE_CALLS}}
    return result


def challenge_calls(heights: Sequence[int]) -> List[tuple]:
    """Request list fetching shared-height block headers."""
    return [("blockchain.block.header", [int(height)]) for height in heights]


def asset_capability_calls(plan: Sequence[dict]) -> List[tuple]:
    """Request list for a bounded asset capability probe plan."""
    return [(item["method"], list(item["params"])) for item in plan]


def parse_challenge_responses(extra: Mapping, heights: Sequence[int],
                              ) -> Dict[int, Optional[str]]:
    """Compute real Ravencoin header hashes for answered challenges.

    A missing, malformed or wrong-length header yields None: no evidence
    rather than a hash of the wrong thing.
    """
    answers: Dict[int, Optional[str]] = {}
    for index, height in enumerate(heights, start=1):
        raw = extra.get(f"blockchain.block.header#{index}")
        answers[int(height)] = (
            _ravencoin_header_hash(raw, int(height))
            if isinstance(raw, str) else None)
    return answers


async def _speak_electrum(reader, writer, limits: Limits,
                          calls: Optional[Sequence[tuple]] = None) -> dict:
    """Issue the probe calls and collect whatever answers arrive.

    Repeated methods (the challenge round asks blockchain.block.header
    once per height) are keyed ``method#request_index`` so individual
    answers stay addressable; the single-shot probe calls keep their
    plain names for backward compatibility with existing tests.
    """
    requests = list(calls if calls is not None else PROBE_CALLS)
    duplicates = {name for name, _ in requests
                  if [n for n, _ in requests].count(name) > 1}
    responses: Dict[str, object] = {}
    for index, (method, params) in enumerate(requests, start=1):
        key = f"{method}#{index}" if method in duplicates else method
        request = json.dumps({"id": index, "method": method, "params": params})
        writer.write((request + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line:
            break
        if len(line) > limits.max_response_bytes:
            raise ValueError(f"{method} response exceeded the size limit")
        try:
            payload = json.loads(line.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            raise ValueError(f"{method} returned malformed JSON") from None
        if isinstance(payload, dict) and "result" in payload:
            responses[key] = payload["result"]
    if "server.version" not in responses:
        raise ValueError("server did not answer server.version")
    return responses


class Crawler:
    """Breadth-first discovery across Electrum peer gossip, with limits."""

    def __init__(self, *, limits: Optional[Limits] = None,
                 allow_private: bool = False,
                 prober: Optional[Callable] = None):
        self.limits = limits or Limits()
        self.allow_private = allow_private
        self.rate_limiter = RateLimiter(self.limits)
        self._prober = prober or probe_endpoint

    async def crawl(self, seeds: Sequence[EndpointId]) -> Tuple[Dict, List]:
        """Probe seeds, follow their peers, and stop at the configured limits.

        Returns ``(results_by_endpoint, edges)`` where an edge records which
        endpoint announced which.  Announcement is provenance, not endorsement.
        """
        results: Dict[EndpointId, ProbeResult] = {}
        edges: List[Tuple[EndpointId, EndpointId]] = []
        seen: Set[EndpointId] = set(seeds)
        queue = deque((seed, 0) for seed in seeds)
        discovered_new = 0
        semaphore = asyncio.Semaphore(self.limits.max_concurrent_probes)

        while queue:
            batch = []
            while queue and len(batch) < self.limits.max_concurrent_probes:
                batch.append(queue.popleft())

            async def run(item):
                endpoint, depth = item
                if not self.rate_limiter.allow(endpoint.hostname):
                    return endpoint, depth, None
                async with semaphore:
                    return endpoint, depth, await self._prober(
                        endpoint, limits=self.limits,
                        allow_private=self.allow_private)

            for endpoint, depth, result in await asyncio.gather(
                    *(run(item) for item in batch)):
                if result is None:
                    continue
                results[endpoint] = result
                if depth >= self.limits.max_crawl_depth:
                    continue
                for peer in result.peers:
                    edges.append((endpoint, peer))
                    if peer in seen:
                        continue
                    if discovered_new >= self.limits.max_new_candidates_per_crawl:
                        continue
                    seen.add(peer)
                    discovered_new += 1
                    queue.append((peer, depth + 1))
        return results, edges
