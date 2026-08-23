# ElectrumX-RVN 1.13.5 hardware qualification

## RESULT: PASS

`1.13.4 -> 1.13.5 artifact_revision 1`, performed on ARM64 hardware with the
normal updater (`electrumx-update`), on an installation adopted from legacy
1.13.1 and therefore carrying `storageMode: named-volumes`.

The qualified artifacts are the ones currently published on the `v1.13.5`
release. Their identity is established by the signed manifest and provenance
digests, not by the git tag. See "Artifact identity of the qualified revision".

`artifact_revision 0` FAILED this qualification and was withdrawn. Its failure,
root cause and fix are recorded below and must not be removed: the revision-0
history is the reason revision 1 exists.

## Scope

Two upgrade paths are in scope for 1.13.5.

1. The one-time legacy path from a healthy ElectrumX-RVN 1.13.1 /
   Ravencoin Core 4.8.0 node to the signed 1.13.5 release, using
   `legacy_1_13_1_apply.py`. This is an adoption step, performed once.
2. The ordinary post-adoption path from a healthy ElectrumX-RVN 1.13.4
   installation to 1.13.5. This path MUST use the normal updater and MUST NOT
   invoke `legacy_1_13_1_apply.py` or request `ADOPT LEGACY 1.13.1`, even when
   the installation was originally adopted from 1.13.1.

Path 2 is the one that failed on revision 0 and passed on revision 1.

The ordinary 1.13.4 -> 1.13.5 qualification specifically proves that:

- `storageMode: named-volumes` persists after the one-time legacy adoption and
  is honoured natively by the normal updater;
- the same Docker named-volume objects remain attached;
- release-owned Compose overlays selected through `.env`, especially
  `compose.tls.yaml`, survive promotion and rollback;
- `compose.chainstrap.yaml` is never reactivated;
- public ElectrumX port 50002 remains published;
- a TLS handshake to `electrumx.raventag.com:50002` succeeds;
- `server.version` returns `ElectrumX-RVN 1.13.5`;
- the external bandwidth controller is suspended during the transaction and
  resumed only after a successful promotion or exact rollback.

This release also closes the failure discovered with the withdrawn 1.13.3
candidate: the external `ravencoin-bandwidth-controller.service` reconciled
persisted `MAX_SESSIONS=800` by issuing its own `docker compose up` while the
updater was simultaneously recreating ElectrumX. Docker Compose then observed a
container ID that the controller had already replaced and failed with
`No such container`; the same race also interfered with rollback.

## Required identities

