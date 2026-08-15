# `rvn-consensus-2026-08-v1` revision 1 audit

The immutable profile digest is the SHA-256 of the canonical JSON profile after
removing only `profileRevision` and `profileSha256`:

`1342d079f2eef7ae0803a247d2908c4b031ee4a542b0f837210f92ba36ae27b2`

The certified release gate has 12 unconditional mandatory tests. The artifact
digest test is conditional on a pinned release artifact, so it was not required
for this candidate. `chainstate-rebuild` is explicitly a live-node gate and the
divergence review is advisory.

| Earlier requirement | Final evidence | Layer |
|---|---|---|
| Exact repository, tag, commit | `provenance-tag-resolves-to-commit`, `build-candidate-commit` | Release certification |
| Mainnet identity | `mainnet-genesis` | Release certification |
| Checkpoint 4,487,775 identity/behavior | `incident-checkpoint-hash` with exact candidate checkpoint data and wrong-hash control | Release certification |
| Checkpoint present on the deployed chain | `checkpoint-present-at-4487775` | Live node validation |
| nHeight binding | `nheight-binding-rejects-forged` with candidate contextual path | Release certification |
| Honest boundary headers | `post-boundary-valid-accepted` and `kawpow-header-shape` | Release certification |
| KAWPOW/PoW behavior | `core-unit-test-suite` (`pow_tests`, `kawpow_tests`) | Release certification |
| transfer_overflow consensus behavior | `transfer-overflow-deployment` candidate behavioral fixture | Release certification |
| transfer_overflow active in deployment | `transfer-overflow-active` | Live node validation |
| Block/index usability | `regtest-consensus-smoke`, `required-indexes-usable` | Release certification |
| Chainstate rebuild and canonical mainnet chain | `chainstate-rebuild`, `canonical-chain` | Live node validation |
| Asset consensus validation | `regtest-asset-consensus` wallet-independent candidate `asset_tx_tests` | Release certification |
| Asset index, RPC, persistence, and historical data | `assetindex-ready`, `asset-rpc-live` | Live node validation |
| txindex and REST in the deployment | `txindex-ready`, `required-indexes-usable` (release behavior) | Both layers |
| Full ElectrumX historical index | `ElectrumX index complete` gate | Live node validation |
| Real `server.ravencoin_backend` and client eligibility | `server-ravencoin-backend`, `client-safe-core-verified` | Live node validation |
| Skipped candidate suites | Four required suites executed; zero mandatory skips | Release certification |

No consensus or security-critical requirement was silently removed. The two
development-only harness limitations were converted into exact candidate probes;
deployment observations remain separate and pending until the protected live
stack completes its work.
