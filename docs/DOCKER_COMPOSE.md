# Docker Compose deployment for a public Ravencoin ElectrumX operator

This path is for a Linux operator who already runs a healthy, fully synchronized
Ravencoin Core **4.8.0 or newer** on the same host. It publishes TLS Electrum on
port 50002, keeps the management RPC on loopback, and never publishes Core RPC.

The Compose service uses host networking so a Core daemon safely bound to
`127.0.0.1:8766` remains reachable without opening it to a container bridge or the
Internet. Review this choice if Core is on another private host.

## Host baseline

- `/Ravencoin:4.8.0/` or newer on mainnet, blocks equal to headers, not in IBD;
- checkpoint 4,487,775 equals
  `000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd`;
- fast SSD storage and unmetered/reliably provisioned network;
- as a conservative starting point, 4 CPU cores, 8 GiB RAM and at least 100 GiB
  free SSD space reserved for ElectrumX, then tune from measured indexing load;
- a stable DNS name whose CA-valid certificate and private key are readable by
  the container's unprivileged `electrumx` user.

Initial indexing is I/O- and memory-intensive and can take many hours. Do not use
the 1.4 GiB Orange Pi Zero 3 RavenTag host: its controlled trial is closed as
unsustainable. A VPS, NUC, or larger dedicated machine is appropriate.

## Configure without committing secrets

```sh
cp .env.example .env
cp contrib/electrumx.env.example contrib/electrumx.env
chmod 600 .env contrib/electrumx.env
```

Edit `.env` with a dedicated host directory containing real (not dangling
symlink) `fullchain.pem` and `privkey.pem` files. Arrange a certificate renewal
deploy hook to refresh that directory atomically and restart ElectrumX. Grant the
container's unprivileged user read access without making the private key
world-readable. Edit
`contrib/electrumx.env` with a dedicated, least-privilege Core RPC user/password
and the public Electrum hostname. Both files are ignored by Git, and `.dockerignore`
also keeps environment files and TLS private keys out of the image build context.
Never place real credentials in `compose.yaml`, an image, a command line, or a
public log. Leave `ALLOW_UNSAFE_RAVENCOIN_CORE` unset.

The example assumes Core RPC is already bound only to loopback. Do not add a
public firewall rule for 8766. Permit inbound TCP 50002 only after the server is
caught up and all validation below passes.

## Build and start

```sh
docker compose config --quiet
docker compose up -d --build
docker compose logs --follow electrumx
```

The named `electrumx-db` volume persists the index across image replacement.
Compose permits at most five automatic restart attempts and grants a 15-minute
shutdown window so LevelDB can flush cleanly. The container runs non-root with all
Linux capabilities dropped. `electrumx_rpc getinfo` supplies the local health check;
external client listeners remain unavailable until ElectrumX catches up.

## Validate before publication

```sh
docker compose exec electrumx electrumx_rpc getinfo
openssl s_client -connect electrum.example.org:50002 \
  -servername electrum.example.org -verify_return_error </dev/null
```

From a machine outside the host network, call all of the following over TLS:

1. `server.version`, `server.features`, `server.ping`, and
   `blockchain.headers.subscribe`;
2. `server.ravencoin_backend` — require mainnet, `coreSafe=true`,
   `minimumSafeCore=4.8.0`, checkpoint success, equal Core blocks/headers, and a
   fresh observation;
3. `blockchain.block.header` at several common heights and compare canonical
   Ravencoin block IDs with a trusted 4.8.0 node. Post-KAWPOW IDs are not Bitcoin
   double-SHA256 header hashes; derive height H from `hashPrevBlock` in H+1;
4. Raven asset calls including `blockchain.asset.get_meta`, asset-aware
   `blockchain.scripthash.get_balance` and `listunspent`, history, mempool, owner
   token/unique-asset queries, and an operator-controlled broadcast test.

Do not advertise `REPORT_SERVICES` or add the endpoint to wallet defaults until
these checks pass. ElectrumX version text is never evidence of the backend Core
version; use the sanitized backend method plus independent chain verification.

## Upgrade or migrate an existing server

Read [MIGRATING_FROM_ELECTRUM_RVN_SIG.md](MIGRATING_FROM_ELECTRUM_RVN_SIG.md)
before reusing an index created around the August 2026 incident. Preserve a backup,
stop the old public listener, upgrade Core first, and let this fork validate the
database tip/checkpoint. If it refuses a stale or conflicting database, follow a
reviewed rewind/rebuild procedure rather than deleting the only copy.

For upgrades of this Compose deployment:

```sh
docker compose build --pull electrumx
docker compose up -d electrumx
```

Re-run every backend, chain, TLS and asset check after the replacement.
