# ElectrumX-RVN 1.13.10 hardware qualification

## RESULT: PASS

This document records the qualification plan and, after execution, the evidence
for ElectrumX-RVN 1.13.10.

A release candidate MUST NOT be considered qualified until every mandatory gate
below has passed on real hardware and the resulting signed artifact identity is
recorded here.

## Why 1.13.10 exists

The updater in every release up to and including 1.13.9 loses the operator
ownership of the preserved operator state when it switches release roots.

`copy_persistent_state` copies a fixed set of operator-owned mutable paths from
the old release root into the new one:

```
PERSISTENT_PATHS = (
    ".env",
    ".secrets",
    "certs",
    "contrib/electrumx.env",
    f"{MONITOR_PATH}/.env",
)
```

The updater runs as root, so `shutil.copyfile` gave every preserved file to
`root:root`. Permission bits were restored, ownership was not.

Observed consequence on the Raspberry Pi 5 qualification node, after an
otherwise healthy 1.13.9 update: `.secrets/raven_rpc_user` and
`.secrets/raven_rpc_password` changed from `lexnox:docker` mode `600` to
`root:root` mode `600`, and the separately deployed Node Monitor, which
bind-mounts that state under an unprivileged uid, crash looped with

```
PermissionError: [Errno 13] Permission denied: '/run/secrets/raven_rpc_user'
```

reaching a restart count of 227 before the ownership was restored by hand.

The same `root:root` imprint is present in earlier
`.electrumx-ravencoin.failed-update-*` trees, so this is systemic and not a
one-off.

Ravencoin Core and ElectrumX never observed the defect: `compose.yaml` routes
the RPC secrets through a root `rpc-secrets-init` container that copies them
into a shared volume with mode `0444`, so neither service reads the host file
directly.

1.13.10 carries the ownership across the release switch, and fails closed if it
cannot:

- every preserved file and directory keeps its source uid and gid;
- if `os.chown` fails, the update raises `UpdateRuntimeError` and refuses the
  switch rather than silently changing the owner;
- permission bits, the symlink refusal and the `0700` hardening of preserved
  directories are unchanged;
- `PERSISTENT_PATHS` is unchanged: no new path crosses the release boundary.

The release trust boundary is untouched. No source file, Compose file,
executable or signing material crosses the boundary, and the ChainStrap
contract is unchanged:

- only allowlisted `blocks/blk*.dat` members may be extracted;
- safe regular foreign members such as `assets/LOCK` and
  `blocks/index/004089.ldb` are ignored wherever they appear in the archive and
  never reach the Ravencoin datadir;
- unsafe paths, unsafe entry types, malformed archives and zero-raw-block parts
  remain fail-closed;
- Ravencoin Core still performs a local full reindex/revalidation of every
  imported raw block, so ChainStrap stays transport acceleration and never a
  consensus trust source.

## Scope note: which updater performs the 1.13.9 to 1.13.10 switch

The release switch is executed by the updater that is already installed on the
host, not by the candidate being installed. The 1.13.9 to 1.13.10 update
therefore still runs the unfixed 1.13.9 `copy_persistent_state`, and is expected
to hand `.secrets` to `root:root` one last time. A single manual ownership
restore after that update is expected and is not evidence that the fix failed.

The fix is proven on the node only by observing the installed 1.13.10
`copy_persistent_state` preserve ownership, which gate 6 below requires
explicitly.

## Required identities

- source version for the ordinary updater path: `1.13.9`
- candidate version: `1.13.10`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity

PENDING.

Populate this section only after the reviewed unsigned 1.13.10 candidate has
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

The repository tests must prove the ownership contract:

- every preserved file and directory keeps its source uid and gid across a
  release switch;
- a release switch that cannot restore ownership fails closed with
  `preserve operator ownership` in the error;
- permission bits of preserved files are still restored unchanged;
- the tests fail against the pre-fix implementation.

Version ordering must also be proven for the first two-digit patch version in
this repository: `1.13.10` must be classified as newer than `1.13.9` by the
canonical comparator, not older, so the host anti-rollback state accepts it.

The ChainStrap classification suite must keep passing unchanged: a
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

## Mandatory gate 2: fresh ChainStrap install of the published artifacts

This gate was left open by 1.13.9, which observed it only against a locally
built Ravencoin Core image. It must now be observed end to end against the
published 1.13.10 artifacts, on an empty datadir:

- the published installer verifies the signed manifest and the bundle digest;
- ChainStrap downloads and verifies every snapshot part;
- only allowlisted `blocks/blk*.dat` members are extracted, and safe derived
  members such as `blocks/index/004089.ldb` and `assets/LOCK` are ignored;
