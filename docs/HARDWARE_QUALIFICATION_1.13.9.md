# ElectrumX-RVN 1.13.9 hardware qualification

## RESULT: PENDING

This document records the qualification plan and, after execution, the evidence
for ElectrumX-RVN 1.13.9.

A release candidate MUST NOT be considered qualified until every mandatory gate
below has passed on real hardware and the resulting signed artifact identity is
recorded here.

## Why 1.13.9 exists

ElectrumX-RVN 1.13.8 fixed ChainStrap mixed-content classification and completed
a real fresh install of the current upstream snapshot: 17/17 parts downloaded and
verified, only allowlisted `blocks/blk*.dat` members extracted, and the mandatory
local Ravencoin Core reindex finished successfully at the exact snapshot tip.

Gate 3 of the 1.13.8 qualification then failed. After the one-shot reindex
completed, the normal `ravencoin-core` service crash looped:

```
Error: Command line contains unexpected token
'9c798a1088fea460d9d5924bb460e5adac6a8349ef9dccec2b8b931c7f6afe45',
see ravend -h for a list of options.
```

Cause: the ChainStrap validation gate in `docker/core/entrypoint.sh` compared the
recorded blocks-marker digest using

```sh
set -- $(sha256sum "$chainstrap_blocks_marker")
```

`set --` replaces the shell positional parameters, which are the container
arguments that the same script forwards to the daemon at the end:

```sh
exec ravend -datadir=... -conf=... -printtoconsole "$@"
```

so the marker digest was passed to `ravend` as a command-line token and the
daemon refused to start.

Affected scope:

- every ChainStrap fresh install on 1.13.8 as published, at first normal Core
  startup after the one-shot reindex;
- ordinary updates of existing healthy installations are not affected, because
  that code path only runs when the ChainStrap validation markers are present.

1.13.9 computes the digest without touching the positional parameters:

```sh
blocks_hash=$(sha256sum "$chainstrap_blocks_marker" | cut -d' ' -f1)
```

The trust boundary is unchanged. The ChainStrap marker comparison, the refusal to
start on a marker mismatch, the extraction allowlist and the mandatory local full
Core reindex/revalidation all behave exactly as in 1.13.8:

- only allowlisted `blocks/blk*.dat` members may be extracted;
- safe regular foreign members such as `assets/LOCK` and
  `blocks/index/004089.ldb` are ignored wherever they appear in the archive and
  never reach the Ravencoin datadir;
- unsafe paths, unsafe entry types, malformed archives and zero-raw-block parts
  remain fail-closed;
- Ravencoin Core still performs a local full reindex/revalidation of every
  imported raw block, so ChainStrap stays transport acceleration and never a
  consensus trust source.

A regression test in `tests/test_chainstrap_entrypoint_gate.py` now asserts the
exact argument vector handed to the daemon after a validated ChainStrap startup.

## Required identities

- source version for the ordinary updater path: `1.13.8`
- candidate version: `1.13.9`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity

PENDING.

Populate this section only after the reviewed unsigned 1.13.9 candidate has
been produced and signed.

Required evidence:

- release source commit
- `artifact_revision`
- release timestamp
- artifact digest
- installer digest
- provenance digest
- signed manifest SHA-256
- signing key ID
- final tag commit

Do not copy artifact identities from an earlier release.

## Mandatory gate 1: regression/security suite

The repository tests must prove the entrypoint contract:

- a validated ChainStrap startup forwards exactly the intended argument vector
  to `ravend`, with no marker digest or other stray token appended;
- a marker mismatch still refuses to start;
- a missing completion marker still refuses to start.

The ChainStrap classification suite from 1.13.8 must keep passing unchanged: a
mixed-content archive containing, at minimum:

- `assets/000111.ldb`
- `assets/CURRENT`
- `assets/LOCK`
- `chainstate/foo`
- `blocks/index/004089.ldb`
- `blocks/index/CURRENT`
- `blocks/index/MANIFEST-000001`
- `blocks/blk00000.dat`
- `blocks/blk00001.dat`

accepts and extracts only the raw `blk*.dat` members, and nothing else reaches
the staging directory. Fail-closed handling for path traversal, absolute/unsafe
paths, symlink and special-file members, encrypted members, duplicate members,
duplicate or missing block indexes, unsupported compression, oversized members,
archive/uncompressed/aggregate caps and zero accepted raw blocks is unchanged.

## Mandatory gate 2: ChainStrap datadir validity

Against a ChainStrap-produced Ravencoin datadir that carries the 1.13.8
validation markers:

- the published 1.13.9 Ravencoin Core image starts the normal service without
  the argument-clobbering defect;
- the ChainStrap validation gate still runs and still compares the recorded
  digests;
- `blocks/index/004089.ldb` was ignored at bootstrap time and is not present as
  ChainStrap-imported material.

## Mandatory gate 3: post-install service state

- Ravencoin Core reaches readiness according to the repository readiness gate;
- Ravencoin Core is healthy and remains `4.8.0`;
- Ravencoin Core stays up across restarts, with no crash loop;
- ElectrumX starts;
- ElectrumX is healthy;
- ElectrumX reports `ElectrumX-RVN 1.13.9`;
- the backend remains the trusted Ravencoin Core 4.8.0 identity.

