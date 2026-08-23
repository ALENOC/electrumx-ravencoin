# ElectrumX-RVN 1.13.6 hardware qualification

## RESULT: PENDING

This document is the qualification plan for the 1.13.6 candidate. It becomes a
PASS only after every gate below is executed on real hardware and its evidence
is recorded here.

Publication of the candidate is part of the qualification, not a statement that
it passed. The installer resolves the release through
`releases/latest/download`, so the signed bytes must be published before any
fresh-install gate can run against them. While this file says PENDING the
release is a candidate under qualification: it is not announced as the
recommended release, and if it fails it is withdrawn and replaced in place, as
1.13.5 `artifact_revision 0` was.

## Why 1.13.6 exists

The published 1.13.5 installer creates the root-owned trusted controller
directory with:

```text
mkdir -p -o root -g root -m 0755 /usr/local/lib/electrumx-ravencoin
```

`mkdir` has no ownership flags, so a fresh installation that enables the
advanced host controller aborts at step 4/4 with:

```text
mkdir: invalid option -- 'o'
error: command failed with exit code 1: /usr/bin/sudo mkdir -p -o root -g root -m 0755 /usr/local/lib/electrumx-ravencoin
```

1.13.6 replaces that call with `install -d -o root -g root -m 0755`, which is
the coreutils command that actually accepts those flags.

This is a change of installer behaviour, so it cannot ship as another
`artifact_revision` of 1.13.5. `docs/release-artifact-revisions.md` freezes
executable behaviour between revisions of the same version, and 1.13.5
`artifact_revision 1` was qualified and published as installable. A behavioural
fix therefore requires this version bump.

The defect was invisible to CI because no test exercised the argv actually
handed to the privileged program. `tests/test_installer_controller_trust.py`
now captures that argv and rehearses it unprivileged against the real coreutils
binary, so an option the program does not accept fails the test suite instead of
failing on an operator's host.

## Required identities

