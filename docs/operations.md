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

Read the release notes and record current service and storage status first.
Installations created by the signed release installer use:

```sh
electrumx-update check
electrumx-update status
electrumx-update show
electrumx-update apply
```

The updater authenticates the candidate, enforces host-wide anti-rollback,
proves the storage model, and performs a transactional switch with exact
rollback. Source checkouts may use normal reviewed Git workflows, but do not
silently gain signed-release updater state or trust.

Re-run backend, chain, index, asset and TLS checks after an upgrade. The bundled
Core is pinned; never replace it with an unreviewed image or use an unsafe
override to make a deployment appear healthy.

## Reindex warnings

Changing Core index settings can require a full reindex. Keep the host powered,
cooled and spacious during that work. ElectrumX can resume its index after a
clean restart, but deleting its database sacrifices the work already done.

## What each status means

Core and ElectrumX have separate readiness states:

| Observation | Meaning |
|---|---|
| Core container healthy | JSON-RPC answers; synchronization may still be incomplete |
| `blocks < headers` | Core knows headers ahead of fully connected blocks; usually normal during sync |
| `Reindexing block file ...` | Core is scanning local files; height fields may not show scan progress |
| ElectrumX waiting for Core | Core RPC/REST or required indexes are not ready |
| ElectrumX database height advances | Historical indexing is working |
| ElectrumX height near Core height | Candidate for live checks, not automatic release readiness |

Use the configured RPC path rather than guessing default credentials:

```sh
docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getblockchaininfo
docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getnetworkinfo
docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getconnectioncount
docker compose exec electrumx electrumx_rpc getinfo
```

Important Core fields are `blocks`, `headers`, `verificationprogress`,
`bestblockhash`, `initial_block_download` and peer count. Progress estimates
are hints, not release gates. During a block-file scan, read the current
`Reindexing block file blkNNNNN.dat` line in the Core log.

## ElectrumX diagnosis

ElectrumX can remain at height zero while it waits for Core. That is not
necessarily a failure. Check that Core JSON-RPC is reachable, REST is enabled,
`txindex` and `assetindex` are available where required, and ElectrumX logs
show progress rather than the same error repeating. `electrumx_rpc getinfo`
should eventually report an advancing database height.

`daemon service refused: Not Found` commonly means Core REST is disabled or
misconfigured. ElectrumX has no JSON-RPC fallback for its block-fetch path.

## Storage, backups and reboot

Monitor the Docker filesystem, volumes and host health with `df -h`,
`docker system df -v`, `docker stats --no-stream` and the host's SMART/NVMe
tooling where available. Keep free-space headroom for chain growth and LevelDB
compaction.

Back up `.env`, deployment configuration, operator notes and protected
secret/certificate material separately from regenerable chain data. Raw blocks,
chainstate, Core indexes and the ElectrumX database are expensive to rebuild;
if you snapshot them, stop the relevant service cleanly and verify consistency.
Copying a live LevelDB directory is not a guaranteed-consistent backup.

When `--enable-reboot` is used, inspect the user unit and lingering state with
`systemctl --user status electrumx-ravencoin.service` and
`loginctl show-user "$USER" -p Linger`. After reboot, check containers and logs;
a healthy service may still be synchronizing.

## Updating safely

Before an update, read the release notes and [Core certification](core-certification.md),
record live status, check disk space, and protect configuration. Do not modify
the release tree while an update transaction is active. Afterward, recheck Core
identity, network, checkpoint, indexes, asset RPC, backend evidence, ElectrumX
height, Node Monitor health, and TLS. A higher semantic version is not a
substitute for a certified exact repository and commit.

## Dangerous cleanup

`docker compose down -v` removes Compose-managed volumes. It can delete raw
blocks, chainstate, Core indexes and the ElectrumX database. Use it only when
deliberate data deletion and a complete rebuild are intended. Do not casually
delete `blk*.dat`, `chainstate`, `txindex`, `assetindex` or the ElectrumX
database, and do not start a second full reindex while one is active.
