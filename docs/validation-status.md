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
| ElectrumX historical index | IN PROGRESS; ElectrumX db height well behind Core tip, indexing from genesis |
| Live `server.ravencoin_backend` | PASS; live call returned `coreSafe: true`, `networkMatches: true`, `backendSynchronized: true`, `kawpowHeightValidation: true`, `checkpoint4487775: true` |
| Client `SAFE_CORE_VERIFIED` against live server | PENDING; requires ElectrumX historical index to finish first |
| Public CA-valid TLS endpoint | PENDING operator/network validation |

The release is certified. The deployment is not yet declared fully validated.
Do not publish the endpoint or call a wallet release production-ready until the
live gates are complete.
