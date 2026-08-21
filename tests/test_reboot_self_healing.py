import os
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
HEALTHCHECK = ROOT / "docker" / "core" / "healthcheck.sh"


def _run_healthcheck(tmp_path, *, blocks=5000, headers=5000, ibd=False,
                     peers=8, tip_age=30, extra_env=None):
    """Run the production POSIX healthcheck against a deterministic fake CLI."""
    fake_cli = tmp_path / "raven-cli"
    tip_time = int(time.time()) - tip_age
    ibd_line = (
        f',\n  "initialblockdownload": {str(ibd).lower()}\n'
        if ibd is not None else "\n"
    )
    fake_cli.write_text(
        "#!/bin/sh\n"
        "set -eu\n"
        "while [ $# -gt 0 ]; do\n"
        "  case \"$1\" in -*) shift ;; *) break ;; esac\n"
        "done\n"
        "cmd=${1:-}\n"
        "case \"$cmd\" in\n"
        "  getblockchaininfo)\n"
        "    cat <<'EOF'\n"
        "{\n"
        f"  \"blocks\": {blocks},\n"
        f"  \"headers\": {headers}{ibd_line}"
        "}\n"
        "EOF\n"
        "    ;;\n"
        f"  getconnectioncount) printf '%s\\n' '{peers}' ;;\n"
        "  getbestblockhash) printf '%s\\n' '0123456789abcdef' ;;\n"
        "  getblockheader)\n"
        "    cat <<'EOF'\n"
        "{\n"
        f"  \"time\": {tip_time}\n"
        "}\n"
        "EOF\n"
        "    ;;\n"
        "  *) printf 'unexpected fake RPC: %s\\n' \"$cmd\" >&2; exit 90 ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake_cli.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["sh", str(HEALTHCHECK)],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_core_readiness_accepts_synced_fresh_connected_node(tmp_path):
    result = _run_healthcheck(tmp_path)
    assert result.returncode == 0, result.stderr


def test_core_readiness_accepts_official_v4_8_response_without_ibd_field(tmp_path):
    result = _run_healthcheck(tmp_path, ibd=None)
    assert result.returncode == 0, result.stderr


def test_core_readiness_rejects_rpc_up_but_500_blocks_behind(tmp_path):
    result = _run_healthcheck(tmp_path, blocks=4500, headers=5000)
    assert result.returncode != 0
    assert "block lag is 500" in result.stderr


def test_core_readiness_rejects_stale_tip_even_when_blocks_equal_headers(tmp_path):
    result = _run_healthcheck(tmp_path, blocks=5000, headers=5000, tip_age=3601)
    assert result.returncode != 0
    assert "best block is" in result.stderr


def test_core_readiness_rejects_ibd_and_zero_peer_boot_states(tmp_path):
    ibd = _run_healthcheck(tmp_path, ibd=True)
    assert ibd.returncode != 0
    assert "initial block download" in ibd.stderr

    no_peers = _run_healthcheck(tmp_path, peers=0)
    assert no_peers.returncode != 0
    assert "peer count is 0" in no_peers.stderr


def test_core_readiness_thresholds_are_operator_configurable(tmp_path):
    result = _run_healthcheck(
        tmp_path,
        blocks=4990,
        headers=5000,
        peers=0,
        tip_age=7200,
        extra_env={
            "RAVENCOIN_HEALTH_MAX_BLOCK_LAG": "10",
            "RAVENCOIN_HEALTH_MAX_TIP_AGE": "8000",
            "RAVENCOIN_HEALTH_MIN_PEERS": "0",
        },
    )
    assert result.returncode == 0, result.stderr


def test_compose_reboots_retry_core_and_electrumx_without_finite_budget():
    base = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    existing = (ROOT / "compose.existing-core.yaml").read_text(encoding="utf-8")

    assert base.count("restart: unless-stopped") == 2
    assert "restart: on-failure:" not in base
    assert "restart: unless-stopped" in existing
    assert "restart: on-failure:" not in existing
    assert "condition: service_healthy" in base
    assert "RAVENCOIN_HEALTH_MAX_BLOCK_LAG" in base
    assert "RAVENCOIN_HEALTH_MAX_TIP_AGE" in base
    assert "RAVENCOIN_HEALTH_MIN_PEERS" in base


def test_monitor_cannot_regress_to_separate_external_network_stack():
    base = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    monitor = (ROOT / "compose.monitor.yaml").read_text(encoding="utf-8")

    assert "name: electrumx-ravencoin" in base
    assert not any(line.startswith("name:") for line in monitor.splitlines())
    assert "external: true" not in base
    assert "external: true" not in monitor
    assert "restart: unless-stopped" in monitor
    assert "ravencoin-backend: {}" in monitor
    assert "monitor-admin:" in monitor
    assert '"127.0.0.1:8899:8899/tcp"' in monitor
