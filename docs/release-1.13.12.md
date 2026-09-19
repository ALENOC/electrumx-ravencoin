# ElectrumX-RVN 1.13.12

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Architecture](architecture.md) · [Security model](security-model.md) ·
[Validation status](validation-status.md)

ElectrumX-RVN 1.13.12 is a correctness release on top of
[1.13.11](release-1.13.11.md). It stops the server from serving a damaged
header record, and gives it a way to notice and repair one. Everything else,
including the wallet-serving path, is unchanged.

## Why it exists

A single header record on the production node, at height 4502646, held 120 zero
bytes. Every wallet that reached that height stalled: the client rejects the
chunk containing the record, retries it about once a second indefinitely, never
finishes header catch-up, and so reports itself as not connected without saying
why. Nothing on the server logged anything, because nothing checked.

The record predates the crash-consistency work of `54aeaa26`, which landed
three hours after that block was mined, and a full scan of every header file
found no other damage in the month since. So this release does not change how
headers are written. It adds the detection and recovery that were missing.

## What changed since 1.13.11

| Area | v1.13.12 behavior |
|---|---|
| Serving headers | A damaged record is repaired from the daemon and then served. If it cannot be repaired the read fails instead of returning zeros, which a client cannot distinguish from real headers without verifying them |
| Repair safety | A replacement is written only if it links to the headers already on disk, or matches the committed tip at the chain head. The daemon says which block belongs at a height; it cannot plant an unlinked header |
| Durability | Repairs are written through the same fsync barrier as ordinary header writes |
| Detection | The store is scanned at startup, controlled by `HEADER_SCAN_ON_STARTUP`, and on demand through `electrumx_rpc verifyheaders` |
| Blast radius | Automatic repair is capped at 64 records per attempt: a handful is damage worth healing in place, thousands is something an operator must look at |

## Operating notes

The startup scan is a sequential read of the header files. On a Raspberry Pi 5
with a fully synced mainnet store it completes in well under a minute. Set
`HEADER_SCAN_ON_STARTUP=false` to skip it if a faster boot matters more.

To check or repair a running server:

```sh
electrumx_rpc verifyheaders          # report only
electrumx_rpc verifyheaders true     # repair what it finds
```

## Compatibility

No protocol change, no database format change, no configuration change beyond
the new optional `HEADER_SCAN_ON_STARTUP`. Downgrading to 1.13.11 is possible
and simply removes the detection and repair behavior.
