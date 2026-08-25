# Security model

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[v1.13.11 overview](release-1.13.11.md) ·
[Core certification](core-certification.md) · [Status](validation-status.md)

## Why a version number is not enough

A version such as `4.8.0` tells us which release label a program reports; it
does not prove which repository commit is running or whether that code handles
consensus edge cases safely. A future `4.9.0` could contain a regression, so it
is not accepted merely because the number is higher.

The beginner-friendly trust path is:

```text
official RavenProject release
  ↓
behavioural tests
  ↓
certification
  ↓
signed safe-Core policy
  ↓
fresh backend evidence
  ↓
independent chain validation
```

The signed policy answers whether a software identity passed release
certification. It does not prove that an unrelated remote server is actually
running that binary; the live server and chain checks still matter.

## Trust is identity-based

The historical 4.8.0 threshold is not a trust rule. A release is identified by
repository plus exact commit, then must pass behavioural certification and appear in a signed
safe-Core policy. Version and tag are metadata.

The only eligible Core source repository is `RavenProject/Ravencoin`, using the
official release channel:

`https://github.com/RavenProject/Ravencoin/releases`

A Core identity from any other repository, including historical third-party
mirrors, is not trusted even if it has the same version or commit contents.
Older signed policy artifacts remain verifiable as historical evidence, but the
current repository trust root prevents those identities from resolving as safe.

The policy is signed with a dedicated Ed25519 key and protected by expiry,
revocation and a persistent anti-rollback high-water mark. A valid newer policy
may add a certified release or revoke one; it cannot rehabilitate a built-in
refusal or introduce a signer.

## Runtime evidence is a separate boundary

Release certification establishes that software passed the profile. A server's
`server.ravencoin_backend` response is still self-reported evidence, not remote
binary attestation. Runtime eligibility additionally requires fresh backend
evidence, network/synchronization flags and independent chain validation.

Discovery, hostname, operator branding, directory labels and endpoint majority
are not trust proofs. Independent operator groups matter; multiple endpoints from
one operator do not become independent consensus.

## Release and update trust

ElectrumX release signing is separate from safe-Core policy signing. Manifest
schema v2 binds all of the following into one authenticated release identity:

- semantic version and artifact revision;
- bundle and provenance digests;
- exact Ravencoin Core repository, tag, version, and commit;
- safe-Core policy version and certification report digest; and
- release timestamp and signing key identity.

The production update private key is offline and unavailable to GitHub Actions.
CI creates unsigned candidates only. The current production public key ID is
`6f4f944c9b0a19a1`; the historical schema-v1 key is retired and is rejected by
production updater trust loading.

Host-wide high-water state prevents a lower semantic version or artifact
revision from being accepted after a newer identity has been promoted. The same
version and revision with different artifact or provenance digests is refused
as equivocation. The state lives outside the installation tree, so choosing a
new directory does not reset rollback protection.

The updater proves storage and rollback preconditions before switching. It
preserves the explicit persistent-state allowlist and ownership, performs the
switch transactionally, runs health gates, and restores the exact previous
release after a failed candidate. It does not start a partially switched stack.

See [release artifact revisions](release-artifact-revisions.md).

## Bootstrap trust

ChainStrap supplies transport bytes, not chain authority. Only allowlisted raw
block files with verified manifest identity may enter staging. Unsafe archive
members and malformed or incomplete block sequences fail closed. Downloaded
chainstate, indexes, configuration, and wallets are never installed. The pinned
Ravencoin Core performs a full local reindex before ElectrumX can use the data.

See [Fast Verified Bootstrap](fast-bootstrap.md).

## Observation and governance are not authority shortcuts

The optional Network Observer gathers endpoint evidence through bounded probes,
shared-height challenges, signed observation bundles, operator-aware quorum,
and height-bound asset comparisons. Its signatures authenticate observations;
they do not make those observations Ravencoin consensus or software-release
authority.

Release governance and safe-Core governance use separate domains. The tested
N-of-M succession framework cannot be activated by an observer snapshot,
endpoint majority, repository redirect, or popularity metric. Production roots
have not yet been distributed to an independent maintainer quorum, so the
project is founder-independence capable rather than founder-independent.

See [Network Observer](network-observer.md) and
[governance and succession](GOVERNANCE_AND_SUCCESSION.md).

## Fail closed

Missing, stale, malformed, contradictory or unavailable evidence rejects a
mainnet server. If policy distribution is unavailable, the client uses its last
verified cache or built-in baseline. It never treats an unknown future Core as
safe merely because its version is higher.

The same principle governs Network Observer SAFE promotion (see
[Network Observer](network-observer.md)): SAFE requires positively verified
chain evidence for the specific endpoint being promoted. The absence of a
detected conflict is not evidence of safety; a configured reference with no
comparable evidence, and height alone, are not agreement. A suspected
conflict is fail-closed on its own and only escalates to a demotion once a
later, independent observation confirms it.

## What remains unchanged

This server has no wallet and does not handle wallet seeds or private keys.
Wallet cryptography, transaction signing and wallet formats are outside this
maintenance boundary.

The legacy Electrum protocol and public `server.ravencoin_backend` contract are
unchanged by Network Observer and governance code. The serving process imports
neither subsystem.
