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
| linux/amd64 | QUALIFIED | Prebuilt release binary, checksum-verified against the certified commit; report persisted under `core-safety/production/certifications/` |
| linux/arm64 | BUILD + PARTIAL CERTIFICATION | Native ARM64 GitHub Actions runner (`ubuntu-24.04-arm`), same pinned source commit. `make check` (the build-time consensus test suite) and a startup/RPC/REST/txindex/restart smoke suite both PASS - runs [31912711067](https://github.com/ALENOC/electrumx-ravencoin/actions/runs/31912711067) and [31913521218](https://github.com/ALENOC/electrumx-ravencoin/actions/runs/31913521218), artifacts `core-artifact-qualification-arm64` (expire 2026-11-13). Observed `ravend` sha256 `7e23e00a470c05ac39921ef3548284f93befad7a174587d0118f4f941648991b`, `raven-cli` sha256 `4797c653d9a51eb27ed2b694f222ff145bae4fb24139b62c3ec733bba567891b`. Not run: the incident-specific probes (`kawpow-header-shape`, `nheight-binding-rejects-forged`, `post-boundary-valid-accepted`, `incident-checkpoint-hash`, `transfer-overflow-deployment`) and mainnet (rather than regtest) genesis that back the amd64 status. No report is persisted in this repository; the CI artifacts are the only copy and they expire. A physical Raspberry Pi 5 deployment reproduced these exact hashes and validated the deployment path end to end; see the next subsection. |

For linux/amd64: version, genesis, regtest startup, real REST
(`/rest/block/<hash>.bin`), txindex, graceful shutdown and container restart
all PASS. `assetindexRpc` is `LIVE-ONLY`: the qualification environment runs a
wallet-disabled regtest node, which cannot legitimately exercise asset
RPC/index behavior. Live asset RPC is proven separately, against the
synchronized mainnet node, in the live deployment gates below. linux/arm64
passed the same startup/REST/txindex/restart checks plus `make check`, but
has not been run through the incident-specific mandatory probes above, so it
is not at parity with the amd64 row.

### Physical Raspberry Pi 5 deployment (2026-08-16)

A real deployment executed the documented Raspberry Pi procedure on physical
hardware: Raspberry Pi 5 (8 GB, aarch64), Raspberry Pi OS Lite 64-bit
(Debian 13 trixie) on microSD, a 2 TB SSD/NVMe in a USB enclosure mounted
by UUID at `/srv/ravencoin`, Docker data-root `/srv/ravencoin/docker`
guarded by `RequiresMountsFor=/srv/ravencoin`, and the documented JMicron
`152d:a580` usb-storage quirk applied as the USB compatibility fallback.

| Check | Result |
|---|---|
| `docker compose up -d --build` | PASS; 35/35 build steps, 967.9 s total, Core ARM64 compile-and-test stage ~951 s (some cached dependency layers reused; a single observed deployment, not a clean-build guarantee) |
| Docker storage | PASS; `DockerRootDir=/srv/ravencoin/docker`; `/srv/ravencoin` on `/dev/sda1 ext4 rw,noatime`; Compose named volumes on the SSD-backed data-root |
| Binary identity | PASS; `ravend --version` reported `Raven Core Daemon version v4.8.0.0-gb60f50e04f`, matching the pinned commit `b60f50e04f1fba425b28804e61be2694faaf3469` |
| RPC identity | PASS; `getnetworkinfo` reported `"version": 4080000`, `"subversion": "/Ravencoin:4.8.0/"` |
| Containers | PASS; `ravencoin-core` healthy, `electrumx` healthy, one-shot `rpc-secrets-init` completed |
| ARM64 binary hashes | MATCH the native ARM64 CI observations above (`ravend` `7e23e00a...`, `raven-cli` `4797c653...`): the physical Raspberry Pi 5 build produced the same SHA-256 values previously observed on the native ARM64 CI build. Evidence of matching build outputs across the two tested ARM64 environments, not a formal reproducible-build guarantee, and not equivalence with the amd64 release artifact |
| Storage stability | PASS; after the quirk, no Buffer I/O errors, critical target errors, USB resets, device offline events or Read Capacity errors during the test period; ~30.8 MB/s direct read observed on the fallback |
| Power/thermal | PASS; `vcgencmd get_throttled` = `throttled=0x0`, ~67.5 C during operation |

Still pending from this physical run, so ARM64 is not consensus-qualified:
full initial blockchain synchronization, full ElectrumX indexing to chain
tip, the incident-specific KAWPOW/nHeight probes, checkpoint validation
around height 4,487,775, affected-chain validation from height 4,487,776,
`transfer_overflow` activation around height 4,493,664, and full
restart/reboot persistence after complete synchronization. At observation
time the node was in initial synchronization (`blocks` 0, headers
increasing) and ElectrumX, while healthy, correctly remained at daemon and
database height 0.

Summary: native ARM64 build validated; physical Raspberry Pi 5 deployment
validated; full incident-specific ARM64 consensus qualification pending.

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
| SAFE-promotion gate scope (second remediation round) | Corrected: promotion is per-endpoint, requiring that specific endpoint's own evidence be positively verified against a corroborated anchor or a comparably-evidenced reference, not merely that the overall crawl verdict came back clean; see [Electrum monitor](electrum-monitor.md) and [Security model](security-model.md#fail-closed) |
| Corrected gate re-run against the live ALENOC endpoint | PASS; real `run_discovery()` against `127.0.0.1:50001` with the real signed production policy (`core-safety/production/safe-core-policy.json`) and the real, live checkpoint header (hash matched the pinned `Ravencoin.INCIDENT_CHECKPOINT_HASH` exactly) classified `UNVERIFIED`, not `SAFE`, alone, as expected: one attested group is still not corroboration. Core and ElectrumX untouched (containers not restarted, no config changed) |

The release is certified, and the private local deployment has passed every
live gate through client `SAFE_CORE_VERIFIED`. The public endpoint is not yet
validated: TLS, external reachability, DuckDNS/CGNAT and renewal remain
operator/network steps. Do not publish the endpoint or call a wallet release
production-ready until that gate is complete too.
