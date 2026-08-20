# Core certification

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md) · [Validation status](validation-status.md)

## Pipeline

The watcher observes only official `RavenProject/Ravencoin` releases from:

`https://github.com/RavenProject/Ravencoin/releases`

Repository membership grants permission to test, never permission to trust:

```text
candidate -> exact commit -> reproducible build -> behavioural tests
          -> PASS / FAIL / REVIEW_REQUIRED -> signed safe-Core policy
```

`BUILD_FAILED` means the candidate did not build. `CERTIFICATION_FAILED` means
candidate behavior violated a mandatory property. `REVIEW_REQUIRED` means a
mandatory property was inconclusive. None of these states becomes safe by
version comparison.

## Certified baseline and source-policy migration

The historical v4.8.0 release certification has 12 mandatory release tests: 12
PASS, no FAIL, no REVIEW_REQUIRED and no mandatory skips. Its exact report and
signed policy remain under `core-safety/production/` as historical evidence.

The source policy is now profile revision 2 with digest
`8606d330e917414d75bfd0225804faa1ca3a3593f6886e0ac5347fc7444ebd40`.
It permits only `RavenProject/Ravencoin` as a candidate source. Historical
third-party repository identities are not eligible for trust or future
promotion.

Policy v1 and v2 are retained as immutable signed historical evidence. They must
not be edited in place because doing so would invalidate their Ed25519
signatures. A newer signed policy revokes the historical third-party identity.
The detached signature and public key are published beside the current policy;
private signing keys remain outside Git.

## Release versus live validation

Release certification is bounded, deterministic and does not require a full
mainnet sync, ElectrumX database or production wallet. Live node validation is
separate: it checks the actual canonical chain, checkpoint presence,
`transfer_overflow` activation, txindex, assetindex, REST, asset RPC, ElectrumX
historical index, backend evidence and client `SAFE_CORE_VERIFIED`.

The release evidence must not be described as a complete HTTP REST test against
a synchronized deployment. Whether the real Core REST endpoint serves blocks
correctly is proved by the live-node gate.

The release PASS must not be read as a deployment PASS.

## Architecture artifact qualification

Between release certification and live deployment sits a third, distinct
check: whether the certified source actually builds and starts correctly on a
given CPU architecture. This is artifact qualification, and it is neither the
release certification above nor the live-node validation in [Validation
status](validation-status.md#live-deployment).

Artifact qualification builds the exact official candidate commit for the
target architecture (from the published release binary on amd64, compiled from
the official source archive on ARM64), then runs it in an isolated,
wallet-disabled regtest container to check startup, RPC, real REST
(`/rest/block/<hash>.bin`), txindex, graceful shutdown and container restart.
Because the qualification environment has no wallet, it cannot legitimately
exercise asset RPC or asset-index behavior; those checks are recorded as
`LIVE-ONLY` rather than `PASS`, and are only proven later against the
synchronized mainnet deployment. Current per-architecture results are in
[Validation status](validation-status.md#architecture-artifact-qualification).

Artifact qualification does not modify, extend or replace release
certification.

## How a candidate becomes known-safe

The watcher polls one permitted source:

- [RavenProject/Ravencoin releases](https://github.com/RavenProject/Ravencoin/releases)

A tag is resolved to an immutable commit before any result is recorded. The
candidate identity is repository plus resolved commit; tag and version are
metadata. A repository name or semantic version never grants trust. The build
environment is isolated from the production node and uses bounded fixtures, so
a full mainnet synchronization is not part of release certification.

The behavioural profile includes positive and negative controls for the
consensus properties it claims to test. A positive fixture must be accepted by
the exact candidate; a deliberately invalid or pre-fix control must be rejected.
Missing fixtures, unavailable candidate paths and skipped mandatory tests are
`REVIEW_REQUIRED`, not PASS. A candidate that demonstrates a mandatory unsafe
behavior is `CERTIFICATION_FAILED`; a build that cannot complete is
`BUILD_FAILED`.

## Evidence and policy

The certification report records the source identity, tag object, build
environment, profile revision and digest, every mandatory test, fixture or
negative control, result and report digest. Only a complete PASS from the
allowed RavenProject source can be proposed for a signed safe-Core policy.

The signing job is separate from candidate building and holds the policy key
only in its protected environment. Before signing anything it independently
checks that every downloaded report's candidate identity was produced by that
same run's discovery step, so a report or artifact cannot be substituted between
jobs.

The CI `certify` job runs the harness with no candidate binaries or probe target
of its own, so every core-scope test currently returns `UNAVAILABLE` and the job
cannot itself produce `CERTIFICATION_PASSED`. A production RavenProject identity
must therefore have real certification evidence before it is added as
`KNOWN_SAFE`; the historical third-party report is not silently relabelled.

## Revocation and future releases

A later policy can revoke a previously certified identity. A persistent
anti-rollback floor prevents replaying an older signed policy after a newer one
has been accepted. Releases from repositories other than
`RavenProject/Ravencoin` cannot be promoted, and a hypothetical future official
release remains unreviewed until its exact commit passes the profile and appears
in a valid newer policy.
