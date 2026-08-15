# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Core types for the public Ravencoin Electrum monitor.

Two things are tracked separately on purpose:

*availability*
    can this endpoint be reached and does it answer.

*security*
    is what it answers acceptable.

Collapsing them loses information.  An endpoint can be perfectly reachable and
completely unusable, and the distinction is what tells an ecosystem "twelve
servers are up, four of them are safe" instead of a single misleading number.

Nothing in this module grants trust.  Discovery produces candidates; only
validation produces a classification.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Tuple


class Availability(str, Enum):
    """Operational reachability, independent of whether the answer is safe."""

    DISCOVERED = "DISCOVERED"      # known, never probed
    REACHABLE = "REACHABLE"        # answering normally
    DEGRADED = "DEGRADED"          # failing, but not yet written off
    OFFLINE = "OFFLINE"            # failing consistently
    STALE = "STALE"                # failing for so long it is probably gone


class Security(str, Enum):
    """Safety classification.  Categorical, never a score."""

    UNKNOWN = "UNKNOWN"                    # not validated yet
    UNVERIFIED = "UNVERIFIED"              # answered, not yet proven safe
    BACKEND_MISSING = "BACKEND_MISSING"    # no server.ravencoin_backend
    UNREVIEWED_CORE = "UNREVIEWED_CORE"    # Core release not certified
    UNSAFE = "UNSAFE"                      # known unsafe or revoked Core
    CONFLICT = "CONFLICT"                  # chain disagrees with the reference
    SAFE = "SAFE"                          # certified backend and consistent chain


class AssetSupport(str, Enum):
    CAPABLE = "ASSET_CAPABLE"
    PARTIAL = "ASSET_PARTIAL"
    UNSUPPORTED = "ASSET_UNSUPPORTED"
    UNKNOWN = "ASSET_UNKNOWN"


class DiscoverySource(str, Enum):
    BOOTSTRAP = "BOOTSTRAP"
    GOSSIP = "GOSSIP"
    REGISTRY = "REGISTRY"
    MANUAL = "MANUAL"


class Transport(str, Enum):
    TLS = "TLS"
    TCP = "TCP"


@dataclass(frozen=True)
class EndpointId:
    """Identity of an endpoint.

    Hostname, port and transport.  Deliberately not the IP address: home nodes
    on dynamic addresses are a supported and encouraged deployment, and an
    address change is not a new server.
    """

    hostname: str
    port: int
    transport: Transport

    def __str__(self) -> str:
        return f"{self.hostname}:{self.port}/{self.transport.value}"

    @property
    def is_encrypted(self) -> bool:
        return self.transport is Transport.TLS


@dataclass
class Thresholds:
    """Hysteresis and scheduling.  Configuration, not magic numbers in code."""

    failures_to_degraded: int = 1
    failures_to_offline: int = 3
    failures_to_stale: int = 48
    successes_to_reachable: int = 2

    interval_reachable_safe: int = 600
    interval_reachable_other: int = 300
    interval_degraded: int = 180
    interval_offline_steps: Tuple[int, ...] = (600, 1800, 3600, 10800, 21600)
    interval_stale: int = 86400
    interval_discovery: int = 1800

    #: Blocks an endpoint may trail the reference before it is worth noticing.
    #: Ravencoin targets one minute per block, so a handful of blocks is ordinary
    #: propagation, not evidence of a different chain.
    height_lag_tolerance: int = 6
    #: Trailing further than this is reported as lag, still not as a conflict.
    height_lag_alarm: int = 60
    #: A conflict needs disagreement about a hash at a shared height, seen more
    #: than once, never a height difference on its own.
    conflict_confirmations: int = 2

    jitter_fraction: float = 0.15

    def next_interval(self, availability: Availability, security: Security,
                      consecutive_failures: int) -> int:
        """How long until this endpoint should be probed again."""
        if availability is Availability.STALE:
            base = self.interval_stale
        elif availability is Availability.OFFLINE:
            steps = self.interval_offline_steps
            index = min(max(consecutive_failures - self.failures_to_offline, 0),
                        len(steps) - 1)
            base = steps[index]
        elif availability is Availability.DEGRADED:
            base = self.interval_degraded
        elif security is Security.SAFE:
            base = self.interval_reachable_safe
        else:
            base = self.interval_reachable_other
        return self.apply_jitter(base)

    def apply_jitter(self, seconds: int, *, rng: Optional[random.Random] = None) -> int:
        """Spread probes out so a restart does not fire everything at once."""
        generator = rng or random
        spread = seconds * self.jitter_fraction
        return max(30, int(seconds + generator.uniform(-spread, spread)))


@dataclass
class Limits:
    """Crawl and connection limits.  A monitor is a crawler, so it must be rude
    to nobody and bounded in every direction."""

    max_crawl_depth: int = 3
    max_new_candidates_per_crawl: int = 200
    max_peers_per_response: int = 100
    max_concurrent_probes: int = 12
    max_probes_per_host_per_hour: int = 30
    max_response_bytes: int = 512 * 1024
    dns_timeout: float = 5.0
    tcp_timeout: float = 10.0
    tls_timeout: float = 15.0
    rpc_timeout: float = 20.0
    max_hostname_length: int = 253


@dataclass
class ProbeResult:
    """What one health probe observed.  Failures carry a reason, not just False."""

    endpoint: EndpointId
    reachable: bool
    vantage_point: str = "local"
    error: Optional[str] = None
    error_category: Optional[str] = None
    server_version: Optional[str] = None
    protocol_version: Optional[str] = None
    genesis_hash: Optional[str] = None
    height: Optional[int] = None
    tip_hash: Optional[str] = None
    backend: Optional[dict] = None
    features: Optional[dict] = None
    peers: tuple = ()
    resolved_ipv4: tuple = ()
    resolved_ipv6: tuple = ()
    tls_valid: Optional[bool] = None
    tls_not_after: Optional[str] = None
    tls_fingerprint: Optional[str] = None
    tls_issuer: Optional[str] = None
    dns_latency_ms: Optional[float] = None
    connect_latency_ms: Optional[float] = None
    rpc_latency_ms: Optional[float] = None
    asset_support: AssetSupport = AssetSupport.UNKNOWN


@dataclass
class EndpointState:
    """Everything persisted about one endpoint."""

    endpoint: EndpointId
    availability: Availability = Availability.DISCOVERED
    security: Security = Security.UNKNOWN
    reason: str = ""
    operator: Optional[str] = None
    operator_group: Optional[str] = None
    sources: set = field(default_factory=set)
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    first_seen: Optional[int] = None
    last_seen: Optional[int] = None
    last_probe: Optional[int] = None
    last_success: Optional[int] = None

    def register_failure(self, thresholds: Thresholds) -> Availability:
        self.consecutive_failures += 1
        self.consecutive_successes = 0
        if self.consecutive_failures >= thresholds.failures_to_stale:
            self.availability = Availability.STALE
        elif self.consecutive_failures >= thresholds.failures_to_offline:
            self.availability = Availability.OFFLINE
        elif self.consecutive_failures >= thresholds.failures_to_degraded:
            self.availability = Availability.DEGRADED
        return self.availability

    def register_success(self, thresholds: Thresholds) -> Availability:
        self.consecutive_successes += 1
        self.consecutive_failures = 0
        if self.availability in (Availability.OFFLINE, Availability.STALE):
            # One good answer is recovery, not health.
            self.availability = (
                Availability.REACHABLE
                if self.consecutive_successes >= thresholds.successes_to_reachable
                else Availability.DEGRADED)
        else:
            self.availability = Availability.REACHABLE
        return self.availability