- source version for the qualified path: `1.13.4`
- source version for the one-time legacy path: `1.13.1`
- candidate version: `1.13.5`
- withdrawn candidate artifact revision: `0`
- qualified and published artifact revision: `1`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key: `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity of the qualified revision

- `artifact_revision`: `1`
- manifest `releaseTimestamp`: `2026-08-23T11:44:47Z`
- `artifactDigest`: `sha256:7fe4c8cb89c73d033ad7be93bd59986364ed5663df832c724b5881dd69d5c241`
- `provenanceDigest`: `sha256:6d1f932508a6cb4e4d3d72decf3b0ae4af858bee9ee86f17d0f581ed989f3867`
- provenance `sourceCommit`: `5b38d0119ae7233b7b3dac7f4a1e2c860fde8f76`

The git tag `v1.13.5` points at `00776bdb0ab2f0581cdc677da61e137df13fdb3e`,
which is the revision-0 release-preparation commit. The tag was deliberately
not moved after the revision-1 rebuild. A tree checked out at `v1.13.5`
therefore does not contain the revision-1 updater fix. Anyone auditing the
published artifacts must use the provenance `sourceCommit` above, and must
verify the artifact and provenance digests against the signed manifest. The tag
is a historical trace, not a release identity.

## Pre-mutation gates

1. Published release bytes must verify against `SHA256SUMS`.
2. `electrumx-update check` must record 1.13.5 as `ELIGIBLE` and `VERIFIED`.
3. For the legacy path, discovery must prove the exact existing named-volume
   identities and a healthy 1.13.1 / Core 4.8.0 runtime.
4. `COMPOSE_FILE` must contain only Compose files shipped by the candidate
   release.
5. No ChainStrap action is allowed on either upgrade path.

## External mutator regression gate

Before the updater stops or recreates any Docker service, an active host-side
bandwidth controller must be suspended. Evidence must include:

```text
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
```

While the updater is in stop/switch/start/health or rollback, the controller
must remain inactive and its journal must contain no
`reapplied electrumx connection limit` event.

After successful promotion, or after an exact rollback, the controller must be
restored only if it was active before the transaction. Evidence must include:

```text
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
```

If rollback is indeterminate, the controller must remain suspended and the
updater must require operator intervention rather than allowing an independent
reconciler to mutate an ambiguous stack.

## PASS criteria

A PASS requires all of the following:

- updater state before apply identifies the previous release as
  `currentRelease`;
- `electrumx-update check` records 1.13.5 as `ELIGIBLE` and `VERIFIED`;
- on the ordinary path, apply is performed through the normal updater only and
  no `ADOPT LEGACY` prompt appears;
- running ElectrumX reports `ElectrumX-RVN 1.13.5`;
- Core remains version 4.8.0 and the exact certified source commit;
- ElectrumX DB height equals daemon/Core height;
- the same Docker named-volume objects remain attached at the same
  destinations;
- the install marker identifies 1.13.5 and retains
  `storageMode: named-volumes` for the adopted legacy node;
- Docker Compose labels still include `compose.tls.yaml`;
- `compose.chainstrap.yaml` is not reactivated;
- Docker still publishes `50002/tcp` on the host;
- external TLS verification succeeds after promotion;
- Electrum protocol `server.version` succeeds after promotion;
- updater state records 1.13.5 as current and clears the pending candidate;
- host-wide artifact high-water is created/advanced only after promotion;
- Node Monitor remains healthy and external;
- after the updater has completed and the controller is resumed, the persisted
  connection limit is reconciled back to `MAX_SESSIONS=800` without racing the
  updater;
- no `No such container` error occurs during promotion or rollback paths.

## Rollback regression

Also execute a controlled failing-health test with the external controller
active before apply. The updater must suspend it, restore the exact old release
and named volumes, then resume the controller only after rollback is complete.
A failed rollback must leave the controller stopped.

## artifact_revision 0: failed qualification

Revision 0 was built, signed and uploaded to the `v1.13.5` release, then failed
hardware qualification of the ordinary 1.13.4 -> 1.13.5 path on an installation
adopted from legacy 1.13.1. It was withdrawn and its assets were replaced in
place by the revision-1 assets. Revision 0 was never a qualified release and
must not be installed, targeted or trusted.

### Failure mode

Preflight passed. The transaction suspended the controller, switched the
release directory, and then failed while starting the candidate stack with:

```text
existing installer volume electrumx-ravencoin_ravencoin-data is not local bind-backed storage
```

Rollback reached the identical proof and emitted the same message, so the
transaction could not prove an exact restore and ended at
`STUCK_NO_BLIND_ROLLBACK`, leaving the controller suspended and requiring
operator intervention. The fail-closed behaviour was correct; the proof it
failed on was wrong for this storage model.

### Root cause

The updater has two persistent storage models: modern bind-backed storage
(`compose.storage.yaml` plus bind `driver_opts`) and plain Docker named volumes
inherited by legacy adoption (`storageMode: named-volumes`, no driver options
at all). Only the preflight proof branched on the marker storage mode. The
three post-release-switch proofs, in candidate start, the health gate and
rollback, called the bind-backed volume primitive directly. `docker volume
inspect` reports a plain Docker-managed volume with `"Options": null`, which
that primitive rejects by design.

The defect was invisible to CI because the legacy wrapper installed
process-local compatibility hooks over that primitive, and one test leaked those
hooks into the rest of the pytest process, so later tests exercised the hooks
instead of the native updater code.

### Fix

Commit `5b38d0119ae7233b7b3dac7f4a1e2c860fde8f76`.

The storage model is now read once from the verified install marker and carried
through the whole transaction, including rollback. A single mode-aware
dispatcher proves the Docker volume objects for the installation's own storage
model, and every post-switch phase goes through it. Named-volume installations
keep a strong continuity proof: the volume objects must exist, keep their exact
project-bound identities, remain plain local volumes with no driver options, and
remain attached at the same destinations. A named volume that has acquired bind
`driver_opts` is rejected rather than silently accepted.

The normal updater no longer depends on any process-local hook from
`legacy_1_13_1_apply.py`; the persistent marker state is sufficient. The leaking
test now restores the hooks at teardown, and `tests/test_update_named_volume_runtime.py`
reproduces the hardware failure at all three post-switch sites.

### Revision scope note

`docs/release-artifact-revisions.md` freezes executable behaviour between
revisions of the same version. That rule protects operators of an artifact that
was actually qualified and published as installable. Revision 0 never reached
that state: it failed qualification and was withdrawn, so there was no qualified
revision-0 baseline whose behaviour could be preserved. Revision 1 accordingly
changes updater code inside the bundle and is not scope-preserving against
revision 0. The offline scope verifier must not be used to compare these two
revisions, and no operator should have been running revision 0.

## Publication decision

1.13.5 `artifact_revision 1` is qualified for publication and is the artifact
published on the `v1.13.5` release.

Do not reuse or alter the withdrawn 1.13.3 candidate bytes or tag, and do not
republish `artifact_revision 0`.
