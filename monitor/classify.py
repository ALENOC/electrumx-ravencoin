# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Turn probe observations into a safety classification.

Three rules shape everything here.

1. A server's own report is a claim, not a proof.  ``server.ravencoin_backend``
   is self-reported by a third party who can simply lie about it, so it decides
   only whether an endpoint is worth checking further.
2. Trust comes from the certified-release policy, keyed on repository plus
   commit.  A newer version number is not a safer one.
3. Chain evidence outranks both.  A backend that claims a certified Core and
   serves a chain that disagrees with the reference is a conflict, and a
   conflict is never resolved by counting endpoints.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from electrumx.lib.coins import Ravencoin

from .model import AssetSupport, EndpointId, IndexHealth, Security, Thresholds

#: Asset methods a wallet needs from a production Ravencoin Electrum server.
REQUIRED_ASSET_METHODS = (
    "blockchain.asset.get_meta",
    "blockchain.asset.get_assets_with_prefix",
)

#: Prefix marking a group as a placeholder for one endpoint with no known
#: operator identity, not an attested independent operator.  operatorGroup
#: itself only ever comes from this operator's own seeds/registry config
#: files, never from a crawled peer's self-report, but an endpoint simply
#: absent from either is not thereby a second operator: an attacker can
#: mint any number of hostnames for free.  See known_group_count().
UNKNOWN_GROUP_PREFIX = "UNKNOWN-"


def operator_group_key(operator_group: Optional[str], hostname: str) -> str:
    """The grouping key used everywhere diversity is counted."""
    return operator_group or f"{UNKNOWN_GROUP_PREFIX}{hostname}"


def known_group_count(groups: Mapping[str, list]) -> int:
    """How many of these groups are attested operator identities.

    Excludes ``UNKNOWN-*`` placeholders: those are one endpoint each with no
    known owner, and an attacker can always mint more of them, so they must
    never count toward independent-operator quorum on their own.
    """
    return sum(1 for group in groups if not group.startswith(UNKNOWN_GROUP_PREFIX))


@dataclass
class ChainObservation:
    """What one endpoint said about the chain."""

    endpoint: EndpointId
    height: Optional[int]
    tip_hash: Optional[str]
    genesis_hash: Optional[str] = None
    checkpoint_hash: Optional[str] = None
    operator_group: Optional[str] = None


@dataclass
class ChainVerdict:
    status: str
    detail: str
    conflicting_groups: tuple = ()
    #: Groups whose own chain evidence was actually compared and agreed --
    #: either with an explicit trusted --reference, or with a height/tip
    #: pair independently reported by at least two ATTESTED operator
    #: groups.  Never populated merely because a group was not flagged
    #: conflicting: silence is not agreement.  See is_corroborated() and
    #: compare_chains()'s module docstring.
    verified_groups: tuple = ()


def classify_backend(backend: Optional[Mapping], policy: Mapping,
                     *, expected_network: str = "main") -> tuple:
    """Classify the backend claim against the certified-release policy.

    Returns ``(Security, reason)``.  The best this function can return is
    UNVERIFIED: a claim that survives policy lookup still has to survive chain
    comparison before anything is called safe.
    """
    if not backend:
        return (Security.BACKEND_MISSING,
                "server does not implement server.ravencoin_backend")

    core = backend.get("backend") or {}
    compatibility = backend.get("compatibility") or {}
    identity = core.get("identity") or {}

    network = core.get("network")
    if network != expected_network:
        return Security.UNSAFE, f"backend reports network {network!r}"

    version = core.get("version")
    if isinstance(version, str) and version in ("4.6.0", "4.6.1", "4.6.1.1", "4.7.0"):
        return Security.UNSAFE, f"backend runs known unsafe Core {version}"

    repository = identity.get("sourceRepository")
    commit = identity.get("sourceCommit")
    if not repository or not commit:
        return (Security.UNREVIEWED_CORE,
                "server does not report which Core build it runs, so it cannot be "
                "matched against the certified-release policy")

    entry = None
    for candidate in policy.get("releases", []):
        if candidate.get("repository") == repository \
                and candidate.get("commit") == str(commit).lower():
            entry = candidate
            break

    if entry is None:
        return (Security.UNREVIEWED_CORE,
                f"Core {version} at {repository}@{str(commit)[:12]} has not been "
                f"certified")
    if entry["status"] == "REVOKED":
        return Security.UNSAFE, "the Core release this backend runs was revoked"
    if entry["status"] == "KNOWN_UNSAFE":
        return Security.UNSAFE, "the Core release this backend runs is known unsafe"

    for flag in ("coreSafe", "backendSynchronized", "kawpowHeightValidation",
                 "checkpoint4487775"):
        if compatibility.get(flag) is not True:
            return Security.UNVERIFIED, f"backend reports {flag} is not true"

    return (Security.UNVERIFIED,
            "backend claims a certified release; chain validation still required")


