from pathlib import Path


def test_monitor_does_not_share_electrumx_network_namespace_or_health_dependency():
    monitor = Path("compose.monitor.yaml").read_text()
    assert "network_mode:" not in monitor
    assert "condition: service_healthy" not in monitor
    assert "rpc-secrets-init:" in monitor
    assert "condition: service_completed_successfully" in monitor
    assert "ELECTRUMX_RPC_HOST: 172.29.81.2" in monitor
    assert 'ELECTRUMX_RPC_PORT: "8001"' in monitor
    assert '"127.0.0.1:8899:8899/tcp"' in monitor


def test_monitor_admin_rpc_is_internal_only_and_survives_public_service_mode_changes():
    base = Path("compose.yaml").read_text()
    tls = Path("compose.tls.yaml").read_text()
    assert "rpc://172.29.81.2:8001" in base
    assert "rpc://172.29.81.2:8001" in tls
    assert "monitor-admin:" in base
    assert "internal: true" in base
    assert "8001:8001" not in base
    assert "8001:8001" not in tls
