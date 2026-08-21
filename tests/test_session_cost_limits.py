# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""The public deployment must not throttle ordinary wallet traffic.

ElectrumX charges one cost unit per BANDWIDTH_UNIT_COST bytes sent and pools
that cost across every client sharing a /24.  At the upstream default of 500
bytes per unit, two large blockchain.scripthash.get_history replies cross
COST_SOFT_LIMIT and the server starts inserting REQUEST_SLEEP delays into
wallet traffic that is doing nothing abusive.  These tests pin the shipped
defaults and the environment variable names they depend on.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "compose.yaml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()
SERVER_ENV = (ROOT / "electrumx" / "server" / "env.py").read_text()

SHIPPED = {
    "BANDWIDTH_UNIT_COST": ("ELECTRUMX_BANDWIDTH_UNIT_COST", 5000),
    "COST_SOFT_LIMIT": ("ELECTRUMX_COST_SOFT_LIMIT", 10000),
    "COST_HARD_LIMIT": ("ELECTRUMX_COST_HARD_LIMIT", 100000),
}


def _compose_default(setting, override):
    match = re.search(
        rf"^\s*{setting}: \$\{{{override}:-(\d+)\}}$", COMPOSE, re.MULTILINE)
    assert match, f"compose.yaml does not set {setting} via {override}"
    return int(match.group(1))


def test_compose_pins_the_cost_settings_the_server_reads():
    for setting, (override, expected) in SHIPPED.items():
        assert _compose_default(setting, override) == expected
        # A rename in env.py would silently drop the override, so tie the
        # Compose keys to the names the server actually looks up.
        assert f"'{setting}'" in SERVER_ENV


def test_bandwidth_is_not_charged_at_the_throttling_upstream_default():
    # 500 bytes per cost unit makes a 2.5 MB history reply cost 5,000, so two
    # of them cross the soft limit.  At 5,000 the same reply costs 500.
    per_unit = _compose_default("BANDWIDTH_UNIT_COST",
                                "ELECTRUMX_BANDWIDTH_UNIT_COST")
    soft = _compose_default("COST_SOFT_LIMIT", "ELECTRUMX_COST_SOFT_LIMIT")
    hard = _compose_default("COST_HARD_LIMIT", "ELECTRUMX_COST_HARD_LIMIT")
    assert (2.5 * 1024 * 1024) / per_unit * 5 < soft
    assert soft < hard


def test_env_example_documents_the_tunables():
    for override, expected in SHIPPED.values():
        assert f"{override}={expected}" in ENV_EXAMPLE
