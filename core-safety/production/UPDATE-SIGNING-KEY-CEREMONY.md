# ElectrumX update-signing key ceremony

The production ElectrumX release/update signing trust root is **provisioned**.

This key is **separate** from the safe-Core policy signing key. Never reuse
`POLICY_SIGNING_KEY` for ElectrumX release manifests, and never reuse this key
for safe-Core policy documents.

## Ceremony record

| Property | Value |
| --- | --- |
| Domain | `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1` |
| Algorithm | Ed25519 (raw 32 byte private key, raw 32 byte public key) |
| Public key | `4dbeb6131495015b1c44d2d61f80d527217623e1b12dee8f34664509ee3d2b35` |
| Key ID | `288e85d43f792f83` (`sha256(publicKey)[:16]`) |
| Ceremony date | 2026-08-21 |
| Operator | ALENOC |
| Secret name | `ELECTRUMX_UPDATE_SIGNING_KEY` |
| Protected environment | `electrumx-release-signing` |

The published public key lives in `update-signing-public-key.hex`. The private
key was generated under `umask 077` outside any repository working tree, was
never written to a tracked file, a build artifact, a log, or a job environment
that executes candidate or source code, and was provisioned into the protected
environment by piping a file into `gh secret set` rather than passing it as a
command argument.

The protected environment requires a reviewer approval and only accepts
deployments from protected branches, so no workflow running on a feature branch
or on a fork can reach the private key.

### Storage and recovery

The only online copy of the private key is the GitHub Actions secret, which is
write-only: it can be replaced but never read back. The operator holds the
offline copy. If the offline copy is lost, the key cannot be recovered and the
trust root must be rotated: generate a new keypair under this same ceremony,
publish the new public key in a reviewed commit, re-run the security audit on
the resulting tree, and treat every manifest signed by the old key as no longer
trusted.

### Proof of possession

`update-signing-key-attestation.json` holds a statement signed by the private
key over the same domain-separated encoding used for release manifests. It
proves that the public key published here is the one whose private half sits in
the protected environment, without exposing any private material.
`tests/test_update_signing_trust_root.py` verifies that attestation, checks the
domain binding, and proves that a manifest signed by any other key fails closed.

## Required properties for any future rotation

1. Generate a new Ed25519 keypair specifically for the ElectrumX release/update
   manifest domain `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1`.
2. Keep the private key only in the protected release-signing environment. It
   must never be committed, logged, placed in build artifacts, or exposed to
   jobs that execute candidate/source code.
3. Record the 32-byte public key, its derived key ID, ceremony date,
   operator(s), and storage/recovery procedure.
4. Add only the public key to
   `core-safety/production/update-signing-public-key.hex` in a reviewed commit.
5. Keep a regression test proving that a manifest signed by the matching
   protected private key verifies and that manifests signed by any other key
   fail closed.
6. Run the mandatory GLM5.3 security audit on the exact commit/tree containing
   the production public key and release wiring before publication.

Do not copy a development or test public key into
`update-signing-public-key.hex`. If the public-key file is absent, the updater
fails closed by design, which is safer than treating an unceremonied
development key as a production trust anchor.
