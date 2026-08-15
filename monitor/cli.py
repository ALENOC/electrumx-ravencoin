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
import json
import pathlib
import sys
import time
from typing import List, Optional

from .classify import (
    ChainObservation, classify_assets, classify_backend, compare_chains,
    count_independent_operators,
)
from .crawl import Crawler
from .directory import build_directory
from .model import (
    Availability, DiscoverySource, EndpointId, Limits, Security, Thresholds, Transport,
)
from .store import Store

PACKAGE_DIR = pathlib.Path(__file__).resolve().parent
DEFAULT_SEEDS = PACKAGE_DIR / "config" / "seeds.json"
DEFAULT_REGISTRY = PACKAGE_DIR / "config" / "operator-registry.json"


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


def load_policy(path: Optional[str]) -> dict:
    """Load the signed safe-Core policy body, or an empty policy.

    An empty policy certifies nothing, so every backend comes out
    UNREVIEWED_CORE.  That is the correct answer when no policy is available: it
    is not a reason to lower the bar.
    """
    if not path:
        return {"releases": []}
    document = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    return document.get("policy", document)


async def run_discovery(store: Store, *, seeds_path: pathlib.Path,
                        registry_path: pathlib.Path, policy: dict,
                        limits: Limits, thresholds: Thresholds,
                        allow_private: bool = False) -> dict:
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
                operator_group=state.operator_group))
        else:
            state.register_failure(thresholds)
            state.reason = result.error or "probe failed"
        store.save_state(state, now=now)

    verdict = compare_chains(observations, thresholds=thresholds)
    if verdict.status == "CHAIN_CONFLICT":
        for state in store.all_states():
            group = state.operator_group or f"UNKNOWN-{state.endpoint.hostname}"
            if group in verdict.conflicting_groups:
                state.security = Security.CONFLICT
                state.reason = verdict.detail
                store.save_state(state, now=now)
    else:
        # Only a chain that compares cleanly can promote a backend claim to SAFE.
        for state in store.all_states():
            if state.security is Security.UNVERIFIED \
                    and state.availability is Availability.REACHABLE:
                state.security = Security.SAFE
                state.reason = "certified backend and consistent chain evidence"
                store.save_state(state, now=now)

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
    parser.add_argument("--allow-private", action="store_true",
                        help="development only: permit probing private addresses")
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
        summary = asyncio.run(run_discovery(
            store,
            seeds_path=pathlib.Path(arguments.seeds),
            registry_path=pathlib.Path(arguments.registry),
            policy=load_policy(arguments.policy),
            limits=Limits(), thresholds=Thresholds(),
            allow_private=arguments.allow_private))
        print(f"probed {summary['probed']} endpoint(s), {summary['edges']} peer edge(s), "
              f"chain {summary['chain']}: {summary['chain_detail']}")
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
