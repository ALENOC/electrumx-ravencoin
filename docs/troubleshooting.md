# Troubleshooting

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Operations](operations.md) · [Validation status](validation-status.md)

## Where does the installer put things?

Three independent locations are involved, and only one is expected to grow
large:

- the **installation directory** holds the Compose files, `.env`, and the install
  marker. It defaults to `electrumx-ravencoin` resolved against the current
  working directory, not against the home directory. Override it with
  `--install-dir`;
- the **project data directory** holds the Ravencoin chain data and ElectrumX
  index. It is selected explicitly with `--storage-root` and is the location that
  can grow to hundreds of gigabytes;
- **Docker images** stay in Docker's existing data-root. The installer does not
  relocate them, so the filesystem holding `/var/lib/docker` still needs free
  space even when `--storage-root` points at another disk.

The installer prints all three locations on success.

## `host anti-rollback preflight failed: root-owned security-state locator is missing`

The full message is:

```text
error: host anti-rollback preflight failed: root-owned security-state locator is missing: /var/lib/electrumx-ravencoin/security-state.locator
```

This is expected on a first installation performed as an unprivileged user and
is not a defect. The host-wide anti-rollback floor lives outside the
installation directory so reinstalling into another directory cannot reset it.
An unprivileged process is deliberately not allowed to create the root-owned
locator itself.

`--check-only` does not touch persistent state, so a successful preflight does
not imply that the locator exists.

To keep the node owned by the current unprivileged user, provision the locator
once and re-run the installer unchanged:

```sh
sudo install -d -o root -g root -m 0755 /var/lib/electrumx-ravencoin

printf '{\n  "schemaVersion": 1,\n  "ownerUid": %s,\n  "path": "%s"\n}\n' \
  "$(id -u)" \
  "${XDG_STATE_HOME:-$HOME/.local/state}/electrumx-ravencoin/security-state.json" \
  | sudo tee /var/lib/electrumx-ravencoin/security-state.locator >/dev/null

sudo chmod 0644 /var/lib/electrumx-ravencoin/security-state.locator
```

The locator must be a regular non-symlink file owned by root with mode `0644`.
The state file it names is created later by the installer, owned by the same
unprivileged user and mode `0600`.

Running the installer under `sudo` instead is also supported. Root provisions
the locator in the root namespace at
`/var/lib/electrumx-ravencoin/security-state.json`, and the installation and its
data become root-owned, so later `docker compose` and `electrumx-update apply`
commands also require `sudo`.

The two ownership models are mutually exclusive. A locator provisioned for an
unprivileged user makes a later root invocation fail with a message such as:

```text
security-state namespace belongs to uid 1000, not caller uid 0
```

Changing the ownership decision later requires deliberately removing the
root-owned locator and its state file, which discards the recorded anti-rollback
high-water. Choose the owning identity before installing.

## `fresh install storage root already exists`

Example:

```text
error: fresh install storage root already exists: /mnt/data/electrumx-ravencoin-storage; preserve or remove it explicitly before retrying
```

A fresh install never writes into an existing storage root, so a directory left
by an earlier attempt is refused rather than reused or overwritten. The
installer suggests `<mountpoint>/electrumx-ravencoin-storage` on writable mounted
filesystems, and an existing directory at that location causes the refusal.

Decide explicitly what the old directory contains. If it holds chain data worth
preserving, rename it and reuse the suggested path:

```sh
mv /mnt/data/electrumx-ravencoin-storage /mnt/data/electrumx-storage-old
```

If it is disposable, remove it explicitly. Container-owned subdirectories may
require `sudo`, and this permanently discards any synced data:

```sh
sudo rm -rf /mnt/data/electrumx-ravencoin-storage
```

Alternatively choose a custom path in the interactive installer or pass
`--storage-root DIR`. The storage root must be a dedicated child directory: not
`/`, not `$HOME`, and not the filesystem mountpoint itself.

## `mkdir: invalid option -- 'o'` with advanced host controls

Example from the historical 1.13.5 installer:

```text
error: command failed with exit code 1: /usr/bin/sudo mkdir -p -o root -g root -m 0755 /usr/local/lib/electrumx-ravencoin
```

This affects the 1.13.5 installer only. It attempted to create the root-owned
controller directory with ownership flags that `mkdir` does not accept. A fresh
1.13.5 install therefore aborted when the advanced bandwidth/connection
controller was requested.

1.13.6 and later use the correct `install -d` path. Operators should use the
current release rather than work around this historical installer defect. The
advanced controller is optional and disabled by default.

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
height passes through a low value early on before reaching the real chain tip.
Older ElectrumX builds on this fork could match that temporary low height, mark
themselves internally as caught up, and then never reconsider that status even
as Core advanced millions of blocks further. This was discovered during the
real mainnet reindex documented in [Validation status](validation-status.md) and
is fixed: ElectrumX now revokes its own caught-up state when the backend's
height moves materially ahead of its indexed height and restores it after a
genuine catch-up. Ordinary single-block tip lag does not trigger this behavior.

A build with the fix recovers on its own. For an older build, restarting only the
ElectrumX container remains a valid low-cost workaround:

```sh
docker compose up -d --build --no-deps electrumx
```

Leave Core and its data untouched.

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
may be behind CGNAT; DuckDNS cannot bypass that. See the
[public-node guide](public-node.md) for ISP, IPv6 and relay options.

## Port 50002 or TLS fails

Test TCP 50002 from outside the home network. Check the node's stable LAN
address, router forwarding rule, firewall rules, certificate hostname and
certificate expiry. Use `openssl s_client` with the hostname and SNI to inspect
the certificate. Do not expose Core RPC or REST to the public Internet.

## No peers or legacy server behavior

Review Core logs and peer counts, then check time, DNS and outbound firewall
rules. An old Electrum endpoint may lack `server.ravencoin_backend` or current
safety evidence. The wallet intentionally rejects missing, stale, contradictory
or unreviewed backend identity; do not bypass that check by forcing a version
string.

## Chain conflict or policy rejection

Record the exact wallet error, server address, `server.version`,
`server.ravencoin_backend`, network, heights and checkpoint evidence. A wrong
repository/commit, revoked release, future unreviewed Core, stale backend or
chain conflict is a fail-closed safety result. Escalate with those facts rather
than asking users to disable validation.
