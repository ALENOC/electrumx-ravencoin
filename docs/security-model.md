# Security model

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
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

## Two trust boundaries

Release certification establishes that software passed the profile. A server's
`server.ravencoin_backend` response is still self-reported evidence, not remote
binary attestation. Runtime eligibility additionally requires fresh backend
evidence, network/synchronization flags and independent chain validation.

Discovery, hostname, operator branding, directory labels and endpoint majority
are not trust proofs. Independent operator groups matter; multiple endpoints from
one operator do not become independent consensus.

## Fail closed

Missing, stale, malformed, contradictory or unavailable evidence rejects a
mainnet server. If policy distribution is unavailable, the client uses its last
verified cache or built-in baseline. It never treats an unknown future Core as
safe merely because its version is higher.

The same principle governs the optional monitor's SAFE promotion (see
[Electrum monitor](electrum-monitor.md)): SAFE requires positively verified
chain evidence for the specific endpoint being promoted. The absence of a
detected conflict is not evidence of safety; a configured reference with no
comparable evidence, and height alone, are not agreement. A suspected
conflict is fail-closed on its own and only escalates to a demotion once a
later, independent observation confirms it.

## What remains unchanged

This server has no wallet and does not handle wallet seeds or private keys.
Wallet cryptography, transaction signing and wallet formats are outside this
maintenance boundary.
