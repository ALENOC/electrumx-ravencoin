# GLM-5.3 SECURITY REMEDIATION REPORT — PR #2

## Baseline

Pre-remediation SHA:

`64ae6c666cacf09f9185081af523fe407b317d50`

Pre-remediation tree:

`f0b5c486216922a6c6842981a189359fef201748`

Pre-remediation audit:

`SECURITY_ADVERSARIAL_AUDIT_GLM53_PREMERGE.md` (verdict: `MERGE_BLOCKED`)

## Final Candidate Identity

- final branch: `work/chainstrap-ravenproject`
- final HEAD SHA: `bff0f71b2ca3df2091b2eb161c5f0d93560547df`
- final TREE SHA: `d70b766bfee0bf8063272376c932cfaea289e2d6`
- final base SHA: `master` = `1e6bbc62ad96a3d1dd6419b5becf965a5065114c` (unchanged)
- commits (oldest first):
  - `46fdaeea` fix(ci): isolate candidate certification from policy decision
  - `4f1ef66c` test(ci): reject forged candidate certification evidence
  - `7d7bd85d` fix(installer): install monitor controller in root-owned trusted path
  - `dfaa5a46` fix(db): validate full tx-hash index extent before serving
  - `f61da535` fix(chainstrap): enforce pending reindex state in core entrypoint
  - `d8b4772b` fix(installer): pin compose project name
  - `1ba88afe` chore(security): harden remaining low-cost audit findings
  - `bff0f71b` test(installer): patch Path.stat for controller ownership check

## Finding Matrix

| Finding | Severity | Status | Fix | Regression Test |
| ------- | -------- | ------ | --- | --------------- |
| GLM53-RVN-001 | HIGH | FIXED | certification/signing trust-domain separation | `core-safety/scripts/test_evaluate_certification.py` (13 tests, incl. A–F) + `tests/test_core_safety_workflow.py` |
| GLM53-RVN-002 | HIGH | FIXED | root-owned trusted controller path | `tests/test_installer_controller_trust.py` (8 tests, incl. A–E) |
| GLM53-RVN-003 | MEDIUM | FIXED | global hashes/tx_counts extent + zero-slot scan, repair refusal gates | `tests/server/test_db_hash_extent.py` (7 tests) |
| GLM53-RVN-004 | MEDIUM | FIXED | entrypoint-level ChainStrap gate | `tests/test_chainstrap_entrypoint_gate.py` (5 tests) |
| GLM53-RVN-008 | MEDIUM | FIXED | `-p` pin in installer+updater compose prefixes | `tests/test_compose_project_pin.py` (4 tests) |
| GLM53-RVN-009 | LOW | FIXED | controlled ChainError instead of reachable assert | `tests/test_remediation_hardening.py` |
| GLM53-RVN-014 | LOW | FIXED | `mktemp` for setup.sh .env rewrite | (class fix; shell syntax validated in CI) |
| GLM53-RVN-020 (keyId TypeError) | LOW | FIXED | type/length validation before dict lookup | `tests/test_remediation_hardening.py` |
| GLM53-RVN-020 (multi-arch manifests) | LOW | FIXED | host-target membership on declared set | `tests/test_remediation_hardening.py` |
| GLM53-RVN-020 (sig length pre-check) | INFO | FIXED | 64-byte check + ValueError catch in policy verifier | `tests/test_remediation_hardening.py` |
| migration `persist-credentials` | LOW | FIXED | false on checkout; token supplied only at push step | workflow structure |
| GLM53-RVN-005 | MEDIUM | PARTIALLY_FIXED | watcher state now authored by the trusted evaluator job (no candidate code in that job), but still transported via the repo-shared actions cache | — |
| GLM53-RVN-006 | MEDIUM | NOT_FIXED | x16r/x16rv2 git deps remain (commit-pinned); vendoring or removal is a packaging decision | — |
| GLM53-RVN-007 | MEDIUM | NOT_FIXED | runtime `requirements.txt` still unpinned/unhashed; needs a lock/hashes decision tied to the release-bundle model | — |
| GLM53-RVN-010 | LOW | NOT_FIXED | merkle binding of repair tx lists (trusted-Core model unchanged) | — |
| GLM53-RVN-011/012 | LOW | NOT_FIXED | installer TOCTOU windows (concurrent installs; storage-root symlink) — accepted residual | — |
| GLM53-RVN-013 | LOW/INFO | NOT_FIXED | monitor password via env_file (docker inspect visibility for docker-group members) | — |
| GLM53-RVN-015/016 | LOW | NOT_FIXED | ChainStrap decompression headroom heuristic / cross-archive duplicates | — |
| GLM53-RVN-017/018 | LOW | NOT_FIXED | Core P2P 0.0.0.0 publish (intended), host-netns overlay, ARG-overridable digests | — |
| GLM53-RVN-019/021 | INFO/scope | NOT_FIXED | bandwidth-controller parser still lives in the external monitor repo (pinned commit); unauditable from this tree | — |

