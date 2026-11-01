# ElectrumX-RVN 1.13.14 hardware qualification

## RESULT: PENDING

The candidate must not be described as hardware-qualified until its exact signed
artifact identity has passed every gate below. Evidence from earlier releases
or a manually repaired deployment does not qualify this candidate.

## Required identities

- source version for the ordinary updater path: `1.13.13`
- candidate version: `1.13.14`
- candidate artifact revision: `0`
- Ravencoin Core: `4.8.0`, commit `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `c73d6a0b96f09196a523d23da7d29858f3715624`
- update-signing key ID: `6f4f944c9b0a19a1`
- update-signing public key:
  `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`

## Mandatory gates

1. Run the regression/security suite and installer trust/rollback tests against
   the exact release source. Verify the signed manifest, bundle, installer and
   provenance with the current public key before deployment.
2. Exercise an ordinary update from 1.13.13 on the qualification node. Preserve
   blockchain data, ElectrumX DB, volume identities, TLS and operator settings.
   Require successful health promotion, no pending candidate and no failure reason.
3. Run a fresh installation with the monitor selected. Require the systemd
   startup service to be enabled and its trusted helper to match the signed
   bundle. The service must run as the installing operator's UID/GID.
4. Delay a published LAN address at boot or Docker restart. Require automatic
   startup after the address arrives, without manual intervention. Verify both
   Docker port metadata and HTTP `/healthz` through every published address.
   Core and ElectrumX must not be recreated by monitor recovery.
5. Verify Core readiness, ElectrumX health and public TLS RPC. Require
   `server.version` to identify ElectrumX-RVN 1.13.14 and retain the existing
   `server.ravencoin_backend` contract.
6. Verify storage and security boundaries after update and rollback.
   `PERSISTENT_PATHS` remains the fixed preserved-state set, including `.secrets`;
   each preserved file/directory keeps its source uid and gid. A switch that
   cannot preserve operator ownership must fail closed.
7. Keep the ChainStrap mixed-content contract: only allowlisted `blocks/blk*.dat` members may be extracted.
   Safe foreign files such as `assets/LOCK` and `blocks/index/004089.ldb` must be
   ignored. Ravencoin Core still performs a local full reindex/revalidation.
   Unsafe members, malformed archives and zero-raw-block parts remain rejected.
8. Verify the installed Network Observer CLI remains usable, with crawler limits
   and SSRF filtering intact. Do not count earlier release evidence as a pass.

## Artifact identity and evidence

Pending candidate build and offline signature. Record exact digests, source
commit, signing key ID and observed hardware results here after execution.

## Qualification result

## RESULT: PENDING

No mandatory gate above has been executed against the signed 1.13.14 artifacts.
Automated source/CI checks are recorded separately and do not replace hardware
qualification.
