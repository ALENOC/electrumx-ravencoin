from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_monitor_does_not_share_electrumx_network_namespace_or_health_dependency():
    monitor = (ROOT / "compose.monitor.yaml").read_text(encoding="utf-8")
    assert "network_mode:" not in monitor
    assert "condition: service_healthy" not in monitor
    assert "rpc-secrets-init:" in monitor
    assert "condition: service_completed_successfully" in monitor
    assert "ELECTRUMX_RPC_HOST: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in monitor
    assert 'ELECTRUMX_RPC_PORT: "8001"' in monitor
    assert '"127.0.0.1:8899:8899/tcp"' in monitor


def test_monitor_admin_rpc_is_internal_only_and_collision_configurable():
    base = (ROOT / "compose.yaml").read_text(encoding="utf-8")
    tls = (ROOT / "compose.tls.yaml").read_text(encoding="utf-8")
    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in base
    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in tls
    assert "ipv4_address: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in base
    assert "subnet: ${MONITOR_ADMIN_SUBNET:-172.29.81.0/29}" in base
    assert "monitor-admin:" in base
    assert "internal: true" in base
    assert "8001:8001" not in base
    assert "8001:8001" not in tls


def test_verified_installer_requires_host_side_port_publication_helper():
    source = (ROOT / "electrumx-ravencoin-install.py").read_text(encoding="utf-8")
    assert "verify_monitor_host_publish" in source
    assert "contrib/verify-published-port.py" in source
    assert '"--repair"' in source
