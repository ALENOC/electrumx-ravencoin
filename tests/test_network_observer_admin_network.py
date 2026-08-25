import importlib.util
from pathlib import Path

import pytest


PATH = Path(__file__).resolve().parents[1] / "core-safety" / "scripts" / \
    "configure_monitor_admin_network.py"
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
