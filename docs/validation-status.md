# Validation status

This is the single human-readable status reference for the current development
line. It deliberately separates historical evidence from evidence that applies
to the exact Core/release identity currently pinned by the branch.

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Core certification](core-certification.md) · [Fast bootstrap](fast-bootstrap.md)

## Current branch identity

The integration branch currently deploys:

- Core repository: `RavenProject/Ravencoin`
- Core version/tag: `4.8.0` / `v4.8.0`
- Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- ElectrumX release line: `1.13.6`

Repository plus exact commit is the trust identity. Tree-equivalent or
historically tested commits are not interchangeable with this identity.

## Core certification and policy migration

| Item | Current status |
|---|---|
| RavenProject v4.8.0 candidate | **CERTIFICATION PASS** for exact commit `22549129888d02e0e08fcdb9f96f3c699167e774`; evidence is under `core-safety/production/certifications/` |
| Reviewed RavenProject-only policy body | `safe-core-policy-v3.unsigned.json`: `RavenProject/Ravencoin@225491...` is `KNOWN_SAFE`; historical `2miners/Ravencoin@b60f50...` is `REVOKED` |
| Current signed `safe-core-policy.json` in this branch | **v3 production policy**, signed and verified under the pinned policy public key |
| v2 -> v3 signing | **PASS** in the protected `core-safety-signing` environment; the one-shot workflow published the signed artifacts and removed itself |
| Candidate discovery going forward | RavenProject-only; a repository/version name alone never grants trust |

The unsigned v3 file remains review material, not a trust anchor. Production
trust comes only from the signed current policy.

## Single-file installer trust status

The installer has two independent trust domains:

1. the ElectrumX release/update manifest key; and
2. the safe-Core policy key.

The safe-Core policy public key is pinned independently in the single-file
bootstrap. A bundle is accepted only if the policy verifies under that key and
the release manifest's exact Core repository, commit, tag, version, policy
version and certification report digest all match one `KNOWN_SAFE` policy
entry with passing certification evidence.

The current ElectrumX release/update public key is independently authenticated
and pinned into each production release candidate. Its private key remains
offline and is unavailable to CI; CI builds only unsigned candidates. The
historical v1 public key and attestation remain in the repository as immutable
evidence, but that CI-held signing identity was retired on 2026-08-22 and grants
no current signing authority. The complete public-key identities, signing
domains and transition semantics are recorded in
`core-safety/production/UPDATE-SIGNING-KEY-CEREMONY.md`.

For pre-release end-to-end testing only,
`core-safety/scripts/build_local_release_validation_bundle.py` creates an
explicit **NON-PRODUCTION** bundle. It uses the complete git-tracked source tree
and exact pinned Node Monitor checkout, signs the reviewed RavenProject-only v3
policy body and release manifest with two fresh ephemeral keys, writes only the
public keys, and never writes either private key to disk. Those keys are usable
only when the operator explicitly passes `--local-release-validation-dir` and
cannot replace either production trust root.

## CI and security regression coverage

The draft integration PR is the current CI gate. Its matrix runs:

- the full pytest suite on Python 3.10, 3.11 and 3.12;
- static safety checks;
- Compose model validation;
- ElectrumX multi-architecture container builds;
- bundled Core artifact build/qualification on amd64 and arm64.

Do not copy an old aggregate test count into release claims: the authoritative
result is the CI run on the exact commit being reviewed.

Recent security work includes load-bearing regression coverage for, among other
items, short `fs_tx_hash` reads, worker-thread mempool side-dictionary races,
legacy protocol handler preservation, RavenProject source-pin consistency,
signed-policy migration, ChainStrap bounds/integrity, monitor trust promotion,
and installer dual-trust-root enforcement.

## ChainStrap Fast Verified Bootstrap

The current pinned snapshot is RVN mainnet at block **4,501,329**, block hash
`000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48`,
from `chainstrap/chainstrap.github.io` commit
`c4ed0750603ea59823cdd21854d7eb75fe365928`.

ChainStrap is transport only. The downloader verifies pinned CIDs, byte counts
and SHA-256 values and extracts only raw `blk*.dat` files. The bundled Core then
performs an offline `-reindex -assumevalid=0` with the required indexes before
normal Core or ElectrumX startup is permitted.