## Mandatory gate 4: ordinary hardware update

On the Raspberry Pi 5 qualification node, an existing healthy ElectrumX-RVN
1.13.8 installation must be able to discover the signed 1.13.9 candidate through
the normal updater path.

The update must preserve:

- existing Ravencoin blockchain data;
- existing ElectrumX database;
- Docker named-volume identities where present;
- `compose.tls.yaml`;
- the external Node Monitor;
- the external bandwidth controller;
- ChainStrap one-shot state, without re-running ChainStrap.

Expected updater outcome:

- candidate `1.13.9`, artifact revision `0`, VERIFIED and ELIGIBLE;
- external mutator suspend PASS;
- release switch PASS;
- external mutator resume PASS;
- `HealthVerdict.PROMOTE_TO_CURRENT`;
- `pendingCandidate = null`;
- `failureReason = null`;
- `lastKnownGoodRelease` records 1.13.8.

## Mandatory gate 5: public endpoint

From an external client:

- TLS certificate verification for `electrumx.raventag.com:50002` passes;
- `server.version` over TLS returns:

  `["ElectrumX-RVN 1.13.9", "1.4"]`

## Observed evidence, 2026-08-24

### Signed artifact identity

- release source commit: `31e680e61a7c5833915768ea41ea9ccf44b953ed`
- `artifact_revision`: `0`
- release timestamp: `2026-08-24T11:57:27Z`
- artifact digest:
  `sha256:7bb1bc02f5586ea74ed426dd2170af7660074ff2c06b2c5515a76266b06c23cc`
- installer digest:
  `sha256:a7b3a55f8c04623ffd206f66de1173ebeda8b163b657912bd7258104141dbaf7`
- provenance digest:
  `sha256:992646796d60394dc601c3d254c06e4fd577bad9138246007523e5aa30285d4d`
- signed manifest SHA-256:
  `56cf3f8abdbcd0e0f0a0010d926e8bbc36c1909c3635044999ba3adc3b572c8f`
- signing key ID: `6f4f944c9b0a19a1`
- offline verification final line: `status=VERIFIED`

### Gate 1: regression/security suite: PASS

The entrypoint regression test asserts the exact argument vector forwarded to
the daemon after a validated ChainStrap startup. Reverting the one-line fix makes
exactly that test fail, and restoring it makes the whole entrypoint gate suite
pass. The ChainStrap classification suite and the release identity suite pass on
Python 3.10, 3.11 and 3.12 in CI.

### Gate 2: ChainStrap datadir validity: PASS on a locally built image

Against the ChainStrap-produced datadir from the 1.13.8 qualification, which
carries both validation markers, a Ravencoin Core image built with only the
entrypoint fix starts the normal service, passes the ChainStrap validation gate
and reaches healthy:

```
Raven Core Daemon version v4.8.0.0-225491298
```

A full fresh ChainStrap install against the published 1.13.9 artifacts has not
been repeated, so gate 2 is not yet observed end to end on a published artifact.

### Gate 3: post-install service state: PASS on the updated node

On the Raspberry Pi 5 node after the 1.13.9 update:

- `ravencoin-core` healthy, no crash loop;
- `electrumx` healthy;
- ElectrumX reports `ElectrumX-RVN 1.13.9`;
- the backend remains the trusted Ravencoin Core 4.8.0 identity.

### Gate 4: ordinary hardware update: PASS

On the Raspberry Pi 5 node, the existing healthy 1.13.8 installation discovered
and applied the signed 1.13.9 candidate through the normal updater path:

```
UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING storage-model=named-volumes volume-objects=3 active-mounts=PASS
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING compose-model=PASS storage-model=named-volumes volume-objects=3
UPDATER_CHECKPOINT release-switch=PASS same-filesystem-renames=COMPLETE new-root=ACTIVE
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
HealthVerdict.PROMOTE_TO_CURRENT: post-update health gates passed
```

Resulting updater state: `currentRelease 1.13.9`, `lastKnownGoodRelease 1.13.8`,
`pendingCandidate = null`, `failureReason = null`. Blockchain data, ElectrumX
database, named-volume identities, `compose.tls.yaml`, the external Node Monitor
and the external bandwidth controller were preserved, and ChainStrap did not
re-run.

### Gate 5: public endpoint: PASS

```
{"jsonrpc":"2.0","result":["ElectrumX-RVN 1.13.9","1.4"],"id":0}
```

TLS certificate verification for `electrumx.raventag.com:50002` passed.

## Qualification result

## RESULT: PENDING

Gates 1, 3, 4 and 5 are observed PASS against the published 1.13.9 artifacts.
Gate 2 is observed only against a locally built Core image carrying the
entrypoint fix, not against a fresh ChainStrap install of the published
artifacts.

Change this document to `RESULT: PASS` only after a fresh ChainStrap install of
the published 1.13.9 artifacts also passes gate 2 end to end.
