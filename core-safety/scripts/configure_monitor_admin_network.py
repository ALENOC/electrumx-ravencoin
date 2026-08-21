#!/usr/bin/env python3
"""Select a collision-free private /29 for the Monitor<->ElectrumX admin RPC.

The installer keeps the admin RPC off the host and off the public Ravencoin
backend network.  This helper inspects existing Docker networks and host routes,
selects a non-overlapping RFC1918 /29, and records the subnet plus the two
service addresses in the project .env.  Existing complete operator settings are
validated and preserved; partial settings fail closed.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import shutil
import subprocess
import sys
from pathlib import Path

KEY_SUBNET = "MONITOR_ADMIN_SUBNET"
KEY_ELECTRUMX = "ELECTRUMX_MONITOR_ADMIN_IP"
KEY_MONITOR = "MONITOR_ADMIN_IP"
KEYS = (KEY_SUBNET, KEY_ELECTRUMX, KEY_MONITOR)
CANDIDATE_POOLS = (
    ipaddress.ip_network("172.31.240.0/20"),
    ipaddress.ip_network("10.255.240.0/20"),
)


class NetworkConfigError(RuntimeError):
    pass


def _run(argv):
    return subprocess.run(list(argv), check=False, capture_output=True, text=True)


def _as_ipv4_network(value):
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    return network if network.version == 4 else None


def docker_subnets():
    listed = _run(["docker", "network", "ls", "-q"])
    if listed.returncode != 0:
        raise NetworkConfigError("cannot list Docker networks")
    ids = [line.strip() for line in listed.stdout.splitlines() if line.strip()]
    if not ids:
        return []
    inspected = _run(["docker", "network", "inspect", *ids])
    if inspected.returncode != 0:
        raise NetworkConfigError("cannot inspect Docker networks")
    try:
        payload = json.loads(inspected.stdout)
    except json.JSONDecodeError as exc:
        raise NetworkConfigError("Docker returned malformed network metadata") from exc
    result = []
    for item in payload if isinstance(payload, list) else []:
        name = item.get("Name") or "<unnamed>"
        for config in ((item.get("IPAM") or {}).get("Config") or []):
            network = _as_ipv4_network((config or {}).get("Subnet"))
            if network is not None:
                result.append((name, network))
    return result


def host_route_subnets():
    if shutil.which("ip") is None:
        return []
    result = _run(["ip", "-j", "route", "show", "table", "all"])
    if result.returncode != 0:
        return []
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return []
    networks = []
    for route in payload if isinstance(payload, list) else []:
        destination = route.get("dst")
        if not destination or destination == "default":
            continue
        network = _as_ipv4_network(destination)
        if network is not None:
            networks.append(("host-route", network))
    return networks


def candidate_subnets():
    for pool in CANDIDATE_POOLS:
        yield from pool.subnets(new_prefix=29)


def choose_subnet(used):
    networks = [network for _name, network in used]
    for candidate in candidate_subnets():
        if not any(candidate.overlaps(existing) for existing in networks):
            return candidate
    raise NetworkConfigError("no collision-free monitor-admin /29 is available")


def addresses_for(subnet):
    hosts = list(subnet.hosts())
    if len(hosts) < 3:
        raise NetworkConfigError("monitor-admin subnet is too small")
    return str(hosts[1]), str(hosts[2])


def read_env(path):
    values = {}
    if not path.exists():
        raise NetworkConfigError(f"environment file does not exist: {path}")
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key in KEYS:
            values[key] = value.strip()
    return values


def validate_existing(values):
    present = [key for key in KEYS if key in values]
    if present and len(present) != len(KEYS):
        raise NetworkConfigError(
            "monitor-admin network settings are partial; set all three or none"
        )
    if not present:
        return None
    try:
        subnet = ipaddress.ip_network(values[KEY_SUBNET], strict=True)
        electrumx_ip = ipaddress.ip_address(values[KEY_ELECTRUMX])
        monitor_ip = ipaddress.ip_address(values[KEY_MONITOR])
    except ValueError as exc:
        raise NetworkConfigError("existing monitor-admin network settings are malformed") from exc
    if subnet.version != 4 or subnet.prefixlen != 29:
        raise NetworkConfigError("MONITOR_ADMIN_SUBNET must be an IPv4 /29")
    if electrumx_ip not in subnet or monitor_ip not in subnet or electrumx_ip == monitor_ip:
        raise NetworkConfigError("monitor-admin service IPs must be distinct members of the /29")
    if electrumx_ip in (subnet.network_address, subnet.broadcast_address) or \
            monitor_ip in (subnet.network_address, subnet.broadcast_address):
        raise NetworkConfigError("monitor-admin service IP cannot be network/broadcast address")
    return subnet


def configure(env_file: Path, *, dry_run=False):
    values = read_env(env_file)
    existing = validate_existing(values)
    if existing is not None:
        return {
            KEY_SUBNET: str(existing),
            KEY_ELECTRUMX: values[KEY_ELECTRUMX],
            KEY_MONITOR: values[KEY_MONITOR],
            "preserved": True,
        }

    used = docker_subnets() + host_route_subnets()
    subnet = choose_subnet(used)
    electrumx_ip, monitor_ip = addresses_for(subnet)
    result = {
        KEY_SUBNET: str(subnet),
        KEY_ELECTRUMX: electrumx_ip,
        KEY_MONITOR: monitor_ip,
        "preserved": False,
    }
    if not dry_run:
        with env_file.open("a", encoding="utf-8") as handle:
            handle.write("\n# Collision-safe internal Monitor <-> ElectrumX admin network\n")
            handle.write(f"{KEY_SUBNET}={subnet}\n")
            handle.write(f"{KEY_ELECTRUMX}={electrumx_ip}\n")
            handle.write(f"{KEY_MONITOR}={monitor_ip}\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    result = configure(args.env_file, dry_run=args.dry_run)
    mode = "preserved" if result["preserved"] else "selected"
    print(
        f"monitor-admin network {mode}: {result[KEY_SUBNET]} "
        f"ElectrumX={result[KEY_ELECTRUMX]} Monitor={result[KEY_MONITOR]}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except NetworkConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