def classify_assets(features: Optional[Mapping],
                    probed_methods: Optional[Mapping] = None) -> AssetSupport:
    """Decide whether the endpoint is usable for Ravencoin asset wallets."""
    if probed_methods:
        working = [name for name, ok in probed_methods.items() if ok]
        if all(probed_methods.get(name) for name in REQUIRED_ASSET_METHODS):
            return AssetSupport.CAPABLE
        if working:
            return AssetSupport.PARTIAL
        return AssetSupport.UNSUPPORTED
    if not features:
        return AssetSupport.UNKNOWN
    ravencoin = features.get("ravencoin")
    if isinstance(ravencoin, Mapping) and ravencoin.get("assets") is True:
        return AssetSupport.CAPABLE
    return AssetSupport.UNKNOWN


def independent_groups(observations: Iterable[ChainObservation]) -> Dict[str, list]:
    """Group observations by operator group.

    Endpoints without a known group are each treated as their own unknown group
    rather than being lumped together: two unrelated unknown operators are two
    operators, and pretending otherwise would either invent or destroy diversity.
    """
    groups: Dict[str, list] = defaultdict(list)
    for index, observation in enumerate(observations):
        group = operator_group_key(observation.operator_group, observation.endpoint.hostname)
        groups[group].append(observation)
    return dict(groups)


def _verified_groups(groups: Mapping[str, list], conflicting: set, *,
                     reference: Optional[ChainObservation],
                     thresholds: Thresholds) -> tuple:
    """Which groups' own chain evidence was actually compared and agreed.

    Corroboration is scoped to evidence that was actually compared, never
    inferred from the absence of a detected conflict:

    - an explicit trusted --reference observation, or
    - a height/tip pair independently reported by at least two ATTESTED
      (non-UNKNOWN-*) operator groups -- the "corroborated anchor".

    Critically, this anchor is *not* the highest self-reported height used
    for conflict detection: the highest height can be, and in the attack
    this defends against is, the very endpoint whose evidence needs
    verifying.  Using it as its own yardstick would let it trivially agree
    with itself.  A group strictly ahead of the corroborated anchor is
    never verified: a higher height was never actually compared to
    anything, so it is not agreement, no matter how internally consistent
    it looks in isolation.
    """
    if reference is not None and reference.tip_hash:
        vanchor = reference
    else:
        support: Dict[Tuple[Optional[int], Optional[str]], set] = defaultdict(set)
        for group, items in groups.items():
            if group.startswith(UNKNOWN_GROUP_PREFIX) or group in conflicting:
                continue
            for item in items:
                support[(item.height, item.tip_hash)].add(group)
        candidates = [key for key, supporters in support.items() if len(supporters) >= 2]
        if not candidates:
            return ()
        best_height, best_tip = max(candidates, key=lambda key: key[0])
        vanchor = next(item for items in groups.values() for item in items
                       if item.height == best_height and item.tip_hash == best_tip)

    verified = set()
    for group, items in groups.items():
        if group in conflicting:
            continue
        for item in items:
            if item.height == vanchor.height and item.tip_hash == vanchor.tip_hash:
                verified.add(group)
            elif item.height < vanchor.height \
                    and vanchor.height - item.height <= thresholds.height_lag_alarm:
                verified.add(group)
            # item.height > vanchor.height: never verified.  See docstring.
    return tuple(sorted(verified))


def compare_chains(observations: Sequence[ChainObservation],
                   *, reference: Optional[ChainObservation] = None,
                   thresholds: Optional[Thresholds] = None,
                   confirmations: int = 1) -> ChainVerdict:
    """Compare chain evidence across independent operators.

    Never a majority vote over endpoints: one operator can run twenty of them.
    Comparison happens between operator groups, and a hash disagreement at a
    shared height is a conflict even if only one group reports it.

    SAFE-relevant callers must read ``verified_groups``, not merely
    ``status == "VALID"``: a clean comparison only means no conflict was
    *detected*, not that every observation was positively verified.  See
    is_corroborated().
    """
    thresholds = thresholds or Thresholds()
    usable = [item for item in observations
              if item.height is not None and item.tip_hash]
    if not usable:
        return ChainVerdict("UNKNOWN", "no usable chain observations")

    groups = independent_groups(usable)
    if reference is not None and reference.tip_hash:
        anchor = reference
    else:
        # Prefer the group reporting the greatest height, but only as a starting
        # point for conflict detection, never as proof of agreement: see
        # _verified_groups() for the anchor actually used for corroboration.
        anchor = max(usable, key=lambda item: item.height)

    conflicting = set()
    lagging = []
    for group, items in groups.items():
        for item in items:
            if item.checkpoint_hash and item.checkpoint_hash != Ravencoin.INCIDENT_CHECKPOINT_HASH:
                # Checked against the network's real, pinned checkpoint,
                # never only against whatever the comparison anchor
                # happens to carry: an anchor with no checkpoint of its
                # own (a bare --reference-height/--reference-tip-hash
                # pair) must never disable this check.
                conflicting.add(group)
                continue
            if item.genesis_hash and anchor.genesis_hash \
                    and item.genesis_hash != anchor.genesis_hash:
                conflicting.add(group)
                continue
            if item.checkpoint_hash and anchor.checkpoint_hash \
                    and item.checkpoint_hash != anchor.checkpoint_hash:
                conflicting.add(group)
                continue
            if item.height == anchor.height and item.tip_hash != anchor.tip_hash:
                # Same height, different block: that is a fork, not lag.
                conflicting.add(group)
                continue
            behind = anchor.height - item.height
            if behind > thresholds.height_lag_alarm:
                lagging.append((group, behind))

    verified = _verified_groups(groups, conflicting, reference=reference,
                                thresholds=thresholds)

    if conflicting and confirmations >= thresholds.conflict_confirmations:
        return ChainVerdict(
            "CHAIN_CONFLICT",
            "independent operator groups disagree about the chain at a shared height",
            tuple(sorted(conflicting)), verified)
    if conflicting:
        return ChainVerdict(
            "CONFLICT_SUSPECTED",
            "a disagreement was seen but not yet confirmed by a second observation",
            tuple(sorted(conflicting)), verified)
    if lagging:
        worst = max(lagging, key=lambda item: item[1])
        return ChainVerdict("TEMPORARY_LAG",
                            f"group {worst[0]} trails the reference by {worst[1]} blocks",
                            (), verified)
    return ChainVerdict("VALID", f"{len(verified)} operator group(s) independently verified",
                        (), verified)


