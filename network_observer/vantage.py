# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Multi-vantage aggregation and selective-serving detection.

Several independently deployed observers can watch the same endpoints
from unrelated networks.  This module merges their *verified* bundles
and surfaces what they disagree about.

Two diversity axes, kept separate by construction:

* operator diversity: how many independent Ravencoin operators back a
  piece of chain evidence (from operator groups, never from observers);
* observer diversity: how many vantage points saw something.  Three
  observers measuring one CIPIG endpoint are 3 vantage points over ONE
  operator, and the aggregate reports exactly that.

Categories are conservative on purpose.  DNS legitimately varies (CDNs,
multi-address records, failover), so DNS_VARIANCE is a reported
observation, not an accusation.  The severe categories need strong,
directly comparable evidence: the same endpoint, the same challenge
height, valid hashes, different answers, from different observers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence

#: Bundles generated further apart than this are not "the same
#: observation window" and are not cross-compared: servers legitimately
#: change certificates and versions over hours.
DEFAULT_WINDOW_SECONDS = 30 * 60


class VantageAgreement(Enum):
    MULTI_VANTAGE_CONSISTENT = "MULTI_VANTAGE_CONSISTENT"
    DNS_VARIANCE = "DNS_VARIANCE"
    TLS_VARIANCE = "TLS_VARIANCE"
    BACKEND_IDENTITY_VARIANCE = "BACKEND_IDENTITY_VARIANCE"
    CHAIN_SELECTIVE_SERVING_SUSPECTED = "CHAIN_SELECTIVE_SERVING_SUSPECTED"
    DATA_SELECTIVE_SERVING_SUSPECTED = "DATA_SELECTIVE_SERVING_SUSPECTED"


@dataclass
class EndpointObservationView:
    """One observer's sanitized observation of one endpoint."""

    observer_id: str
    generated_at: str
    endpoint: str
    address_families: List[str] = field(default_factory=list)
    tls_fingerprint: Optional[str] = None
    server_version: Optional[str] = None
    backend_claim: Optional[dict] = None
    challenge_hashes: Dict[str, str] = field(default_factory=dict)
    asset_capability: Optional[dict] = None


@dataclass
class VantageSummary:
    endpoint: str
    observers: List[str]
    agreement: VantageAgreement
    detail: str


def _epoch(timestamp: str) -> float:
    import datetime
    try:
        parsed = datetime.datetime.fromisoformat(timestamp)
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed.timestamp()


def views_from_bundles(bundles: Sequence[Mapping]) -> List[EndpointObservationView]:
    """Flatten verified bundle bodies into per-endpoint views."""
    views: List[EndpointObservationView] = []
    for bundle in bundles:
        body = bundle["observation"] if "observation" in bundle else bundle
        observer_id = body.get("observerId", "?")
        generated_at = body.get("generatedAt", "")
        for item in body.get("observations", []):
            views.append(EndpointObservationView(
                observer_id=observer_id,
                generated_at=generated_at,
                endpoint=item.get("endpoint", ""),
                address_families=list(item.get("addressFamilies", [])),
                tls_fingerprint=item.get("tlsFingerprint"),
                server_version=item.get("serverVersion"),
                backend_claim=item.get("backendClaim"),
                challenge_hashes=dict(item.get("challengeHashes", {})),
                asset_capability=item.get("assetCapability")))
    return views


def compare_vantage_views(views: Sequence[EndpointObservationView],
                          *, window_seconds: int = DEFAULT_WINDOW_SECONDS,
                          ) -> List[VantageSummary]:
    """Cross-compare observers per endpoint within the time window.

    Severity ordering is deliberate: chain-hash divergence at one height
    outranks everything; backend identity divergence outranks generic
    data divergence; TLS and DNS variance are observations, not
    accusations.  A single observer's endpoint yields
    MULTI_VANTAGE_CONSISTENT with one observer listed: no comparison
    happened, and the summary says so plainly rather than implying
    corroboration.
    """
    by_endpoint: Dict[str, List[EndpointObservationView]] = {}
    for view in views:
        by_endpoint.setdefault(view.endpoint, []).append(view)

    summaries = []
    for endpoint, group in sorted(by_endpoint.items()):
        observers = sorted({view.observer_id for view in group})
        windows: List[List[EndpointObservationView]] = []
        for view in sorted(group, key=lambda item: _epoch(item.generated_at)):
            placed = False
            for window in windows:
                if abs(_epoch(view.generated_at)
                        - _epoch(window[0].generated_at)) <= window_seconds:
                    window.append(view)
                    placed = True
                    break
            if not placed:
                windows.append([view])

        agreement = VantageAgreement.MULTI_VANTAGE_CONSISTENT
        detail = (f"{len(observers)} observer(s): "
                  f"{', '.join(observers)}")
        for window in windows:
            distinct_observers = {view.observer_id for view in window}
            if len(distinct_observers) < 2:
                continue
            fingerprints = {view.tls_fingerprint for view in window
                            if view.tls_fingerprint}
            if len(fingerprints) > 1:
                agreement = VantageAgreement.TLS_VARIANCE
                detail = (f"{len(fingerprints)} different TLS certificates "
                          f"seen for the same endpoint within one window")
                continue
            families = {tuple(sorted(view.address_families)) for view in window}
            if len(families) > 1:
                agreement = VantageAgreement.DNS_VARIANCE
                detail = ("observers resolved the same hostname to "
                          "different address families in one window")
                continue
            claims = {
                ((view.backend_claim or {}).get("repository"),
                 (view.backend_claim or {}).get("commit"))
                for view in window
                if view.backend_claim
            }
            if len(claims) > 1:
                agreement = VantageAgreement.BACKEND_IDENTITY_VARIANCE
                detail = "observers saw different backend Core identities"
                continue
            versions = {view.server_version for view in window
                        if view.server_version}
            if len(versions) > 1:
                agreement = VantageAgreement.DATA_SELECTIVE_SERVING_SUSPECTED
                detail = "observers saw different server versions"
                continue
            heights = {height for view in window
                       for height in view.challenge_hashes}
            selective = False
            for height in sorted(heights, key=int):
                answers = {view.challenge_hashes[height] for view in window
                           if height in view.challenge_hashes}
                if len(answers) > 1:
                    selective = True
                    detail = (f"observers received different block hashes "
                              f"for height {height} from the same endpoint")
                    break
            if selective:
                agreement = VantageAgreement.CHAIN_SELECTIVE_SERVING_SUSPECTED
        summaries.append(VantageSummary(endpoint, observers, agreement, detail))
    return summaries


def count_operator_vs_observer_diversity(
        views: Sequence[EndpointObservationView],
        operator_of_endpoint: Mapping[str, str]) -> dict:
    """The two-axis diversity statement for one endpoint population.

    Answers, per endpoint, how many vantage points observed it and how
    many operators run it, and never lets one stand in for the other.
    """
    per_endpoint: Dict[str, set] = {}
    for view in views:
        per_endpoint.setdefault(view.endpoint, set()).add(view.observer_id)
    return {
        endpoint: {
            "observers": len(observers),
            "operatorGroup": operator_of_endpoint.get(endpoint, "UNKNOWN"),
        }
        for endpoint, observers in sorted(per_endpoint.items())
    }
