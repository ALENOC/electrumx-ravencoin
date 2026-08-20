#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def write_new(path: str, content: str) -> None:
    p = Path(path)
    if p.exists():
        raise SystemExit(f"refusing to overwrite existing {path}")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


NETWORK_HELPER = r'''#!/usr/bin/env python3
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
'''

NETWORK_TEST = r'''import importlib.util
from pathlib import Path

import pytest


PATH = Path("core-safety/scripts/configure_monitor_admin_network.py")
SPEC = importlib.util.spec_from_file_location("monitor_network", PATH)
network = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(network)


def test_choose_subnet_skips_overlapping_docker_or_host_network():
    first = next(network.candidate_subnets())
    selected = network.choose_subnet([("occupied", first)])
    assert selected != first
    assert not selected.overlaps(first)


def test_addresses_are_inside_selected_subnet_and_distinct():
    subnet = next(network.candidate_subnets())
    electrumx, monitor = network.addresses_for(subnet)
    assert network.ipaddress.ip_address(electrumx) in subnet
    assert network.ipaddress.ip_address(monitor) in subnet
    assert electrumx != monitor


def test_configure_writes_complete_triple(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("EXAMPLE=1\n", encoding="utf-8")
    occupied = next(network.candidate_subnets())
    monkeypatch.setattr(network, "docker_subnets", lambda: [("occupied", occupied)])
    monkeypatch.setattr(network, "host_route_subnets", lambda: [])
    result = network.configure(env)
    text = env.read_text(encoding="utf-8")
    assert f"MONITOR_ADMIN_SUBNET={result['MONITOR_ADMIN_SUBNET']}" in text
    assert f"ELECTRUMX_MONITOR_ADMIN_IP={result['ELECTRUMX_MONITOR_ADMIN_IP']}" in text
    assert f"MONITOR_ADMIN_IP={result['MONITOR_ADMIN_IP']}" in text
    assert result["MONITOR_ADMIN_SUBNET"] != str(occupied)


def test_complete_operator_override_is_preserved(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "MONITOR_ADMIN_SUBNET=10.77.88.0/29\n"
        "ELECTRUMX_MONITOR_ADMIN_IP=10.77.88.2\n"
        "MONITOR_ADMIN_IP=10.77.88.3\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(network, "docker_subnets", lambda: (_ for _ in ()).throw(AssertionError()))
    result = network.configure(env)
    assert result["preserved"] is True
    assert result["MONITOR_ADMIN_SUBNET"] == "10.77.88.0/29"


def test_partial_operator_override_fails_closed(tmp_path):
    env = tmp_path / ".env"
    env.write_text("MONITOR_ADMIN_SUBNET=10.77.88.0/29\n", encoding="utf-8")
    with pytest.raises(network.NetworkConfigError, match="partial"):
        network.configure(env)
'''

MONITOR_FAILURE_TEST = r'''from pathlib import Path


def test_monitor_does_not_share_electrumx_network_namespace_or_health_dependency():
    monitor = Path("compose.monitor.yaml").read_text()
    assert "network_mode:" not in monitor
    assert "condition: service_healthy" not in monitor
    assert "rpc-secrets-init:" in monitor
    assert "condition: service_completed_successfully" in monitor
    assert "ELECTRUMX_RPC_HOST: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in monitor
    assert 'ELECTRUMX_RPC_PORT: "8001"' in monitor
    assert '"127.0.0.1:8899:8899/tcp"' in monitor


def test_monitor_admin_rpc_is_internal_only_and_collision_configurable():
    base = Path("compose.yaml").read_text()
    tls = Path("compose.tls.yaml").read_text()
    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in base
    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in tls
    assert "ipv4_address: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in base
    assert "subnet: ${MONITOR_ADMIN_SUBNET:-172.29.81.0/29}" in base
    assert "monitor-admin:" in base
    assert "internal: true" in base
    assert "8001:8001" not in base
    assert "8001:8001" not in tls


def test_verified_installer_requires_host_side_port_publication_helper():
    source = Path("electrumx-ravencoin-install.py").read_text()
    assert "verify_monitor_host_publish" in source
    assert "contrib/verify-published-port.py" in source
    assert '"--repair"' in source
'''

