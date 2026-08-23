# Legacy 1.13.1 adoption for the transactional updater

This procedure exists only for production ElectrumX-RVN 1.13.1 nodes that were installed with the historical `setup.sh` path and therefore predate both `.electrumx-ravencoin-installed.json` and the bind-backed `compose.storage.yaml` layout.

The adoption path is intentionally separate from the normal updater. It preserves the existing Docker named volumes and does **not** convert them to bind mounts or derive stable storage configuration from Docker's private data-root.

Adoption is a one-time step. Once it has completed, `storageMode: named-volumes` is durable installation state in `.electrumx-ravencoin-installed.json`, the normal updater interprets that state natively, and every later update is an ordinary `electrumx-update apply`. This wrapper must not be used again.

## Preconditions

The wrapper refuses unless it can prove all of the following while the old node remains running:

- fixed Compose project `electrumx-ravencoin`;
- exactly one running `ravencoin-core` and one running `electrumx` service from `compose.yaml`;
- ElectrumX RPC reports `ElectrumX-RVN 1.13.1`;
- the Core binary reports version `4.8.0`;
- `ravencoin-data`, `ravencoin-config`, `electrumx-data`, `rpc-secrets`, and `raven-secrets` are plain local Docker named volumes under the fixed project namespace;
- the running Core and ElectrumX containers are attached to the expected named volumes at the expected container destinations;
- the rendered Compose model contains the expected named-volume storage and has not silently changed to bind-backed storage.

An independent Node Monitor deployment is not absorbed into the ElectrumX Compose project. It remains external and untouched as a deployment, but the updater now coordinates with the fixed host service `ravencoin-bandwidth-controller.service` when that service is active. The controller can recreate ElectrumX while reconciling `MAX_SESSIONS`, so version-changing updater transactions suspend it before Docker mutation and resume it only after successful promotion or an exact rollback. This prevents the container-identity race found during 1.13.3 hardware qualification.

## Read-only discovery

Use the dedicated discovery command before any adoption attempt:

```bash
python3 core-safety/scripts/legacy_1_13_1_apply.py discover
```

`discover` performs the legacy installation and storage proofs only. It does not load or apply a release candidate, does not write `.electrumx-ravencoin-installed.json`, does not stop services and does not modify Docker volumes.

A successful discovery ends with:

```text
UPDATER_CHECKPOINT legacy-discovery=PASS mutation=NONE old-stack=RUNNING
```

## Candidate-first rule

The adoption marker is **not** written merely because the legacy node is valid.

Before any install-root mutation, `apply` requires an already discovered pending candidate and revalidates the exact tagged signed manifest, artifact revision/high-water constraints, eligibility and safe-Core policy. It then downloads the exact artifact, verifies its SHA-256, verifies the signed provenance binding, validates the release bundle and proves both installed trust roots are continuous with the candidate.

The candidate must be exactly ElectrumX-RVN `1.13.4`. If no verified pending 1.13.4 candidate exists, adoption refuses with the old 1.13.1 install root untouched and no marker created.

A successful preflight ends with:

```text
UPDATER_CHECKPOINT legacy-candidate-preflight=PASS version=1.13.4 marker=UNTOUCHED old-stack=RUNNING
```

The normal updater deliberately performs its own candidate revalidation again after adoption and immediately before the transaction. The duplicate verification is intentional: the first pass protects the adoption boundary; the second protects the transactional apply boundary.

## Operator consent

Interactive adoption requires typing exactly:

```text
ADOPT LEGACY 1.13.1
```

For controlled hardware qualification only, the equivalent non-interactive acknowledgement is:

```text
--yes-adopt-legacy
```

The default is refusal.

## Apply

After the replacement 1.13.4 trust root has been authenticated out-of-band, `electrumx-update check` has recorded the signed v2 candidate, and the candidate-first preflight can succeed, invoke the one-time wrapper from the exact reviewed/signed 1.13.4 candidate tree:

```bash
python3 core-safety/scripts/legacy_1_13_1_apply.py apply
```

or, for the controlled qualification executor only:

```bash
python3 core-safety/scripts/legacy_1_13_1_apply.py apply --yes-adopt-legacy
```

Only after candidate preflight and operator consent does the wrapper write the private schema-v1 adoption marker atomically. It then delegates the transactional switch, health evaluation, rollback, high-water advancement and audit recording to the normal v2 updater.

For a version-changing transaction, the updater next checks the fixed systemd unit `ravencoin-bandwidth-controller.service`. If it is inactive or absent, no action is taken. If it is active, the updater must stop it and prove it is no longer active before stopping or recreating any Docker service. A successful suspension emits:

```text
UPDATER_CHECKPOINT external-mutator-suspend=PASS service=ravencoin-bandwidth-controller.service
```

After a successful promotion or an exact rollback, the updater restarts the controller only if this transaction suspended it. A successful restore emits:

```text
UPDATER_CHECKPOINT external-mutator-resume=PASS service=ravencoin-bandwidth-controller.service
```

If rollback itself is indeterminate, the controller deliberately remains suspended so it cannot mutate an already ambiguous Docker state. Operator intervention is then required.

If this invocation created the adoption marker but the normal updater returns without promotion, the wrapper removes the marker again only when the restored install root still contains the exact legacy 1.13.1 adoption marker. It refuses to delete a marker that identifies a promoted 1.13.4 tree or any unknown state. This prevents a failed pre-promotion attempt from leaving the production node silently half-adopted while avoiding destructive cleanup after an ambiguous switch/recovery state.

The process-local compatibility hooks change only the storage proof model for this legacy transaction: candidate and restored stacks use `compose.yaml` without `compose.storage.yaml`, retaining the exact existing project-scoped named-volume identities. The normal new-installer/bind-backed updater remains unchanged.

## Safety invariants

- no adoption marker before a completely revalidated/downloaded/verified 1.13.4 candidate;
- no `docker compose down -v`;
- no named-volume deletion or recreation;
- no conversion of Docker private mountpoints into host bind-storage API;
- no ChainStrap resolver/download/import during the upgrade;
- discovery and candidate preflight occur while the old 1.13.1 stack is still serving;
- explicit operator consent occurs before adoption mutation;
- storage identity is proved before stop, against the staged candidate, after rename, and before promotion;
- an active external bandwidth controller must be suspended before Docker mutation;
- the controller is resumed only after successful promotion or exact rollback;
- failure before stop leaves the old stack running;
- failure after switch uses the existing transactional rollback path and reattaches the same named volumes;
- a marker created by the current invocation is removed after a non-promoted result only when exact restored legacy state is provable;
- the separate external Node Monitor project is not modified.

## Hardware qualification evidence

For a legacy baseline, B0/B0.5 evidence must capture the exact named-volume identities and live container mount destinations instead of requiring the four new-installer `*_HOST_DIR` variables. Record Docker volume metadata and mount identity, but do not treat `/var/lib/docker/volumes/.../_data` or a relocated Docker data-root as a stable storage configuration interface.

Before authorizing mutation, capture both successful read-only discovery and the candidate-preflight checkpoint. If the signed candidate does not yet exist, stop after `discover`; do not create an adoption marker simply to prepare the node for a future release.

When an external bandwidth controller is present, qualification must also prove suspend/resume ordering and confirm that the controller re-applies its persisted `MAX_SESSIONS` value only after the updater has completed its Docker transaction.
