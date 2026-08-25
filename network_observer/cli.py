#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Command line entry point for the Electrum monitor.

    python -m network_observer.cli status
    python -m network_observer.cli discover-now
    python -m network_observer.cli publish --directory-version 3

Nothing here deletes data.  A rescan adds observations; it never resets history.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import pathlib
import sys
import time
from typing import List, Optional

from .classify import (
    ChainObservation, classify_assets, classify_backend, classify_index_lag,
    compare_chains, count_independent_operators, count_unknown_safe_endpoints,
    independent_groups, is_corroborated, operator_group_key,
)
from .crawl import (
    Crawler, asset_capability_calls, challenge_calls, parse_asset_capability_matrix,
    parse_challenge_responses,
    probe_endpoint,
)
from .directory import build_directory
from .model import (
    Availability, DiscoverySource, EndpointId, Limits, Security, Thresholds, Transport,
)
from .store import Store

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_SEEDS = PACKAGE_DIR / "config" / "seeds.json"
DEFAULT_SENTINELS = PACKAGE_DIR / "config" / "asset-sentinels.json"
DEFAULT_REGISTRY = PACKAGE_DIR / "config" / "operator-registry.json"
DEFAULT_POLICY_TRUSTED_KEY = (
    REPO_ROOT / "core-safety" / "production" / "core-policy-signing-public-key.hex")


def _load_core_safety_policy_module():
    """Import the canonical signed-policy verifier from core-safety/scripts.

    That module is not an installed package (it is loaded the same
    ad-hoc way by tests/test_core_safety_policy.py), so it is loaded by
    file path rather than reimplementing signature verification here.
    """
    path = REPO_ROOT / "core-safety" / "scripts" / "policy.py"
    spec = importlib.util.spec_from_file_location("core_safety_policy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_core_policy = _load_core_safety_policy_module()


def load_trusted_policy_keys(path: pathlib.Path) -> dict:
    """Read the pinned Ed25519 public key(s) the policy must be signed with.

    The file holds one raw 32-byte key, hex-encoded. Keyed by the same
    key id the signer publishes, so a rotated key can be added by adding
    a line without ever trusting a key the policy itself supplies.
    """
    trusted = {}
    for line in pathlib.Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        public_bytes = bytes.fromhex(line)
        trusted[_core_policy.key_id_for(public_bytes)] = public_bytes
    return trusted


def load_seeds(path: pathlib.Path) -> List[tuple]:
    """Read bootstrap seeds.  A seed is a hint, never an endorsement."""
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for seed in document.get("seeds", []):
        group = seed.get("operatorGroup")
        if seed.get("sslPort"):
            entries.append((EndpointId(seed["hostname"], int(seed["sslPort"]),
                                       Transport.TLS), group, seed.get("operator")))
        if seed.get("tcpPort"):
            entries.append((EndpointId(seed["hostname"], int(seed["tcpPort"]),
                                       Transport.TCP), group, seed.get("operator")))
    return entries


def load_registry(path: pathlib.Path) -> List[tuple]:
    """Read the voluntary registry.  Registration is not endorsement."""
    if not path.exists():
        return []
    document = json.loads(path.read_text(encoding="utf-8"))
    entries = []
    for operator in document.get("operators", []):
        group = operator.get("operatorGroup")
        name = operator.get("operator")
        for endpoint in operator.get("endpoints", []):
            if endpoint.get("sslPort"):
                entries.append((EndpointId(endpoint["hostname"],
                                           int(endpoint["sslPort"]), Transport.TLS),
                                group, name))
            if endpoint.get("tcpPort"):
                entries.append((EndpointId(endpoint["hostname"],
                                           int(endpoint["tcpPort"]), Transport.TCP),
                                group, name))
    return entries


EMPTY_POLICY = {"releases": []}


def load_policy(path: Optional[str], *, trusted_keys: dict,
                minimum_policy_version: int = 0) -> dict:
    """Load and verify the signed safe-Core policy body, or an empty policy.

    An empty policy certifies nothing, so every backend comes out
    UNREVIEWED_CORE.  That is the correct answer both when no policy is
    available and when one is available but does not verify: a tampered,
    unsigned, wrongly-signed, malformed or rolled-back policy must never be
    treated as an endorsement, because this monitor signs and publishes
    what it derives from it.  Verification failure is never silently
    upgraded to SAFE.
    """
    if not path:
        return dict(EMPTY_POLICY)
    try:
        document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: could not read policy {path}: {exc}; treating as "
              f"no policy (UNREVIEWED_CORE)", file=sys.stderr)
        return dict(EMPTY_POLICY)
    try:
        return _core_policy.verify_policy(
            document, trusted_keys, minimum_policy_version=minimum_policy_version)
    except _core_policy.PolicyError as exc:
        print(f"warning: policy {path} failed verification: {exc}; treating as "
              f"no policy (UNREVIEWED_CORE)", file=sys.stderr)
        return dict(EMPTY_POLICY)


