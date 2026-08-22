# Release artifact revisions and 1.13.2 trust migration

This document defines the release/update trust model introduced by ElectrumX-RVN 1.13.2. It is intentionally separate from the Ravencoin Core safe-policy trust root. Nothing in this mechanism changes the pinned Ravencoin Core 4.8.0 identity or makes release metadata a source of consensus trust.

## 1.13.1 cannot auto-update to 1.13.2

An existing ElectrumX-RVN 1.13.1 node **cannot and must not auto-update to 1.13.2**.

There are two independent reasons:

1. the 1.13.1 updater accepts release-manifest schema v1 and rejects the v2 schema introduced by 1.13.2; and
2. 1.13.1 knows only the retired release/update trust root. It does not know the replacement Ed25519 public key used for 1.13.2 and later manifests.

There is deliberately no bridge manifest signed by the retired key. The retired key does not sign, certify, endorse, or attest its own replacement.

### Manual operator upgrade path

The operator must perform the transition explicitly:

1. Obtain the new Ed25519 release/update **public** key through an authenticated out-of-band channel controlled independently of the 1.13.1 release/update key. Preferably compare its full 64-hex fingerprint through two independent authenticated channels.
2. Do not accept a statement merely because it is signed by the retired 1.13.1 update key. Such a statement is not a trust-root migration mechanism.
3. Download the reviewed 1.13.2 installer, signed manifest, provenance and checksums through the normal distribution channel.
4. Verify the v2 manifest directly against the independently authenticated replacement public key, then verify the installer, bundle and provenance digests bound by that manifest.
5. Provision the host-wide anti-rollback locator as described below. For a root installation this is created only in the root-owned `/var/lib/electrumx-ravencoin` namespace. An unprivileged installation requires an administrator-provisioned root-owned locator.
6. Perform the 1.13.2 installation/upgrade manually. Automatic update discovery may be enabled only after the new updater and new release/update public key are installed.

This is a deliberate trust discontinuity. A compromised retired signing key therefore cannot authorize the new root of trust.

## Manifest v2 and `artifact_revision`

Manifest schema v2 adds the signed fields `artifact_revision` and `provenanceDigest`.

Release identity is ordered as `(electrumxVersion, artifact_revision)`. `artifact_revision` is a canonical, non-negative integer and is monotonic within one ElectrumX version.

- a higher semantic version is a new software release;
- the same version with a higher `artifact_revision` is a reviewed artifact revision;
- a lower revision is a rollback and is rejected;
- the same version and revision with a different `artifactDigest` is equivocation and is rejected;
- the same version/revision/digest is the same artifact identity.

A revision-only promotion is informational for an already-running node. It must not stop services, rebuild images, reindex Ravencoin Core, alter databases, or otherwise change the running node. Only the verified release/high-water records advance.

## Frozen scope for same-version revisions

The offline scope verifier compares the actual previously published release artifacts with the candidate structurally. It does not compare two newly rebuilt gzip streams.

For the same ElectrumX version, application logic, installer behavior, gateway policy, policy caps, Core pins, signing keys and other executable behavior are frozen. Only the reviewed ChainStrap floor evidence, monotonic `artifact_revision`, timestamp, generated provenance, source-commit provenance metadata and the digests that necessarily bind those reviewed bytes may change. A behavioral change requires a version bump.

The standalone installer must remain byte-identical between revisions of the same version. Tar member paths, types, modes and all frozen member contents must also remain identical. This makes a revision incapable of silently becoming a software update.

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

This design prevents a local unprivileged user from pre-creating a world-writable `/var/tmp` locator and feeding attacker-controlled anti-rollback state to a root installer.

## Release signing and publication

GitHub Actions does not hold the 1.13.2 release/update private key and does not publish a release.

`release.yml` can build only an unsigned deterministic candidate. `signing.yml` can download a specifically reviewed candidate and prepare canonical bytes/evidence for transfer to an offline signing machine. Neither workflow has a private-key input, a signing secret, `contents: write`, or a release-publication step.

On the isolated signing machine, `core-safety/scripts/offline_sign_release.py` re-verifies the unsigned manifest and all bound artifact/provenance digests. The private-key file must be a regular non-symlink file, owned by the signing UID and mode `0600`. The derived public key must equal the independently authenticated replacement public key and must not equal the retired key or key ID. Only then is the manifest signed.

Publication is a separate explicit maintainer action after offline signature verification. The signed manifest is the binding object; mixed or partially replaced release assets fail digest verification rather than becoming executable input.

## Provenance

`release-provenance.json` records the reviewed source commit, Node Monitor pin, immutable Ravencoin Core identity/certification, artifact revision, release timestamp and replacement release/update public-key identity. Its exact SHA-256 is signed as `provenanceDigest` in the manifest.

Provenance is evidence, not consensus authority. Ravencoin block validity remains determined by the locally pinned Ravencoin Core validation path.
