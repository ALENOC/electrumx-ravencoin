# Architecture

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Fork features](fork-features.md) · [Current release](release-1.13.11.md) ·
[Security model](security-model.md)

ElectrumX-RVN separates the wallet-serving path from deployment controls,
network observation, and release governance. A failure or trust decision in
one plane must not silently become authority in another.

## Serving plane

```text
Electrum wallet / RavenTag client
              |
              | legacy Electrum protocol + server.ravencoin_backend
              v
        ElectrumX-RVN 1.13.11
              |
              | private JSON-RPC and REST
              v
   Ravencoin Core 4.8.0 (pinned identity)
              |
              v
      Ravencoin peer-to-peer network
```

Ravencoin Core validates consensus and maintains `txindex` and `assetindex`.
ElectrumX builds its own wallet-query index from Core's validated chain. It has
no wallet, holds no seed phrases or private keys, and does not sign
transactions.

The bundled Compose stack keeps Core RPC and REST private. The public
`server.ravencoin_backend` method reports sanitized local evidence about Core
identity, network, height, synchronization, checkpoints, indexes, and
compatibility. It is a stable compatibility contract, not remote binary
attestation and not a substitute for independent chain validation.

## Installation and bootstrap plane

```text
signed standalone installer
       |
       +-- verifies release manifest and provenance
       +-- verifies signed safe-Core policy
       +-- selects storage and Compose overlays
       +-- optionally stages ChainStrap raw block files
       v
local Core reindex + offline chain/index gate -> normal serving plane
```

The installer verifies two independent trust domains: the ElectrumX
release/update signature and the safe-Core policy signature. A release signer
cannot replace the Core-policy verification key inside the same bundle.

ChainStrap is optional transport acceleration for fresh bundled-Core installs.
Only verified raw `blocks/blk*.dat` files may be staged; downloaded databases
are not trusted. Core then performs the complete local reindex with networking
disabled and must pass the offline post-reindex chain/index gate. ChainStrap
never becomes a consensus source.

## Update plane

```text
signed manifest + artifact + provenance
                 |
                 v
      eligibility and anti-rollback
                 |
                 v
 storage proof -> transactional switch -> health gates
                 |                         |
                 | success                 | failure
                 v                         v
              promote                exact rollback
```

The updater is explicit and operator-driven. Host-wide high-water state binds
the highest accepted semantic version, artifact revision, and digests outside
the installation directory. A release switch preserves selected persistent
state and ownership, coordinates known external reconcilers, and uses
same-filesystem renames. An unprovable rollback fails closed.

## Observation plane

```text
Observer A ---- signed bundle ----+
Observer B ---- signed bundle ----+--> aggregator --> evidence/report
Observer C ---- signed bundle ----+
      |                                  (no release authority)
      +--> bounded probes of public Electrum endpoints
```

The optional `network_observer` package discovers and probes public Electrum
servers. It provides shared-height chain challenges, signed multi-vantage
observations, operator-aware diversity, active asset capability probes, and
height-bound Asset Data Quorum.

It does not proxy wallet traffic, modify ElectrumX, decide Ravencoin consensus,
or authorize releases. Its database high-water marks protect observation,
operator declaration, policy, and snapshot history from replay or rollback.

## Local monitoring plane

The separately maintained `ravencoin-node-monitor` is a local operational
dashboard for one deployment. It observes local Core, ElectrumX, host, and
network health. Its dashboard is loopback-only by default, and its optional
privileged bandwidth controller is disabled unless explicitly selected.

The local Node Monitor and the distributed Network Observer are distinct
systems, packages, trust boundaries, and deployment choices.

## Governance plane

Release governance and safe-Core governance use separate signing domains and
keys. The governance library supports N-of-M policies, epoch transitions,
revocation, anti-rollback, and explicit successor adoption. Network Observer
popularity or output has no path into signature validity.

The framework is founder-independence capable but production threshold
governance is not activated. Current production roots have not yet been
distributed to an independent maintainer quorum.

## Boundary summary

| Component | May do | Must not do |
|---|---|---|
| Ravencoin Core | Validate Ravencoin consensus | Trust ChainStrap databases or observer votes |
| ElectrumX | Index the validated chain and serve wallet queries | Hold wallet keys or authorize releases |
| Installer/updater | Authenticate and transactionally deploy reviewed releases | Infer trust from a version label or repository URL |
| Network Observer | Collect, sign, compare, and report endpoint evidence | Become consensus or governance authority |
| Node Monitor | Report local operational health | Classify the public Electrum network |
| Governance verifier | Validate authorized policy and release transitions | Consume observer popularity as authority |

Further detail: [Fork features](fork-features.md),
[Network Observer](network-observer.md),
[Governance and succession](GOVERNANCE_AND_SUCCESSION.md), and
[Release identity and revisions](release-artifact-revisions.md).
