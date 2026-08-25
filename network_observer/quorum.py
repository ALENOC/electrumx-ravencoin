# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Chain Quorum 2.0: shared-height header challenges.

Servers naturally sit at different heights, so tip hashes cannot be
compared directly.  This module derives a *stable shared anchor height*
from independently attested operator groups, then challenges every
eligible server for real Ravencoin block headers at a deterministic set
of heights around that anchor plus a small unpredictable set derived from
a cryptographic crawl nonce.

Invariants carried over from the Phase 1 monitor and made stronger here:

* height difference alone is never a chain conflict;
* a single server claiming an absurd future height cannot anchor
  consensus, because the anchor needs several attested groups to have
  reached it;
* unknown operators never manufacture quorum (``UNKNOWN-*`` groups are
  excluded from anchor support exactly as in
  ``network_observer.classify.known_group_count``);
* a malformed or missing header is no evidence at all, never a hash of
  the wrong thing (see ``network_observer.crawl._ravencoin_header_hash``);
* conflict requires hash disagreement at the SAME height between
  independently attested operator groups;
* absence of answers is absence of evidence: missing responses yield
  CHALLENGE_INCOMPLETE, never silent agreement.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from electrumx.lib.coins import Ravencoin

from .classify import UNKNOWN_GROUP_PREFIX, ChainObservation
from .model import EndpointId, Thresholds

#: Domain separation for the challenge-height derivation.  Without it the
#: same nonce reused in another context could be steered into predictable
#: heights.
CHALLENGE_DERIVATION_DOMAIN = b"RAVENCOIN-NETWORK-OBSERVER-CHAIN-CHALLENGE-v1\x00"

#: Ravencoin targets one block per minute.  The deterministic challenge
#: deltas therefore cover roughly "now" (6 blocks), "the last hour"
#: (60 blocks) and "the last half day" (720 blocks), mirroring
#: Thresholds.height_lag_tolerance (6) and height_lag_alarm (60) already
#: used for lag semantics.
SHORT_DELTA = 6
MEDIUM_DELTA = 60
LONG_DELTA = 720


class ChallengeVerdict(Enum):
    """Structured outcome of a shared-height challenge round."""

    VALID = "VALID"
    TEMPORARY_LAG = "TEMPORARY_LAG"
    INSUFFICIENT_CORROBORATION = "INSUFFICIENT_CORROBORATION"
    CHALLENGE_INCOMPLETE = "CHALLENGE_INCOMPLETE"
    CONFLICT_SUSPECTED = "CONFLICT_SUSPECTED"
    CHAIN_CONFLICT = "CHAIN_CONFLICT"


@dataclass(frozen=True)
class ChallengeHeight:
    height: int
    kind: str  # "anchor" | "short" | "medium" | "long" | "checkpoint" | "random"


@dataclass
class ChallengeSet:
    """The exact heights this crawl asks every eligible server about."""

    anchor_height: int
    challenge_nonce: str
    heights: Tuple[ChallengeHeight, ...]

    def height_values(self) -> Tuple[int, ...]:
        return tuple(item.height for item in self.heights)


@dataclass
class ChallengeRecord:
    """One server's answer to one challenge height.

    ``block_hash`` is the real Ravencoin header hash (KAWPOW semantics)
    computed locally from the returned header, never the server's own
    claim about it, and is ``None`` when the answer was missing or
    malformed: no evidence rather than wrong evidence.
    """

    endpoint: EndpointId
    operator_group: str
    height: int
    block_hash: Optional[str]
    observer: str = "local"
    observed_at: Optional[int] = None


@dataclass
class ChallengeVerdictDetail:
    status: ChallengeVerdict
    detail: str
    #: Groups whose answers at every challenge height they could answer
    #: agreed with the corroborated hashes.
    verified_groups: Tuple[str, ...] = ()
    #: Groups that returned a different hash at some shared height.
    conflicting_groups: Tuple[str, ...] = ()
    #: Corroborated hash per challenge height (only where >= 2 attested
    #: groups agreed).  Persisted for post-mortem inspection.
    corroborated_hashes: Dict[int, str] = field(default_factory=dict)


