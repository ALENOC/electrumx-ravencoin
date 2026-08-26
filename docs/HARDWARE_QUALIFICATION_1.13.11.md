# ElectrumX-RVN 1.13.11 hardware qualification

## RESULT: PASS

This document records the qualification plan and, after execution, the evidence
for ElectrumX-RVN 1.13.11.

A release candidate MUST NOT be considered qualified until every mandatory gate
below has passed on real hardware and the resulting signed artifact identity is
recorded here.

## Why 1.13.11 exists

1.13.11 ships the Ravencoin Network Observer Phase 1 on top of 1.13.10:

- the `monitor` package becomes `network_observer` before its first release;
- Chain Quorum 2.0 shared-height challenges, signed observation bundles,
  cryptographic operator declarations, multi-vantage aggregation, asset
  capability probing and Asset Data Quorum v1;
- the final adversarial audit remediations for that work: RPC error responses
  no longer shift id-less answer attribution (which could classify a broken
  asset method as working or record a challenge header against the wrong
  height), the operator declaration rollback high-water reads the
  pruning-surviving marker table, REGISTRY_ATTESTED declarations are actually
  applied at classification time with the documented precedence, and
  `aggregate-observations` refuses unverified bundles by default;
- the founder-independence-capable governance and succession library
  (`core-safety/scripts/governance.py`), tested but not yet activated: current
  production governance remains the single offline release key.

Nothing in the serving path changes: `server.ravencoin_backend`, the legacy
Electrum protocol surface, the safe-Core policy chain, the updater trust
model and the ChainStrap trust boundary are untouched.

The release trust boundary is unchanged, and the ChainStrap contract is
unchanged:

- only allowlisted `blocks/blk*.dat` members may be extracted;
- safe regular foreign members such as `assets/LOCK` and
  `blocks/index/004089.ldb` are ignored wherever they appear in the archive and
  never reach the Ravencoin datadir;
- unsafe paths, unsafe entry types, malformed archives and zero-raw-block parts
  remain fail-closed;
- Ravencoin Core still performs a local full reindex/revalidation of every
  imported raw block, so ChainStrap stays transport acceleration and never a
  consensus trust source.

The 1.13.10 persistent-state ownership contract is inherited unchanged:
`PERSISTENT_PATHS` is the same fixed set, every preserved file and directory
keeps its source uid and gid, and a switch that cannot preserve operator ownership
fails closed rather than silently changing the owner. `.secrets`
handling is exactly as qualified for 1.13.10.

## Required identities

- source version for the ordinary updater path: `1.13.10`
- candidate version: `1.13.11`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Artifact identity

Built by the protected release workflow (run 32813304259) from the exact
reviewed commit, signed by the documented offline ceremony (sign exit 0;
`--verify-only` exit 0, `status=VERIFIED`), published as the release
`v1.13.11` with the five publication bytes only:

- release source commit: `152b5134b849a31b2fdd9ef9efe643683a5bcb5c`
- `artifact_revision`: `0`
- release timestamp: `2026-08-25T05:33:27Z`
- artifact digest: `sha256:d3b81d1f7e3a0d096c5a41b64285fd9a7afdc9fa3807cebb5f86a37df973f5d4`
- installer digest: `sha256:2cc3d87e8f2db98dd7ede3ee4d39261ad4943a2871cc28e3b80288c19e7601ee`
- provenance digest: `sha256:fed02b1a993cac1c4591e0cfd5c15dda07f78c8a33e5512ae7503c8a689f7130`
- signed manifest SHA-256: `d6abc30e34a186cae553560dcfd3295e06abbd6b9005bf672cf63db08ffd2e51`
- signing key ID: `6f4f944c9b0a19a1`
- final tag commit: `152b5134b849a31b2fdd9ef9efe643683a5bcb5c` (verified via
  `git ls-remote origin refs/tags/v1.13.11`)

## Mandatory gate 1: regression/security suite

The repository tests must cover the audit remediations shipped here:

- an id-less server erroring on one probe request can no longer shift later
  answers into the errored slot (no false ASSET_CAPABLE, no wrong-height
  challenge evidence), and duplicate JSON-RPC ids fail closed;
- the operator declaration high-water survives deletion of declaration rows;
- attested declarations apply at classification time, and expired or
  SELF_SIGNED declarations resolve nothing;
- `aggregate-observations` refuses unverified bundles and verifies against
  trusted observer keys;
- observation anti-replay and time-boundary semantics hold at their exact
  edges;
- the governance succession matrix passes with specific exceptions;
- the RavenTag `server.ravencoin_backend` contract suite passes unchanged.

The ChainStrap classification suite and the version-ordering suite must keep
passing unchanged.

## Mandatory gate 2: ordinary hardware update

On the Raspberry Pi 5 qualification node, an existing healthy ElectrumX-RVN
1.13.10 installation must be able to discover the signed 1.13.11 candidate
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

- candidate `1.13.11`, artifact revision `0`, VERIFIED and ELIGIBLE;
- external mutator suspend PASS;
- release switch PASS;
- external mutator resume PASS;
- `HealthVerdict.PROMOTE_TO_CURRENT`;
- `pendingCandidate = null`;
- `failureReason = null`;
- `lastKnownGoodRelease` records 1.13.10.

## Mandatory gate 3: post-install service state

