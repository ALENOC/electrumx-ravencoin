# Troubleshooting

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Operations](operations.md) · [Validation status](validation-status.md)

## Core is syncing

An initial `blocks`/`headers` gap is normal. During block-file reindex, `blocks`
may remain zero while the log reports progress. Do not restart Core or delete
data because of that phase. Check disk space, cooling, peers and the current log.

## ElectrumX waits for Core

Confirm Core is healthy, RPC credentials/configuration match, and REST is enabled
for the bundled path. ElectrumX cannot build its history until Core provides the
chain it needs.

## Server is rejected

Inspect `server.ravencoin_backend`. Missing or stale evidence, wrong network,
unsynchronized blocks, checkpoint failure, chain conflict, unknown repository or
unknown commit are intentional fail-closed states. `server.version` is the
ElectrumX version, not backend Core identity.

## Existing-Core index errors

`txindex=1`, `assetindex=1` and `rest=1` must be active before choosing
existing-Core mode. Adding indexes can require a full Core reindex. Preserve
the existing data and follow the migration guide rather than deleting a database.

## Public TLS fails

Check DNS, CGNAT, forwarding, certificate name/expiry, mounted paths and the
renewal hook. Test from outside the LAN. Rotate certificates by restarting
ElectrumX only.
