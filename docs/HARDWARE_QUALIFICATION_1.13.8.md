# ElectrumX-RVN 1.13.8 hardware qualification

## RESULT: PENDING

This document records the qualification plan and, after execution, the evidence
for ElectrumX-RVN 1.13.8.

A release candidate MUST NOT be considered qualified until every mandatory gate
below has passed on real hardware and the resulting signed artifact identity is
recorded here.

## Why 1.13.8 exists

ElectrumX-RVN 1.13.7 introduced handling for mixed-content ChainStrap archives,
but classified ZIP members by location. Any member under the `blocks/`
namespace that did not match the raw block allowlist was fatal.

A real fresh-install qualification of 1.13.7 against the current upstream
ChainStrap Ravencoin snapshot failed on part 14/17, after parts 1 to 13 had
downloaded, passed SHA-256 verification, passed ZIP preflight and extracted:

```
download complete in 5m42s (average 5.6 MiB/s)
verifying SHA-256...
SHA-256 verified in 3s
chainstrap-bootstrap: non-allowlisted member in ChainStrap ZIP: 'blocks/index/004089.ldb'
```

The installer failed closed and refused automatic P2P fallback, which is the
correct fail-closed behaviour, but the bootstrap could not complete against a
valid current upstream snapshot.

Current ChainStrap RVN archives can carry derived Ravencoin datadir material
both beside the raw blocks:

- `assets/*.ldb`
- `assets/*.log`
- `assets/CURRENT`
- `assets/LOCK`

and inside the blocks namespace:

- `blocks/index/004089.ldb`
- `blocks/index/CURRENT`
- `blocks/index/MANIFEST-000001`

alongside the raw blockchain files:

- `blocks/blk*.dat`

1.13.8 classifies members structurally rather than by location. The trust
boundary is unchanged:

- only allowlisted `blocks/blk*.dat` members may be extracted;
- safe regular foreign members are ignored wherever they appear in the archive,
  including inside `blocks/`, and are never decompressed, never written to disk
  and never imported into the Ravencoin datadir;
- derived ChainStrap databases, chainstate, block index, asset databases, wallet
  material and other foreign state are never trusted or imported;
- unsafe paths, traversal, absolute paths, symlinks/special files, encrypted
  entries, duplicate paths, duplicate block indexes, unsupported compression and
  size/cap violations remain fail-closed;
- a snapshot that yields no accepted raw block file is refused. Current
  upstream snapshots split derived material into whole parts that carry no
  `blk*.dat` at all (observed on part 15/17 of the 2026-08-19 snapshot:
  `blocks/index/*.ldb` and `blocks/rev*.dat` only), so this refusal is enforced
  snapshot wide rather than part by part. Part contents are pinned by the
  resolved metadata digest and SHA-256 verified before preflight;
- the complete raw-block set must still satisfy the existing contiguous block
  sequence validation before the blocks-ready marker is written, which is where
  an empty or gapped raw-block set fails closed;
- Ravencoin Core still performs a local full reindex/revalidation with
  `-assumevalid=0`.

This is a compatibility change to executable bootstrap behaviour, so it is a new
software version rather than an artifact revision of 1.13.7.

## Required identities

- source version for the ordinary updater path: `1.13.7`
- candidate version: `1.13.8`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity

PENDING.

Populate this section only after the reviewed unsigned 1.13.8 candidate has
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

The repository tests must prove that a mixed-content archive containing, at
minimum:

- `assets/000111.ldb`
- `assets/CURRENT`
- `assets/LOCK`
- `chainstate/foo`
- `blocks/index/004089.ldb`
- `blocks/index/CURRENT`
- `blocks/index/MANIFEST-000001`
- `blocks/blk00000.dat`
- `blocks/blk00001.dat`

accepts and extracts only the raw `blk*.dat` members, and that nothing else
reaches the staging directory.

The suite must also prove fail-closed handling for:

- path traversal;
- absolute/unsafe paths;
- symlink and special-file members, including inside `blocks/index/`;
- encrypted members;
- duplicate ZIP members;
- duplicate block indexes within or across parts;
- unsupported raw-block compression/type;
- oversized block members;
- archive/uncompressed/aggregate caps;
- unsafe paths and unsafe entry types inside a block-free part;
- a snapshot with zero accepted raw blocks across all parts;
- missing `blk00000.dat`;
- gaps in the final raw-block sequence.

## Mandatory gate 2: current upstream ChainStrap fresh install

Perform a real fresh installation with the default ChainStrap bootstrap against
the current official RVN ChainStrap snapshot, using isolated installation and
storage paths so that no unrelated Docker workload or existing chain data is
touched.

Evidence must demonstrate that:

- current upstream ChainStrap metadata is resolved and accepted;
- every downloaded part passes SHA-256 verification;
- `blocks/index/004089.ldb` is ignored and not extracted;
- other safe foreign members are ignored;
- only raw `blocks/blk*.dat` files are extracted into the datadir;
- all 17 ChainStrap parts complete;
- no automatic P2P fallback occurs;
- Ravencoin Core starts the mandatory local reindex;
- the raw-block safety limits remain active.

## Mandatory gate 3: post-install service state

- Ravencoin Core reaches readiness according to the repository readiness gate;
- Ravencoin Core is healthy and remains `4.8.0`;
- ElectrumX starts;
- ElectrumX is healthy;
- ElectrumX reports `ElectrumX-RVN 1.13.8`;
- the backend remains the trusted Ravencoin Core 4.8.0 identity.

## Mandatory gate 4: ordinary hardware update

On the Raspberry Pi 5 qualification node, an existing healthy ElectrumX-RVN
1.13.7 installation must be able to discover the signed 1.13.8 candidate through
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

- candidate `1.13.8`, artifact revision `0`, VERIFIED and ELIGIBLE;
- external mutator suspend PASS;
- release switch PASS;
- external mutator resume PASS;
- `HealthVerdict.PROMOTE_TO_CURRENT`;
- `pendingCandidate = null`;
- `failureReason = null`;
- `lastKnownGoodRelease` records 1.13.7.

## Mandatory gate 5: public endpoint

From an external client:

- TLS certificate verification for `electrumx.raventag.com:50002` passes;
- `server.version` over TLS returns:

  `["ElectrumX-RVN 1.13.8", "1.4"]`

## Qualification result

PENDING.

Change this document to `RESULT: PASS` only after the signed candidate has
passed all mandatory gates above.

Record the exact final artifact identity and real hardware evidence when the
qualification completes.
