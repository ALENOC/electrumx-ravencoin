# What this fork adds to ElectrumX for Ravencoin

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Architecture](architecture.md) · [Security model](security-model.md) ·
[Current release](release-1.13.11.md)

This page is the canonical, release-independent overview of the functionality
added and maintained by **ElectrumX-RVN** on top of the historical
ElectrumX-Ravencoin codebase. It explains what each subsystem does, what trust
boundary it belongs to, and what it deliberately does **not** claim to prove.

For operator commands, installation steps, protocol details, and release
qualification evidence, follow the linked specialist guides instead of treating
this page as an exhaustive runbook.

## 1. Maintained Ravencoin deployment path

The fork turns the historical ElectrumX-Ravencoin server into a maintained
Ravencoin node stack with a defined production path for Linux amd64 and ARM64.
The normal serving chain is:

```text
Electrum wallet / RavenTag
        |
        v
ElectrumX-RVN
        |
        v
Ravencoin Core
        |
        v
Ravencoin P2P network
```

Ravencoin Core remains the consensus engine. ElectrumX builds a wallet-query
index from the chain Core accepted and serves Electrum protocol requests. The
server has no wallet, does not hold seed phrases or private keys, and does not
sign transactions.

The bundled deployment pins the official `RavenProject/Ravencoin` Core release
identity instead of accepting an arbitrary daemon merely because it prints an
expected version string. The current production line is bound to official
Ravencoin Core 4.8.0 at commit
`22549129888d02e0e08fcdb9f96f3c699167e774`.

Detailed design: [Architecture](architecture.md),
[Core certification](core-certification.md).

## 2. Exact Core identity and safe-Core certification

A version such as `4.8.0` is metadata, not proof of what source code is
running. The fork therefore separates Core eligibility into explicit evidence:

1. exact source repository and commit identity;
2. behavioral certification against the maintained Ravencoin safety profile;
3. a signed safe-Core policy identifying releases that passed certification;
4. fresh runtime evidence from the local backend; and
5. independent chain evidence where a remote endpoint is being evaluated.

The safe-Core policy uses its own Ed25519 trust domain. Its signing key is not
the ElectrumX release/update key. A valid ElectrumX release therefore cannot, by
itself, mark a different Ravencoin Core build as safe.

Unknown, stale, revoked, malformed, contradictory, or unreviewed Core identity
evidence fails closed. A future version is not automatically accepted simply
because its version number is higher.

Detailed design: [Security model](security-model.md),
[Core certification](core-certification.md).

## 3. `server.ravencoin_backend` compatibility and backend evidence

The fork exposes the public `server.ravencoin_backend` Electrum RPC used by
RavenTag and compatible clients. It reports sanitized evidence about the local
backend, including network and synchronization state, Core source identity,
indexes, checkpoint/compatibility information, and related status fields.

This RPC is a **compatibility and evidence interface**, not remote binary
attestation. A remote server controls its own response and can lie. Consumers
must treat the response as a claim that can be checked against signed policy and
independent chain evidence.

The public contract is regression-tested so Network Observer, governance, and
update work cannot silently change its field types or semantics.

## 4. Fast Verified Bootstrap with ChainStrap

For a fresh bundled-Core deployment, the fork can use ChainStrap to accelerate
acquisition of historical raw block data.

The security boundary is intentionally narrow:

- ChainStrap is a **transport source**, never Ravencoin consensus authority;
- the repository pins a reviewed RVN mainnet snapshot manifest;
- every archive part is bound to expected identity, size, and SHA-256;
- only safe regular `blocks/blk*.dat` members are admitted to the Ravencoin
  datadir;
- downloaded chainstate, LevelDB indexes, asset databases, configuration,
  wallets, undo databases, and unrelated archive content are not trusted;
- raw block files must form the expected contiguous sequence;
- the pinned Core runs a full local `-reindex -assumevalid=0` with the required
  indexes before ElectrumX may start; and
- the completed bootstrap is bound to manifest/progress marker state so a later
  snapshot cannot silently reinterpret an already validated installation.

Interrupted downloads can resume only when the partial object and HTTP range
response remain manifest-consistent. Integrity failures discard unsafe partial
state. Gateway fallback is bounded and the selected bootstrap never silently
changes to P2P after a failure.

After reindex, an additional offline verification gate checks the active chain
height/hash and real asset/index reads before the bootstrap completion marker is
written. Successful process exit alone is not considered sufficient evidence.

Detailed design: [Fast Verified Bootstrap](fast-bootstrap.md).

## 5. Signed standalone installer

The recommended production entry point is a standalone Python installer. It is
downloaded first and executed locally rather than piped directly from the
network.

