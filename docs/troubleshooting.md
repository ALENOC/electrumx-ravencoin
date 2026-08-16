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

## Docker or Compose is unavailable

Symptom: `setup.sh` stops before changing the project. Check `docker --version`,
`docker compose version` and `docker info`. Install Docker Engine and the
Compose v2 plugin using your distribution's documented method, then make sure
the operator account can reach the Docker daemon. Do not run the stack as root
just to hide a permissions problem.

## Core is slow or appears stuck

Compare `blocks` and `headers` in `getblockchaininfo`. During ordinary sync,
`blocks < headers` means Core is catching up. CPU, disk and peer activity can
also make progress uneven. During a block-file reindex, `blocks` can remain
zero while the log advances through `Reindexing block file blkNNNNN.dat`; use
that log as the progress indicator. Do not delete `blocks`, `chainstate`, the
indexes or Docker volumes to improve progress.

## ElectrumX is waiting or not indexing

ElectrumX may wait while Core is starting, reindexing or still lacks the
required indexes. Inspect `docker compose logs --tail=100 electrumx` and the
Core status first. Once Core exposes a usable chain, ElectrumX should move from
waiting to historical indexing and then follow the tip. A missing `txindex` or
`assetindex`, a disabled REST interface, or an unhealthy Core is a configuration
problem to fix deliberately, not a reason to recreate databases.

## ElectrumX and a Core reindex that moves the backend height a lot

If Core is reindexed on a host that also runs ElectrumX, Core's own reported
height passes through a low value early on (while it is rebuilding its block
index) before reaching the real chain tip. Older ElectrumX builds on this fork
could match that temporary low height, mark themselves internally as caught
up, and then never reconsider that status even as Core went on to advance
millions of blocks further; `self.caught_up` in
`electrumx/server/block_processor.py` was a one-way latch. This was discovered
during the real mainnet reindex documented in
[Validation status](validation-status.md) and is fixed: ElectrumX now revokes
its own caught-up state when the backend's height moves materially ahead of
its own indexed height (more than one prefetch batch, the codebase's existing
unit for ordinary catch-up work), and restores it once it has genuinely
indexed up to the new height again. Ordinary single-block tip lag does not
trigger this; only a real, material gap does.

A build with the fix recovers on its own; no manual ElectrumX rebuild or
restart should be needed for this specific scenario anymore. If you are
running an older build, restarting the ElectrumX container is still a valid,
low-cost workaround (`docker compose up -d --build --no-deps electrumx`,
leaving Core untouched); it is cheapest done as soon as you notice, since a
fresh process has to re-catch-up in full.

## Disk space is low

Check `df -h`, Docker's storage usage and the filesystem containing the mounted
Core and ElectrumX data. Stop new indexing work only through the documented
operational procedure, free unrelated files, and preserve adequate headroom.
Never remove blockchain data or use `docker compose down -v` as routine cleanup.

## DNS, DuckDNS or CGNAT prevents access

Use `dig +short your-name.duckdns.org` and compare the answer with the current
public address. If DuckDNS is stale, inspect the updater service/timer and its
last response without printing the token. If the router WAN address differs
from the address reported by an independent Internet service, the connection
may be behind CGNAT; DuckDNS cannot bypass that. See the [public-node guide](public-node.md)
for ISP, IPv6 and relay options.

## Port 50002 or TLS fails

Test TCP 50002 from outside the home network. Check the node's stable LAN
address, the router's TCP forwarding rule, firewall rules, certificate hostname
and certificate expiry. Use `openssl s_client` with the hostname and SNI to
inspect the certificate. Do not expose Core RPC or REST to the public Internet.

## No peers or legacy server behavior

Review Core logs and peer counts, then check time, DNS and outbound firewall
rules. An old Electrum endpoint may lack `server.ravencoin_backend` or the
current safety evidence. The wallet intentionally rejects missing, stale,
contradictory or unreviewed backend identity; do not bypass that check by
forcing a version string.

## Chain conflict or policy rejection

Record the exact wallet error, the server address, `server.version`,
`server.ravencoin_backend`, network, heights and checkpoint evidence. A wrong
repository/commit, revoked release, future unreviewed Core, stale backend or
chain conflict is a fail-closed safety result. Escalate with those facts rather
than asking users to disable validation.
