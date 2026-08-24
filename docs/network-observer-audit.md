# Network Observer Phase 1: architecture audit of the existing monitor

Audit performed before any code change, on master commit
`45279df6adf504e280cc25ea032f88d0a939e62b`. Every claim below cites the
file and symbol that proves it. Where the Phase 1 task description assumed
something the repository does not do, the difference is called out
explicitly in the final section.

## A. Existing multi-node discovery design

* `monitor/crawl.py:288-342` `Crawler.crawl()`: breadth-first crawl over
  `server.peers.subscribe` gossip with `max_crawl_depth`, per-crawl
  candidate cap, bounded concurrency (`asyncio.Semaphore`) and a per-host
  hourly rate budget (`RateLimiter`, lines 54-69). Peer edges are recorded
  as provenance, "announcement is provenance, not endorsement" (line 302).
* `monitor/netsafety.py:183-206` `parse_peers_response()`: bounded peer
  list parsing (`max_peers_per_response`), junk-tolerant per-record parse.
* Seeds and registry are hints only: `monitor/cli.py:80-113`
  `load_seeds()` / `load_registry()`; the config files themselves state
  "a seed is a hint, never an endorsement"
  (`monitor/config/seeds.json`).
* Discovery state machine: `monitor/model.py:60-64`
  `DiscoverySource` (BOOTSTRAP, GOSSIP, REGISTRY, MANUAL).

## B. Existing network-safety protections

* `monitor/netsafety.py:48-81` `normalize_hostname()`: RFC 1123 label
  validation, length cap, separator/host-string attacks, onion handling,
  bare-IP classification.
* `monitor/netsafety.py:95-116` `classify_address()`: refuses loopback,
  RFC1918/private, link-local, multicast, reserved, unspecified,
  site-local, non-global and the cloud metadata addresses pinned at lines
  37-41.
* `monitor/netsafety.py:119-138` `safe_resolved_addresses()`: filters
  every DNS answer, not just the first, which is the DNS-rebinding shape.
* `monitor/crawl.py:72-100` `resolve_endpoint()`: DNS timeout, refuses
  `.onion` rather than leaking it to the system resolver.
* Response amplification bounds: `monitor/model.py:147-162` `Limits`
  (`max_response_bytes`, `max_peers_per_response`,
  `max_probes_per_host_per_hour`, `max_concurrent_probes`);
  `monitor/crawl.py:275-276` enforces the per-response size limit.

## C. Existing backend/Core trust model

* `monitor/classify.py:85-138` `classify_backend()`: a self-reported
  `server.ravencoin_backend` can at best yield UNVERIFIED; certification
  comes from the signed safe-Core policy keyed on repository+commit
  (`policy["releases"]`), known-unsafe versions are hard-failed, and
  compatibility flags must all be true.
* `monitor/cli.py:119-146` `load_policy()`: verification failure of the
  signed policy collapses to an empty policy (everything
  UNREVIEWED_CORE), never to trust.
* Anti-rollback for policy versions: `monitor/store.py:326-344`
  `load_minimum_policy_version()` / `record_policy_version()` backed by
  the single-row `policy_state` table (`store.py:108-111`).
* The server side the monitor reads is
  `electrumx/server/ravencoin_backend.py:174-207`
  `RavencoinBackendStatus.public_dict()`: `backend.blocks` (Core height),
  `backend.headers`, `compatibility.coreSafe`, `backend.identity`
  (sourceRepository/sourceCommit) with explicit evidence levels
  (`ravencoin_backend.py:35-47`).

## D. Existing operator-group / Sybil-resistance model

* `monitor/classify.py:44-56` `operator_group_key()` and
  `known_group_count()`: grouping key is the configured operatorGroup or
  an `UNKNOWN-<hostname>` placeholder; placeholders never count toward
  independent-operator quorum because an attacker can mint hostnames for
  free.
* `monitor/classify.py:159-170` `independent_groups()`.
* `monitor/classify.py:320-341` `count_independent_operators()`: counts
  attested groups, not endpoints.
* Grouping only ever comes from this operator's own seeds/registry config
  (`monitor/config/operator-registry.json`), never from crawled
  self-reports.

## E. Existing chain comparison behavior

* `monitor/classify.py:224-301` `compare_chains()`: operator-group
  scoped; conflict requires hash disagreement at a shared height
  (lines 274-277); the pinned incident checkpoint
  (`electrumx/lib/coins.py:243-244`) is checked against the network
  constant, never against the anchor (lines 258-263); height lag alone is
  never conflict (lines 278-280).