- the mandatory local Ravencoin Core reindex completes at the snapshot tip;
- the normal `ravencoin-core` service then starts and stays up, with no
  argument-clobbering defect and no crash loop.

## Mandatory gate 3: post-install service state

- Ravencoin Core reaches readiness according to the repository readiness gate;
- Ravencoin Core is healthy and remains `4.8.0`;
- Ravencoin Core stays up across restarts, with no crash loop;
- ElectrumX starts;
- ElectrumX is healthy;
- ElectrumX reports `ElectrumX-RVN 1.13.10`;
- the backend remains the trusted Ravencoin Core 4.8.0 identity.

## Mandatory gate 4: ordinary hardware update

On the Raspberry Pi 5 qualification node, an existing healthy ElectrumX-RVN
1.13.9 installation must be able to discover the signed 1.13.10 candidate
through the normal updater path.

The update must preserve:

- existing Ravencoin blockchain data;
- existing ElectrumX database;
- Docker named-volume identities where present;
- `compose.tls.yaml`;
- the external Node Monitor;
- the external bandwidth controller;
- ChainStrap one-shot state, without re-running ChainStrap.

Expected updater outcome:

- candidate `1.13.10`, artifact revision `0`, VERIFIED and ELIGIBLE;
- external mutator suspend PASS;
- release switch PASS;
- external mutator resume PASS;
- `HealthVerdict.PROMOTE_TO_CURRENT`;
- `pendingCandidate = null`;
- `failureReason = null`;
- `lastKnownGoodRelease` records 1.13.9.

## Mandatory gate 5: public endpoint

From an external client:

- TLS certificate verification for `electrumx.raventag.com:50002` passes;
- `server.version` over TLS returns:

  `["ElectrumX-RVN 1.13.10", "1.4"]`

## Mandatory gate 6: deployed ownership preservation

On the qualification node, after the update, the installed 1.13.10
`copy_persistent_state` must be observed preserving the operator ownership of
the real `.secrets` state when run as root against a scratch destination:

- the preserved files keep the operator uid and gid, not `root:root`;
- the preserved permission bits are unchanged;
- the live installation is not modified by the observation.

The Node Monitor must be healthy, with restart count `0`, at the end of the
gate.

## Observed evidence, 2026-08-25

### Signed artifact identity

- release source commit: `45279df6adf504e280cc25ea032f88d0a939e62b`
- `artifact_revision`: `0`
- release timestamp: `2026-08-24T17:34:30Z`
- bundle SHA-256: `2f6baf495d219c5349743751761df5b0523abcb6714cad1274abd9d6fd7e66bf`
- installer SHA-256: `66a12e6b9848faf75bc2eab27844db45d8b33a3ea279a39389d010ded0c1d506`
- provenance SHA-256: `f3f8ae0d00d82fe5fedfeb746b3b9bbd747c195ce4d80f23a8540c96bba97b30`
- signed manifest SHA-256: `543481cb995f7b3ef88745e4794930a8e0cd736ca43b2ef2c3125ea37d57bb09`
- signing key ID: `6f4f944c9b0a19a1`
- annotated tag `v1.13.10`, tagger ALENOC
- offline verification against the production public key with the
  repository's own verifier (`update_manifest.verify_manifest`)
  concluded `status=VERIFIED`

### Gate 1: regression/security suite

PASS (prerelease). `tests/test_update_persistent_state_ownership.py`
proves uid/gid preservation and the `preserve operator ownership`
fail-closed error, and fails against the pre-fix implementation;
`tests/test_two_digit_patch_ordering.py` proves `1.13.10 > 1.13.9` in
`classify_release_order`, `enforce_high_water` and
`_updater_version_compatible`. Full suite on the release source commit:
481 passed, 3 skipped, with the 3 known pre-existing failures
unrelated to this release.

### Gate 2: fresh ChainStrap install of the published artifacts

PASS, observed on 2026-08-24/25 on a fresh workstation install with
the PUBLISHED v1.13.10 installer (assets downloaded from the GitHub
release), a new empty datadir
(`/mnt/colibri-models/rvn-qual-11310`, ~272G free), default ChainStrap
path, no P2P bootstrap.

- published installer `--check-only`: all signed release and
  independent Core-policy checks passed against the published
  manifest (ElectrumX 1.13.10, Core 4.8.0 @ 2254912988);
- upstream snapshot 2026-08-24 (height 4507204, hash
  `0000000000062844713d38e0d197ae35264a85b14278558bfab12aceba41663f`,
  metadata SHA-256
  `585a0be8a3424b9114d0a8baf72ad92798c16b89295680bae702e287e779831f`):
  all 17 parts downloaded, each part SHA-256 verified;
