# Legacy 1.13.1 adoption for the 1.13.2 transactional updater

This procedure exists only for production ElectrumX-RVN 1.13.1 nodes that were installed with the historical `setup.sh` path and therefore predate both `.electrumx-ravencoin-installed.json` and the bind-backed `compose.storage.yaml` layout.

The adoption path is intentionally separate from the normal updater. It preserves the existing Docker named volumes and does **not** convert them to bind mounts or derive stable storage configuration from Docker's private data-root.

## Preconditions

The wrapper refuses unless it can prove all of the following while the old node remains running:

- fixed Compose project `electrumx-ravencoin`;
- exactly one running `ravencoin-core` and one running `electrumx` service from `compose.yaml`;
- ElectrumX RPC reports `ElectrumX-RVN 1.13.1`;
- the Core binary reports version `4.8.0`;
- `ravencoin-data`, `ravencoin-config`, `electrumx-data`, `rpc-secrets`, and `raven-secrets` are plain local Docker named volumes under the fixed project namespace;
- the running Core and ElectrumX containers are attached to the expected named volumes at the expected container destinations;
- the rendered Compose model contains the expected named-volume storage and has not silently changed to bind-backed storage.

An independent Node Monitor deployment is not absorbed into the ElectrumX Compose project. It remains external and untouched.

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

After the replacement 1.13.2 trust root has been authenticated out-of-band and the signed v2 candidate has been discovered and verified, invoke the one-time wrapper from the exact reviewed/signed 1.13.2 candidate tree:

```bash
python3 core-safety/scripts/legacy_1_13_1_apply.py
```

The wrapper writes a private schema-v1 adoption marker atomically and then delegates candidate verification, download, transaction switching, health evaluation, rollback, high-water advancement, and audit recording to the normal v2 updater.

The process-local compatibility hooks change only the storage proof model for this legacy transaction: candidate and restored stacks use `compose.yaml` without `compose.storage.yaml`, retaining the exact existing project-scoped named-volume identities. The normal new-installer/bind-backed updater remains unchanged.

## Safety invariants

- no `docker compose down -v`;
- no named-volume deletion or recreation;
- no conversion of Docker private mountpoints into host bind-storage API;
- no ChainStrap resolver/download/import during the upgrade;
- discovery and adoption occur while the old 1.13.1 stack is still serving;
- storage identity is proved before stop, against the staged candidate, after rename, and before promotion;
- failure before stop leaves the old stack running;
- failure after switch uses the existing transactional rollback path and reattaches the same named volumes;
- the separate external Node Monitor project is not modified.

## Hardware qualification evidence

For a legacy baseline, B0/B0.5 evidence must capture the exact named-volume identities and live container mount destinations instead of requiring the four new-installer `*_HOST_DIR` variables. Record Docker volume metadata and mount identity, but do not treat `/var/lib/docker/volumes/.../_data` or a relocated Docker data-root as a stable storage configuration interface.
