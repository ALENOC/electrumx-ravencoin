# ElectrumX-RVN 1.13.4 hardware qualification

## Scope

Qualify the real legacy upgrade path from a healthy ElectrumX-RVN 1.13.1 / Ravencoin Core 4.8.0 node to the signed 1.13.4 release candidate on ARM64 hardware.

The qualification specifically closes the failure discovered with the withdrawn 1.13.3 candidate: the external `ravencoin-bandwidth-controller.service` reconciled persisted `MAX_SESSIONS=800` by issuing its own `docker compose up` while the updater was simultaneously recreating ElectrumX. Docker Compose then observed a container ID that the controller had already replaced and failed with `No such container`; the same race also interfered with rollback.

## Required identities

- source version: `1.13.1`
- candidate version: `1.13.4`
- candidate artifact revision: `0`
- Ravencoin Core version: `4.8.0`
- Ravencoin Core commit: `22549129888d02e0e08fcdb9f96f3c699167e774`
- Node Monitor pin: `b59e7efdea2fe8c0114b5f72e139931fe86ae571`
- update-signing public key: `1fd5547dd69443337454f158e3985ca2b7d86657975a177b647ba69319491778`
- update-signing key ID: `6f4f944c9b0a19a1`

## Pre-mutation gates

1. Published release bytes must verify against `SHA256SUMS`.
2. `electrumx-update check` must record 1.13.4 as `ELIGIBLE` and `VERIFIED`.
3. Legacy discovery must prove the exact existing named-volume identities and healthy 1.13.1/Core 4.8.0 runtime.
4. `COMPOSE_FILE` must contain only Compose files shipped by the candidate release.
5. No ChainStrap action is allowed on the legacy upgrade path.

## External mutator regression gate

Before the updater stops or recreates any Docker service, an active host-side bandwidth controller must be suspended. Evidence must include:

```text
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
```

While the updater is in stop/switch/start/health or rollback, the controller must remain inactive and its journal must contain no `reapplied electrumx connection limit` event.

After successful promotion, or after an exact rollback, the controller must be restored only if it was active before the transaction. Evidence must include:

```text
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
```

If rollback is indeterminate, the controller must remain suspended and the updater must require operator intervention rather than allowing an independent reconciler to mutate an ambiguous stack.

## Successful promotion evidence

A PASS requires all of the following:

- running ElectrumX reports `ElectrumX-RVN 1.13.4`;
- Core remains version 4.8.0 and the exact certified source commit;
- ElectrumX DB height equals daemon/Core height;
- the same legacy Docker named-volume objects remain attached at the same destinations;
- the install marker identifies 1.13.4 and retains `storageMode: named-volumes` for the adopted legacy node;
- updater state records 1.13.4 revision 0 as current and clears the pending candidate;
- host-wide artifact high-water is created/advanced only after promotion;
- Node Monitor remains healthy and external;
- after the updater has completed and the controller is resumed, the persisted connection limit is reconciled back to `MAX_SESSIONS=800` without racing the updater;
- no `No such container` error occurs during promotion or rollback paths.

## Rollback regression

Also execute a controlled failing-health test with the external controller active before apply. The updater must suspend it, restore the exact old release and named volumes, then resume the controller only after rollback is complete. A failed rollback must leave the controller stopped.

## Publication decision

Do not mark 1.13.4 stable unless the real 1.13.1 -> 1.13.4 promotion passes on hardware and the external-mutator ordering above is demonstrated from timestamps/checkpoints. Do not reuse or alter the withdrawn 1.13.3 candidate bytes or tag.