async def run_discovery(store: Store, *, seeds_path: pathlib.Path,
                        registry_path: pathlib.Path, policy: dict,
                        limits: Limits, thresholds: Thresholds,
                        allow_private: bool = False,
                        vantage_point: str = "local",
                        reference: Optional[ChainObservation] = None) -> dict:
    now = int(time.time())
    seeds = []
    for endpoint, group, operator in load_seeds(seeds_path):
        store.upsert_endpoint(endpoint, source=DiscoverySource.BOOTSTRAP,
                              operator=operator, operator_group=group, now=now)
        seeds.append(endpoint)
    for endpoint, group, operator in load_registry(registry_path):
        store.upsert_endpoint(endpoint, source=DiscoverySource.REGISTRY,
                              operator=operator, operator_group=group, now=now)
        if endpoint not in seeds:
            seeds.append(endpoint)

    crawler = Crawler(limits=limits, allow_private=allow_private)
    results, edges = await crawler.crawl(seeds)

    for source, target in edges:
        store.record_peer_edge(source, target, now=now)

    observations = []
    for endpoint, result in results.items():
        result.vantage_point = vantage_point
        store.record_probe(result, now=now)
        state = store.load_state(endpoint)
        if state is None:
            continue
        state.last_probe = now
        if result.reachable:
            state.last_success = now
            state.register_success(thresholds)
            security, reason = classify_backend(result.backend, policy)
            state.security, state.reason = security, reason
            observations.append(ChainObservation(
                endpoint=endpoint, height=result.height, tip_hash=result.tip_hash,
                genesis_hash=result.genesis_hash,
                checkpoint_hash=result.checkpoint_hash,
                operator_group=state.operator_group))
        else:
            state.register_failure(thresholds)
            state.reason = result.error or "probe failed"
        store.save_state(state, now=now)

    # First pass: a pure, this-crawl-only comparison, used to (a) know which
    # groups conflicted or were verified just now, and (b) update the
    # persisted cross-crawl conflict-confirmation counters below.  The
    # confirmations=1 default here is deliberate: whether a conflict is
    # *confirmed* depends on what earlier, independent crawls saw, which
    # compare_chains itself has no memory of -- that memory lives in the
    # store (see conflict_confirmations / R-03).
    probe_verdict = compare_chains(observations, thresholds=thresholds, reference=reference)
    usable_now = [item for item in observations if item.height is not None and item.tip_hash]
    seen_groups = set(independent_groups(usable_now).keys())
    conflicting_now = set(probe_verdict.conflicting_groups)
    verified_now = set(probe_verdict.verified_groups)

    max_confirmations = 1
    for group in seen_groups:
        if group in conflicting_now:
            max_confirmations = max(
                max_confirmations, store.record_conflict(group, now=now))
        elif group in verified_now:
            # Recovery requires a positively verified clean comparison, not
            # merely the absence of a conflict this crawl: an endpoint that
            # simply withholds checkpoint evidence for one crawl must not
            # launder away a prior confirmed conflict by going quiet.
            store.clear_conflict(group)
        # Neither conflicting nor verified this crawl (e.g. lagging, or
        # simply uncorroborated): leave any existing confirmation count
        # untouched.

    verdict = probe_verdict
    if conflicting_now and max_confirmations > 1:
        verdict = compare_chains(observations, thresholds=thresholds,
                                 reference=reference, confirmations=max_confirmations)

    if verdict.status == "CHAIN_CONFLICT":
        for state in store.all_states():
            group = operator_group_key(state.operator_group, state.endpoint.hostname)
            if group in verdict.conflicting_groups:
                state.security = Security.CONFLICT
                state.reason = verdict.detail
                store.save_state(state, now=now)
    elif is_corroborated(verdict, reference_supplied=reference is not None):
        # SAFE requires positively verified chain evidence (verdict.verified_
        # groups), never merely the absence of a detected conflict: a
        # configured --reference with no comparable evidence, or a claim
        # ahead of everything it was actually compared against, counts for
        # nothing (see is_corroborated() and _verified_groups()).  Only the
        # specific endpoints whose own evidence was verified are promoted --
        # never every UNVERIFIED+REACHABLE endpoint store-wide, which is
        # what let an uncorroborated rider ride a genuine corroboration to
        # SAFE.
        for state in store.all_states():
            group = operator_group_key(state.operator_group, state.endpoint.hostname)
            if state.security is Security.UNVERIFIED \
                    and state.availability is Availability.REACHABLE \
                    and group in verdict.verified_groups:
                state.security = Security.SAFE
                state.reason = "certified backend and independently corroborated chain evidence"
                store.save_state(state, now=now)
    # A VALID-but-uncorroborated verdict, CONFLICT_SUSPECTED, TEMPORARY_LAG or
    # UNKNOWN all leave existing classifications as they were: no promotion.

    # ---- Chain Quorum 2.0: shared-height header challenges -----------------
    # The anchor comes from the same observations the cheap probe collected,
    # so a second connection per reachable endpoint is the only extra load,
    # bounded by the challenge set (a handful of heights).
    challenge_summary = {"anchor": None, "status": "SKIPPED", "detail": ""}
    from . import quorum as quorum_module
    challenge_set = quorum_module.build_challenge_set(
        observations, thresholds=thresholds)
    challenge_results = {}
    if challenge_set is not None:
        crawl_id = f"{now}-{challenge_set.challenge_nonce[:8]}"
        heights = challenge_set.height_values()
        records = []
        tips_by_group = {}
        for endpoint, result in results.items():
            if not result.reachable:
                continue
            state = store.load_state(endpoint)
            group = operator_group_key(
                state.operator_group if state else None, endpoint.hostname)
            if result.height is not None:
                best = tips_by_group.get(group)
                if best is None or result.height > best:
                    tips_by_group[group] = result.height
            challenge_calls_list = (
                [("server.version", ["Ravencoin-Network-Observer/1.0", "1.4"])]
                + challenge_calls(heights))
            try:
                challenge_result = await probe_endpoint(
                    endpoint, limits=limits, allow_private=allow_private,
                    calls=challenge_calls_list)
            except (OSError, ValueError):
                continue
            if not challenge_result.reachable:
                continue
            answers = parse_challenge_responses(
                challenge_calls_list, challenge_result.extra_responses or {},
                heights)
            challenge_results[str(endpoint)] = answers
            endpoint_id = store.endpoint_id(endpoint)
            if endpoint_id is None:
                continue
            for height, block_hash in answers.items():
                records.append(quorum_module.ChallengeRecord(
                    endpoint=endpoint, operator_group=group, height=height,
                    block_hash=block_hash, observer=vantage_point,
                    observed_at=now))
        confirmations = {group: store.conflict_confirmations(group)
                         for group in tips_by_group}
        challenge_verdict = quorum_module.evaluate_challenges(
            records, challenge_set, tips=tips_by_group,
            confirmations=confirmations, thresholds=thresholds)
        challenge_id = store.record_challenge_round(
            crawl_id, challenge_set.anchor_height, challenge_set.challenge_nonce,
            json.dumps(list(heights)), challenge_verdict.status.value,
            challenge_verdict.detail, now=now)
        for record in records:
            store.record_challenge_response(
                challenge_id, store.endpoint_id(record.endpoint),
                record.height, record.block_hash, record.operator_group,
                record.observer, now=now)
        # Cross-crawl confirmation bookkeeping for challenge conflicts,
        # same discipline as the tip-level conflicts above.
        for group in tips_by_group:
            if group in challenge_verdict.conflicting_groups:
                store.record_conflict(group, now=now)
            elif group in challenge_verdict.verified_groups:
                store.clear_conflict(group)
        challenge_summary = {
            "anchor": challenge_set.anchor_height,
            "status": challenge_verdict.status.value,
            "detail": challenge_verdict.detail,
        }

    # ---- Index health (Part 4): backend Core height vs served tip ----------
    index_states = {}
    for endpoint, result in results.items():
        health, lag = classify_index_lag(result.core_height, result.height,
                                         thresholds)
        index_states[str(endpoint)] = {"health": health.value, "lag": lag}

    # ---- Active asset capability probes (Part 5) ----------------------------
    # Disabled cleanly when no sentinel is configured: the features flag
    # answer stands and nothing is probed.
    from . import assets as assets_module
    sentinels = assets_module.load_sentinels(DEFAULT_SENTINELS)
    asset_summary = {"probed": 0, "capability": {}}
    plan = assets_module.capability_probe_plan(
        sentinels, limit=thresholds.asset_sentinel_queries)
    if plan:
        for endpoint, result in results.items():
            if not result.reachable:
                continue
            calls = ([("server.version", ["Ravencoin-Network-Observer/1.0",
                                          "1.4"])]
                     + asset_capability_calls(plan))
            try:
                asset_result = await probe_endpoint(
                    endpoint, limits=limits, allow_private=allow_private,
                    calls=calls)
            except (OSError, ValueError):
                continue
            if not asset_result.reachable:
                continue
            extra = asset_result.extra_responses or {}
            # Correlate through the shared request-key scheme: with more
            # than one sentinel the same method is asked once per sentinel
            # and answers are keyed method#request_index, so a lookup by
            # bare method name would misread every answer as missing.
            matrix = parse_asset_capability_matrix(plan, extra, calls)
            result.asset_methods = matrix
            asset_summary["capability"][str(endpoint)] = (
                assets_module.summarize_capability(matrix, result.features).value)
            asset_summary["probed"] += 1

    return {"probed": len(results), "edges": len(edges), "chain": verdict.status,
            "chain_detail": verdict.detail,
            "challenge": challenge_summary, "index": index_states,
            "assets": asset_summary}