A real interactive validation attempt discovered a ChainStrap-bootstrap
failure. That run is **invalid and is not a release PASS**. The installer now
preserves useful ChainStrap diagnostics before teardown, refuses silent P2P
fallback, removes the failed fresh run including its named volumes, and refuses
a new fresh install if Compose-labelled runtime state already exists. A new
clean interactive run is still required to establish a PASS for the complete
ChainStrap path.

## Node Monitor integration

The optional bundled `ravencoin-node-monitor` remains a separate pinned source
checkout. The default integration:

- runs unprivileged and read-only;
- drops Linux capabilities and enables `no-new-privileges`;
- receives no Docker socket;
- reaches ElectrumX admin RPC by sharing the ElectrumX network namespace rather
  than publishing that RPC;
- exposes the dashboard on host loopback `127.0.0.1:8899`;
- keeps history in memory by default;
- keeps the optional root-owned bandwidth/connection controller in a separate,
  explicit opt-in security domain.

A full fresh interactive install must still validate the actual Node Monitor
container, Core/ElectrumX connectivity, history/mempool/resource/network data
and restart behavior on the newly created stack.

## Discovery and backend trust

`server.ravencoin_backend` remains additive and does not change legacy Electrum
protocol negotiation. The server reports sanitized backend evidence; clients
and the monitor still apply their own policy.

The trust pipeline intentionally keeps these states separate:

`DISCOVERED -> CAPABILITY_SUPPORTED -> BACKEND_VERIFIED -> TRUSTED_BY_OPERATOR`

Seed membership is never trust. The current seed preference is the RavenTag
reference endpoint first, then the three Cipig Ravencoin endpoints on their
actual ports (SSL `20051`, TCP `10051`), followed by rvn4lyfe and Moontree.
Multiple Cipig hosts remain one operator group for diversity purposes.

## Historical evidence that does **not** automatically transfer to the current pin

Earlier live and physical ARM64 validation was performed against the historical
v4.8.0 identity `b60f50e04f1fba425b28804e61be2694faaf3469`. That work remains useful
historical evidence, including the Raspberry Pi 5 build/storage observations
and the earlier synchronized-node backend/checkpoint/asset checks, but it must
not be presented as live validation of the current exact RavenProject commit
`22549129888d02e0e08fcdb9f96f3c699167e774`.

The current commit has its own certification and CI artifact gates. The current
installer/ChainStrap/Node Monitor composition still requires a new clean
end-to-end run.

## Current release gates

The branch is not production-release-ready until all of these are true on the
exact final commit:

1. full CI matrix: all required jobs PASS;
2. the promoted RavenProject-only safe-Core policy v3 verifies under the pinned
   policy public key;
3. the release artifacts and manifest are generated and signed only by the
   protected ElectrumX release publication workflow;
4. a fresh host validation starts with no previous `electrumx-ravencoin`
   containers/networks/volumes or installer state;
5. the real interactive single-file installer is traversed from its menu,
   selecting ChainStrap and Node Monitor without post-menu workarounds;
6. ChainStrap download/integrity/offline-reindex succeeds, or the run is
   correctly reported as failed (never silently converted into P2P success);
7. fresh Core starts on mainnet and its live identity/sync/checkpoint evidence
   agrees with the signed release metadata;
8. fresh ElectrumX indexes from that Core and `server.ravencoin_backend` agrees
   with live `raven-cli` evidence;
9. Node Monitor is healthy and connected to the new Core/ElectrumX services;
10. stop/restart persistence succeeds without data loss;
11. rerunning the installer against the installed node behaves safely and does
    not duplicate or silently destroy state.

Only a run that satisfies those gates without manual post-menu repair counts as
the qualifying installer validation.

## Public endpoint

Publishing a public Electrum TLS endpoint is a separate operational gate from
software correctness. DNS, external reachability, CA-valid TLS and renewal must
be verified from outside the operator's LAN before claiming that a particular
host is a validated public service. Core JSON-RPC and REST must not be exposed
publicly.
