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

## Observed evidence

PENDING.

Record here, with dates, the observed result of every gate above, including the
signed artifact identity actually published.

## Qualification result

## RESULT: PENDING

Change this document to `RESULT: PASS` only after every mandatory gate above has
been observed to pass against the published 1.13.9 artifacts.
