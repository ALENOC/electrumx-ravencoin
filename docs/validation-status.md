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

## Live deployment

| Gate | Status |
|---|---|
| Core reindex | IN PROGRESS |
| Canonical checkpoint observed | PENDING |
| `transfer_overflow` active on deployment | PENDING |
| txindex, assetindex and REST live | PENDING; this is the end-to-end synchronized-node gate |
| Asset RPC with historical data | PENDING |
| ElectrumX historical index | IN PROGRESS |
| Live `server.ravencoin_backend` | PENDING final synchronized evidence |
| Client `SAFE_CORE_VERIFIED` against live server | PENDING |
| Public CA-valid TLS endpoint | PENDING operator/network validation |

The release is certified. The deployment is not yet declared fully validated.
Do not publish the endpoint or call a wallet release production-ready until the
live gates are complete.