## GLM53-RVN-001

New pipeline (`.github/workflows/core-safety-watch.yml`):

```
DISCOVERY (trusted)
  → CERTIFY (untrusted): orchestrator core-safety/scripts/run_candidate_certification.py
      builds candidate in container (unchanged build_candidate.sh),
      runs certify_core.py INSIDE the candidate image:
        --network none --cap-drop ALL --no-new-privileges
        workspace mounted read-only, datadirs in tmpfs, --report -
      report leaves the container over its stdout pipe; the orchestrator
      (which never executes candidate code) writes the raw evidence file
  → EVALUATE (trusted, executes no candidate code):
      core-safety/scripts/evaluate_certification.py validates completeness,
      identity binding, report digest, profile binding, schema, re-derives
      the verdict from per-test results via aggregate_state + the pinned
      profile, refuses on any claim/derivation mismatch, emits canonical
      reports + digest-bound evaluation-summary.json, authors watcher state
  → SIGNING (protected environment): consumes only canonical reports,
      verifies each digest against the trusted summary, verifies the
      evaluated set exactly matches discovery, fails on missing artifacts
```

Why a malicious candidate can no longer author the authoritative PASS: the workspace files a candidate process could write are no longer trusted input anywhere. The report travels over the container stdout pipe (candidate subprocesses inherit DEVNULL/captured pipes, not the container stdout); the deciding verdict is recomputed in a job that executes no candidate code from per-test results the evaluator cross-checks against the profile-derived required list; a forged `overall` whose results do not support it is refused (Test A), as are tampered digests (B), missing evidence (C), colliding identities (D) and foreign identities (E). Residual (documented): a candidate that escapes its container, or writes to the orchestrator's stdout pipe via `/proc` from inside the container, is outside this remediation's claim and remains a container-isolation trust assumption.

## GLM53-RVN-002

Root executes exactly `/usr/local/lib/electrumx-ravencoin/ravencoin-bandwidth-controller.py`. The installer (`install_trusted_controller`) places it with `install -o root -g root -m 0755` to a root-owned staging name inside the root-owned 0755 directory, then `mv -fT` (atomic rename); no command writes the final path directly and no user-writable intermediate state exists. `verify_trusted_controller` fails unless the copy is `root:root`, not group/world-writable, and (for non-root invocations) the containing directory is root-owned and not group/world-writable; `install_controller` runs it before enabling the unit, and the unit's `ExecStart` references only the trusted path (`vendor/ravencoin-node-monitor` appears nowhere in the unit). Uninstall removes the copy and directory. The operator-owned vendor checkout remains in the tree but is dead weight for the service: modifying it cannot change what systemd executes.

## GLM53-RVN-003

`fs_metadata_needs_recovery` (electrumx/server/db.py) now checks, after the tip-block checks: `hashes_file.logical_size() == state.tx_count * 32` (truncation and unexpected growth both trigger recovery) and a streamed whole-history scan for all-zero 32-byte slots (`LogicalFile.find_zero_slot`, new in electrumx/lib/util.py, chunked with boundary carry — the signature of a sparse hole from a past-EOF write). `_repair_trailing_fs_metadata` (block_processor.py) refuses — raising DBError, i.e. startup fails closed, full reindex required — when the hash extent is below the recovery-window base, extends beyond the committed tx_count, or contains a zero slot below the window; it verifies the boundary slot and the post-write extent. Legitimate crash tails inside the 64-block window still repair exactly as before; the healthy path is unchanged (verified by the pre-existing crash-consistency tests).

## GLM53-RVN-004

`docker/core/entrypoint.sh` now refuses normal startup (exit 1, loud stderr) when `/var/lib/ravencoin/.chainstrap-blocks-ready.json` exists and either `.chainstrap-reindex-complete` is absent or its content (sha256 of the blocks marker) does not match. No override environment variable exists — the gate is unconditional. Normal startup with no markers and startup after a completed validated reindex are unchanged. The one-shot reindex container bypasses this entrypoint by Compose `entrypoint:` override, so the gate cannot deadlock it. The data/config directory roots are parameterized (`RAVENCOIN_DATA_DIR`/`RAVENCOIN_CONFIG_DIR`, same defaults) so the gate is testable without root; the marker paths derive from the data dir.

## GLM53-RVN-008

`_compose_prefix` in `electrumx-ravencoin-install.py` and `core-safety/scripts/update_runtime.py` now start every invocation with `docker compose -p electrumx-ravencoin`; the explicit flag outranks the `COMPOSE_PROJECT_NAME` environment variable in Compose v2, so the fresh-install preflight label, the monitor/controller container names and teardown all operate on the pinned namespace regardless of operator environment.