The installer:

- checks supported host architecture and required tooling;
- verifies signed release metadata and provenance;
- verifies the independent safe-Core policy trust domain;
- lets the operator select storage explicitly;
- supports bundled Core or deliberate existing-Core deployment;
- offers ChainStrap or traditional P2P bootstrap;
- can install the optional local Ravencoin Node Monitor;
- keeps the privileged bandwidth/connection controller opt-in; and
- refuses ambiguous pre-existing state instead of silently adopting it as a
  fresh installation.

`--check-only` performs host and signed metadata verification without creating a
persistent installation.

Detailed operator path: [Getting started](getting-started.md).

## 6. Revision-aware signed releases

ElectrumX-RVN uses a dedicated Ed25519 release/update trust domain. Production
private signing material is kept outside GitHub Actions; CI can build and test
unsigned candidates but cannot independently create an authorized production
release.

Manifest schema v2 binds more than a semantic version. The authenticated release
identity includes, among other fields:

- ElectrumX version;
- artifact revision;
- artifact digest;
- provenance digest;
- exact bundled Ravencoin Core identity;
- safe-Core policy identity;
- release time; and
- signing-key identity.

Release ordering is parsed semantically, not compared as strings, so two-digit
patch versions order correctly. Reusing the same version/revision with different
digests is treated as equivocation rather than as the same release.

Same-version artifact revisions are deliberately constrained: executable
behavior is frozen and a behavioral change requires a version bump.

Detailed design: [Release identity and revisions](release-artifact-revisions.md),
[Offline signing](OFFLINE_RELEASE_SIGNING_1.13.11.md).

## 7. Host-wide anti-rollback state

The highest accepted signed release identity is recorded in a protected
host-wide security namespace outside the selected installation tree. Choosing a
new install directory therefore cannot silently reset the security floor.

The anti-rollback rules reject:

- a lower semantic version;
- a lower artifact revision at the same version;
- malformed or incomplete revision identity; and
- the same version/revision paired with different authenticated digests.

Ownership, file type, symlink behavior, permissions, and canonical path rules
are verified before the state is trusted.

## 8. Transactional update and exact rollback

Updates are explicit and operator-driven; availability of a newer release does
not imply silent installation.

Before switching releases, the updater authenticates the candidate, verifies
eligibility, proves the storage model, records the previous state, and
coordinates known external reconcilers. The switch uses same-filesystem
operations, preserves the explicitly allowlisted persistent operator state and
ownership, then runs health gates.

If the candidate fails, the updater attempts to restore the exact previous
release. If exact rollback cannot be proven, it fails closed instead of
starting a partially switched or ambiguous stack.

Persistent Core/ElectrumX storage is preserved, as are operator-selected Compose
overlays. ChainStrap is a fresh-install bootstrap and is not re-run during an
ordinary update.

## 9. Database crash-consistency hardening

The maintained fork adds startup checks around historical ElectrumX database
extent consistency. Unsafe corruption is refused rather than silently indexed
past. Bounded crash-tail recovery remains explicit and separate from accepting
arbitrary historical corruption.

This protects the ElectrumX index layer only; it does not replace Ravencoin
Core consensus validation.

Detailed design: [Crash consistency](crash-consistency.md).

## 10. Safer Docker/Compose deployment model

The maintained deployment adds a deterministic Compose project identity,
explicit storage selection, isolated Core RPC/REST networking, generated
credentials that are not printed, optional TLS overlays, and safer cleanup
semantics for failed fresh installs.

The project distinguishes container health from full node readiness. Core may be
healthy while still synchronizing or reindexing, and ElectrumX may be healthy
while still catching up. Public TLS publication is treated as a later operator
step after local chain, index, backend, and asset evidence are coherent.

Detailed design: [Getting started](getting-started.md),
[Operations](operations.md), [Public node](public-node.md).

## 11. amd64 and ARM64 support with real-hardware qualification

The same pinned Core source identity is built and tested for Linux amd64 and
ARM64. Raspberry Pi 5 is the current physically qualified low-power ARM64 path;
other ARM64 boards can use the supported build path but do not automatically
inherit the same hardware qualification claim.

The documentation explicitly separates **supported architecture** from
**physically qualified hardware**.

Detailed evidence: [Hardware](hardware.md),
[Validation status](validation-status.md),
[current hardware qualification](HARDWARE_QUALIFICATION_1.13.11.md).

## 12. Optional local Ravencoin Node Monitor

The installer can deploy the separate `ravencoin-node-monitor` project to
observe one local installation. It reports local Core, ElectrumX, host, and
network health.

