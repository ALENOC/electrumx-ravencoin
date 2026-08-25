# Core certification

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md) · [Validation status](validation-status.md)

## Pipeline

The watcher considers only official `RavenProject/Ravencoin` releases from
GitHub. Repository membership grants permission to test, never permission to
trust:

```text
candidate
  -> exact repository + commit
  -> reproducible/controlled candidate build
  -> behavioural profile + mandatory probes
  -> PASS / FAIL / REVIEW_REQUIRED
  -> reviewed policy proposal
  -> protected policy signing
```

`BUILD_FAILED`, `CERTIFICATION_FAILED` and `REVIEW_REQUIRED` never become safe
because of a semantic version number. A release is eligible only after its
exact repository/commit identity appears as `KNOWN_SAFE` in a valid signed
policy.

## Current v4.8.0 identity

The current v1.13.11 release line pins the official RavenProject v4.8.0 tag
target:

```text
repository: RavenProject/Ravencoin
version:    4.8.0
tag:        v4.8.0
commit:     22549129888d02e0e08fcdb9f96f3c699167e774
```

The exact candidate has persisted certification evidence under
`core-safety/production/certifications/` and the reviewed policy candidate
`safe-core-policy-v3.unsigned.json` records it as `KNOWN_SAFE` with passing
certification evidence.

Repository + commit is the identity key. The historical commit
`b60f50e04f1fba425b28804e61be2694faaf3469`, although associated with the same
incident-era v4.8.0 work and previously validated in this project, is not the
official tag target and must not be substituted for `225491...` merely because
the trees were observed to be equivalent.

## Historical signed v2 and current signed v3

Policy v1 and v2 are immutable historical signed evidence. The currently
tracked `core-safety/production/safe-core-policy.json` is signed policy v3 and
is the RavenProject-only production policy for this release line.

The reviewed `safe-core-policy-v3.unsigned.json` candidate performs one atomic
transition:

- `2miners/Ravencoin@b60f50...` -> `REVOKED`;
- `RavenProject/Ravencoin@225491...` -> `KNOWN_SAFE`.

The unsigned file remains review evidence, not a trust anchor. The one-shot
protected migration regenerated it from signed v2 plus the persisted
RavenProject certification report, compared it with the reviewed candidate,
signed v3 inside `core-safety-signing`, verified it with the published public
key, and published the signed artifacts. The workflow removed itself after
that atomic promotion.

This preserves both audit history and the RavenProject-only trust boundary.

## What the certification profile proves

Release certification is bounded and deterministic. It records:

- exact source identity and tag resolution;
- profile revision and digest;
- build/candidate evidence;
- mandatory consensus/security probes;
- positive and negative controls;
- per-test outcomes;
- a certification report digest bound into policy/release metadata.

A missing mandatory probe, unavailable required candidate path or inconclusive
result is never upgraded to PASS.

The profile is not a substitute for a synchronized production node. Mainnet
sync, live REST/index behavior, ElectrumX indexing, live backend evidence and
operator networking belong to separate deployment gates.

## Architecture artifact qualification

Artifact qualification sits between source certification and live deployment.
It checks whether the exact pinned source/artifact can build and start on a
supported architecture and runs the architecture-appropriate startup/RPC/REST/
txindex/restart checks.

On amd64 the deployment can use the pinned official release artifact. On ARM64
the Docker build compiles the exact pinned RavenProject source because an
identical prebuilt artifact is not assumed. ARM64 build/startup evidence is not
the same claim as full incident-specific consensus qualification unless those
mandatory probes were actually run on that artifact.

See [Validation status](validation-status.md) for current release, CI, and
real-hardware evidence, and for the limits of claims that can be transferred
between architectures or exact Core identities.

## Independent installer enforcement

The single-file installer does not trust a Core merely because `compose.yaml`
names RavenProject. It independently verifies the signed safe-Core policy under
a policy public key pinned in the bootstrap itself, separate from the ElectrumX
release/update key.

For the Core named by the release manifest, the installer requires one unique
policy entry whose:

- repository is `RavenProject/Ravencoin`;
- commit matches the manifest exactly;
- status is `KNOWN_SAFE`;
- version and tag match;
- certification result is `PASS`;
- `reportDigest` matches `certificationReportDigest` in the release manifest;
- policy version matches `safeCorePolicyVersion` in the release manifest.

The bundled copy of the Core-policy public key must also equal the independently
pinned bootstrap key. A release signer therefore cannot replace both a Core
policy and its verification key inside the same bundle.

## Revocation and future releases

A later signed policy can revoke a previously certified identity. Persistent
anti-rollback state prevents an already accepted newer policy from being
silently replaced by an older one. New RavenProject releases remain unreviewed
until their exact commits complete the certification pipeline and appear in a
valid newer policy.

Repositories other than `RavenProject/Ravencoin` are not eligible for new
`KNOWN_SAFE` promotion. Historical third-party identities may remain in policy
history only in non-trusted/revoked form where needed for an auditable
transition.