## Additional Hardening

- migration workflow: `persist-credentials: false` on the signing-job checkout; the publish step configures its own authenticated remote only at push time.
- `policy.py` / `update_manifest.py`: `keyId` must be a string ≤128 chars before any dict lookup; malformed types fail closed as PolicyError/ManifestError.
- `policy.py`: 64-byte Ed25519 signature-length pre-check and `except (InvalidSignature, ValueError)`.
- `update_decision.py`: comma-form multi-architecture manifests accepted exactly when the host platform is among the declared targets; non-string architectures still refused.
- `block_processor.spend_utxo`: short `fs_tx_hash` reads now raise a descriptive ChainError instead of a reachable assert killing the sync task.
- `setup.sh`: unpredictable `mktemp` name for the `.env` rewrite.
- Watcher processed-state (GLM53-RVN-005 partial): authored exclusively by the trusted evaluator job; untrusted jobs can no longer write it.

## Test Results

Local (Python 3.12.3, venv with requirements.txt + pytest 8.x, cryptography 41.0.7, pytest-asyncio; clean tree at `bff0f71b`):

- `pytest tests/ core-safety/scripts/ -q` — **795 passed, 13 skipped, 1 warning** (pre-existing deprecation warning in mempool.py)
- `python -m compileall -q electrumx contrib/bootstrap` — OK
- `flake8` (CI target list + all new files) — clean
- Compose validation: `docker compose config --quiet` for base, +tls, +chainstrap, existing-core, +storage, storage+chainstrap+monitor — OK
- `sh -n` on all shipped shell scripts — OK

New regression coverage: 13 evaluator tests (incl. required Tests A–F), 8 controller-trust tests (incl. required Tests A–E), 7 hash-extent tests, 5 entrypoint-gate tests, 4 project-pin tests, 8 hardening tests; updated workflow-structure tests.

Mutation validation (performed, then fully restored; no MUTATION artifacts remain — verified by grep):
- RVN-001: reverting the derivation check to trust the claimed `overall` makes Tests A, unavailable-results and mutation-style tests fail.
- RVN-002: reverting `ExecStart` to the operator-writable vendor copy makes the ExecStart-binding and vendor-isolation tests fail.

## GitHub Actions

- Run `32506061481` (PR, first push): tests 3.10 FAILED (test monkeypatched `os.stat`, not effective on Python 3.10's pathlib) — root-caused, fixed in `bff0f71b`; 3.11/3.12/artifacts passed.
- Runs `32507294895` (push) and `32507299555` (PR) on final HEAD `bff0f71b`: **all jobs PASS** — tests 3.10, tests 3.11, tests 3.12, Core artifact amd64, Core artifact arm64, and container (multi-arch image build, 13m55s). Both runs concluded success.

## Remaining Findings

Not fixed (deliberate, non-blocking): GLM53-RVN-005 (cache transport of watcher state), RVN-006 (x16r git deps), RVN-007 (runtime dependency pinning — needs a lock/hashes decision tied to the release-bundle model), RVN-010 (merkle binding of repair tx lists, consistent with trusted-Core model), RVN-011/012 (installer TOCTOU windows), RVN-013 (monitor password in env_file), RVN-015/016 (ChainStrap compression/duplicate heuristics), RVN-017/018 (P2P exposure/host-netns overlay/ARG-overridable build digests), RVN-019/021 (`:ro` socket mount is not a write barrier; bandwidth-controller parser still unauditable from this repo). Also documented residual for RVN-001: container-escape or `/proc` stdout-injection by candidate code is assumed blocked by container isolation.

## Remaining Production Release Gates

Explicitly preserved (none were weakened or bypassed):

- signed safe-Core policy v3 promotion (production trust set remains empty until the protected signing stage runs; the unsigned v3 body remains non-authoritative);
- production ElectrumX release/update signing-key ceremony (all production update/install paths still fail closed without it);
- clean interactive installation qualification (ChainStrap + Core + ElectrumX + Node Monitor + restart/persistence);
- external Node Monitor bandwidth-controller parser audit (root-run hostile-input surface, still out-of-tree).

## Re-Audit Requirement

`THE PREVIOUS AUDIT PASS/FAIL STATE DOES NOT APPLY TO THE NEW TREE.`

The remediation changed security-relevant code (CI trust architecture, installer, DB recovery, entrypoint, compose invocation, crypto input validation). A complete GLM-5.3 adversarial re-audit is required against the exact final SHA `bff0f71b2ca3df2091b2eb161c5f0d93560547df` and TREE `d70b766bfee0bf8063272376c932cfaea289e2d6` before merge.
