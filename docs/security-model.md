# Security model

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Fork features](fork-features.md) · [Current release](release-1.13.11.md) ·
[Core certification](core-certification.md) · [Status](validation-status.md)

## Why a version number is not enough

A version such as `4.8.0` tells us which release label a program reports; it
does not prove which repository commit is running or whether that code handles
consensus edge cases safely. A future `4.9.0` could contain a regression, so it
is not accepted merely because the number is higher.

The maintained trust path is:

```text
official RavenProject release
  ↓
behavioral tests
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
running that binary; live server and chain checks still matter.

## Trust is identity-based

The historical 4.8.0 threshold is not a trust rule. A release is identified by
repository plus exact commit, then must pass behavioral certification and
appear in a signed safe-Core policy. Version and tag are metadata.

The only eligible Core source repository is `RavenProject/Ravencoin`, using the
official release channel. A Core identity from another repository is not
trusted merely because it reports the same version or even equivalent contents.
Older signed policy artifacts remain verifiable as historical evidence, but the
current repository trust root prevents those identities from resolving as safe.

The policy is signed with a dedicated Ed25519 key and protected by expiry,
revocation, and a persistent anti-rollback high-water mark. A valid newer policy
may add a certified release or revoke one; it cannot bypass a built-in refusal
or introduce an unrelated signer.

## Endpoint trust ladder

Remote Electrum endpoints move through distinct states. These states must never
be collapsed into one score:

1. **DISCOVERED** — a seed, signed directory, registry, manual input, or peer
   gossip named an endpoint. This is only a candidate.
2. **CAPABILITY_SUPPORTED** — the endpoint answered methods such as
   `server.ravencoin_backend`. This proves only that it can answer the method.
3. **BACKEND_VERIFIED** — its claimed Core repository/commit matches signed
   safe-Core policy and comparable independent chain evidence agrees.
4. **TRUSTED_BY_OPERATOR** — a local operator explicitly configured or accepted
   the backend identity for their own deployment or trust set.

Important consequences:

- seed-list membership is not trust;
- answering `server.ravencoin_backend` is not trust;
- reporting Core >= 4.8.0 is not build-identity verification;
- a server strictly ahead of the corroborated comparison height is not verified
  merely because it is ahead; and
- endpoint majority is never substituted for independent operator evidence.

`server.ravencoin_backend` is therefore a stable evidence interface, not remote
binary attestation. A remote endpoint controls its own reply and can lie.

## Why the project does not add signed per-peer capability claims

A peer signing its own capability or backend claim authenticates who made the
claim, not whether the claim is true. The security problem is comparison with
independent ground truth, not merely adding another signature around a
self-report.

The Network Observer therefore uses signed observation bundles, signed
operator declarations, signed directories, and signed policy where signatures
are useful for authenticity and replay resistance, while chain and asset claims
still require independent comparable evidence. This avoids creating a second
peer PKI that would add key-management surface without solving the underlying
verification problem.

## Runtime evidence is a separate boundary

Release certification establishes that software passed the profile. A server's
`server.ravencoin_backend` response is still self-reported evidence, not remote
binary attestation. Runtime eligibility additionally requires fresh backend
evidence, network/synchronization flags, and independent chain validation.

Discovery, hostname, operator branding, directory labels, and endpoint majority
are not trust proofs. Independent operator groups matter; multiple endpoints
from one operator do not become independent consensus.

## Release and update trust

ElectrumX release signing is separate from safe-Core policy signing. Manifest
schema v2 binds all of the following into one authenticated release identity:

- semantic version and artifact revision;
- bundle and provenance digests;
- exact Ravencoin Core repository, tag, version, and commit;
- safe-Core policy version and certification report digest; and
- release timestamp and signing-key identity.

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

See [Release identity and revisions](release-artifact-revisions.md).

## Bootstrap trust

ChainStrap supplies transport bytes, not chain authority. Only allowlisted raw
block files with verified manifest identity may enter staging. Unsafe archive
members and malformed or incomplete block sequences fail closed. Downloaded
chainstate, indexes, configuration, and wallets are never installed. The pinned
Ravencoin Core performs a full local reindex before ElectrumX can use the data.

A successful reindex process exit is necessary but not sufficient. The offline
post-reindex gate also verifies the expected snapshot height/hash and real asset
metadata/address-index reads before writing the bootstrap completion marker.

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
[Governance and succession](GOVERNANCE_AND_SUCCESSION.md).

## Fail closed

Missing, stale, malformed, contradictory, or unavailable evidence rejects a
mainnet server from a safety decision. If policy distribution is unavailable,
consumers use the last verified cache or built-in baseline where supported;
unknown future Core is never treated as safe merely because its version is
higher.

The same principle governs Network Observer SAFE promotion: SAFE requires
positively verified chain evidence for the specific endpoint being promoted.
The absence of a detected conflict is not evidence of safety; a configured
reference with no comparable evidence, and height alone, are not agreement. A
suspected conflict is fail-closed on its own and only escalates to a confirmed
demotion after the required independent confirmation.

## What remains unchanged

This server has no wallet and does not handle wallet seeds or private keys.
Wallet cryptography, transaction signing, and wallet formats are outside this
maintenance boundary.

The legacy Electrum protocol and public `server.ravencoin_backend` contract are
unchanged by Network Observer and governance code. The serving process imports
neither subsystem.