- extraction of raw `blocks/blk*.dat` only: parts 15 to 17 carried no
  raw blocks and were accepted with nothing extracted, e.g.
  `preflight ignored 538 foreign ZIP member(s)` and
  `preflight ignored 1080 foreign ZIP member(s); only raw blocks/blk*.dat
  members are eligible for extraction`; derived members such as
  `blocks/index/004089.ldb` and `assets/LOCK` were ignored, never
  extracted;
- `ChainStrap stage complete: 291 contiguous raw block files (291 newly
  extracted entries) in 1h00m55s`; after completion the extraction
  staging held exactly 291 `blk*.dat` entries and nothing else;
- no automatic P2P fallback at any point; a first install attempt
  failed closed (all four allowlisted IPFS gateways returned transport
  errors) with the partial run removed and no fallback, which is the
  intended behavior, and succeeded on retry;
- offline Core reindex with networking disabled completed:
  `[OK] Offline Ravencoin Core validation`, after which the normal
  `ravencoin-core` service started first try, no argument-clobbering
  defect, no crash loop (`RestartCount=0`, healthy), and caught up to
  the live network tip under normal operation;
- `blocks/` after normal operation contains 583 entries: the 291
  extracted `blk*.dat` plus `rev*.dat` undo files and `blocks/index/`
  written by Ravencoin Core itself during its own validated
  operation, which is expected post-reindex state and not extraction
  output;
- unrelated workstation containers (open-webui, worldmonitor,
  aentech-*) were untouched throughout.

### Gate 3: post-install service state

PASS. `electrumx-ravencoin-ravencoin-core-1`: healthy, RestartCount 0,
`Raven version v4.8.0.0-225491298`. `electrumx-ravencoin-electrumx-1`:
healthy, RestartCount 0,
`software version: ElectrumX-RVN 1.13.10`, backend
`/Ravencoin:4.8.0/ on main: blocks=4,509,282 headers=4,509,282
synchronized=True`.

### Gate 4: ordinary hardware update

PASS, observed on the Raspberry Pi 5 node (2026-08-24). The updater
checkpoints recorded:

```
UPDATER_CHECKPOINT storage-preflight=PASS old-stack=RUNNING storage-model=named-volumes volume-objects=3 active-mounts=PASS
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
UPDATER_CHECKPOINT candidate-storage=PASS old-stack=RUNNING compose-model=PASS storage-model=named-volumes volume-objects=3
UPDATER_CHECKPOINT release-switch=PASS same-filesystem-renames=COMPLETE new-root=ACTIVE
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
HealthVerdict.PROMOTE_TO_CURRENT: post-update health gates passed
```

State after the update: `currentRelease 1.13.10`,
`lastKnownGoodRelease 1.13.9`, `pendingCandidate null`,
`failureReason null`. Blockchain data, ElectrumX database, named
volumes, `compose.tls.yaml`, the external Node Monitor and the
bandwidth controller were preserved; ChainStrap did not rerun. An
earlier apply attempt failed on intermittent node DNS
(`lookup registry-1.docker.io ... server misbehaving`) and rolled
back cleanly ("exact previous release restored"); the retry applied
normally.

Expected post-update note: the 1.13.9 to 1.13.10 switch was executed
by the INSTALLED 1.13.9 updater (not yet containing the fix), so it
left `.secrets/raven_rpc_user` and `.secrets/raven_rpc_password` at
`root:root`; ownership was restored by hand to `1000:984` mode 600.
This was anticipated and documented before the update: from 1.13.10
onward the updater preserves ownership, and gate 6 is the proof of
the fix.

### Gate 5: public endpoint

PASS, from the external workstation client on 2026-08-24:
TLS 1.3 connection to `electrumx.raventag.com:50002` with certificate
verification succeeded (cipher `TLS_AES_256_GCM_SHA384`), and
`server.version` returned exactly
`{"jsonrpc":"2.0","result":["ElectrumX-RVN 1.13.10","1.4"],"id":0}`.

### Gate 6: deployed ownership preservation

PASS, observed on the node with the INSTALLED 1.13.10 code, run as
root against a scratch destination in `/tmp` (non destructive, the
live installation untouched). `copy_persistent_state` output:

```
.env 0 0 0o600
.secrets 0 0 0o700
.secrets/raven_rpc_password 1000 984 0o600
.secrets/raven_rpc_user 1000 984 0o600
```

`.secrets` content kept the operator ownership `1000:984` with mode
600 (not `root:root`); `.env` is `root:root` in the live source tree
as well, so `0 0` is correct preservation, not a defect. The Node
Monitor ended the gate healthy with `RestartCount=0`, and
`http://127.0.0.1:8899/` answered `401` (alive and authentication
protected, the expected response).

## Qualification result

## RESULT: PASS

All six mandatory gates were observed passing against the published
v1.13.10 artifacts and the signed identity recorded above.
