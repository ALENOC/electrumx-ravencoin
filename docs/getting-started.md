# Getting started

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Hardware](hardware.md) · [Operations](operations.md)

## What the services do

Ravencoin Core downloads and validates the chain. ElectrumX reads Core's
validated history and builds a wallet query index. ElectrumX does not hold
wallet keys or coins. The default stack keeps Core RPC and REST private and
starts Electrum on loopback until the operator explicitly enables TLS.

## Bundled Core mode

Requirements: 64-bit Linux, Docker Engine, Compose v2, Git, OpenSSL, an NVMe or
SSD, and enough memory for the chosen hardware. Run:

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

## Existing Core mode

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
