# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Active asset capability verification and Asset Data Quorum v1.

Two separate questions, never mixed:

* does the server actually answer the asset RPCs a wallet needs
  (capability, cheap to ask, actively probed instead of trusting a
  features flag);
* do independent operators serve the SAME asset data for the same
  chain height (data integrity, comparable only under strict
  preconditions).

Height-bound comparison semantics were proven from the server code, not
assumed: every ``*_history`` RPC (meta, verifier string, qualifier tags,
h160 tags, freezes, restricted associations) returns entries that each
carry a confirmed ``height`` and are deterministically sorted by
``(height, tx_hash)`` (electrumx/server/db.py, the six lookup functions
cited in docs/network-observer-audit.md section I).  Requesting a
history with ``include_mempool=false`` and folding only entries with
``height <= H``, in the order returned, therefore reconstructs the
confirmed state as of height H exactly.  Anything not reconstructable
that way is NOT compared: NOT_COMPARABLE_AT_SHARED_HEIGHT is a valid,
honest answer, and wrong certainty is worse than admitted ignorance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .classify import UNKNOWN_GROUP_PREFIX
from .model import AssetSupport

#: Capability probes: cheap, read-only, bounded.  Each entry is a method
#: plus how to build its params from a sentinel asset name.  Deliberately
#: excluded: blockchain.asset.list_addresses_by_asset (unbounded server
#: work) and anything that mutates state.  No method here spends RVN or
#: creates assets, ever.
CAPABILITY_PROBES: Tuple[Tuple[str, str], ...] = (
    # (method, sentinel placeholder)
    ("blockchain.asset.get_meta", "{asset}"),
    ("blockchain.asset.get_assets_with_prefix", "{prefix}"),
    ("blockchain.asset.get_meta_history", "{asset}"),
    ("blockchain.tag.qualifier.history", "{qualifier}"),
)

#: The two methods without which a server is not usable for asset
#: wallets, mirroring monitor.classify.REQUIRED_ASSET_METHODS.
REQUIRED_METHODS = (
    "blockchain.asset.get_meta",
    "blockchain.asset.get_assets_with_prefix",
)

#: Domain separator per data type for canonical digests, so a digest of
#: verifier-string state can never collide with one of tag state by
#: accident of shape.
DIGEST_DOMAIN = b"RAVENCOIN-NETWORK-OBSERVER-ASSET-QUORUM-v1\x00"

#: Data types whose histories allow exact height-bound reconstruction
#: (see module docstring).  Anything else must answer
#: NOT_COMPARABLE_AT_SHARED_HEIGHT rather than be approximated.
RECONSTRUCTIBLE_TYPES = (
    "meta_history",
    "verifier_string_history",
    "qualifier_history",
    "frozen_history",
)


class AssetDataVerdict(Enum):
    """Asset-data integrity, a separate failure domain from consensus."""

    AGREE = "ASSET_DATA_AGREE"
    MISMATCH_SUSPECTED = "ASSET_DATA_MISMATCH_SUSPECTED"
    CONFLICT = "ASSET_DATA_CONFLICT"
    INSUFFICIENT_QUORUM = "ASSET_DATA_INSUFFICIENT_QUORUM"
    NOT_COMPARABLE = "ASSET_DATA_NOT_COMPARABLE"
    UNSUPPORTED = "ASSET_DATA_UNSUPPORTED"


def default_sentinels() -> dict:
    """The shipped, default-safe sentinel configuration.

    Ships EMPTY on purpose: a sentinel must be a cheap, public, permanent
    query on the real chain, and inventing one without operator review
    would be this repository guessing what is worth monitoring.  The
    mechanism is complete; operators opt in by editing
    network_observer/config/asset-sentinels.json.  An empty list disables active
    asset probing cleanly (capability falls back to the features flag,
    quorum reports INSUFFICIENT_QUORUM rather than fabricating samples).
    """
    return {
        "note": "Sentinel assets for active capability and data-quorum "
                "probes. Keep queries cheap, public and permanent. Never "
                "create assets or spend RVN for monitoring. An empty list "
                "disables active asset probing.",
        "sentinels": [],
    }


