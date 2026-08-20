# ElectrumX-Ravencoin documentation

Use this page as a human navigation menu. If you are new to nodes, read the
first section in order. If you already operate ElectrumX, jump directly to the
operator or reference section.

## New here?

1. [What the node stack does](getting-started.md#what-the-services-do)
2. [Choose hardware](hardware.md)
3. [Start a private node](getting-started.md)
4. [Understand the August 2026 incident](incident-2026.md)

## I want to run a private server

- [Getting started](getting-started.md): concepts, bundled Core and
  existing-Core deployment.
- [Fast Verified Bootstrap](fast-bootstrap.md): optional ChainStrap/IPFS block
  transport followed by a full local Core reindex before ElectrumX can start.
- [Hardware](hardware.md): RAM, NVMe, cooling, boards and storage reasoning.
- [Operations](operations.md): start, stop, status, progress, backups and
  upgrades.
- [Troubleshooting](troubleshooting.md): symptoms, checks and safe fixes.

## I want to publish my server

- [Public node guide](public-node.md): private versus public operation.
- [Dynamic DNS and DuckDNS](public-node.md#dynamic-dns-and-duckdns): stable
  hostnames for changing residential addresses.
- [CGNAT](public-node.md#carrier-grade-nat-cgnat): recognizing and handling
  networks that cannot accept inbound IPv4.
- [TLS and external testing](public-node.md#tls): certificates, renewal and
  testing from outside the LAN.

## I want to understand the security design

- [August 2026 incident](incident-2026.md): KAWPOW, `nHeight`, checkpoint and
  recovery context.
- [Security model](security-model.md): release identity, policy, evidence and
  fail-closed behavior.
- [Core certification](core-certification.md): candidate pipeline, profile and
  signed policy.
- [Validation status](validation-status.md): the single current status source.

## Developer and operator reference

- [Architecture](architecture.md): service boundaries and data flow.
- [Electrum monitor](electrum-monitor.md): discovery, health, SSRF protection,
  operator groups and vantage points.
- [Docker Compose reference](DOCKER_COMPOSE.md): deployment model details.
- [Protocol documentation](protocol.rst): Electrum protocol reference.
- [RPC interface](rpc-interface.rst): server RPC methods.
- [Migration guide](MIGRATING_FROM_ELECTRUM_RVN_SIG.md): moving from the older
  fork.

## History and attribution

- [Upstream and credits](upstream-and-credits.md): lineage, MIT license and
  notices.

Documentation: [Home](../README.rst) · [Getting started](getting-started.md) ·
[Security](security-model.md) · [Status](validation-status.md)
