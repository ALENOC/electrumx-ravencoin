#!/usr/bin/env python3
from pathlib import Path


def replace_once(path, old, new):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected exactly one anchor, found {count}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


# Legacy fixture deliberately calls BlockProcessor.fetch_and_process_blocks as
# an unbound method on a SimpleNamespace.  Model the new startup-repair hook so
# the test keeps exercising the ChainError path it was written for.
replace_once(
    "tests/server/test_block_processor.py",
'''        reorg_count=None,
        next_block_hashes=AsyncMock(side_effect=ChainError('bad asset reference')),
        flush=AsyncMock(),
''',
'''        reorg_count=None,
        _repair_trailing_fs_metadata=AsyncMock(),
        next_block_hashes=AsyncMock(side_effect=ChainError('bad asset reference')),
        flush=AsyncMock(),
''',
)

# Make the backend-ordering test explicitly cover the new recovery position:
# DB open -> bounded repair -> independent daemon/DB chain verification -> scan.
replace_once(
    "tests/server/test_ravencoin_backend.py",
'''    async def stub_next_block_hashes():
        raise StopTest

    monkeypatch.setattr(block_processor, "verify_database_chain", stub_verify)
''',
'''    async def stub_repair_trailing_fs_metadata():
        calls.append("repair_trailing_fs_metadata")

    async def stub_next_block_hashes():
        raise StopTest

    monkeypatch.setattr(block_processor, "verify_database_chain", stub_verify)
''',
)
replace_once(
    "tests/server/test_ravencoin_backend.py",
'''        state=None,
        next_block_hashes=stub_next_block_hashes,
    )
''',
'''        state=None,
        _repair_trailing_fs_metadata=stub_repair_trailing_fs_metadata,
        next_block_hashes=stub_next_block_hashes,
    )
''',
)
replace_once(
    "tests/server/test_ravencoin_backend.py",
'''    assert calls == ["open_for_sync", "verify_database_chain", "scan_files"]
''',
'''    assert calls == [
        "open_for_sync",
        "repair_trailing_fs_metadata",
        "verify_database_chain",
        "scan_files",
    ]
''',
)

# The full suite may change cwd; tests must always address the repository root.
path = Path("tests/test_monitor_failure_isolation.py")
path.write_text('''from pathlib import Path\n\n\nROOT = Path(__file__).resolve().parents[1]\n\n\ndef test_monitor_does_not_share_electrumx_network_namespace_or_health_dependency():\n    monitor = (ROOT / "compose.monitor.yaml").read_text(encoding="utf-8")\n    assert "network_mode:" not in monitor\n    assert "condition: service_healthy" not in monitor\n    assert "rpc-secrets-init:" in monitor\n    assert "condition: service_completed_successfully" in monitor\n    assert "ELECTRUMX_RPC_HOST: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in monitor\n    assert 'ELECTRUMX_RPC_PORT: "8001"' in monitor\n    assert '\"127.0.0.1:8899:8899/tcp\"' in monitor\n\n\ndef test_monitor_admin_rpc_is_internal_only_and_collision_configurable():\n    base = (ROOT / "compose.yaml").read_text(encoding="utf-8")\n    tls = (ROOT / "compose.tls.yaml").read_text(encoding="utf-8")\n    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in base\n    assert "rpc://${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}:8001" in tls\n    assert "ipv4_address: ${ELECTRUMX_MONITOR_ADMIN_IP:-172.29.81.2}" in base\n    assert "subnet: ${MONITOR_ADMIN_SUBNET:-172.29.81.0/29}" in base\n    assert "monitor-admin:" in base\n    assert "internal: true" in base\n    assert "8001:8001" not in base\n    assert "8001:8001" not in tls\n\n\ndef test_verified_installer_requires_host_side_port_publication_helper():\n    source = (ROOT / "electrumx-ravencoin-install.py").read_text(encoding="utf-8")\n    assert "verify_monitor_host_publish" in source\n    assert "contrib/verify-published-port.py" in source\n    assert '\"--repair\"' in source\n''', encoding="utf-8")