def load_sentinels(path) -> dict:
    """Load and bound the sentinel config; a malformed file disables
    probing rather than probing something unreviewed."""
    import pathlib

    try:
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default_sentinels()
    sentinels = document.get("sentinels")
    if not isinstance(sentinels, list):
        return default_sentinels()
    bounded = []
    for item in sentinels[:32]:
        if not isinstance(item, Mapping):
            continue
        name = item.get("asset")
        if not isinstance(name, str) or not 3 <= len(name) <= 32:
            continue
        entry = {"asset": name}
        for optional in ("qualifier", "restricted", "prefix"):
            value = item.get(optional)
            if isinstance(value, str) and 1 <= len(value) <= 32:
                entry[optional] = value
        bounded.append(entry)
    return {"note": document.get("note", ""), "sentinels": bounded}


def capability_probe_plan(sentinels: Mapping, limit: int = 4) -> List[dict]:
    """The bounded, polite probe plan for one crawl.

    At most ``limit`` sentinels, each with the fixed cheap method set.
    The plan is deterministic given the config so two crawls of the same
    config ask the same things.
    """
    plan = []
    for sentinel in sentinels.get("sentinels", [])[:max(0, limit)]:
        asset = sentinel["asset"]
        prefix = sentinel.get("prefix") or asset[:1]
        qualifier = sentinel.get("qualifier") or asset
        for method, template in CAPABILITY_PROBES:
            params = template.format(asset=asset, prefix=prefix,
                                     qualifier=qualifier)
            plan.append({"method": method, "params": [params, False]
                         if method.endswith("_history") else [params]})
    return plan[: 4 * max(0, limit)]


def summarize_capability(matrix: Optional[Mapping], features=None) -> AssetSupport:
    """Map an actively probed per-method matrix to a capability class.

    LEGACY means the server claims assets in server.features but the
    required methods do not actually work: a flag without a function.
    """
    if not matrix:
        claimed = False
        if isinstance(features, Mapping):
            ravencoin = features.get("ravencoin")
            claimed = isinstance(ravencoin, Mapping) \
                and ravencoin.get("assets") is True
        return AssetSupport.LEGACY if claimed else AssetSupport.UNKNOWN
    working = [name for name, ok in matrix.items() if ok]
    if all(matrix.get(name) for name in REQUIRED_METHODS):
        return AssetSupport.CAPABLE
    if working:
        return AssetSupport.PARTIAL
    return AssetSupport.UNSUPPORTED


# ------------------------------------------------------------- canonical form

def canonical_digest(data_type: str, payload) -> str:
    """Deterministic digest with per-type domain separation.

    Mappings: keys sorted (JSON objects are unordered in transit, so the
    order cannot carry meaning there).  Lists: order preserved verbatim,
    because history order is protocol semantics proven in the server
    code (sorted by height, tx_hash) and re-sorting it here could hide
    exactly the divergence this digest exists to expose.  Numbers stay
    numbers and strings stay strings: no implicit coercion where the
    protocol does not guarantee equivalence.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                         ensure_ascii=True)
    return hashlib.sha256(
        DIGEST_DOMAIN + data_type.encode("ascii") + b"\x00"
        + encoded.encode("utf-8")).hexdigest()


def entries_at_height(history: Sequence[Mapping], height: int) -> List[Mapping]:
    """Entries confirmed at or below ``height``, order preserved."""
    bounded = []
    for entry in history:
        if not isinstance(entry, Mapping):
            continue
        entry_height = entry.get("height")
        if isinstance(entry_height, int) and not isinstance(entry_height, bool) \
                and 0 <= entry_height <= height:
            bounded.append(entry)
    return bounded


def reconstruct_state(data_type: str, history: Sequence[Mapping],
                      height: int):
    """Fold a confirmed history into the state as of ``height``.

    Returns None when the data type is not exactly reconstructible
    (callers must then report NOT_COMPARABLE rather than guess), or the
    folded state:

    * meta_history: fields of the last entry (sats/divisions/ipfs/...)
    * verifier_string_history / frozen_history: the last entry's payload
    * qualifier_history: last flag per tagged h160
    """
    bounded = entries_at_height(history, height)
    if data_type == "meta_history":
        return dict(bounded[-1]) if bounded else {}
    if data_type in ("verifier_string_history", "frozen_history"):
        return dict(bounded[-1]) if bounded else None
    if data_type == "qualifier_history":
        state = {}
        for entry in bounded:
            subject = entry.get("h160") or entry.get("asset")
            if isinstance(subject, str):
                state[subject] = entry.get("flag")
        return state
    return None


def is_reconstructible(data_type: str) -> bool:
    return data_type in RECONSTRUCTIBLE_TYPES


# ------------------------------------------------------------------- quorum

@dataclass(frozen=True)
class AssetSample:
    """One operator's canonical asset state at one shared height."""

    data_type: str
    sentinel: str
    height: int
    digest: str
    operator_group: str