def new_challenge_nonce() -> str:
    """A cryptographically strong nonce for this crawl's challenges.

    Deliberately not ``random``: challenge unpredictability is a security
    property (a lying server must not be able to precompute the quiz), so
    it comes from the OS CSPRNG.
    """
    return secrets.token_hex(16)


def select_stable_anchor(observations: Sequence[ChainObservation],
                         thresholds: Optional[Thresholds] = None) -> Optional[int]:
    """Derive the highest practical height supported by attested groups.

    Algorithm (documented here because its security depends on its exact
    shape, and unit-tested in tests/test_network_observer_quorum.py):

    1. Keep only usable observations (a height and a tip hash).
    2. Keep only attested operator groups (``UNKNOWN-*`` excluded): an
       attacker can mint unlimited unknown hostnames, so unknown groups
       must not be able to manufacture an anchor.
    3. Let ``k = minimum_attested_groups_for_anchor``.  Take the k-th
       highest height reported by distinct attested groups: that is the
       highest height at which k independent operators can all answer.
       One absurd future height therefore never moves the anchor, and one
       lagging server only drags the anchor down to its own height when
       it is among the top k, which is exactly when it must.
    4. Subtract ``stable_height_margin`` so ordinary propagation skew and
       shallow reorgs do not turn into different answers, and clamp
       above genesis.

    The result is never "max(height) as proof" and never an average: it
    is the highest height with positive multi-operator support, minus a
    margin.  Returns None when fewer than k attested groups reported
    usable evidence, which callers must treat as
    INSUFFICIENT_CORROBORATION, never as agreement.
    """
    thresholds = thresholds or Thresholds()
    usable = [item for item in observations
              if item.height is not None and item.tip_hash]
    groups: Dict[str, int] = {}
    for item in usable:
        group = item.operator_group or ""
        if group.startswith(UNKNOWN_GROUP_PREFIX) or not group:
            continue
        # A group is represented by its own highest usable observation.
        if group not in groups or item.height > groups[group]:
            groups[group] = item.height
    k = max(1, thresholds.minimum_attested_groups_for_anchor)
    if len(groups) < k:
        return None
    heights = sorted(groups.values(), reverse=True)
    anchor = heights[k - 1] - thresholds.stable_height_margin
    return max(1, anchor)


def derive_challenge_heights(anchor_height: int, challenge_nonce: str,
                             thresholds: Optional[Thresholds] = None,
                             ) -> Tuple[ChallengeHeight, ...]:
    """The deterministic baseline set plus bounded random challenges.

    Baseline: the anchor, three fixed deltas back from it (6, 60, 720
    blocks: minutes, hour, half-day at Ravencoin's one-minute blocks) and
    the pinned August-2026 incident checkpoint.  Everything is clamped to
    [1, anchor] and deduplicated in first-occurrence order, so near
    genesis the set degrades gracefully instead of requesting nonsense
    heights.

    Random additions are derived, not drawn: each is
    ``SHA256(domain || nonce || anchor || index)`` mapped into the recent
    history window below the anchor.  The nonce is stored with the
    observation, so the selection is auditable after the fact, and a
    server cannot precompute the quiz without the nonce.
    """
    thresholds = thresholds or Thresholds()
    anchor_height = int(anchor_height)

    def clamp(height: int) -> int:
        return max(1, min(height, anchor_height))

    chosen: List[ChallengeHeight] = []
    seen = set()

    def add(height: int, kind: str) -> None:
        height = clamp(height)
        if height not in seen:
            seen.add(height)
            chosen.append(ChallengeHeight(height, kind))

    add(anchor_height, "anchor")
    add(anchor_height - SHORT_DELTA, "short")
    add(anchor_height - MEDIUM_DELTA, "medium")
    add(anchor_height - LONG_DELTA, "long")
    if Ravencoin.INCIDENT_CHECKPOINT_HEIGHT <= anchor_height:
        # Below the checkpoint height it cannot be challenged through this
        # set; the standard probe already fetches it independently.
        add(Ravencoin.INCIDENT_CHECKPOINT_HEIGHT, "checkpoint")

    window = max(1, min(thresholds.challenge_random_window, anchor_height - 1))
    for index in range(max(0, thresholds.random_challenges)):
        digest = hashlib.sha256(
            CHALLENGE_DERIVATION_DOMAIN
            + bytes.fromhex(challenge_nonce)
            + str(anchor_height).encode("ascii")
            + str(index).encode("ascii")).digest()
        offset = 1 + (int.from_bytes(digest[:8], "big") % window)
        add(anchor_height - offset, f"random{index}")
    return tuple(chosen)