The dashboard is loopback-only by default. The privileged bandwidth/connection
controller is a separate component and remains disabled unless the operator
explicitly enables it.

The **local Node Monitor** is not the **Ravencoin Network Observer** described
below. They have different purposes, codebases, privileges, and trust
boundaries.

## 13. Ravencoin Network Observer

The optional `network_observer` package observes public Electrum infrastructure
without entering the wallet-serving path. It does not proxy client traffic and
is never imported as an authority by the ElectrumX server.

### Chain Quorum 2.0

The observer compares block-header evidence at shared, stable heights instead of
assuming that different server tips are directly comparable. Challenge heights
include deterministic offsets plus nonce-derived random challenges. Header
hashes are computed with Ravencoin/KAWPOW semantics locally from returned header
bytes.

Quorum is counted over independent attested operator identities, not endpoint
count. One operator running many hostnames remains one operator for quorum.
Unknown or self-signed identities do not inflate trusted diversity.

Absence of a detected conflict is not treated as agreement. Positive comparable
chain evidence is required for SAFE promotion, and repeated independent
observations are required before a suspected conflict becomes a confirmed chain
conflict.

### Signed multi-vantage observations

Independent observer instances can sign observation bundles with local Ed25519
keys. Sequence high-water marks, expiry, clock-skew bounds, and domain separation
protect the observation channel from straightforward replay or rollback.

An aggregator compares only observers whose public keys it was explicitly
configured to trust. A self-signed observation authenticates who produced the
bundle; it does not make the endpoint claim true.

Cross-vantage comparison can surface DNS, TLS, backend identity, chain, or data
variance that may indicate selective serving. Variance is reported
conservatively rather than automatically adjudicated as malicious behavior.

### Operator identity

Operators can publish signed declarations with sequence and validity bounds.
Registry-attested identities can count toward independent quorum. Self-signed
identities verify cryptographically but do not, by themselves, create trusted
operator diversity.

### Asset capability probes and Asset Data Quorum

The observer actively probes selected bounded asset RPCs instead of trusting a
remote `"assets": true` claim. It classifies method support and can compare
canonical, confirmed asset state at a shared height across independent attested
operators.

Asset samples exclude mempool state, are height-bound, canonicalized by data
type, and compared only when their chain context is comparable. A first mismatch
is suspicious evidence; repeated comparable mismatch is required before an
asset-data conflict is confirmed.

Asset-data conflict is intentionally a separate failure domain from Ravencoin
chain conflict. A server can be unsuitable for asset wallets without that
observation being reinterpreted as proof of a consensus fork.

### Network-safety controls

Crawler and probe inputs are bounded. Hostname normalization, DNS answer
filtering, refusal of private/loopback/link-local/metadata destinations,
response-size limits, per-host budgets, bounded concurrency, and conservative
`.onion` handling reduce SSRF, rebinding, and amplification risk.

Detailed design: [Network Observer](network-observer.md).

## 14. Governance and succession framework

The fork includes a tested N-of-M governance and succession library with
separate domains for ElectrumX release governance and safe-Core policy
governance. It supports threshold verification, epoch transitions, revocation,
anti-rollback, and explicit successor adoption.

Production threshold governance is **not currently activated** because the
active roots have not yet been distributed to an independent maintainer quorum.
The correct project status is therefore **founder-independence capable**, not
founder-independent.

Network Observer output, endpoint popularity, repository redirects, or server
majority can never authorize a release or rotate a trust root.

Detailed design: [Governance and succession](GOVERNANCE_AND_SUCCESSION.md).

## 15. Compatibility guarantees

The hardening layers are designed around the existing wallet-serving contract.
In particular:

- the legacy Electrum protocol remains available;
- `server.ravencoin_backend` remains a stable public contract;
- Network Observer is outside the server request path;
- governance is outside the wallet protocol;
- the server still holds no wallet keys; and
- Ravencoin consensus remains external to ElectrumX and is validated by Core.

Protocol reference: [Electrum protocol](protocol.rst),
[RPC interface](rpc-interface.rst).

## 16. What the fork deliberately does not claim

The project intentionally avoids collapsing different evidence domains into one
trust score.

It does **not** claim that:

- a version string proves the running Core binary;
- a signed release can redefine Ravencoin consensus;
- ChainStrap snapshot data is trusted chainstate;
- a signed observer report makes a remote server honest;
- endpoint majority equals consensus;
- uptime or low latency equals security;
- an ahead-of-quorum height proves agreement;
- a self-signed operator key creates independent quorum;
- Network Observer can authorize software or governance; or
- supported ARM64 code means every ARM board is physically qualified.

These boundaries are central to the fork's security model rather than caveats
added after the fact.
