#!/usr/bin/env python3
"""Make source-tree Compose CI validation provide the vendored monitor env stub."""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def main() -> int:
    text = CI.read_text(encoding="utf-8")
    old = '          mkdir -p "$STORAGE_TEST_ROOT"/{ravencoin-data,ravencoin-config,electrumx-data,monitor-data}\n'
    new = (
        old
        + '          mkdir -p vendor/ravencoin-node-monitor\n'
        + '          : > vendor/ravencoin-node-monitor/.env\n'
    )
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"storage CI fixture anchor: expected one, found {count}")
    CI.write_text(text.replace(old, new, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