- Ravencoin Core reaches readiness according to the repository readiness gate;
- Ravencoin Core is healthy and remains `4.8.0`;
- Ravencoin Core stays up across restarts, with no crash loop;
- ElectrumX starts;
- ElectrumX is healthy;
- ElectrumX reports `ElectrumX-RVN 1.13.11`;
- the backend remains the trusted Ravencoin Core 4.8.0 identity.

## Mandatory gate 4: public endpoint

From an external client:

- TLS certificate verification for `electrumx.raventag.com:50002` passes;
- `server.version` over TLS returns:

  `["ElectrumX-RVN 1.13.11", "1.4"]`

- `server.ravencoin_backend` returns every RavenTag contract field with the
  same semantics as 1.13.10.

## Mandatory gate 5: deployed ownership preservation

On the qualification node, after the update, the preserved operator state
(`.env`, `.secrets`, `certs`, `contrib/electrumx.env`, Node Monitor `.env`)
must still be owned by the operator uid/gid, not `root:root`. The Node Monitor
must be healthy, with restart count `0`, at the end of the gate.

## Mandatory gate 6: Network Observer on the node

- `python -m network_observer.cli --help` and `status` work from the installed
  release root;
- no stale `monitor/` package remains in the installed tree;
- one controlled `status`/discovery round stays inside crawler limits and
  performs no unsafe connection (SSRF filtering intact);
- the separate `ravencoin-node-monitor` remains the LOCAL node health system
  and is unaffected.

## Qualification evidence (observed 2026-08-25)

Gate 1 (regression/security suite), local venv on the reviewed source:

- final full suite: 1,131 passed, 15 skipped, 2 warnings;
- focused trust-root/updater/installer selection: 93 passed;
- broader updater/installer/release selection: 217 passed;
- the final audit coverage includes RPC response correlation, observer bundle
  verification, declaration application and rollback high-water, source trust
  loading, artifact-wide key consistency, and governance domain separation.

Gate 2 (ordinary hardware update) on the Raspberry Pi 5 qualification node
(aarch64, Docker 29.7.2):

- `check`: candidate 1.13.11 r0 VERIFIED and ELIGIBLE against the pinned
  trust key; digests equal the signed ceremony output;
- `apply`: `PROMOTE_TO_CURRENT`, "post-update health gates passed" (updater
  audit log, 2026-08-25T05:47:23+00:00);
- `pendingCandidate = null`, `failureReason = null`,
  `lastKnownGoodRelease = 1.13.10`, host high-water advanced to 1.13.11;
- blockchain data, ElectrumX DB and named-volume identities preserved
  (storage proofs PASS); `compose.tls.yaml` preserved; external Node Monitor
  and bandwidth reconciler untouched; ChainStrap one-shot state not re-run.
- Operational note: the operator's SSH session was detached mid-apply; the
  remote updater process completed the transaction on its own and the audit
  log records the successful promotion. No manual mutation of release state
  occurred at any point.

Gate 3 (post-install service state): Core healthy `4.8.0`
(`/Ravencoin:4.8.0(RG5MujXzxARjWChWdU2awbAQa9ZCH52yrh)/`), mainnet,
blocks == headers, 17 peers, restart count 0; ElectrumX healthy, reporting
`ElectrumX-RVN 1.13.11`, restart count 0, db height == daemon height at every
hourly checkpoint since the update.

Gate 4 (public endpoint): from an external client, TLS certificate for
`electrumx.raventag.com:50002` verifies (`Verify return code: 0 (ok)`),
`server.version` returns `["ElectrumX-RVN 1.13.11", "1.4"]`, and
`server.ravencoin_backend` returns every RavenTag contract field with
unchanged semantics (BUILD_IDENTITY_VERIFIED, RavenProject/Ravencoin @
22549129888d02e0e08fcdb9f96f3c699167e774, all compatibility flags true,
fresh observedAt); `blockchain.asset.get_meta` answers on the live node.

Gate 5 (deployed ownership preservation): `.secrets/raven_rpc_user` and
`.secrets/raven_rpc_password` kept `lexnox:docker` mode `600` across the
release switch; Node Monitor healthy with restart count `0` (its HTTP port
answers with authentication as designed) and the ElectrumX admin poller
service active.

Gate 6 (Network Observer on the node): `network_observer` package present in
the installed tree with no stale `monitor/` package and no old-namespace
imports; `python -m network_observer.cli --help` and `status` run against the
installed release (the CLI needs a Python environment with the repository
dependencies plus `cryptography`, which a plain install does not provision on
the host; qualified through a derived container image built from the release
image itself, mounting the release tree read-only); one controlled
`discover-now` round from the node: 14 endpoints probed inside the standard
crawler limits, shipped asset sentinels empty (no active asset probing), and
Chain Quorum 2.0 reported `anchor 4509249, status VALID: 2 attested group(s)
agree at 7 corroborated challenge height(s)` against real public servers.

Soak: containers stable across the post-update window with restart counts 0,
Core and ElectrumX heights progressing together, `server.ravencoin_backend`
fresh, CPU/RAM/disk nominal, no unexpected errors in the ElectrumX log (Core
logs only routine P2P socket timeouts, ~1 per 10 minutes with 17 connected
peers).

## Qualification result

## RESULT: PASS

Every mandatory gate above was observed against the published 1.13.11
artifacts on the real node.