* Corroboration: `monitor/classify.py:173-221` `_verified_groups()`
  builds a "corroborated anchor" only from a height/tip pair reported by
  at least two attested groups, or an explicit trusted `--reference`;
  a group strictly ahead of that anchor is never verified.
* `monitor/classify.py:304-317` `is_corroborated()`: SAFE promotion
  requires positive verification; absence of conflict is not agreement.
* Header hashing: `monitor/crawl.py:103-125`
  `_ravencoin_header_hash()` uses `Ravencoin.header_hash` (real KAWPOW,
  `electrumx/lib/coins.py:270-277`) and rejects wrong-length headers as
  non-evidence.

## F. Existing cross-crawl conflict persistence

* `monitor/store.py:347-388`: `record_conflict()`, `clear_conflict()`,
  `conflict_confirmations()` over the `chain_conflicts` table
  (`store.py:122-127`): first sighting is CONFLICT_SUSPECTED, confirmed
  CHAIN_CONFLICT needs `Thresholds.conflict_confirmations`
  (`model.py:117`) independent crawls for the same group; recovery
  requires a positively verified clean comparison, never mere absence of
  a fresh conflict (`monitor/cli.py:213-218`).

## G. Existing signed directory architecture

* `monitor/directory.py:26-27` schema v1 with signature domain
  separation; `build_directory()` (lines 40-82) emits a compact hint with
  an explicit "Discovery hint only" note; `verify_directory()`
  (lines 97-164) enforces trusted-key allowlist, canonical Ed25519
  signature, schema version, monotonic `directoryVersion` anti-rollback
  and expiry; `candidates_from_directory()` (lines 167-184) returns
  candidates, not approvals.

## H. Existing vantage-point support

* `monitor/model.py:170` `ProbeResult.vantage_point` (default "local");
  persisted per observation (`monitor/store.py:63` column
  `vantage_point`, recorded at `store.py:278`); CLI flag
  `--vantage-point` (`monitor/cli.py:317-319`). It is a label today:
  there is no aggregation, cross-vantage comparison or observer signing
  yet. Phase 1 Part 2 builds on this label without changing its meaning.

## I. Existing Ravencoin asset RPC methods relevant to asset quorum

Present in `electrumx/server/session.py` dispatch (lines 2261-2291):
`blockchain.asset.get_meta`, `get_assets_with_prefix`,
`list_addresses_by_asset`, `verifier_string`, `restricted_associations`,
`get_meta_history`, `verifier_string_history`,
`blockchain.tag.qualifier.history`, `blockchain.tag.h160.history`,
`blockchain.asset.frozen_history`, `restricted_associations_history`.

Every `*_history` handler accepts `include_mempool` as a positional
parameter (e.g. `session.py:1963`), and every underlying DB history
lookup returns entries deterministically sorted by
`(height, tx_hash)` with per-entry `height`:

* meta history: `electrumx/server/db.py:1712-1741`
  `lookup_asset_meta_history`
* verifier string history: `db.py:1561-1583` `get_restricted_string_history`
* h160 tag history: `db.py:1395-1420` `qualifications_for_h160_history`
* qualifier tag history: `db.py` `qualifications_for_qualifier_history`
* freeze history: `db.py:1511-1531` `restricted_frozen_history`
* qualifier/restricted association history: `db.py:1614-1649`
  `lookup_qualifier_associations_history`

This is the property Phase 1 Part 6.2 needs: state as of a past height H
can be reconstructed exactly on the client by requesting the history with
`include_mempool=false` and folding only entries with `height <= H`, in
the order the server returned them. Reconstruction is exact for confirmed
state; mempool state is excluded on purpose because it is not comparable
across servers.

## Differences between the task description and the repository

1. The description's ladder lists "Chain Quorum 2.0 [improve]" over
   health+backend identity: accurate. `compare_chains` compares tips and
   the pinned checkpoint only; there is no shared-height header
   challenge today. Confirmed.
2. The description lists candidate asset RPCs
   `blockchain.asset.list_addresses_by_asset` etc. All exist, but
   `list_addresses_by_asset` is unbounded/expensive
   (`session.py:1877`) and is excluded from active probing by design.
3. The description assumes "the database already has a vantage_point
   concept": true (`store.py:63`), but it is a passive label.
4. `blockchain.block.header` is already used once per probe for the
   pinned incident checkpoint (`monitor/crawl.py:50`); the shared-height
   challenge generalizes this existing pattern.
5. No existing table stores crawl challenge nonces, observer keys,
   operator identities, asset samples or snapshots. Schema version is 4
   (`store.py:27`); Phase 1 adds a forward-safe version 5 migration.
