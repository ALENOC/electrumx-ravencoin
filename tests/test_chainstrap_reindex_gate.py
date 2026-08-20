# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docker" / "core" / "bootstrap-reindex.sh"


def test_reindex_completion_requires_exact_snapshot_tip():
    text = SCRIPT.read_text(encoding="utf-8")

    # Import and verification must both remain offline.
    assert text.count("-connect=0") >= 2
    assert text.count("-listen=0") >= 2
    assert text.count("-dnsseed=0") >= 2

    # The completion marker must be guarded by active-chain checks against the
    # vetted marker, not just by ravend's process exit status.
    assert 'snapshot_height=$(sed -n' in text
    assert 'snapshot_hash=$(sed -n' in text
    assert 'observed_height=$(rpc getblockcount' in text
    assert 'observed_tip=$(rpc getbestblockhash' in text
    assert 'observed_snapshot_hash=$(rpc getblockhash "$snapshot_height"' in text

    marker_write = text.index('temporary_marker="${done_marker}.new.$$"')
    assert text.index('[ "$observed_height" = "$snapshot_height" ]') < marker_write
    assert text.index('[ "$observed_tip" = "$snapshot_hash" ]') < marker_write
    assert text.index('[ "$observed_snapshot_hash" = "$snapshot_hash" ]') < marker_write
