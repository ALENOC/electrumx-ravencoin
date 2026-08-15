# Security model

## Trust boundary

ElectrumX serves indexed blockchain data from a configured Ravencoin Core
daemon. It does not make a remote Electrum client trust that data. Clients must
verify genesis, sampled history, trusted anchors where available, and operator
independence. The custom backend RPC is evidence, not consensus proof.

## Defenses

- The server deployment must use a Core identity accepted by the signed
  safe-Core policy. The historical 4.8.0 threshold is metadata, not
  sufficient trust, and future releases require their own certification.
- Server and daemon networks must match.
- Mainnet block 4,487,775 is checked against the last-unaffected checkpoint.
- Mainnet KAWPOW `nHeight` is checked from block 4,487,776.
- An indexed DB tip that is not on Core's canonical history refuses startup.
- Backend version/health is refreshed periodically. Broadcast forces a fresh
  check, preventing a downgrade from retaining transaction relay authority.
- `server.ravencoin_backend` returns only sanitized identity, height, network,
  synchronization, compatibility, and observation-time fields.

## Residual risks

A malicious server can lie in its self-reported backend method; a client must
not treat it as cryptographic attestation. Dependency compromise, TLS-key
compromise, stale server lists, resource exhaustion, and operator compromise
remain deployment risks. A signed capability may be added later, but it is not
a substitute for independent chain verification.

The unsafe-version override is solely for isolated development. Setting
`ALLOW_UNSAFE_RAVENCOIN_CORE=1` weakens the startup and periodic version gate,
produces critical warnings, and is prohibited for production deployments.

## Reporting

Open a private security advisory on the maintained ALENOC fork:
<https://github.com/ALENOC/electrumx-ravencoin/security/advisories/new>.
Do not include RPC credentials, wallet material, private keys, or production
host details in public reports.
