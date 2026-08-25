# ElectrumX update-signing key ceremony and history

This file records the two ElectrumX release/update signing roots. The tracked
`update-signing-public-key.hex` file is the **live production trust root**, not
a historical template. Release candidates, source-checkout deployments, and
installed release bundles must all use that same public key.

The update-signing key is separate from the safe-Core policy signing key.
Never reuse either private key across those signing domains.

## Current offline production key

| Property | Value |
| --- | --- |
| Status | **CURRENT — production release/update trust root** |
| Algorithm | Ed25519 (raw 32-byte public key) |
| Public key | `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778` |
| Key ID | `6f4f944c9b0a19a1` (`sha256(raw_public_key)[:16]`) |
| Signing domain | `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v2` |
| Custody model | Offline only; CI builds unsigned candidates and has no release private-key input |
| Repository copy | `core-safety/production/update-signing-public-key.hex` — canonical live public root |

The exact published v1.13.9, v1.13.10, and v1.13.11 manifests verify under
this key. Their standalone installers, bundled public-key files, release
provenance, and manifest signature key IDs all name this same root. The
replacement v2 trust architecture was introduced for the 1.13.3 transition;
there was no bridge signature from the retired key.

`core-safety/scripts/build_production_release.py` requires the independently
supplied release key to equal the canonical tracked public key. It then renders
that key into the standalone installer, writes the same key into the bundle,
and records its public key and derived ID in provenance and the offline signing
handoff. `offline_sign_release.py` derives the public half from an owner-owned
mode-`0600` offline private-key file, requires it to match the handoff, rejects
the retired key, and signs only after verifying all bound digests. Private-key
material must never be committed, logged, added to an artifact, or supplied to
a job that executes repository or candidate code.

The current key's private storage location and recovery details are deliberately
not published. Loss of the private key means release signing stops; it does not
authorize bypassing signature verification.

## Historical / retired CI-held key

| Property | Value |
| --- | --- |
| Status | **RETIRED 2026-08-22 — forbidden for production use** |
| Algorithm | Ed25519 (raw 32-byte public key) |
| Public key | `4dbeb6131495015b1c44d2d61f80d527217623e1b12dee8f34664509ee3d2b35` |
| Key ID | `288e85d43f792f83` (`sha256(raw_public_key)[:16]`) |
| Signing domain | `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1` |
| Provisioned | 2026-08-21 in the protected GitHub environment `electrumx-release-signing` as `ELECTRUMX_UPDATE_SIGNING_KEY` |
| Published use | v1.13.1 release manifest |
| Retirement reason | The private key had been reachable by CI. On 2026-08-22 release signing moved to an offline-only v2 process in which CI builds unsigned candidates and cannot sign or publish. |

The v1 key's `update-signing-key-attestation.json` remains tracked as immutable
historical evidence. It embeds its own public key and still verifies under the
original v1 domain; it grants no present authority. Repository and artifact
verifiers explicitly reject the retired public key and key ID. Repository
evidence proves that no current workflow consumes the historical signing
secret; secret deletion itself is an external administrative fact and is not
asserted by this document.

## Rotation semantics

The v1-to-v2 change was a deliberate trust discontinuity, not an ordinary
in-band key rotation:

1. v1.13.1 knows only the retired key and schema v1.
2. The retired key did not sign or endorse its replacement.
3. Operators had to authenticate the replacement public key out of band and
   perform the documented manual migration before enabling v2 updates.
4. Once on the v2 line, ordinary updates require the candidate bundle's update
   public key to be byte-identical to the already installed key. Consequently,
   v1.13.10 authenticated v1.13.11 with the same key; no rotation occurred.

A future replacement must therefore update this canonical public file and all
retirement records in one reviewed change, but that change alone cannot rotate
existing nodes. Existing nodes require a separately authenticated manual
migration or an already-deployed, explicitly authorized governance transition.
There is no inactivity, lost-key, or unsigned escape hatch.

If `update-signing-public-key.hex` is absent, malformed, or names a known-retired
key, the production updater fails closed.
