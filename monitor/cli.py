#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Command line entry point for the Electrum monitor.

    python -m monitor.cli status
    python -m monitor.cli discover-now
    python -m monitor.cli publish --directory-version 3

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
    ChainObservation, classify_assets, classify_backend, compare_chains,
    count_independent_operators, independent_groups,
)
from .crawl import Crawler
from .directory import build_directory
from .model import (
    Availability, DiscoverySource, EndpointId, Limits, Security, Thresholds, Transport,
)
from .store import Store

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
DEFAULT_SEEDS = PACKAGE_DIR / "config" / "seeds.json"
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

    verdict = compare_chains(observations, thresholds=thresholds, reference=reference)
    if verdict.status == "CHAIN_CONFLICT":
        for state in store.all_states():
            group = state.operator_group or f"UNKNOWN-{state.endpoint.hostname}"
            if group in verdict.conflicting_groups:
                state.security = Security.CONFLICT
                state.reason = verdict.detail
                store.save_state(state, now=now)
    elif verdict.status == "VALID" and (
            reference is not None or len(independent_groups(observations)) >= 2):
        # Promotion requires both a clean comparison AND independent
        # corroboration: a trusted reference observation, or agreement across
        # at least two independent operator groups.  CONFLICT_SUSPECTED and
        # TEMPORARY_LAG must never promote, and neither may a lone
        # self-consistent group: a single attacker-controlled endpoint (or
        # any number of hostnames under one attacker) is always internally
        # consistent with itself.  The comparison anchor being the highest
        # self-reported height when no reference is supplied is therefore
        # never enough on its own; it only matters once a second independent
        # group is required to agree with it.
        for state in store.all_states():
            if state.security is Security.UNVERIFIED \
                    and state.availability is Availability.REACHABLE:
                state.security = Security.SAFE
                state.reason = "certified backend and independently corroborated chain evidence"
                store.save_state(state, now=now)
    # A VALID-but-uncorroborated verdict, CONFLICT_SUSPECTED, TEMPORARY_LAG or
    # UNKNOWN all leave existing classifications as they were: no promotion.

    return {"probed": len(results), "edges": len(edges), "chain": verdict.status,
            "chain_detail": verdict.detail}


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", default="monitor.sqlite3")
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
                             "own Core), if you have one; enables SAFE promotion "
                             "from agreement with it alone, without needing a "
                             "second independent operator group")
    parser.add_argument("--reference-tip-hash", default=None,
                        help="tip hash from that trusted reference node; required "
                             "together with --reference-height")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("discover-now")
    publish = subparsers.add_parser("publish")
    publish.add_argument("--directory-version", type=int, required=True)
    publish.add_argument("--output", default="monitor-directory.json")

    arguments = parser.parse_args(argv)
    store = Store(arguments.database)
    try:
        if arguments.command == "status":
            return command_status(store)
        if arguments.command == "publish":
            return command_publish(store, version=arguments.directory_version,
                                   output=arguments.output)
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
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
