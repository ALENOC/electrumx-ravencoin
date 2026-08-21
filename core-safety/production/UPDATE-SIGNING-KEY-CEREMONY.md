# ElectrumX update-signing key ceremony

The production ElectrumX release/update signing trust root is intentionally **not provisioned yet**.

Do not copy a development/test public key into `update-signing-public-key.hex`, and do not generate a replacement key ad hoc from CI, an AI coding session, or an ordinary developer shell.

Before the first release that enables `electrumx-update check` in production, perform and record a dedicated key ceremony with these properties:

1. Generate a new Ed25519 keypair specifically for the ElectrumX release/update manifest domain `ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1`.
2. Keep the private key only in the protected release-signing environment. It must never be committed, logged, placed in build artifacts, or exposed to jobs that execute candidate/source code.
3. Record the 32-byte public key, its derived key ID, ceremony date, operator(s), and storage/recovery procedure.
4. Add only the public key to `core-safety/production/update-signing-public-key.hex` in a reviewed commit.
5. Add a regression test proving that a manifest signed by the matching protected private key verifies and that manifests signed by any other key fail closed.
6. Run the mandatory GLM5.3 security audit on the exact commit/tree containing the production public key and release wiring before publication.

Until those steps are complete, the updater is expected to fail closed when the public-key file is absent. This is intentional and is safer than treating an unceremonied development key as a production trust anchor.

This key is **separate** from the safe-Core policy signing key. Never reuse `POLICY_SIGNING_KEY` for ElectrumX release manifests.
