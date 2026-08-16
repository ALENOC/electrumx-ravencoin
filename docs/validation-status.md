# Validation status

This is the single human-readable status reference. It intentionally separates
software-release evidence from live deployment evidence.

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Core certification](core-certification.md)

## Release certification

| Item | Status |
|---|---|
| Certified identity | `2miners/Ravencoin` `v4.8.0` at `b60f50e04f1fba425b28804e61be2694faaf3469` |
| Profile | `rvn-consensus-2026-08-v1`, revision 1 |
| Mandatory release tests | 12 PASS, 0 FAIL, 0 REVIEW_REQUIRED, 0 skipped |
| Release profile scope | Bounded candidate behavior/configuration; no synchronized-node HTTP REST test |
| Signed policy | policy v2 current; v1 retained historically |
| Certification report | persisted under `core-safety/production/certifications/` |
| Core-safety focused subset | 43 tests PASS |
| Server CI matrix | `pytest -q` plus static checks on Python 3.10, 3.11 and 3.12; aggregate count is intentionally not duplicated here |
| Client policy/backend/directory tests | 58 PASS, 11 subtests |

## Architecture artifact qualification

Distinct from release certification (source) and live deployment (a specific
running node), this checks that the certified source actually builds and
starts correctly on each supported architecture. See [Core
certification](core-certification.md#architecture-artifact-qualification) for
what the qualification suite does and does not prove.

| Architecture | Status | Notes |
|---|---|---|
| linux/amd64 | QUALIFIED | Prebuilt release binary, checksum-verified against the certified commit |
| linux/arm64 | QUALIFIED | Compiled from the certified source archive; `make check` passed as part of the build |

Both architectures: version, genesis, regtest startup, real REST
(`/rest/block/<hash>.bin`), txindex, graceful shutdown and container restart
all PASS. `assetindexRpc` is `LIVE-ONLY` on both architectures: the
qualification environment runs a wallet-disabled regtest node, which cannot
legitimately exercise asset RPC/index behavior. Live asset RPC is proven
separately, against the synchronized mainnet node, in the live deployment
gates below.

## Live deployment

| Gate | Status |
|---|---|
| Core reindex | COMPLETE; `blocks == headers == 4,495,881`, `verificationprogress 0.9999961890996617` |
| Canonical checkpoint observed | PASS; live `getblockhash 4487775` matches `INCIDENT_CHECKPOINT_HASH` exactly, nHeight at 4487774-4487777 all correct |
| `transfer_overflow` active on deployment | ACTIVE; `bip9_softforks.transfer_overflow.status = active`, since height 4,493,664 |
| txindex, assetindex and REST live | PASS; REST `/rest/block/<hash>.bin` returned HTTP 200, 3135 bytes; `getrawtransaction` by bare txid returned a real historical tx (confirmations 8,100) |
| Asset RPC with historical data | PASS; `listassets`/`getassetdata` return real mainnet asset metadata; a nonexistent asset name correctly returns empty, not an error |
| ElectrumX historical index | COMPLETE; `db height == daemon height == 4,496,069` |
| Live `server.ravencoin_backend` | PASS; live call returned `coreSafe: true`, `networkMatches: true`, `backendSynchronized: true`, `kawpowHeightValidation: true`, `checkpoint4487775: true`, plus a full `identity` block (`BUILD_IDENTITY_VERIFIED`, source commit `b60f50e0...`) |
| Independent chain validation | PASS; ElectrumX's own indexed headers at the checkpoint and at tip were independently re-hashed under KAWPOW light verification (not read from Core RPC) and matched Core's canonical hashes exactly at both heights |
| Live asset evidence (metadata, owner tokens, address history, positive/negative lookups) | PASS; see below |
| Client `SAFE_CORE_VERIFIED` against live server | **PASS**; real client code (`electrum.ravencoin_backend.classify_backend_evidence`) run against the live `server.ravencoin_backend` response returned `SAFE_CORE_VERIFIED` |
| Public CA-valid TLS endpoint | PENDING operator/network validation |

### Live asset evidence detail

Gathered against the fully-indexed live server over the real Electrum protocol
(not Core RPC directly):

| Check | Result |
|---|---|
| Asset metadata (`blockchain.asset.get_meta`, existing asset `000`) | PASS; real issuance data (circulation, divisions, source tx/height) |
| Asset metadata, nonexistent asset | PASS; empty result, no error |
| Owner token metadata (`000!`) | PASS; real issuance data, non-reissuable, 0 divisions as expected for an owner token |
| Address-by-asset (`blockchain.asset.list_addresses_by_asset`) | PASS; real holder address and balance |
| Address history (`blockchain.scripthash.get_history`) for a real asset-holder address | PASS; multi-entry real transaction history spanning heights 435,650 to 1,083,923+ |
| Address balance/UTXO (`blockchain.scripthash.get_balance` / `listunspent`) | PASS; well-formed responses (this address's RVN balance was legitimately 0/empty) |

## ElectrumX synchronization robustness

| Item | Status |
|---|---|
| `caught_up` latch defect | FIXED; `self.caught_up` no longer stays latched true after a material backend-height jump, see [Troubleshooting](troubleshooting.md#electrumx-and-a-core-reindex-that-moves-the-backend-height-a-lot) |
| Regression coverage | PASS; `tests/server/test_block_processor.py` (`_catch_up_state`): normal single-block lag does not revoke, a jump beyond one prefetch batch (100 blocks) revokes, a full reindex-like scenario revokes then restores; the incident-scenario assertion fails against the pre-fix latch-forever behavior |
| `server.ravencoin_backend` field freshness audit | PASS; every field (`blocks`, `headers`, `coreSafe`, `networkMatches`, `backendSynchronized`, `kawpowHeightValidation`, `checkpoint4487775`) is recomputed from a live Core RPC call on every request (`max_age=0` on the client-facing path); none were found to be latched or sticky |
| Live re-validation after the fix | PASS; ElectrumX rebuilt (`--no-deps`, Core untouched, verified via unchanged `StartedAt`/`RestartCount`) and run against the already-synchronized reference Core; `server.version`, `server.features`, `server.ravencoin_backend`, independent chain validation and client `SAFE_CORE_VERIFIED` all re-confirmed PASS on the fixed build |

## Electrum monitor integration

| Item | Status |
|---|---|
| ALENOC endpoint probed with the real monitor pipeline (`monitor.crawl.probe_endpoint`, `monitor.classify.classify_backend`) | PASS (LOCAL only, `127.0.0.1:50001`); reachable, real `server.version`/features/backend evidence retrieved |
| Reachability classification | LOCAL VALIDATED ENDPOINT, not a publicly reachable Internet endpoint; not added to `monitor/config/operator-registry.json`, which already carries an `ALENOC` group with an empty endpoint list awaiting a real public hostname |
| Backend classification of the real endpoint alone | `UNVERIFIED`, not `SAFE`; policy match alone is never enough, matching `classify_backend`'s own documented contract that independent chain comparison is required |
| Path to `SAFE` | Demonstrated with one real observation (ALENOC) plus one explicitly-labeled synthetic second observation; `compare_chains` returns `VALID` only when independent groups agree, confirming no automatic trust is granted to the maintainer's own endpoint |
| operatorGroup dedup | PASS; `count_independent_operators` and `independent_groups` count multiple ALENOC endpoints as one operator (`tests/test_monitor.py::test_two_alenoc_endpoints_are_one_operator`), and ALENOC plus another operator as two, not three |
| Public Internet endpoint | PENDING; not configured this session, no router/firewall change made |
| Independent public operator diversity | NOT SOLVED by this work; one more validated operator (once publicly reachable) does not by itself establish ecosystem-wide diversity |

The release is certified, and the private local deployment has passed every
live gate through client `SAFE_CORE_VERIFIED`. The public endpoint is not yet
validated: TLS, external reachability, DuckDNS/CGNAT and renewal remain
operator/network steps. Do not publish the endpoint or call a wallet release
production-ready until that gate is complete too.
