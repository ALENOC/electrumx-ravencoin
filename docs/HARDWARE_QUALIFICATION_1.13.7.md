# ElectrumX-RVN 1.13.7 hardware qualification

## RESULT: PENDING

This document records the qualification plan and, after execution, the evidence
for ElectrumX-RVN 1.13.7.

A release candidate MUST NOT be considered qualified until every mandatory gate
below has passed on real hardware and the resulting signed artifact identity is
recorded here.

## Why 1.13.7 exists

ElectrumX-RVN 1.13.7 fixes compatibility with current upstream Ravencoin
ChainStrap snapshots.

Current ChainStrap RVN archives can contain derived Ravencoin datadir material,
including members such as:

- `assets/*.ldb`
- `assets/*.log`
- `assets/CURRENT`
- `assets/LOCK`

alongside the raw blockchain files:

- `blocks/blk*.dat`

Earlier ElectrumX-RVN ChainStrap handling assumed that every ZIP member had to
belong to the raw-block allowlist. A current valid upstream snapshot containing
derived `assets/` material was therefore rejected before bootstrap could begin.

1.13.7 keeps the existing trust boundary unchanged:

- only allowlisted `blocks/blk*.dat` members may be extracted;
- safe foreign members outside the security-sensitive `blocks/` namespace are
  ignored and never extracted into the Ravencoin datadir;
- derived ChainStrap databases, chainstate, indexes, asset databases, wallet
  material and other foreign state are never trusted or imported;
- unexpected members inside `blocks/`, including `rev*.dat`, remain refused;
- unsafe paths, traversal, absolute paths, symlinks/special files, duplicate
  paths, duplicate block indexes, unsupported compression and size/cap
  violations remain fail-closed;
- a ChainStrap part containing no accepted raw block files is refused;
- the complete raw-block set must still satisfy the existing contiguous block
  sequence validation before the blocks-ready marker is written;
- Ravencoin Core still performs a local full reindex/revalidation with
  `-assumevalid=0`.

This is a compatibility change to executable bootstrap behaviour, so it is a new
software version rather than an artifact revision of 1.13.6.

## Required identities

- source version for the ordinary updater path: `1.13.6`
- candidate version: `1.13.7`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity

PENDING.

Populate this section only after the reviewed unsigned 1.13.7 candidate has
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
- `assets/000112.ldb`
- `assets/000157.log`
- `assets/CURRENT`
- `assets/LOCK`
- `blocks/blk00000.dat`
- `blocks/blk00001.dat`

accepts and extracts only the two raw `blk*.dat` members.

The suite must also prove fail-closed handling for:

- path traversal;
- absolute/unsafe paths;
- symlink and special-file members;
- duplicate ZIP members;
- duplicate block indexes within or across parts;
- unexpected members in `blocks/`;
- unsupported raw-block compression/type;
- oversized block members;
- archive/uncompressed/aggregate caps;
- parts with zero accepted raw blocks;
- missing `blk00000.dat`;
- gaps in the final raw-block sequence.

## Mandatory gate 2: current upstream ChainStrap compatibility

Resolve the current official RVN ChainStrap snapshot using the production
runtime resolver.

Using a bounded qualification test that does not overwrite production chain
data, prove that the current upstream archive shape containing foreign derived
members is accepted when valid raw blocks are present.

Evidence must demonstrate that:

- foreign safe members are ignored;
- no `assets/*`, `chainstate/*`, `indexes/*` or other derived state is extracted;
- only allowlisted raw block members are selected;
- the raw-block safety limits remain active.

The test MUST NOT require importing a full snapshot into an already populated
production Ravencoin datadir.

## Mandatory gate 3: ordinary hardware update

On the Raspberry Pi 5 qualification node, start from a healthy ElectrumX-RVN
1.13.6 installation and apply the signed 1.13.7 candidate using only the normal
updater path.

The update must preserve:

- existing Ravencoin blockchain data;
- existing ElectrumX database;
- Docker named-volume identities where present;
- `compose.tls.yaml`;
- the external Node Monitor;
- the external bandwidth controller;
- ChainStrap one-shot state, without re-running ChainStrap.

Expected updater outcome:

- candidate `1.13.7`, artifact revision `0`, VERIFIED and ELIGIBLE;
- external mutator suspend PASS;
- release switch PASS;
- external mutator resume PASS;
- `HealthVerdict.PROMOTE_TO_CURRENT`;
- `pendingCandidate = null`;
- `failureReason = null`;
- `lastKnownGoodRelease` records 1.13.6.

## Mandatory gate 4: post-update service state

After promotion:

- ElectrumX reports `ElectrumX-RVN 1.13.7`;
- Ravencoin Core remains `4.8.0`;
- Core is healthy;
- ElectrumX is healthy;
- Node Monitor remains healthy;
- bandwidth controller is active;
- TCP 50001 remains bound to localhost only;
- TLS 50002 remains published;
- selected Compose overlays remain intact.

## Mandatory gate 5: public endpoint

From an external client:

- TLS certificate verification for `electrumx.raventag.com:50002` passes;
- `server.version` over TLS returns:

  `["ElectrumX-RVN 1.13.7", "1.4"]`

## Qualification result

PENDING.

Change this document to `RESULT: PASS` only after the signed candidate has
passed all mandatory gates above.

Record the exact final artifact identity and real hardware evidence when the
qualification completes.
