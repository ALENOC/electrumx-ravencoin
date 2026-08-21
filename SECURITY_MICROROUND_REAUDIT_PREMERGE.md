# Security Micro-Round Re-Audit — PR #2

Date: 2026-08-21

Target branch: `work/chainstrap-ravenproject`

This micro-round was performed after the GLM-5.3 pre-merge remediation report and addresses the two residual findings explicitly left for a final focused pass.

## Scope

Security-sensitive changes reviewed in this round:

- `core-safety/scripts/run_candidate_certification.py`
- `core-safety/scripts/test_run_candidate_certification.py`
- `electrumx-ravencoin-install.py`
- `tests/test_installer_controller_trust.py`
- README documentation of the production single-link installer and release-readiness boundary

The earlier broader remediation remains unchanged except for the focused fixes below.

## REAUDIT-001 — certification exit-code / report-channel binding

**Status: FIXED**

The certification orchestrator no longer accepts process exit code `0` or `1` independently of the semantic report outcome.

The trusted capture layer now requires:

- exactly one `CERTIFICATION_REPORT_JSON=` frame on container stdout;
- valid JSON object payload;
- an outcome in the explicit allow-list;
- `CERTIFICATION_PASSED` **only** with exit code `0`;
- every non-PASS terminal/review outcome **only** with exit code `1`;
- any mismatch, duplicate frame, missing frame, malformed JSON, non-object payload, or infrastructure exit outside `{0,1}` fails closed and produces no accepted evidence.

This removes ambiguity at the stdout trust boundary and binds the process-status channel to the semantic certification channel.

Regression coverage is provided by `core-safety/scripts/test_run_candidate_certification.py`, including PASS/0, non-PASS/1, mismatch cases, duplicate/missing frames, malformed JSON, non-object JSON and unexpected infrastructure exits.

## REAUDIT-002 — same-user race before privileged controller installation

**Status: FIXED**

The root systemd controller is now bound to the exact controller bytes authenticated by the already verified signed release bundle, rather than trusting the extracted operator-writable tree as the reference source.

The installer now:

1. derives the expected SHA-256 directly from `CONTROLLER_SCRIPT` inside the immutable in-memory release bundle before extraction is trusted for privileged execution;
2. copies the extracted controller into a root-owned, non-user-writable, unpredictable staging path;
3. hashes the root-owned staged copy and compares it with the signed-bundle-derived expected digest;
4. refuses promotion on mismatch and removes the staging file;
5. atomically renames only a digest-matching staged file into the fixed root-owned execution path;
6. verifies ownership/mode and the final SHA-256 again before systemd enable/start.

A same-user process that modifies the extracted vendor tree between validation and privileged copy can therefore at most cause installation failure. It cannot cause modified bytes to reach the root execution path.

Regression coverage in `tests/test_installer_controller_trust.py` includes direct signed-bundle digest derivation, missing-member failure, final-copy digest mismatch, unpredictable root-owned staging, digest-bound enable ordering, and a simulated malicious content race that verifies no atomic rename to the trusted path occurs after a digest mismatch.

## README / installation UX

The README now documents a stable production installation URL:

`https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py`

The documented safe flow is download-then-run, not `curl | python`/`curl | bash`. The README also documents `--check-only`, P2P bootstrap, monitor opt-out, explicit privileged-controller opt-in, and clearly separates merge readiness from production-release readiness.

The source-tree installer continues to fail closed until the production release/update public key is provisioned through its dedicated ceremony.

## Residual production gates

This micro-round is a **pre-merge** security closure and does not claim that a production release is already publishable.

The following remain release/deployment gates and were not weakened:

- RavenProject-only safe-Core policy promotion through the protected signing path;
- production ElectrumX release/update signing-key ceremony;
- clean interactive fresh-install qualification (ChainStrap + Core + ElectrumX + Node Monitor + restart/persistence);
- final release audit/gate against the exact production commit/tree and signed artifacts;
- external Node Monitor privileged parser review where applicable.

## Merge verdict

Subject to a green GitHub Actions matrix on the final PR head and GitHub reporting the PR mergeable against current `master`:

`MERGE_ALLOWED`

This verdict is for merging the integration branch into `master`. It is **not** a production release approval.
