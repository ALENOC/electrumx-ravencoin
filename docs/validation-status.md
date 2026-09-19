# Validation status

This is the current human-readable status source. It separates source
certification, release-artifact verification, real-node qualification, and
claims that remain outside the available evidence.

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[v1.13.11 overview](release-1.13.11.md) ·
[Core certification](core-certification.md) ·
[Hardware evidence](HARDWARE_QUALIFICATION_1.13.11.md)

## Current production release

| Item | Current identity or result |
|---|---|
| ElectrumX-RVN | **1.13.11**, artifact revision `0` |
| Release source/tag | `v1.13.11` at `152b5134b849a31b2fdd9ef9efe643683a5bcb5c` |
| Bundle SHA-256 | `d3b81d1f7e3a0d096c5a41b64285fd9a7afdc9fa3807cebb5f86a37df973f5d4` |
| Installer SHA-256 | `2cc3d87e8f2db98dd7ede3ee4d39261ad4943a2871cc28e3b80288c19e7601ee` |
| Provenance SHA-256 | `fed02b1a993cac1c4591e0cfd5c15dda07f78c8a33e5512ae7503c8a689f7130` |
| Manifest signing key ID | `6f4f944c9b0a19a1` (offline production key) |
| Hardware qualification | **PASS** on the published artifact |

The Git tag, manifest, provenance, and release assets are distinct pieces of
evidence. The signed manifest binds the executable artifact and provenance
digests; a tag name alone is not an installable identity.

## Ravencoin Core identity

The current release pins:

```text
repository: RavenProject/Ravencoin
version:    4.8.0
tag:        v4.8.0
commit:     22549129888d02e0e08fcdb9f96f3c699167e774
```

This exact identity has persisted certification evidence under
`core-safety/production/certifications/` and is `KNOWN_SAFE` in the signed
RavenProject-only safe-Core policy v3. Historical third-party or tree-equivalent
identities are not substitutes for the official repository and commit.

The installer verifies the safe-Core policy through a key pinned independently
from the ElectrumX release/update key. Repository membership and a higher
version number grant permission to evaluate a candidate, never automatic
trust.

## Release and updater trust

Production release candidates are built unsigned in CI. The release/update
private key remains offline and is not available to GitHub Actions. Offline
signing re-verifies all artifact and provenance bytes before producing the
manifest signature.

The historical schema-v1 update key is retained in source only as immutable
evidence and is explicitly rejected for production updater use. Source-checkout
installation does not silently enable the signed-release updater; current-key
provisioning is required and trust loading fails closed otherwise.

Manifest v2 identity, semantic version ordering, artifact revisions,
host-wide anti-rollback state, and transactional update/rollback are covered in
[release artifact revisions](release-artifact-revisions.md).

## CI and regression status

The final v1.13.11 integration passed all exact-head required checks:

- full pytest matrices on Python 3.10, 3.11, and 3.12;
- static security checks and protected-path enforcement;
- Compose validation and multi-architecture ElectrumX container builds; and
- bundled Core artifact build/qualification for amd64 and arm64.

The final local audit totals were 1,131 passed, 15 skipped, and 2 warnings.
Focused trust-root/updater/installer coverage passed 93 tests; the broader
updater/installer/release selection passed 217 tests.

Regression coverage includes the legacy Electrum protocol, the complete
`server.ravencoin_backend` contract, release and policy signatures,
anti-rollback, transactional recovery, persistent ownership, ChainStrap archive
bounds, Network Observer SSRF/DNS controls, operator-aware quorum, signed
observations, anti-replay, asset RPC correlation, persistent security
high-water state, and governance domain separation.

## Real-node qualification

The published v1.13.11 artifact was qualified on a Raspberry Pi 5 qualification
node through the ordinary signed update path from v1.13.10.

Observed PASS evidence includes:

- candidate signature, artifact, provenance, eligibility, and anti-rollback;
- transactional promotion with `pendingCandidate = null` and
  `failureReason = null`;
- preserved Ravencoin chain data, ElectrumX database, named-volume identities,
  TLS overlay, secrets ownership, Node Monitor, and controller state;
- Ravencoin Core 4.8.0 and ElectrumX-RVN 1.13.11 healthy and synchronized with
  zero container restarts;
- externally verified TLS, `server.version`, `server.ravencoin_backend`, and
  live asset RPC behavior; and
- installed `network_observer` package, no stale `monitor` package, bounded
  discovery, SSRF filtering, and a valid shared-height Chain Quorum round over
  real public endpoints.

The detailed timestamps, digests, checkpoints, and observations are in
[v1.13.11 hardware qualification](HARDWARE_QUALIFICATION_1.13.11.md).

## Architecture evidence

The same exact Core source identity is built and checked for amd64 and ARM64 in
CI. The Raspberry Pi 5 qualification additionally proves the published ARM64
deployment and update path on real hardware. It does not imply that every
ARM64 board, kernel, storage device, or network environment is qualified.

Orange Pi 5-class systems remain supported build targets, but have not received
the same complete physical qualification recorded for the Raspberry Pi 5.
Existing-Core mode remains available, but the operator must independently
establish the identity and configuration of that Core deployment.

## Network Observer and governance status

Network Observer Phase 1 is implemented and tested. Its signed bundles
authenticate observation provenance, not Ravencoin consensus. Operator and
observer diversity remain separate, and a self-signed identity does not count
as an independently attested operator.

The governance framework is implemented and tested, including N-of-M policies,
rotation, revocation, anti-rollback, domain separation, and explicit successor
adoption. Current production roots are still single-maintainer roots. The
precise status is:

> **FOUNDER-INDEPENDENCE CAPABLE — PRODUCTION THRESHOLD ACTIVATION PENDING A
> FUTURE SIGNING CEREMONY**

Network Observer output cannot authorize releases, rotate keys, lower a
threshold, or adopt a successor.

## Scope limits

Certification and one qualified node do not prove every future Core release,
third-party image, public endpoint, hardware combination, or operator
configuration. New software identities require fresh certification and signed
policy authorization. A public endpoint also needs its own DNS, TLS, firewall,
reachability, and ongoing operational checks.