- source version for the ordinary update path: `1.13.5`
- source version for the one-time legacy path: `1.13.1`
- candidate version: `1.13.6`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key: `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity of the qualified revision

- `artifact_revision`: `0`
- manifest `releaseTimestamp`: `2026-08-23T13:59:25Z`
- `artifactDigest`: `sha256:7ef4723c07b0ac518e8aeab1adae596e724139aa93fced80fea9fb747b4a9903`
- `installerDigest`: `sha256:a54fe6f0dae0f96939ab0b85fec10b880980bab2090b3fbb1a18dcc0f7da0e77`
- `provenanceDigest`: `sha256:24eed223b068253103bee3ccc6bdf2b26666886d076a0d6cb86ba5dc9b596066`
- provenance `sourceCommit`: `9ce6d93dd139e9491f1b2a955467ce967c0385c1`
- signed manifest SHA-256: `76f1b2338269ea557855849ca145602de9bdd5e667263ba9ae2e848f205152ba`

The `v1.13.6` tag points at that same `sourceCommit`, so the tag does identify
the published bytes for this release.

Release identity is the signed manifest with its `artifactDigest` and
`provenanceDigest`.

## Scope

Three paths are in scope for 1.13.6.

1. Fresh installation with the advanced host controller enabled. This is the
   path the 1.13.5 defect broke and is the headline gate for this release. It
   MUST be executed with `Y` at step 4/4. A 1.13.6 qualification that never
   enabled the controller does not qualify this release.
2. The ordinary post-adoption path from a healthy ElectrumX-RVN 1.13.5
   installation to 1.13.6, using the normal updater (`electrumx-update`). This
   path MUST NOT invoke `legacy_1_13_1_apply.py` or request
   `ADOPT LEGACY 1.13.1`, even when the installation was originally adopted from
   1.13.1.
3. The one-time legacy path from a healthy ElectrumX-RVN 1.13.1 /
   Ravencoin Core 4.8.0 node to the signed 1.13.6 release, using
   `legacy_1_13_1_apply.py`, whose `TARGET_ELECTRUMX_VERSION` is now `1.13.6`.

Paths 2 and 3 carry no intended behavioural change from 1.13.5
`artifact_revision 1`. They are re-qualified because the version pins moved and
because the storage/overlay guarantees proved in 1.13.5 must not regress:

- `storageMode: named-volumes` persists after the one-time legacy adoption and
  is honoured natively by the normal updater;
- the same Docker named-volume objects remain attached;
- release-owned Compose overlays selected through `.env`, especially
  `compose.tls.yaml`, survive promotion and rollback;
- `compose.chainstrap.yaml` is never reactivated;
- public ElectrumX port 50002 remains published;
- a TLS handshake to `electrumx.raventag.com:50002` succeeds;
- `server.version` returns `ElectrumX-RVN 1.13.6`.

## Fresh-install controller gate

On a host with no prior installation, no `/usr/local/lib/electrumx-ravencoin`
and no `/var/lib/electrumx-ravencoin` state, run the published 1.13.6 installer
and answer `Y` at step 4/4. Evidence must include all of the following:

- the installer completes with exit code `0`, and no `invalid option` error
  appears anywhere in the run;
- `/usr/local/lib/electrumx-ravencoin` exists, is `root:root` and mode `0755`;
- the trusted controller script under that directory is `root`-owned and its
  SHA-256 equals the digest the installer verified against the bundle;
- `systemctl is-enabled` and `systemctl is-active` for the controller unit both
  succeed;
- the running controller reconciles the persisted connection limit without a
  `docker compose` error;
- an uninstall/reinstall cycle re-creates the directory through the same
  `install -d` path rather than leaving a stale root-owned tree.

## External mutator regression gate

The withdrawn 1.13.3 candidate failed because the external
`ravencoin-bandwidth-controller.service` reconciled its persisted
`MAX_SESSIONS` by issuing its own `docker compose up` while the updater was
recreating ElectrumX. Docker Compose then observed a container ID the controller
had already replaced and failed with `No such container`; the same race also
interfered with rollback. That gate is unchanged for 1.13.6 and must be
re-executed on the update path.

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

## Pre-mutation gates

1. Published release bytes must verify against `SHA256SUMS`.
2. `electrumx-update check` must record 1.13.6 as `ELIGIBLE` and `VERIFIED`.
3. For the legacy path, discovery must prove the exact existing named-volume
   identities and a healthy 1.13.1 / Core 4.8.0 runtime.
4. `COMPOSE_FILE` must contain only Compose files shipped by the candidate
   release.
5. No ChainStrap action is allowed on either upgrade path.

## PASS criteria

A PASS requires the fresh-install controller gate above plus all of the
following on the update path:

- updater state before apply identifies 1.13.5 as `currentRelease`;
- `electrumx-update check` records 1.13.6 as `ELIGIBLE` and `VERIFIED`;
- on the ordinary path, apply is performed through the normal updater only and
  no `ADOPT LEGACY` prompt appears;
- running ElectrumX reports `ElectrumX-RVN 1.13.6`;
- Core remains version 4.8.0 and the exact certified source commit;
- ElectrumX DB height equals daemon/Core height;
- the same Docker named-volume objects remain attached at the same
  destinations;
- the install marker identifies 1.13.6 and retains `storageMode` unchanged;
- Docker Compose labels still include `compose.tls.yaml`;
- `compose.chainstrap.yaml` is not reactivated;
- Docker still publishes `50002/tcp` on the host;
- external TLS verification succeeds after promotion;
- Electrum protocol `server.version` succeeds after promotion;
- updater state records 1.13.6 as current and clears the pending candidate;
- host-wide artifact high-water is created/advanced only after promotion;
- Node Monitor remains healthy and external;
- no `No such container` error occurs during promotion or rollback paths.

## Rollback regression

Also execute a controlled failing-health test with the external controller
active before apply. The updater must suspend it, restore the exact old release
and named volumes, then resume the controller only after rollback is complete.
A failed rollback must leave the controller stopped.

## Publication decision

The candidate was published on the `v1.13.6` release for qualification. It
becomes the recommended release only when this file records a PASS with the
artifact identity below. Until then it must not be announced as recommended.
