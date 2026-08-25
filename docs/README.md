# ElectrumX-Ravencoin documentation

Use this page as a human navigation menu. If you are new to nodes, read the
first section in order. If you already operate ElectrumX, jump directly to the
operator or reference section.

## New here?

1. [What the node stack does](getting-started.md#what-the-services-do)
2. [What changed in v1.13.11](release-1.13.11.md)
3. [Choose hardware](hardware.md)
4. [Start a private node](getting-started.md)
5. [Understand the August 2026 incident](incident-2026.md)

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

- [v1.13.11 technical overview](release-1.13.11.md): changes since v1.13.1,
  compatibility, qualification, and links to each detailed subsystem.
- [Architecture](architecture.md): serving, deployment, observation, and
  governance boundaries.
- [August 2026 incident](incident-2026.md): KAWPOW, `nHeight`, checkpoint and
  recovery context.
- [Security model](security-model.md): release identity, policy, evidence and
  fail-closed behavior.
- [Core certification](core-certification.md): candidate pipeline, profile and
  signed policy.
- [Release and update identity](release-artifact-revisions.md): manifest v2,
  artifact revisions, provenance, transactional updates, and anti-rollback.
- [Governance and succession](GOVERNANCE_AND_SUCCESSION.md): current trust
  roots, N-of-M transitions, and explicit successor adoption.
- [Validation status](validation-status.md): the single current status source.

## I want to understand Network Observer

- [Network Observer architecture](network-observer.md): Chain Quorum 2.0,
  signed observations, operator identity, multi-vantage comparison, asset
  probes, and Asset Data Quorum.
- [Observer audit](network-observer-audit.md): the architecture review and
  threat-model reasoning behind Phase 1.
- [Monitoring terminology](electrum-monitor.md): the distinction between the
  distributed Network Observer and the separate local Ravencoin Node Monitor.

## Developer and operator reference

- [Architecture](architecture.md): service and trust boundaries.
- [Network Observer](network-observer.md): discovery, health, SSRF protection,
  operator groups, quorum, signed observations, and asset correlation.
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
