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

## Observed evidence, 2026-08-23

### Signed artifact identity

- release source commit: `c279608f22763e7bcfcf1587bc56cd0e6682f418`
- merge commit on master: `d00f73a2beef65175994ecb4f1fa114e08326768`
- `artifact_revision`: `0`
- release timestamp: `2026-08-23T19:22:01Z`
- signing key ID: `6f4f944c9b0a19a1`
- bundle SHA-256: `f58020543b2ef8ad4449d266917fd18a50361563b1b6140151f3bbfbdd24423e`
- installer SHA-256: `74af3b119210fd4e5fc628f2fe9cb5c9da76d62801d15b6d430d1e64480438f0`
- provenance SHA-256: `ca02c7c7a629d43f3b3d3c114fbe994e333a9d3c82917dd93cb6c398fdf42812`
- signed manifest SHA-256: `659af82009af675be8b504a1eb847992a0f082b6933b65f45b7589aa7eb9aec5`
- offline verify-only result: `status=VERIFIED`

### Gate 1: regression/security suite

PASS. Focused suite over `tests/test_chainstrap*.py` and
`tests/test_release_version_identity.py`: 149 passed. Full CI on the release
source commit: tests 3.10, 3.11 and 3.12, container, Core artifact amd64 and
arm64, and Protected path scope all pass.

### Gate 2: current upstream ChainStrap fresh install

PASS. Isolated fresh install against the 2026-08-19 upstream snapshot
(`chainstrap/chainstrap.github.io@509dc251d4896f245912e8212a3b2b1ea5bc7add`,
metadata SHA-256 `1d6aa9a05106880aaed13d7b4c86bd7110f9ac80ee46e6b091fc3e3542031ff1`,
height 4505776), using a dedicated Compose project and bind-mounted storage
paths.

- all 17 parts downloaded and passed SHA-256 verification;
- part 14, which aborted 1.13.7 on `blocks/index/004089.ldb`, was accepted:
  `part accepted: 22 raw block file(s) | snapshot 83.2% (14/17)`;
- parts 15, 16 and 17 carry no raw blocks at all and were accepted without
  extracting anything, for example:
  `preflight ignored 538 foreign ZIP member(s)` then
  `preflight accepted 0 raw block member(s), 0 B uncompressed`;
- `ChainStrap stage complete: 291 contiguous raw block files`;
- datadir purity after completion: `blocks/` holds 291 entries, every one
  matching `blk[0-9]{5,8}.dat`, with zero non-matching entries. No
  `blocks/index/*`, no `rev*.dat` and no `assets/*` was written to disk;
- no automatic P2P fallback occurred at any point;
- Ravencoin Core started the mandatory network-isolated reindex.

### Gate 3: post-install service state

FAILED for 1.13.8 as published, observed on 2026-08-24.

The mandatory network-isolated reindex completed successfully:

```
Release-floor ancestry verified at 4501329:000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48.
Full local Core reindex completed; exact snapshot tip
4505776:000000000005d3d76dc5a29b280e67f047697102c0b273a120e65a9f7bf88ac9,
release-floor ancestry, and asset database/index probes were verified.
Normal Core startup is now allowed.
```

The first normal Core startup then crash looped:

```
Error: Command line contains unexpected token
'9c798a1088fea460d9d5924bb460e5adac6a8349ef9dccec2b8b931c7f6afe45',
see ravend -h for a list of options.
```

Cause: `docker/core/entrypoint.sh` read the staged blocks-marker digest with
`set -- $(sha256sum ...)`, which replaces the container positional parameters
that the same script forwards to `ravend`. The digest and the marker path were
appended to the Core command line. The refusal gate itself behaved correctly;
only the argument handling was wrong.

Scope: every ChainStrap fresh installation reaching its first normal Core
startup, on 1.13.8 as published and on the releases that carry the same
entrypoint. Ordinary updates of an existing installation are unaffected, which
is why gate 4 passed on the Raspberry Pi 5 node and the public endpoint kept
serving.

With that one line corrected, the same installation and the same validated
datadir passed the gate:

- Ravencoin Core started, reached the repository readiness gate, is healthy and
  reports `Raven Core Daemon version v4.8.0.0-225491298`;
- ElectrumX started and is healthy;
- `electrumx_rpc getinfo` reports `"version": "ElectrumX-RVN 1.13.8"`,
  `"daemon height": 4507854`, with the ElectrumX index building from the
  validated chain state;
- the backend remained the trusted Ravencoin Core 4.8.0 identity pinned by this
  release.

That re-test used a locally built Core image carrying only the entrypoint fix,
not a published artifact. It demonstrates the cause and the remedy; it does not
qualify 1.13.8 as published.

### Gate 4: ordinary hardware update

PASS on the Raspberry Pi 5 node (`electrumx.raventag.com`), from a healthy
1.13.7 installation over the normal updater path:

```
UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING storage-model=named-volumes volume-objects=3 active-mounts=PASS
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING compose-model=PASS storage-model=named-volumes volume-objects=3
UPDATER_CHECKPOINT release-switch=PASS same-filesystem-renames=COMPLETE new-root=ACTIVE
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
HealthVerdict.PROMOTE_TO_CURRENT: post-update health gates passed
```

After promotion: `pendingCandidate = null`, `failureReason = null`,
`lastKnownGoodRelease` records 1.13.7, the installed
`release-install-metadata.json` records `1.13.8` at source commit
`c279608f22763e7bcfcf1587bc56cd0e6682f418`, ElectrumX runs
`alenoc/electrumx-ravencoin:1.13.8` healthy, Ravencoin Core remains
`alenoc/ravencoin-core:4.8.0` healthy, the Node Monitor stayed up, existing
chain data and ElectrumX database were preserved, and ChainStrap was not
re-run. `electrumx_rpc getinfo` reports `"version": "ElectrumX-RVN 1.13.8"`.

### Gate 5: public endpoint

PASS. With default certificate verification against
`electrumx.raventag.com:50002` (certificate common name
`electrumx.raventag.com`):

```
{"jsonrpc":"2.0","result":["ElectrumX-RVN 1.13.8","1.4"],"id":0}
```

## Qualification result

PENDING.

Gates 1, 2, 4 and 5 passed on real hardware and are recorded above. Gate 3
failed: the ChainStrap fix that 1.13.8 exists for works, the snapshot downloads
and validates end to end, but the Core container entrypoint then crash loops on
the first normal startup of a fresh ChainStrap installation. 1.13.8 was
published ahead of this gate on the maintainer's explicit instruction.

1.13.8 therefore does not qualify as a fresh-install release and must be
superseded. The corrected entrypoint is the only change required, and the same
validated datadir passes the gate with it.

Change this document to `RESULT: PASS` only if a build that includes the
entrypoint fix passes gate 3 as a published artifact.