def command_status(store: Store) -> int:
    states = store.all_states()
    by_availability = {}
    by_security = {}
    for state in states:
        by_availability[state.availability] = by_availability.get(state.availability, 0) + 1
        by_security[state.security] = by_security.get(state.security, 0) + 1

    print(f"Known endpoints: {len(states)}")
    for availability in Availability:
        count = by_availability.get(availability, 0)
        if count:
            print(f"  {availability.value:<12} {count}")
    print()
    for security in Security:
        count = by_security.get(security, 0)
        if count:
            print(f"  {security.value:<18} {count}")

    groups = count_independent_operators(states)
    print()
    print(f"Independent safe operator groups: {len(groups)}")
    for group, endpoints in groups.items():
        print(f"  {group:<16} SAFE   {endpoints} endpoint(s)")
    if not groups:
        print("  none yet; endpoint count is not operator diversity")
    unknown_safe = count_unknown_safe_endpoints(states)
    if unknown_safe:
        print(f"  ({unknown_safe} additional SAFE endpoint(s) with no known operator "
              f"identity; not counted as independent diversity)")
    return 0


def command_publish(store: Store, *, version: int, output: str) -> int:
    body = build_directory(store.all_states(), directory_version=version)
    path = pathlib.Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote unsigned directory version {version} with {len(body['servers'])} "
          f"entries to {path}")
    print("sign it with the monitor signing key before publishing; an unsigned "
          "directory is not usable by a client")
    return 0


