# Core certification

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md) · [Validation status](validation-status.md)

## Pipeline

The watcher observes both `2miners/Ravencoin` and `RavenProject/Ravencoin`.
Repository names grant permission to test, never permission to trust:

```text
candidate -> exact commit -> reproducible build -> behavioural tests
          -> PASS / FAIL / REVIEW_REQUIRED -> signed safe-Core policy
```

`BUILD_FAILED` means the candidate did not build. `CERTIFICATION_FAILED` means
candidate behavior violated a mandatory property. `REVIEW_REQUIRED` means a
mandatory property was inconclusive. None of these states becomes safe by
version comparison.

## Certified baseline

The current release certification has 12 mandatory release tests: 12 PASS, no
FAIL, no REVIEW_REQUIRED and no mandatory skips. The exact report and signed
policy are under `core-safety/production/`. Profile revision 1 has digest
`1342d079f2eef7ae0803a247d2908c4b031ee4a542b0f837210f92ba36ae27b2`.

Policy v1 is retained as historical evidence. Policy v2 includes the immutable
profile metadata. The detached signature and public key are published beside
the current policy; private signing keys remain outside Git.

## Release versus live validation

Release certification is bounded, deterministic and does not require a full
mainnet sync, ElectrumX database or production wallet. Live node validation is
separate: it checks the actual canonical chain, checkpoint presence,
`transfer_overflow` activation, txindex, assetindex, REST, asset RPC, ElectrumX
historical index, backend evidence and client `SAFE_CORE_VERIFIED`.

The release PASS must not be read as a deployment PASS.

## How a candidate becomes known-safe

The watcher polls the two permitted source repositories:

- [2miners/Ravencoin](https://github.com/2miners/Ravencoin)
- [RavenProject/Ravencoin](https://github.com/RavenProject/Ravencoin)

A tag is resolved to an immutable commit before any result is recorded. The
candidate identity is the repository, tag, version and resolved commit; a
repository name or semantic version never grants trust. The build environment
is isolated from the production node and uses bounded fixtures, so a full
mainnet synchronization is not part of release certification.

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
negative control, result and report digest. Only a complete PASS can be
proposed for a signed safe-Core policy. The signing job is separate from
candidate building and holds the policy key only in its protected environment.

The current certified baseline is `2miners/Ravencoin` `v4.8.0` at commit
`b60f50e04f1fba425b28804e61be2694faaf3469`. Its 12 mandatory release tests
passed. Exact evidence and policy artifacts are kept under
`core-safety/production/`; do not copy private signing keys there.

## Revocation and future releases

A later policy can revoke a previously certified identity. A persistent
anti-rollback floor prevents replaying an older signed policy after a newer
one has been accepted. A hypothetical `4.9.0` remains unreviewed until its own
repository and commit pass the profile and appear in a valid newer policy.
