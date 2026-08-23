# Release artifact revisions and 1.13.3 trust migration

This document defines the release/update trust model introduced by ElectrumX-RVN 1.13.3. It is intentionally separate from the Ravencoin Core safe-policy trust root. Nothing in this mechanism changes the pinned Ravencoin Core 4.8.0 identity or makes release metadata a source of consensus trust.

## Withdrawn 1.13.2 candidate

Release 1.13.2 was built and offered as a candidate but failed real hardware qualification during the staged apply of the legacy 1.13.1 upgrade. Its GitHub Release was deleted and 1.13.2 was never published as an installable release. The git tag `v1.13.2` is retained only as a historical trace of that failed candidate. No operator should ever install, target or trust 1.13.2, and the version number is not reused. The first published release carrying the manifest-v2 trust root is 1.13.3.

## 1.13.8

`v1.13.8` exists because the published 1.13.7 bootstrap classifies ChainStrap
ZIP members by location. Any member under `blocks/` that is not an allowlisted
raw block was fatal, so a fresh install against the current upstream snapshot
aborted on part 14/17 at `blocks/index/004089.ldb`. 1.13.8 classifies members
structurally: an allowlisted raw `blocks/blk*.dat` member is extracted, any
other safe regular member is ignored wherever it appears, and unsafe entries
still fail closed.

That is a change of executable bootstrap behaviour, so it could not ship as
another artifact revision of 1.13.7: the frozen-scope rule below requires a
version bump for a behavioural change. 1.13.8 starts again at
`artifact_revision 0`.

The extraction allowlist is unchanged. Only `blocks/blk*.dat` is ever written
into the Ravencoin datadir, and ChainStrap remains transport acceleration rather
than a consensus trust source.

Qualification evidence is recorded in `docs/HARDWARE_QUALIFICATION_1.13.8.md`.

## 1.13.6

`v1.13.6` exists because the published 1.13.5 installer aborts a fresh
installation whenever the advanced host controller is requested. It created the
root-owned trusted controller directory with `mkdir -p -o root -g root -m 0755`,
and `mkdir` has no ownership flags, so the run ends with
`mkdir: invalid option -- 'o'`. 1.13.6 uses `install -d -o root -g root -m 0755`.

That is a change of installer behaviour, so it could not ship as another
artifact revision of 1.13.5: the frozen-scope rule below protects operators of
1.13.5 `artifact_revision 1`, which was qualified and published as installable,
and requires a version bump for a behavioural change. 1.13.6 starts again at
`artifact_revision 0`.

1.13.5 `artifact_revision 1` remains the last installable 1.13.5 artifact. It is
not withdrawn, but it cannot install the advanced host controller.

Qualification evidence is recorded in `docs/HARDWARE_QUALIFICATION_1.13.6.md`.

## 1.13.5 artifact revisions

`v1.13.5` carries two artifact revisions. Only revision 1 is qualified and installable.

`artifact_revision 0` was built, signed and uploaded to the `v1.13.5` release, then failed real hardware qualification of the ordinary 1.13.4 -> 1.13.5 update path on an installation adopted from legacy 1.13.1. The post-release-switch storage proofs rejected the plain Docker named volumes that adoption preserves, and rollback failed the same proof, so the transaction stopped fail-closed and required operator intervention. Revision 0 was withdrawn and its assets were replaced in place. No operator should install, target or trust revision 0.

`artifact_revision 1` fixes that defect, passed hardware qualification, and is the artifact published on the `v1.13.5` release. Its provenance `sourceCommit` is `5b38d0119ae7233b7b3dac7f4a1e2c860fde8f76`. The git tag `v1.13.5` still points at the revision-0 release-preparation commit `00776bdb0ab2f0581cdc677da61e137df13fdb3e` and was deliberately not moved, so the tag does not identify the published bytes. Release identity for 1.13.5 is the signed manifest with its `artifactDigest` and `provenanceDigest`.

Revision 1 changes updater code inside the bundle and is therefore not scope-preserving against revision 0. That is not a violation of the frozen-scope rule below: the rule protects operators of a revision that was actually qualified and published as installable, and revision 0 never reached that state. The offline scope verifier must not be used to compare revision 0 with revision 1.

