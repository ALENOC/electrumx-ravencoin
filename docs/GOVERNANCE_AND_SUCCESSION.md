# ElectrumX-RVN governance and succession

ElectrumX-RVN must be able to survive its original maintainer.  This
document is the canonical description of how project trust is rooted
today, how it can rotate, and what happens when maintainers disappear.

Precise status language, used consistently:

* **TECHNICALLY SUPPORTS THRESHOLD GOVERNANCE**: the format, verifier,
  rotation, anti-rollback and adoption mechanism exist and are tested
  (core-safety/scripts/governance.py, tests/test_governance_succession.py).
* **CURRENT GOVERNANCE IS ACTUALLY DISTRIBUTED**: false today.  The
  active deployed roots are single-maintainer keys.  The project is
  therefore "founder-independence capable", NOT founder independent,
  until independent real maintainer keys are added through a signed
  transition.

## Trust root inventory (repository evidence)

| Trust domain | Root | Classification | Evidence |
| --- | --- | --- | --- |
| Release/update signing | Ed25519 key id `6f4f944c9b0a19a1`, pinned in installer | SINGLE-MAINTAINER | `electrumx-ravencoin-install.py:RELEASE_PUBLIC_KEY_HEX`; verifier `core-safety/scripts/update_manifest.py:verify_manifest`; domain `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1` (update_manifest.py) |
| Release anti-rollback | per-node high-water state | SELF-SIGNED / LOCAL | `/var/lib/electrumx-ravencoin/security-state.json` (locator module embedded in installer; `enforce_high_water`) |
| Safe-Core policy signing | pinned public key | SINGLE-MAINTAINER | `core-safety/production/core-policy-signing-public-key.hex`; verifier `core-safety/scripts/policy.py:verify_policy`; domain `ALENOC-RVN-CORE-POLICY-v1` |
| Policy anti-rollback | policyVersion floor | SELF-SIGNED / LOCAL | `core-safety/scripts/policy.py`; observer side `network_observer/store.py:record_policy_version` |
| Key custody/recovery | offline private key + ceremony docs | SINGLE-MAINTAINER | `core-safety/production/UPDATE-SIGNING-KEY-CEREMONY.md`, `update-signing-key-attestation.json`; policy: never in CI, never in repo (`docs/OFFLINE_RELEASE_SIGNING_1.13.10.md`) |
| Download origin | github.com/ALENOC/electrumx-ravencoin + release gateway allowlist | EXTERNAL-UPSTREAM (transport only; authority is the signature) | installer `--secrets`/release URL constants; ChainStrap gateways pinned in bootstrap image |
| Signed directory | observer-local keys | SELF-SIGNED / LOCAL | `network_observer/directory.py` (domain `ALENOC-RVN-ELECTRUM-DIRECTORY-v1`, predates Phase 1 and shipped in released 1.13.7+; kept for verifiability of already-signed directories) |
| Observer / operator / snapshot identities | local self-generated keys | SELF-SIGNED / LOCAL | `network_observer/observer.py`, `operators.py`, `snapshot.py` (neutral `RAVENCOIN-NETWORK-OBSERVER-*` domains, never released before the rename) |
| Ravencoin consensus | RavenProject/Ravencoin Core | NO TRUST AUTHORITY of this project | external; the observer and this file never modify consensus |
| GitHub write access | repository owner | INFORMATIONAL ONLY: can publish unsigned artifacts, cannot forge signatures; every trust decision verifies Ed25519 first | all verifiers above |

Where possession of GitHub write access would be "enough": nowhere
cryptographically.  It can serve attackers unsigned content, which
every verifier here refuses; the residual risk is social (users
trusting files without verifying), documented in the installer
fingerprint instructions.

## Governance domains (never merged)

* RELEASE GOVERNANCE (`electrumx-release`): signs update manifests.
* CORE SAFETY GOVERNANCE (`electrumx-core-safety`): signs safe-Core
  policies.  Separate keys, separate domain string: a release key
  cannot mark a Core build safe, and a core-safety key cannot sign a
  release (tested).
* Observer, operator and snapshot identities: local self-signed
  models, NOT governed here, with no path into software trust.
* Ravencoin consensus: entirely external.

## The governance policy format