def _attested(group: str) -> bool:
    return bool(group) and not group.startswith(UNKNOWN_GROUP_PREFIX)


def compare_asset_samples(samples: Sequence[AssetSample], *,
                          chain_comparable: bool,
                          required_groups: int = 2) -> Tuple[
                              AssetDataVerdict, str, Dict[str, str]]:
    """Compare canonical asset-state digests across operators.

    Preconditions, enforced before any comparison:

    * all samples must be for the same data type, sentinel and height;
    * the chain context must be comparable (a Chain Quorum 2.0 round at
      this height completed without conflict).  Nodes observed at
      different heights see legitimately different asset state, and
      calling that a mismatch would be a false accusation;
    * only attested operator groups participate: three endpoints of one
      operator are one data source, and unknown operators never
      manufacture agreement.

    Returns ``(verdict, detail, digest_by_group)``.  One differing crawl
    is MISMATCH_SUSPECTED; escalation to CONFLICT is the caller's job,
    on persisted cross-crawl confirmations, mirroring chain conflicts.
    """
    if not samples:
        return (AssetDataVerdict.INSUFFICIENT_QUORUM,
                "no asset samples were collected", {})
    keys = {(sample.data_type, sample.sentinel, sample.height)
            for sample in samples}
    if len(keys) != 1:
        return (AssetDataVerdict.NOT_COMPARABLE,
                "samples span different types, sentinels or heights", {})
    if not chain_comparable:
        return (AssetDataVerdict.NOT_COMPARABLE,
                "chain context at the shared height is not corroborated", {})

    digest_by_group: Dict[str, str] = {}
    for sample in samples:
        if not _attested(sample.operator_group):
            continue
        digest_by_group.setdefault(sample.operator_group, sample.digest)
        if digest_by_group[sample.operator_group] != sample.digest:
            # The operator's own endpoints disagree with each other.
            digest_by_group[sample.operator_group] = f"!{sample.digest}"
    if len(digest_by_group) < required_groups:
        return (AssetDataVerdict.INSUFFICIENT_QUORUM,
                f"fewer than {required_groups} attested operators produced "
                f"comparable samples", digest_by_group)

    distinct = set(
        value for value in digest_by_group.values() if not value.startswith("!"))
    self_divergent = any(value.startswith("!")
                         for value in digest_by_group.values())
    if len(distinct) > 1 or self_divergent:
        # Without a trusted reference there is no "correct" digest to side
        # with, so disagreement is reported as exactly that, never
        # resolved by counting which side has more endpoints.
        return (AssetDataVerdict.MISMATCH_SUSPECTED,
                f"attested operators returned different canonical asset "
                f"state at height {samples[0].height}: {digest_by_group}",
                digest_by_group)
    return (AssetDataVerdict.AGREE,
            f"{len(digest_by_group)} attested operator(s) agree on canonical "
            f"state at height {samples[0].height}", digest_by_group)


def escalate_asset_verdict(verdict: AssetDataVerdict,
                           confirmations: int,
                           *, required: int = 2) -> AssetDataVerdict:
    """Escalate a suspected mismatch only with repeated comparable
    evidence, exactly like chain conflicts: one transient observation is
    never a confirmed conflict."""
    if verdict is AssetDataVerdict.MISMATCH_SUSPECTED \
            and confirmations >= required:
        return AssetDataVerdict.CONFLICT
    return verdict
