# ElectrumX-Ravencoin documentation

This is the documentation hub for the maintained ElectrumX-RVN fork. Start with
the smallest guide that matches what you are trying to do; deep implementation
notes and historical material are intentionally kept out of the normal operator
path.

## Start here

1. [What this fork adds](fork-features.md) — canonical overview of every
   maintained subsystem, improvement, trust boundary, and compatibility promise.
2. [Getting started](getting-started.md) — install a private node and understand
   the Core/ElectrumX service path.
3. [Current release: v1.13.11](release-1.13.11.md) — changes and qualification
   specific to the current production release.
4. [Architecture](architecture.md) — serving, installation, update, observation,
   monitoring, and governance planes.
5. [Security model](security-model.md) — what is trusted, what is evidence, and
   where the project fails closed.

## Run a node

- [Getting started](getting-started.md): bundled Core and existing-Core paths.
- [Hardware](hardware.md): architecture support, RAM, NVMe, cooling, and
  qualification boundaries.
- [Storage selection](storage-selection.md): choose persistent storage before
  installation.
- [Fast Verified Bootstrap](fast-bootstrap.md): ChainStrap transport, resume,
  archive filtering, full local Core reindex, and post-reindex validation.
- [Docker Compose reference](DOCKER_COMPOSE.md): deployment model details.
- [Operations](operations.md): lifecycle, synchronization, backups, and safe
  updates.
- [Troubleshooting](troubleshooting.md): symptoms, checks, and safe recovery.
- [Public node](public-node.md): DNS, CGNAT, TLS, firewalling, and external
  testing after the private node is healthy.

## Understand the fork's security design

- [Fork features](fork-features.md): permanent feature map and subsystem
  boundaries.
- [Security model](security-model.md): Core identity, endpoint trust ladder,
  release/update trust, bootstrap trust, observer evidence, and fail-closed
  behavior.
- [Core certification](core-certification.md): candidate pipeline, behavior
  profile, exact official Core identity, and signed safe-Core policy.
- [Release identity and revisions](release-artifact-revisions.md): manifest v2,
  provenance, artifact revisions, transactional updates, and host-wide
  anti-rollback.
- [Crash consistency](crash-consistency.md): ElectrumX database extent checks
  and bounded recovery behavior.
- [Governance and succession](GOVERNANCE_AND_SUCCESSION.md): N-of-M framework,
  current single-maintainer roots, transitions, and successor adoption.
- [Validation status](validation-status.md): canonical current qualification and
  verification status.

## Ravencoin Network Observer

- [Network Observer](network-observer.md): Chain Quorum 2.0, signed
  multi-vantage observations, operator identity, network-safety controls,
  active asset probes, and Asset Data Quorum.
- [Monitoring terminology](electrum-monitor.md): distinction between the
  distributed Network Observer and the separate local Ravencoin Node Monitor.

The Network Observer is optional observation tooling. It is outside the wallet
serving path and cannot authorize releases, rotate governance, or replace
Ravencoin Core consensus validation.

## Maintainer and migration procedures

These are not normal first-install reading:

- [Offline release signing](OFFLINE_RELEASE_SIGNING_1.13.11.md): current
  production signing procedure.
- [Legacy 1.13.1 adoption](LEGACY_1.13.1_ADOPTION.md): explicit one-time trust
  transition for historical 1.13.1 installations.
- [Migration from Electrum-RVN-SIG](MIGRATING_FROM_ELECTRUM_RVN_SIG.md): moving
  from the older fork lineage.
- [Current hardware qualification](HARDWARE_QUALIFICATION_1.13.11.md): detailed
  real-hardware evidence for the current release.

## Protocol and upstream reference

The `.rst` reference set is retained where it still documents the Electrum
protocol, RPC surface, environment, peer discovery, and upstream history. It is
reference material, not the recommended production installation guide.

- [Protocol documentation](protocol.rst)
- [RPC interface](rpc-interface.rst)
- [Environment reference](environment.rst)
- [Peer discovery reference](peer_discovery.rst)
- [Upstream and credits](upstream-and-credits.md)

## Historical material

Superseded release notes, pre-implementation audit working papers, and obsolete
installation guides are intentionally not kept in the active documentation
tree. Git history and release/tag history remain the audit trail. The maintained
historical release/update record is [Release identity and revisions](release-artifact-revisions.md).

Documentation: [Home](../README.rst) · [Fork features](fork-features.md) ·
[Security](security-model.md) · [Status](validation-status.md)
