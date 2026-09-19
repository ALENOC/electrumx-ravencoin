# ElectrumX-RVN 1.13.13

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Architecture](architecture.md) · [Security model](security-model.md) ·
[Validation status](validation-status.md)

ElectrumX-RVN 1.13.13 makes [1.13.12](release-1.13.12.md) startable. Deploy this
instead of 1.13.12, which is published but crash-loops on startup and can never
serve.

## Why it exists

1.13.12 carried the header-record integrity work and could not start:

```text
File "electrumx/server/block_processor.py", line 612, in check_cache_size_loop
    tx_hash_size = ((self.state.tx_count - self.db.fs_tx_count) * 32
AttributeError: 'NoneType' object has no attribute 'tx_count'
```

`check_cache_size_loop()` is spawned next to `fetch_and_process_blocks()`, which
opens the databases and only afterwards publishes chain state. The loop read
that state in its first statement, so its correctness depended on the databases
opening before the event loop scheduled it. That was never a guarantee. The
startup header scan added in 1.13.12 put real work on the open path, and the
loop began winning the race.

The scan was not the defect. A loop whose correctness depends on scheduling
order was.

## What changed since 1.13.12

| Area | v1.13.13 behavior |
|---|---|
| Startup ordering | `BlockProcessor.state_ready` is set exactly where chain state is assigned, and the cache-measurement loop waits for it before reading anything |
| Future work on the open path | Can take as long as it needs without breaking startup |

Everything introduced in 1.13.12 is unchanged: a damaged header record is
repaired from the daemon and never served, a replacement is written only if it
links to the headers already on disk, the store is scanned at startup under
`HEADER_SCAN_ON_STARTUP`, and `electrumx_rpc verifyheaders` reports and repairs
on demand.

## Compatibility

No protocol change, no database format change, no configuration change.
Upgrading from 1.13.11 is direct; 1.13.12 never ran anywhere, so there is no
state to migrate from it.