write_new("core-safety/scripts/configure_monitor_admin_network.py", NETWORK_HELPER)
write_new("tests/test_monitor_admin_network.py", NETWORK_TEST)
Path("tests/test_monitor_failure_isolation.py").write_text(MONITOR_FAILURE_TEST, encoding="utf-8")

# Compose network values become installer-selected while retaining safe source defaults.
replace_once(
    "compose.yaml",
    "rpc://172.29.81.2:8001",
    "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001",
)
replace_once(
    "compose.yaml",
    "        ipv4_address: 172.29.81.2\n",
    "        ipv4_address: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}\n",
)
replace_once(
    "compose.yaml",
    "        - subnet: 172.29.81.0/29\n",
    "        - subnet: ${MONITOR_ADMIN_SUBNET:-172.29.81.0/29}\n",
)
replace_once(
    "compose.tls.yaml",
    "rpc://172.29.81.2:8001",
    "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001",
)
replace_once(
    "compose.monitor.yaml",
    "        ipv4_address: 172.29.81.3\n",
    "        ipv4_address: ${MONITOR_ADMIN_IP:-172.29.81.3}\n",
)
replace_once(
    "compose.monitor.yaml",
    "      ELECTRUMX_RPC_HOST: 172.29.81.2\n",
    "      ELECTRUMX_RPC_HOST: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}\n",
)

# Source and verified installs both configure a free subnet before Compose config/build.
replace_once(
    "setup.sh",
'''if [ ! -e .env ]; then
    cp .env.example .env
    chmod 600 .env
fi

if [ "$configure_ddns" = true ]; then
''',
'''if [ ! -e .env ]; then
    cp .env.example .env
    chmod 600 .env
fi

if [ "$mode" = bundled ]; then
    command -v python3 >/dev/null 2>&1 \
        || fail 'python3 is required to select the internal monitor-admin network'
    python3 core-safety/scripts/configure_monitor_admin_network.py --env-file .env \
        || fail 'could not select a collision-free internal monitor-admin network'
fi

if [ "$configure_ddns" = true ]; then
''',
)

# Pin the Node Monitor commit that contains the host-side publish verifier.
pin_path = Path("release/install-sources.json")
pin = json.loads(pin_path.read_text(encoding="utf-8"))
old_pin = pin["nodeMonitor"]["commit"]
if old_pin != "fabaaee1184e954cd2c585e28bc2e9f9f95f2c19":
    raise SystemExit(f"unexpected Node Monitor pin {old_pin}")
pin["nodeMonitor"]["commit"] = "b59e7efdea2fe8c0114b5f72e139931fe86ae571"
pin_path.write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")