def build_challenge_set(observations: Sequence[ChainObservation],
                        thresholds: Optional[Thresholds] = None,
                        challenge_nonce: Optional[str] = None,
                        ) -> Optional[ChallengeSet]:
    """Anchor + heights for this crawl, or None without an anchor."""
    thresholds = thresholds or Thresholds()
    anchor = select_stable_anchor(observations, thresholds)
    if anchor is None:
        return None
    nonce = challenge_nonce or new_challenge_nonce()
    return ChallengeSet(
        anchor_height=anchor,
        challenge_nonce=nonce,
        heights=derive_challenge_heights(anchor, nonce, thresholds))


def _attested(group: str) -> bool:
    return bool(group) and not group.startswith(UNKNOWN_GROUP_PREFIX)


def evaluate_challenges(records: Sequence[ChallengeRecord],
                        challenge: ChallengeSet,
                        *, tips: Optional[Mapping[str, int]] = None,
                        confirmations: Optional[Mapping[str, int]] = None,
                        thresholds: Optional[Thresholds] = None,
                        ) -> ChallengeVerdictDetail:
    """Turn challenge answers into a verdict.  Rules, in order:

    1. Group a server's answers per operator group.  A group whose own
       endpoints disagree with each other at the same height is itself
       conflicting evidence about that height.
    2. A challenge height is *corroborated* when at least two attested
       groups produced the same valid hash for it.  Those hashes are the
       comparison baseline; the baseline can never be a single group's
       unilateral claim, which is what stops a newly selected anchor
       (or a lying server at it) from validating itself.
    3. Any attested group whose valid hash disagrees with a corroborated
       hash at the same height is conflicting evidence.  At the pinned
       incident checkpoint, disagreement with the network constant is
       immediately serious regardless of corroboration.
    4. Missing evidence is not agreement: an attested group that could
       have answered a height (its own reported tip reached it) but did
       not produce a valid hash leaves the round CHALLENGE_INCOMPLETE.
    5. An attested group whose tip is below the anchor is lagging, which
       is TEMPORARY_LAG, never conflict.
    """
    thresholds = thresholds or Thresholds()
    tips = dict(tips or {})
    confirmations = dict(confirmations or {})
    heights = challenge.height_values()

    per_group: Dict[str, Dict[int, set]] = {}
    for record in records:
        per_group.setdefault(record.operator_group, {}).setdefault(
            record.height, set()).add(record.block_hash or "")

    corroborated: Dict[int, str] = {}
    conflicting: set = set()
    incomplete: set = set()
    lagging: set = set()
    # First pass: one value per attested group per height (a group whose
    # own endpoints disagree collapses to "!<digest>" and conflicts with
    # itself).  Two attested groups with different valid values at the
    # same height are mutual conflict evidence even with no third group
    # to break the tie: there is no majority rule here, and none is
    # needed to say "these two contradict each other".
    values_by_height: Dict[int, Dict[str, str]] = {}
    for group, answers in per_group.items():
        if not _attested(group):
            continue
        for height, produced in answers.items():
            valid = {value for value in produced if value}
            if not valid:
                continue
            value = next(iter(valid)) if len(valid) == 1 \
                else "!" + next(iter(valid))
            values_by_height.setdefault(height, {})[group] = value
    for height, by_group in values_by_height.items():
        distinct = set(by_group.values())
        if len(distinct) > 1 or any(v.startswith("!") for v in distinct):
            conflicting.update(by_group)

    for group, answers in per_group.items():
        if not _attested(group):
            continue
        group_tip = tips.get(group)
        for height in heights:
            produced = answers.get(height)
            valid = {value for value in (produced or set()) if value}
            if not valid:
                if group_tip is None or group_tip >= height:
                    # Could (or might) have answered but did not: the
                    # round is incomplete for this group either way when
                    # we cannot prove it was below the height.  A group
                    # whose tip is below the height could not have
                    # answered, so silence there is not incompleteness.
                    incomplete.add(group)
                continue
            if len(valid) > 1:
                # The group's own endpoints disagree at one height.
                conflicting.add(group)
                continue
            value = next(iter(valid))
            if height == Ravencoin.INCIDENT_CHECKPOINT_HEIGHT \
                    and value != Ravencoin.INCIDENT_CHECKPOINT_HASH:
                conflicting.add(group)
                continue
            peers = [
                g for g, other in per_group.items()
                if _attested(g) and g != group
                and next(iter({v for v in other.get(height, set()) if v}), None)
                == value
            ]
            if len(peers) + 1 >= 2:
                if height in corroborated and corroborated[height] != value:
                    conflicting.add(group)
                else:
                    corroborated.setdefault(height, value)
                    # Existing corroborated value wins as the baseline;
                    # first corroborated group sets it deterministically
                    # because per_group iteration is insertion-ordered.
        if group_tip is not None and group_tip < challenge.anchor_height:
            lagging.add(group)

    for group, answers in per_group.items():
        if not _attested(group) or group in conflicting:
            continue
        for height, baseline in corroborated.items():
            valid = {value for value in answers.get(height, set()) if value}
            if valid and valid != {baseline}:
                conflicting.add(group)
                break

    if conflicting:
        worst = max((confirmations.get(group, 1) for group in conflicting),
                    default=1)
        if worst >= thresholds.conflict_confirmations:
            return ChallengeVerdictDetail(
                ChallengeVerdict.CHAIN_CONFLICT,
                "attested operator groups returned different Ravencoin block "
                "hashes at the same challenge height across independent crawls",
                conflicting_groups=tuple(sorted(conflicting)),
                corroborated_hashes=corroborated)
        return ChallengeVerdictDetail(
            ChallengeVerdict.CONFLICT_SUSPECTED,
            "a shared-height hash disagreement was observed but is not yet "
            "confirmed by an independent crawl",
            conflicting_groups=tuple(sorted(conflicting)),
            corroborated_hashes=corroborated)

    if len([g for g in per_group if _attested(g)]) < 2:
        return ChallengeVerdictDetail(
            ChallengeVerdict.INSUFFICIENT_CORROBORATION,
            "fewer than two attested operator groups answered the challenges",
            corroborated_hashes=corroborated)

    if incomplete:
        return ChallengeVerdictDetail(
            ChallengeVerdict.CHALLENGE_INCOMPLETE,
            f"attested group(s) {sorted(incomplete)} did not return valid "
            f"headers for heights they could answer",
            corroborated_hashes=corroborated)

    # Reaching this point means no attested group is conflicting or
    # incomplete, so every one of them answered every height its own tip
    # allowed and matched the corroborated baseline.  A group counts as
    # verified when it actually produced at least one valid hash: silence
    # is not agreement, and a group that answered nothing verified
    # nothing.
    verified = tuple(sorted(
        group for group, answers in per_group.items()
        if _attested(group)
        and any(value for by_height in answers.values() for value in by_height)))
    if lagging and not corroborated:
        return ChallengeVerdictDetail(
            ChallengeVerdict.INSUFFICIENT_CORROBORATION,
            "no challenge height was corroborated by two attested groups",
            corroborated_hashes=corroborated)
    status = ChallengeVerdict.VALID
    detail = (f"{len(verified)} attested group(s) agree at "
              f"{len(corroborated)} corroborated challenge height(s)")
    if lagging:
        status = ChallengeVerdict.TEMPORARY_LAG
        detail += f"; {sorted(lagging)} trail the anchor height"
    return ChallengeVerdictDetail(
        status, detail, verified_groups=verified,
        corroborated_hashes=corroborated)