Full evidence is recorded in `docs/HARDWARE_QUALIFICATION_1.13.5.md`.

## 1.13.1 cannot auto-update to 1.13.3

An existing ElectrumX-RVN 1.13.1 node **cannot and must not auto-update to 1.13.3**.

There are two independent reasons:

1. the 1.13.1 updater accepts release-manifest schema v1 and rejects the v2 schema introduced by 1.13.3; and
2. 1.13.1 knows only the retired release/update trust root. It does not know the replacement Ed25519 public key used for 1.13.3 and later manifests.

There is deliberately no bridge manifest signed by the retired key. The retired key does not sign, certify, endorse, or attest its own replacement.

### Manual operator upgrade path

The operator must perform the transition explicitly:

1. Obtain the new Ed25519 release/update **public** key through an authenticated out-of-band channel controlled independently of the 1.13.1 release/update key. Preferably compare its full 64-hex fingerprint through two independent authenticated channels.
2. Do not accept a statement merely because it is signed by the retired 1.13.1 update key. Such a statement is not a trust-root migration mechanism.
3. Download the reviewed 1.13.3 installer, signed manifest, provenance and checksums through the normal distribution channel.
4. Verify the v2 manifest directly against the independently authenticated replacement public key, then verify the installer, bundle and provenance digests bound by that manifest.
5. Provision the host-wide anti-rollback locator as described below. For a root installation this is created only in the root-owned `/var/lib/electrumx-ravencoin` namespace. An unprivileged installation requires an administrator-provisioned root-owned locator.
6. Perform the 1.13.3 installation/upgrade manually. Automatic update discovery may be enabled only after the new updater and new release/update public key are installed.

This is a deliberate trust discontinuity. A compromised retired signing key therefore cannot authorize the new root of trust.

Legacy updater state is not silently upgraded into a revision-aware identity. If an existing state file has a `currentRelease`, `lastKnownGoodRelease`, or pending manifest that lacks authenticated `artifact_revision`, `artifactDigest`, or `provenanceDigest` identity, the 1.13.3 state loader refuses it. It does not invent `artifact_revision = 0`. The only supported way forward is the separately authenticated manual trust transition above: authenticate the replacement public key out of band, verify the complete 1.13.3 v2 release identity, and establish fresh revision-aware operational/high-water state from that verified release.

## Manifest v2 and `artifact_revision`

Manifest schema v2 adds the signed fields `artifact_revision` and `provenanceDigest`.

Release identity is ordered first by `electrumxVersion`, then by `artifact_revision`; equality at one version/revision additionally requires both `artifactDigest` and `provenanceDigest` to be present, well formed and identical. `artifact_revision` is a canonical, non-negative integer and is monotonic within one ElectrumX version.

- a higher semantic version is a new software release;
- the same version with a higher `artifact_revision` is a reviewed artifact revision;
- a lower semantic version or lower revision is a rollback and is rejected;
- the same version and revision with a different artifact or provenance digest is equivocation and is rejected;
- missing revision data is a refusal, not version-only compatibility;
- malformed revision data is a distinct refusal, not an older revision;
- missing or malformed digest data is a refusal and can never be classified as sameness;
- only the same version, revision, artifact digest and provenance digest is the same artifact identity.

There is one canonical ordering implementation in `core-safety/scripts/electrumx_core_safety/artifact_revision.py`. The legacy top-level `artifact_revision` module is only an alias to that same Python module object; it defines no independent enums or ordering policy. Updater eligibility delegates to the canonical implementation rather than maintaining a second version/revision comparison.

A revision-only promotion is informational for an already-running node. It must not stop services, rebuild images, reindex Ravencoin Core, alter databases, or otherwise change the running node. Only the verified release/high-water records advance.

## Frozen scope for same-version revisions

The offline scope verifier compares the actual previously published release artifacts with the candidate structurally. It does not compare two newly rebuilt gzip streams.

For the same ElectrumX version, application logic, installer behavior, gateway policy, policy caps, Core pins, signing keys and other executable behavior are frozen. Only the reviewed ChainStrap floor evidence, monotonic `artifact_revision`, timestamp, generated provenance, source-commit provenance metadata and the digests that necessarily bind those reviewed bytes may change. A behavioral change requires a version bump.