A policy body is `{schemaVersion: 1, domain, epoch, threshold,
createdAt, maintainers: [{keyId, publicKey}...]}`; the cryptographic
identity is the key, names are informational.  Validation is fail
closed: exact schema, known domain, `1 <= threshold <= len(maintainers)`,
no duplicate keys, keyId must derive from publicKey, Ed25519 only.
N-of-M is N distinct valid signatures from the active policy's M keys:
duplicates count once, unknown keys count zero, no custom threshold
cryptography.  `policy_digest` is canonical and deterministic.

## Rotation, revocation, epochs

A successor policy is authorized only by a signed transition binding
`currentPolicyHash`, `nextPolicyHash`, `fromEpoch`, `toEpoch` and
`domain`, signed by at least `threshold` of the CURRENT maintainers.
The next policy can never sign itself into authority; removed keys
stop counting at the next epoch; new keys cannot authorize earlier
epochs.  Emergency revocation is an ordinary transition (drop the
compromised key, bump the epoch).  If the remaining keys cannot meet
the threshold, governance STOPS: that failure mode is deliberate, and
there is no inactivity escape hatch, no hidden recovery key, no
"if no maintainer signs for N days" rule anywhere in this codebase.

## Migration from the current single key (phases)

* PHASE 0 (today): installed nodes verify the single release key.
* PHASE 1 (transition release): an ordinary release, signed exactly as
  installed nodes expect, ships governance verification and carries a
  genesis governance document: the epoch-1 policy (real maintainer set,
  recommended target 3-of-5 once real co-maintainers exist) signed by
  the EXISTING release key (`verify_policy_document`).  Installed
  1.13.x nodes parse nothing new; nothing is reinterpreted as 3-of-5.
* PHASE 2 (threshold governance): releases may carry `signatures[]`
  verified against the active policy
  (`verify_release_governance`), with all existing rollback, freshness
  and digest rules unchanged.  Popularity (observer adoption metrics)
  never influences signature validity.

If the production ceremony for the genesis document is pending, the
status is: IMPLEMENTATION COMPLETE, PRODUCTION ACTIVATION PENDING
SIGNING CEREMONY.  No development key may ever stand in.

## Successor (fork) adoption

If governance is permanently unavailable, the community can fork (the
licence permits it) and continue; existing nodes keep running and the
updater refuses unauthenticated successors.  Adopting a successor root
on a node is ONE explicit local action
(`governance.adopt_successor`): it displays the old and new policy
fingerprints, epochs, domains and source identity, requires the
COMPLETE expected 64-hex fingerprint on the command line (never
"trust latest"), requires explicit confirmation, persists the adopted
root and records the event; anti-rollback applies afterwards.  It can
never be triggered remotely, by the Network Observer, by endpoint
majorities, or by repository redirects.

## Repository relocation

A move (for example ALENOC to RavencoinCommunity) is a field inside a
policy transition signed by the active threshold: relocation is
authenticated exactly like a key change.  A URL or redirect alone
creates zero trust; download location and cryptographic authority stay
separated.

## Installer bootstrap

Fresh installs have no local trust state.  The installer pins the
release public key and (from the transition release on) the initial
governance root in the package; the first-install threat model is
explicit: installation source is not proof, fingerprints are published
out of band in the release documentation, artifacts remain signed, and
there is no TOFU from arbitrary repository content.

## What happens if the original maintainer disappears?

* deployed nodes keep serving: Ravencoin Core, ElectrumX, wallet RPC,
  `server.ravencoin_backend` and legacy Electrum clients never depend
  on governance availability (tested: the server never imports
  governance);
* the automatic updater fails closed: without an authorized quorum
  there are no valid future releases, and that is safer than accepting
  unauthenticated successors;
* an already-authorized maintainer quorum continues normally through
  signed transitions;
* if the governance quorum is completely lost, operators who want to
  follow a successor perform the explicit adoption once, binding the
  successor's exact fingerprint;
* no hidden founder dependency is required for continued network
  operation.

## What the Network Observer may and may not do

It REPORTS adoption, chain agreement, asset agreement, backend
identity and index health.  It MUST NOT declare releases authorized,
rotate governance keys, lower thresholds, trust forks, modify
safe-Core policy, or treat popularity as authority.  Observation and
governance are separate systems with separate keys; no code path
connects observer output to any trust decision.

## Compatibility guarantees

`server.ravencoin_backend` and the legacy Electrum protocol surface
are untouched by this workstream (regression suites
`tests/test_ravencoin_backend_contract.py` and the existing session
tests remain release blockers).  Governance is update-layer
infrastructure: wallets and legacy clients never see it.