def command_observer_keygen(args) -> int:
    from .observer import generate_observer_keypair
    info = generate_observer_keypair(args.key_dir, name=args.name)
    print(f"observer public key: {info['publicKeyHex']}")
    print(f"observer key id:     {info['keyId']}")
    print(f"public key written:  {info['publicKeyPath']}")
    print(f"private key written: {info['privateKeyPath']} (mode 0600; never "
          f"share, publish or commit it)")
    return 0


def command_verify_observation(store: Store, args) -> int:
    from .observer import verify_observation_bundle
    document = json.loads(pathlib.Path(args.bundle).read_text(encoding="utf-8"))
    trusted = {}
    for line in pathlib.Path(args.trusted_observers).read_text(
            encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            public = bytes.fromhex(line)
            trusted[generate_key_id(public)] = public
    body = verify_observation_bundle(
        document, trusted,
        observer_sequence_high_water=store.observer_high_water())
    store.record_accepted_observation(
        body["observerKeyId"], body["observerId"], body["sequence"],
        json.dumps(document), body.get("crawlId"))
    print(f"verified observation from observer {body['observerId']} "
          f"(key {body['observerKeyId']}, sequence {body['sequence']}, "
          f"{len(body['observations'])} endpoint observation(s))")
    return 0


def command_aggregate_observations(store: Store, args) -> int:
    from .vantage import compare_vantage_views, views_from_bundles
    bundles = []
    for path in args.bundles:
        bundles.append(json.loads(pathlib.Path(path).read_text(encoding="utf-8")))
    if len(bundles) > Limits().max_observation_bundles:
        print("error: too many bundles for one aggregation", file=sys.stderr)
        return 1
    summaries = compare_vantage_views(views_from_bundles(bundles))
    for summary in summaries:
        print(f"{summary.endpoint:<44} {summary.agreement.value:<40} "
              f"{summary.detail}")
    return 0


def command_operator_verify(store: Store, args) -> int:
    from .operators import (IdentityState, verify_operator_declaration)
    attested = {}
    if pathlib.Path(args.attested_keys).exists():
        for line in pathlib.Path(args.attested_keys).read_text(
                encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                attested[parts[0]] = parts[1]
    document = json.loads(pathlib.Path(args.declaration).read_text(
        encoding="utf-8"))
    declaration = verify_operator_declaration(
        document, attested,
        sequence_high_water=store.operator_sequence_high_water())
    store.record_operator_declaration(declaration)
    state = ("REGISTRY_ATTESTED (counts toward operator diversity)"
             if declaration.state is IdentityState.REGISTRY_ATTESTED else
             f"{declaration.state.value} (valid signature, NOT independent "
             f"quorum: see docs/network-observer.md)")
    print(f"operator {declaration.operator_name} group "
          f"{declaration.operator_group} key {declaration.operator_key_id}: "
          f"{state}")
    print(f"{len(declaration.endpoints)} endpoint(s) bound: "
          f"{', '.join(declaration.endpoints)}")
    return 0


def command_publish_snapshot(store: Store, args) -> int:
    from .observer import load_observer_private_key
    from .snapshot import build_snapshot, sign_snapshot
    states = store.all_states()
    body = build_snapshot(
        states, snapshot_version=args.snapshot_version,
        chain=json.loads(args.chain_summary) if args.chain_summary else {},
        infrastructure=json.loads(args.infrastructure) if args.infrastructure else {},
        observers=json.loads(args.observers) if args.observers else {},
        asset_sampling=json.loads(args.asset_sampling) if args.asset_sampling else {})
    minimum = store.minimum_snapshot_version()
    if args.snapshot_version <= minimum:
        print(f"error: snapshot version {args.snapshot_version} is not above "
              f"the accepted {minimum}; refusing a rollback", file=sys.stderr)
        return 1
    private_key, public_hex = load_observer_private_key(args.signing_key)
    document = sign_snapshot(body, private_key, key_id=generate_key_id(
        bytes.fromhex(public_hex)))
    path = pathlib.Path(args.output)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    store.record_snapshot(args.snapshot_version, json.dumps(body))
    print(f"wrote signed network snapshot version {args.snapshot_version} "
          f"({len(body['servers'])} endpoints) to {path}")
    return 0


def generate_key_id(public_bytes: bytes) -> str:
    import hashlib
    return hashlib.sha256(public_bytes).hexdigest()[:16]


def resolve_database_path(explicit: Optional[str]) -> str:
    """Pick the observer database file without ever abandoning an
    existing one: an explicit --database wins; otherwise a new install
    uses network-observer.sqlite3, and an installation that already has
    the legacy monitor.sqlite3 keeps using it until the operator moves
    it deliberately."""
    if explicit:
        return explicit
    new = pathlib.Path("network-observer.sqlite3")
    legacy = pathlib.Path("monitor.sqlite3")
    if legacy.exists() and not new.exists():
        print("note: continuing with the legacy database monitor.sqlite3")
        return str(legacy)
    return str(new)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default=None,
                        help="observer database; defaults to "
                             "network-observer.sqlite3, or the legacy "
                             "monitor.sqlite3 when only that exists")
    parser.add_argument("--seeds", default=str(DEFAULT_SEEDS))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    parser.add_argument("--policy", default=None,
                        help="signed safe-Core policy; without it nothing is certified")
    parser.add_argument("--policy-key", default=str(DEFAULT_POLICY_TRUSTED_KEY),
                        help="pinned Ed25519 public key(s) the policy must verify "
                             "against, one hex-encoded 32-byte key per line")
    parser.add_argument("--allow-private", action="store_true",
                        help="development only: permit probing private addresses")
    parser.add_argument("--vantage-point", default="local",
                        help="stable label for this crawler vantage point")
    parser.add_argument("--max-depth", type=int, default=None,
                        help="crawl depth limit for this run")
    parser.add_argument("--max-candidates", type=int, default=None,
                        help="new candidates accepted in this run")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="concurrent probes; keep it low to stay polite")
    parser.add_argument("--reference-height", type=int, default=None,
                        help="tip height from a trusted reference node (e.g. your "
                             "own Core), if you have one; an endpoint that "
                             "actually agrees with it can then be promoted from "
                             "one attested operator group instead of two -- but "
                             "only for what was actually compared: a height "
                             "the reference never reached is never agreement "
                             "on its own, no matter how the reference is set")
    parser.add_argument("--reference-tip-hash", default=None,
                        help="tip hash from that trusted reference node; required "
                             "together with --reference-height")
    parser.add_argument("--reference-checkpoint-hash", default=None,
                        help="optional: the reference node's hash for the "
                             "network's incident checkpoint block, if it has one; "
                             "strengthens the reference by letting it be compared "
                             "on real chain-identity evidence, not just a height/"
                             "tip pair")
    parser.add_argument("--reference-genesis-hash", default=None,
                        help="optional: the reference node's genesis hash, if "
                             "you want it compared too")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("discover-now")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--directory-version", type=int, required=True)
    publish.add_argument("--output", default="network-observer-directory.json")

    keygen = subparsers.add_parser(
        "observer-keygen",
        help="generate a local Ed25519 observer keypair (Phase 1 observer)")
    keygen.add_argument("--key-dir", default="observer-keys")
    keygen.add_argument("--name", default="observer")

    verify_obs = subparsers.add_parser(
        "verify-observation",
        help="verify a signed observation bundle and record its sequence")
    verify_obs.add_argument("bundle")
    verify_obs.add_argument(
        "--trusted-observers", default="observer-trusted-keys.hex",
        help="hex Ed25519 public keys of observers this aggregator trusts, "
             "one per line; a bundle signed by any other key is refused")

    aggregate = subparsers.add_parser(
        "aggregate-observations",
        help="cross-compare verified bundles from several vantage points")
    aggregate.add_argument("bundles", nargs="+")

    operator_verify = subparsers.add_parser(
        "operator-verify",
        help="verify a signed operator declaration and store its identity")
    operator_verify.add_argument("declaration")
    operator_verify.add_argument(
        "--attested-keys", default="operator-attested-keys.txt",
        help="lines of '<operatorKeyId> <operatorGroup>' this deployment's "
             "policy attests; anything else is SELF_SIGNED, never quorum")

    publish_snapshot = subparsers.add_parser(
        "publish-snapshot",
        help="build and sign the network observation snapshot")
    publish_snapshot.add_argument("--snapshot-version", type=int, required=True)
    publish_snapshot.add_argument("--output",
                                  default="network-observer-snapshot.json")
    publish_snapshot.add_argument("--signing-key", required=True,
                                  help="local Ed25519 signing key (hex seed file)")
    publish_snapshot.add_argument("--chain-summary", default=None)
    publish_snapshot.add_argument("--infrastructure", default=None)
    publish_snapshot.add_argument("--observers", default=None)
    publish_snapshot.add_argument("--asset-sampling", default=None)

    arguments = parser.parse_args(argv)
    if arguments.command == "observer-keygen":
        return command_observer_keygen(arguments)
    store = Store(resolve_database_path(arguments.database))
    try:
        if arguments.command == "status":
            return command_status(store)
        if arguments.command == "publish":
            return command_publish(store, version=arguments.directory_version,
                                   output=arguments.output)
        if arguments.command == "verify-observation":
            return command_verify_observation(store, arguments)
        if arguments.command == "aggregate-observations":
            return command_aggregate_observations(store, arguments)
        if arguments.command == "operator-verify":
            return command_operator_verify(store, arguments)
        if arguments.command == "publish-snapshot":
            return command_publish_snapshot(store, arguments)
        limits = Limits()
        if arguments.max_depth is not None:
            limits.max_crawl_depth = arguments.max_depth
        if arguments.max_candidates is not None:
            limits.max_new_candidates_per_crawl = arguments.max_candidates
        if arguments.concurrency is not None:
            limits.max_concurrent_probes = arguments.concurrency
        trusted_keys = load_trusted_policy_keys(pathlib.Path(arguments.policy_key))
        policy = load_policy(
            arguments.policy, trusted_keys=trusted_keys,
            minimum_policy_version=store.load_minimum_policy_version())
        policy_version = policy.get("policyVersion")
        if isinstance(policy_version, int) and not isinstance(policy_version, bool):
            store.record_policy_version(policy_version)
        reference = None
        if arguments.reference_height is not None and arguments.reference_tip_hash:
            reference = ChainObservation(
                endpoint=EndpointId("(operator reference)", 0, Transport.TCP),
                height=arguments.reference_height,
                tip_hash=arguments.reference_tip_hash,
                checkpoint_hash=arguments.reference_checkpoint_hash,
                genesis_hash=arguments.reference_genesis_hash,
                operator_group="TRUSTED-REFERENCE")
        summary = asyncio.run(run_discovery(
            store,
            seeds_path=pathlib.Path(arguments.seeds),
            registry_path=pathlib.Path(arguments.registry),
            policy=policy,
            limits=limits, thresholds=Thresholds(),
            allow_private=arguments.allow_private,
            vantage_point=arguments.vantage_point,
            reference=reference))
        print(f"probed {summary['probed']} endpoint(s), {summary['edges']} peer edge(s), "
              f"chain {summary['chain']}: {summary['chain_detail']}")
        challenge = summary.get("challenge") or {}
        print(f"chain quorum 2.0: anchor {challenge.get('anchor')} "
              f"status {challenge.get('status')}: {challenge.get('detail', '')}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