The standalone installer must remain byte-identical between revisions of the same version. Tar member paths, types, modes and all frozen member contents must also remain identical. This makes a revision incapable of silently becoming a software update.

The comparison baseline is a revision that was published as installable and passed qualification. A candidate revision that failed qualification and was withdrawn is not such a baseline, and is not compared against. See the 1.13.5 section above.

## Host-wide anti-rollback namespace

The artifact high-water is intentionally not stored under the selected installation directory. Reinstalling into another directory must not reset rollback protection.

The single host locator is:

`/var/lib/electrumx-ravencoin/security-state.locator`

It must be a regular non-symlink file, owned by root, mode `0644`. Its schema fixes exactly one `ownerUid` and one canonical state path.

When the installer/updater runs as root, the only accepted namespace is:

`/var/lib/electrumx-ravencoin/security-state.json`

The state file, when present, must be a regular non-symlink file owned by root and mode `0600`. A root-run installer fails closed if the locator names an unprivileged UID, a different path, a non-root-owned locator or target, or an unsafe mode.

When it runs as an unprivileged user, the canonical state path is:

`${XDG_STATE_HOME:-$HOME/.local/state}/electrumx-ravencoin/security-state.json`

The target must be owned by that exact UID and mode `0600`. The unprivileged process is never allowed to create or replace the root-owned locator; an administrator must provision the locator with that UID and exact canonical path. If the same host is later invoked under a different UID (including root), the owner/path mismatch is a hard failure. The implementation never silently chooses a second namespace.

Security-state reads are descriptor-bound: the implementation opens with `O_NOFOLLOW`, validates ownership/type/mode with `fstat()` on that descriptor, and reads JSON from that same descriptor. Replacing the pathname after `open()` therefore cannot substitute different locator or high-water bytes for the bytes that were validated.

High-water schema v2 records both a per-version revision/digest record and a global `highestAcceptedVersion`. The global value is advanced only after a successful install/promotion. A candidate whose semantic version is below `highestAcceptedVersion` is rejected before updater operational state (`HostFacts`) is used for ordering. Consequently, lowering or replacing `currentRelease` in the ordinary updater-state file cannot authorize a version rollback. For the highest accepted version, the host-wide record separately enforces its highest accepted revision plus artifact/provenance digest binding.

For root installs this anti-rollback state is root-owned `0600`, so an unprivileged local user cannot lower either floor. For an explicitly unprivileged installation, the selected state target is owned by that same UID and therefore shares that user's trust boundary; another UID still cannot select a divergent namespace because the locator remains root-owned.

This design prevents a local unprivileged user from pre-creating a world-writable `/var/tmp` locator and feeding attacker-controlled anti-rollback state to a root installer.

## Release signing and publication

GitHub Actions does not hold the 1.13.3 release/update private key and does not publish a release.

`release.yml` can build only an unsigned deterministic candidate. `signing.yml` can download a specifically reviewed candidate and prepare canonical bytes/evidence for transfer to an offline signing machine. Neither workflow has a private-key input, a signing secret, `contents: write`, or a release-publication step.

On the isolated signing machine, `core-safety/scripts/offline_sign_release.py` re-verifies the unsigned manifest and all bound artifact/provenance digests. The private-key file must be a regular non-symlink file, owned by the signing UID and mode `0600`. The derived public key must equal the independently authenticated replacement public key and must not equal the retired key or key ID. Only then is the manifest signed.

Publication is a separate explicit maintainer action after offline signature verification. The signed manifest is the binding object; mixed or partially replaced release assets fail digest verification rather than becoming executable input.

## Provenance

`release-provenance.json` records the reviewed source commit, Node Monitor pin, immutable Ravencoin Core identity/certification, artifact revision, release timestamp and replacement release/update public-key identity. Its exact SHA-256 is signed as `provenanceDigest` in the manifest.

Provenance is evidence, not consensus authority. Ravencoin block validity remains determined by the locally pinned Ravencoin Core validation path.
