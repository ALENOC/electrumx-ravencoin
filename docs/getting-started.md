# Getting started

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Hardware](hardware.md) · [Operations](operations.md)

## What the services do

Ravencoin Core downloads and validates the chain. ElectrumX reads Core's
validated history and builds a wallet query index. ElectrumX does not hold
wallet keys or coins. The default stack keeps Core RPC and REST private and
starts Electrum on loopback until the operator explicitly enables TLS.

## PATH A - Bundled Core mode (amd64/x86-64 and arm64/aarch64)

Requirements: 64-bit Linux, Docker Engine, Compose v2, Git, OpenSSL, an NVMe or
SSD, and enough memory for the chosen hardware. Docker builds the Core image
for the host's own architecture; the same commands work on amd64 and ARM64.
Run:

```sh
git clone https://github.com/ALENOC/electrumx-ravencoin.git
cd electrumx-ravencoin
./setup.sh --enable-reboot
docker compose up -d --build
docker compose ps
```

`setup.sh` validates Docker and architecture, creates ignored credentials
without printing them, and validates the Compose model. It does not delete
existing data. The user service is optional; confirm its behavior before
relying on it for reboot recovery.

The tracked `core-safety/production/update-signing-public-key.hex` in a source
checkout is the live production public trust root. It is not a historical value
that packaging later replaces. `setup.sh` does not create signed-release updater
state or install the `electrumx-update` command; if an operator deliberately
wires the updater to a source deployment, a known-retired trust file is refused.

### Optional fast bootstrap for a fresh node

For a new bundled-Core deployment on an empty data volume, the project can use
ChainStrap/IPFS to obtain historical raw block files before Core starts:

```sh
./fast-bootstrap.sh --enable-reboot
docker compose up -d --build
```

This path does **not** trust a downloaded chainstate or index. It uses a
release-pinned RVN mainnet manifest, verifies each part, extracts only
`blocks/blk*.dat`, and requires the bundled Core 4.8.0 to run a complete local
`-reindex -assumevalid=0` before normal Core and ElectrumX startup. See
[Fast Verified Bootstrap](fast-bootstrap.md) for the trust model, storage
requirements and operational details.

## PATH B - Existing Core mode

Use this only with a non-pruned mainnet Core whose deployment identity is known
and whose `txindex=1`, `assetindex=1`, and `rest=1` are already active:

```sh
./setup.sh --existing-core
${EDITOR:-vi} contrib/electrumx.env
${EDITOR:-vi} .env
docker compose -f compose.existing-core.yaml config --quiet
docker compose -f compose.existing-core.yaml up -d --build
```

Adding either index requires a Core reindex. Do not start that operation on a
production node without a maintenance plan and sufficient storage.

## Readiness sequence

1. Core starts and catches up or rebuilds its indexes.
2. ElectrumX opens its database and indexes historical blocks.
3. Read-only backend, chain, asset and index checks pass.
4. Only then consider publishing TLS.

`blocks < headers` is normal during Core initial sync. A healthy container is
not the same as a synchronized or wallet-usable server.

## What you need

For the bundled path, use a current 64-bit Linux host with Docker Engine,
Compose v2, Git and OpenSSL. Choose storage before installing: Core's raw
blocks and chainstate, `txindex`, `assetindex` and the ElectrumX database all
need persistent space. NVMe or SSD is strongly preferred; microSD is not a
reasonable location for this workload. See [Hardware](hardware.md) before
buying a board.

The bundled Core image is qualified for amd64. It also builds for ARM64,
including Raspberry Pi 5 and Orange Pi 5-class boards, from the same pinned
source commit, on native ARM64 GitHub Actions hardware, passing `make check`
plus a startup/RPC/REST/txindex/restart smoke suite; it has not been run
through the incident-specific probes the amd64 status rests on, and no report
is persisted in this repository. See [Validation
status](validation-status.md) for the current per-architecture evidence.
Existing-Core mode remains available for operators who already
manage a separate Core deployment. A board being recommended hardware is not
the same as its complete runtime having been validated.

## What the setup script changes

`setup.sh` checks that Docker and Compose are available, checks the selected
architecture, creates `.env` without overwriting an existing file, generates
RPC credentials below the Git-ignored `.secrets/` directory, and validates the
Compose files. It does not print credentials or delete blockchain data.

`fast-bootstrap.sh` first runs that same bundled-Core setup, then enables the
ChainStrap Compose overlay in `.env`. It refuses to overwrite a custom
`COMPOSE_FILE` value.

`--enable-reboot` installs a user systemd unit for the bundled stack. If the
host must start user services before login, configure user lingering as
described by the script. Read the generated unit and confirm that its behavior
fits your host before relying on unattended reboot recovery.

## Private readiness checklist

Before public networking, confirm all of these locally:

- Core answers JSON-RPC and reports the expected mainnet network.
- Core is still making progress or has reached the network tip.
- `txindex`, `assetindex` and REST are enabled for the bundled deployment.
- ElectrumX is no longer waiting for Core and its database height advances.
- `server.ravencoin_backend` reports fresh, coherent backend evidence.
- The independent chain and asset checks in [Validation status](validation-status.md)
  are complete for the deployment.

## Existing-Core mode in more detail

This mode is for operators who already manage Core. It does not make an
unreviewed Core safe merely because it is reachable. The existing node should
be non-pruned and provide the capabilities ElectrumX needs, including
`txindex=1`, `assetindex=1` and `rest=1`. Changing those settings can require a
reindex; plan that work separately and never delete a production database as a
shortcut.

ElectrumX needs REST because its block-fetch path reads
`rest/block/<hash>.bin`. If REST is disabled or inaccessible, the index can
stop even while JSON-RPC appears healthy. Keep Core's JSON-RPC and REST on the
private network. REST has no authentication of its own.

## Private versus public

The private path ends when your own wallet can use the local Electrum listener.
It needs no router changes. The public path adds a stable hostname, a stable
LAN address, inbound TCP forwarding, a CA-valid TLS certificate, renewal and
tests from another network. Follow [Public node](public-node.md) only after
private synchronization and indexing are complete.