def is_corroborated(verdict: ChainVerdict, *, reference_supplied: bool) -> bool:
    """Central SAFE-promotion predicate.  The only question that matters:

    SAFE requires positively verified chain evidence.  Absence of a
    confirmed conflict is NOT evidence of safety.  Corroboration is
    limited to the exact chain evidence compared (verdict.verified_groups).
    A configured --reference without concrete comparable evidence, or
    height proximity to one, does not count on its own.
    """
    if verdict.status != "VALID":
        return False
    if reference_supplied:
        return bool(verdict.verified_groups)
    return known_group_count(verdict.verified_groups) >= 2


def count_independent_operators(states: Iterable) -> Dict[str, int]:
    """Count attested operator groups, not endpoints and not raw hostnames.

    Two endpoints from one organisation are one independent operator.  This is
    the number that matters for diversity, and reporting the endpoint count in
    its place would overstate the resilience of the ecosystem.

    ``UNKNOWN-*`` placeholders (an endpoint with no known operator identity)
    are excluded here: counting them would let an attacker inflate this
    figure for free with any number of hostnames.  See
    count_unknown_safe_endpoints() for that population, reported separately
    rather than folded into "independent groups".
    """
    counts: Dict[str, set] = defaultdict(set)
    for state in states:
        if state.security is not Security.SAFE:
            continue
        group = operator_group_key(state.operator_group, state.endpoint.hostname)
        if group.startswith(UNKNOWN_GROUP_PREFIX):
            continue
        counts[group].add(str(state.endpoint))
    return {group: len(endpoints) for group, endpoints in sorted(counts.items())}


def count_unknown_safe_endpoints(states: Iterable) -> int:
    """SAFE endpoints with no attested operator identity.

    Reported separately from independent-operator diversity, never folded
    into it: see count_independent_operators().
    """
    return sum(
        1 for state in states
        if state.security is Security.SAFE and not state.operator_group)


def classify_index_lag(core_height: Optional[int], electrum_height: Optional[int],
                       thresholds: Optional[Thresholds] = None) -> tuple:
    """Distance between the backend Core chain and what ElectrumX serves.

    Returns ``(IndexHealth, index_lag)`` where ``index_lag`` is
    ``core_height - electrum_height`` or None when either side is
    missing.  This is deliberately a separate axis from Security: an
    endpoint with a certified Core is not UNSAFE because its indexer is
    catching up, and conversely a perfectly synced index does not certify
    anything about the Core underneath.
    """
    thresholds = thresholds or Thresholds()
    if core_height is None or electrum_height is None:
        return IndexHealth.UNKNOWN, None
    lag = core_height - electrum_height
    if lag <= thresholds.index_lag_synced:
        return IndexHealth.SYNCED, lag
    if lag <= thresholds.index_lag_lagging:
        return IndexHealth.LAGGING, lag
    return IndexHealth.STALE, lag


def suggest_operator_group(hostname: str, known: Mapping[str, str]) -> Optional[str]:
    """Suggest a group from a shared registrable domain.

    A suggestion only.  Merging operator groups changes how consensus diversity
    is counted, so it is never done automatically from a heuristic: an attacker
    who could split their endpoints into apparently separate groups would
    manufacture diversity that does not exist.
    """
    labels = hostname.lower().split(".")
    if len(labels) < 2:
        return None
    domain = ".".join(labels[-2:])
    for candidate_host, group in known.items():
        candidate_labels = candidate_host.lower().split(".")
        if len(candidate_labels) >= 2 and ".".join(candidate_labels[-2:]) == domain:
            return group
    return None
