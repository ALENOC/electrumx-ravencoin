# ElectrumX-RVN 1.13.10 hardware qualification

## RESULT: PENDING

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

## Qualification result

## RESULT: PENDING

Change this document to `RESULT: PASS` only after every mandatory gate above has
been observed against the published 1.13.10 artifacts.
