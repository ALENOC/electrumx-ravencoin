# ElectrumX-Ravencoin 1.13.3 — offline release signing

This is the complete maintainer signing procedure for the 1.13.3 release candidate.
It is deliberately limited to **two commands**. The Ed25519 private key never enters
GitHub Actions, the repository, an executor host, or any remote session.

## Preconditions

Before starting, the maintainer has locally obtained the exact unsigned candidate
directory produced by the reviewed release build. It must contain at least:

- `electrumx-ravencoin-bundle.tar.gz`
- `electrumx-ravencoin-install.py`
- `release-provenance.json`
- `unsigned-release-manifest.json`
- `offline-signing-inputs.json`
- `SHA256SUMS.build`

The private key is a local regular non-symlink file containing exactly 32 raw
Ed25519 private-key bytes encoded as 64 lowercase hexadecimal characters. It is
owned by the signing user and already has mode `0600`.

The maintainer also has the independently authenticated **public** key as 64
lowercase hexadecimal characters. Substitute only these three placeholders in
the commands below:

- `/ABS/PATH/release-candidate`
- `/ABS/PATH/update-signing-private-key.hex`
- `<NEW_PUBLIC_KEY_HEX>`

Do not copy the private key to the candidate directory.

## Command 1 — sign

From the reviewed 1.13.3 source checkout, run exactly:

```sh
python3 core-safety/scripts/offline_sign_release.py \
  --candidate-dir /ABS/PATH/release-candidate \
  --private-key /ABS/PATH/update-signing-private-key.hex \
  --expected-public-key-hex '<NEW_PUBLIC_KEY_HEX>'
```

Successful exit code: **0**.

Expected output shape:

```text
signed=/ABS/PATH/release-candidate/release-manifest.json
```

The command fails closed if the private key is not a regular owner-owned `0600`
file, if it does not derive the independently supplied public key, if the retired
CI-held key is supplied, or if any candidate digest/body/handoff value changed.
It creates `release-manifest.json` and `SHA256SUMS` only after those checks pass.

## Command 2 — verify before publication

Run exactly:

```sh
python3 core-safety/scripts/offline_sign_release.py \
  --candidate-dir /ABS/PATH/release-candidate \
  --expected-public-key-hex '<NEW_PUBLIC_KEY_HEX>' \
  --verify-only
```

Successful exit code: **0**.

Expected output shape:

```text
version=1.13.3
artifactRevision=0
keyId=<16-lowercase-hex-key-id>
bundleSha256=<64-lowercase-hex>
installerSha256=<64-lowercase-hex>
provenanceSha256=<64-lowercase-hex>
manifestSha256=<64-lowercase-hex>
status=VERIFIED
```

`--verify-only` does **not** accept or open a private key. It verifies all of the
following against the independently supplied public key:

- the Ed25519 signature on `release-manifest.json`;
- the signed body is exactly the reviewed unsigned manifest body;
- bundle SHA-256 equals signed `artifactDigest`;
- installer SHA-256 equals signed `installerDigest`;
- provenance SHA-256 equals signed `provenanceDigest`;
- `SHA256SUMS` exactly names and hashes the four publication files produced by
  the signing step.

## Publication decision

Publish **nothing** unless command 2 exits `0`, the final line is exactly
`status=VERIFIED`, `version=1.13.3`, `artifactRevision=0`, and the printed `keyId`
matches the maintainer's independently recorded identity for
`<NEW_PUBLIC_KEY_HEX>`.

The publication bytes are then exactly these files from the verified candidate
directory:

- `electrumx-ravencoin-bundle.tar.gz`
- `electrumx-ravencoin-install.py`
- `release-provenance.json`
- `release-manifest.json`
- `SHA256SUMS`

Do not regenerate, edit, normalize, recompress or re-sign any of those files
between command 2 and publication. If any byte changes, repeat both commands on
the reviewed candidate.
