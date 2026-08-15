# Operations

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Getting started](getting-started.md) · [Troubleshooting](troubleshooting.md)

## Lifecycle

```sh
docker compose ps
docker compose logs --tail=100 ravencoin-core
docker compose logs --tail=100 electrumx
docker compose stop
docker compose start
```

Restart only the service that needs it. During a Core reindex, do not restart
Core, remove volumes, run `docker compose down -v`, delete chainstate, or delete
the ElectrumX database.

## Read-only Core checks

Use the configured RPC path and credentials. Check `getblockchaininfo`, compare
`blocks` and `headers`, inspect `txindex`/`assetindex`, and confirm the Core log
advances through its current phase. Do not treat `blocks=0` as data loss during
the initial block-file scan.

## Storage and backups

Monitor free space and NVMe SMART data. Back up `.env`, ignored secrets and
configuration separately from regenerable chain/index data. Snapshot LevelDB
only after a clean stop; a live directory is not a consistent backup.

## Upgrade and reboot

Read release notes, preserve a rollback snapshot, and use fast-forward Git
updates. Re-run backend, chain, index, asset and TLS checks after an upgrade.
The bundled Core is pinned; never replace it with an unreviewed image or use an
unsafe override to make a deployment appear healthy.

## Reindex warnings

Changing Core index settings can require a full reindex. Keep the host powered,
cooled and spacious during that work. ElectrumX can resume its index after a
clean restart, but deleting its database sacrifices the work already done.
