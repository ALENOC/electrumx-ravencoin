# ElectrumX-RVN 1.13.11

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Architecture](architecture.md) · [Security model](security-model.md) ·
[Validation status](validation-status.md)

ElectrumX-RVN 1.13.11 is the current production release. It combines the
deployment and update hardening added after 1.13.1 with Ravencoin Network
Observer Phase 1. The wallet-serving path remains compatible with existing
Electrum clients.

## What changed since 1.13.1

| Area | v1.13.11 behavior |
|---|---|
| Releases and updates | Revision-aware signed manifests, offline release signing, provenance binding, host-wide anti-rollback state, and transactional promotion/rollback |
| Existing installations | Explicit migration from legacy 1.13.1 state; storage, configuration, secrets, ownership, and selected Compose overlays are preserved |
| Fast bootstrap | ChainStrap is restricted to verified raw block transport; Ravencoin Core performs the complete local validation and index rebuild |
| Core identity | Exact official `RavenProject/Ravencoin` v4.8.0 source identity, certification evidence, and signed safe-Core policy |
| Network visibility | Chain Quorum 2.0, signed multi-vantage observations, operator-aware diversity, active asset probes, and height-bound Asset Data Quorum |
| Governance | Tested N-of-M policy, rotation, domain separation, and explicit successor adoption; production threshold governance is not activated |
| Compatibility | Legacy Electrum protocol and the public `server.ravencoin_backend` contract remain unchanged |

## Release and update security

Manifest schema v2 binds the semantic version, artifact revision, artifact
digest, provenance digest, exact Core identity, and safe-Core policy identity.
Release ordering uses parsed versions rather than string comparison, so
`1.13.11` correctly follows `1.13.9` and `1.13.10`.

The updater keeps a host-wide high-water mark outside the installation tree.
A lower version or revision is rejected, and reusing the same version and
revision with different digests is treated as equivocation. Reinstalling into
another directory cannot silently reset this floor.

Updates are explicit and transactional. Before switching releases, the updater
authenticates the candidate and proves the storage model. It preserves the
operator-controlled state, suspends known external reconcilers, switches by
same-filesystem renames, runs health gates, and either promotes the candidate
or restores the exact previous release. If exact recovery cannot be proven, it
fails closed instead of starting an ambiguous stack.

Production manifests are signed by the offline update key with ID
`6f4f944c9b0a19a1`. CI can build unsigned candidates but cannot sign or publish
a production release. The tracked historical schema-v1 key is retired; a
source checkout does not silently enable the updater with that key.

Detailed design: [release artifact revisions](release-artifact-revisions.md)
and [offline signing procedure](OFFLINE_RELEASE_SIGNING_1.13.11.md).

## Core and ChainStrap boundaries

The bundled Core identity is the official `RavenProject/Ravencoin` v4.8.0 tag
at commit `22549129888d02e0e08fcdb9f96f3c699167e774`. Repository, commit,
certification report, and signed policy must agree; a version string alone is
not enough.

ChainStrap accelerates transport of historical `blocks/blk*.dat` files. It is
not a consensus authority. Digests and archive structure are checked before
staging, unsafe entries fail closed, and the pinned Core performs a complete
offline `-reindex -assumevalid=0` with the required indexes before normal
service starts.

Detailed design: [Core certification](core-certification.md) and
[Fast Verified Bootstrap](fast-bootstrap.md).

## Ravencoin Network Observer

The `network_observer` package is an optional observation system, separate
from both ElectrumX serving and the local Ravencoin Node Monitor. It adds:

- shared-height Chain Quorum 2.0 challenges over independent operator groups;
- domain-separated Ed25519 observation bundles with expiry and sequence
  anti-replay;
- cryptographic operator declarations with rollback-resistant high-water state;
- comparison of signed observations from independent network vantage points;
- active asset RPC capability checks instead of trusting feature flags; and
- height-bound Asset Data Quorum with canonical samples and repeated-conflict
  confirmation.

The observer reports evidence; it never decides Ravencoin consensus, authorizes
software releases, changes governance, or modifies wallet traffic.

Detailed design: [Network Observer](network-observer.md).

## Governance status

The governance library implements and tests N-of-M policies, epoch transitions,
revocation, domain separation, anti-rollback, and explicit successor adoption.
The current production roots are not distributed to an independent maintainer
quorum, so the precise status is:

> **FOUNDER-INDEPENDENCE CAPABLE — PRODUCTION THRESHOLD ACTIVATION PENDING A
> FUTURE SIGNING CEREMONY**

Network Observer output cannot authorize a release or governance transition.
Detailed design: [Governance and succession](GOVERNANCE_AND_SUCCESSION.md).

## Qualification

The published v1.13.11 artifact passed the required regression, signed-release,
ordinary-update, service-health, public TLS, ownership-preservation, and
Network Observer gates on the Raspberry Pi 5 qualification node. Ravencoin
Core and ElectrumX remained synchronized and all qualified containers finished
with zero restarts.

Exact identities and observed evidence:
[v1.13.11 hardware qualification](HARDWARE_QUALIFICATION_1.13.11.md).