# Installer structural contract + post-activation host-side verification.
replace_once(
    "electrumx-ravencoin-install.py",
'''CONTROLLER_SCRIPT = f"{MONITOR_PATH}/contrib/ravencoin-bandwidth-controller.py"
CONTROLLER_UNIT = "electrumx-ravencoin-monitor-controller.service"
CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
''',
'''CONTROLLER_SCRIPT = f"{MONITOR_PATH}/contrib/ravencoin-bandwidth-controller.py"
MONITOR_PORT_VERIFY = f"{MONITOR_PATH}/contrib/verify-published-port.py"
NETWORK_CONFIG_HELPER = "core-safety/scripts/configure_monitor_admin_network.py"
CONTROLLER_UNIT = "electrumx-ravencoin-monitor-controller.service"
CHAINSTRAP_OVERLAY = "compose.chainstrap.yaml"
''',
)
replace_once(
    "electrumx-ravencoin-install.py",
'''    f"{MONITOR_PATH}/.env.example",
    CONTROLLER_SCRIPT,
})
''',
'''    f"{MONITOR_PATH}/.env.example",
    CONTROLLER_SCRIPT,
    MONITOR_PORT_VERIFY,
    NETWORK_CONFIG_HELPER,
})
''',
)
replace_once(
    "electrumx-ravencoin-install.py",
'''        "ELECTRUMX_ENABLED=true\\nELECTRUMX_RPC_HOST=172.29.81.2\\n"
        "ELECTRUMX_RPC_PORT=8001\\nELECTRUMX_SSL_HOST=electrumx\\n"
        "ELECTRUMX_SSL_PORT=50002\\nELECTRUMX_SSL_VERIFY=false\\n"
''',
'''        "ELECTRUMX_ENABLED=true\\n"
        "ELECTRUMX_SSL_VERIFY=false\\n"
''',
)
replace_once(
    "electrumx-ravencoin-install.py",
'''    if completed.returncode != 0:
        raise InstallError(
            f"command failed with exit code {completed.returncode}: {' '.join(argv)}")


def compose_files(bootstrap: str, monitor: bool, controller: bool = False) -> list[str]:
''',
'''    if completed.returncode != 0:
        raise InstallError(
            f"command failed with exit code {completed.returncode}: {' '.join(argv)}")


def verify_monitor_host_publish(root: Path, files: Sequence[str]) -> None:
    """Prove the Monitor is published on the host, not merely alive in-container.

    The helper performs at most one monitor-only force-recreate when Docker has
    lost the 8899 host publication after a reboot.  Failure after that single
    repair attempt aborts the fresh install; Core and ElectrumX are never
    recreated by this recovery path.
    """
    script = root / MONITOR_PORT_VERIFY
    argv = [
        sys.executable, str(script),
        "--compose-dir", str(root),
        "--container", "ravencoin-node-monitor",
        "--host", "127.0.0.1",
        "--port", "8899",
        "--repair",
    ]
    for filename in files:
        argv += ["--compose-file", filename]
    run_checked(argv, cwd=root)


def compose_files(bootstrap: str, monitor: bool, controller: bool = False) -> list[str]:
''',
)
replace_once(
    "electrumx-ravencoin-install.py",
'''            raise

        # Marker/state are commit records and are written only after Compose
''',
'''            raise

        if monitor:
            verify_monitor_host_publish(target, files)

        # Marker/state are commit records and are written only after Compose
''',
)

# Synthetic installer bundles must exercise both newly required helpers.
replace_once(
    "core-safety/scripts/test_installer.py",
'''        "vendor/ravencoin-node-monitor/contrib/ravencoin-bandwidth-controller.py": b"#!/usr/bin/env python3\\n",
        "compose.monitor-controller.yaml": (
''',
'''        "vendor/ravencoin-node-monitor/contrib/ravencoin-bandwidth-controller.py": b"#!/usr/bin/env python3\\n",
        "vendor/ravencoin-node-monitor/contrib/verify-published-port.py": b"#!/usr/bin/env python3\\n",
        "core-safety/scripts/configure_monitor_admin_network.py": b"#!/usr/bin/env python3\\n",
        "compose.monitor-controller.yaml": (
''',
)

# Document the two independent reboot-resilience controls.
doc = Path("docs/crash-consistency.md")
text = doc.read_text(encoding="utf-8")
addition = '''

## Monitor network and host-port resilience

The Monitor admin network is no longer tied to a single hard-coded Docker
subnet.  `configure_monitor_admin_network.py` inspects existing Docker IPv4
subnets and host routes, chooses a free RFC1918 `/29`, and records the subnet,
ElectrumX address and Monitor address in `.env`.  Complete operator overrides
are preserved; partial overrides fail closed.

The Monitor container healthcheck intentionally remains an in-container liveness
check.  Because that cannot prove Docker actually installed the host-side 8899
mapping, the verified installer additionally executes the pinned Node Monitor
`contrib/verify-published-port.py` after activation.  It requires both Docker
port metadata and a real host-loopback `/healthz` response.  On the known reboot
failure it may force-recreate **only** the Monitor once, then verifies again and
fails instead of looping or touching Core/ElectrumX.
'''
if "## Monitor network and host-port resilience" in text:
    raise SystemExit("crash-consistency doc section already present")
doc.write_text(text.rstrip() + addition + "\n", encoding="utf-8")
