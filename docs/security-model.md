# Security model

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Core certification](core-certification.md) · [Status](validation-status.md)

## Trust is identity-based

The historical 4.8.0 threshold is not a trust rule. A release is identified by
repository plus exact commit, then must pass behavioural certification and appear in a signed
safe-Core policy. Version and tag are metadata. The initial certified identity
is `2miners/Ravencoin` `v4.8.0` at
`b60f50e04f1fba425b28804e61be2694faaf3469`.

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

## What remains unchanged

This server has no wallet and does not handle wallet seeds or private keys.
Wallet cryptography, transaction signing and wallet formats are outside this
maintenance boundary.
