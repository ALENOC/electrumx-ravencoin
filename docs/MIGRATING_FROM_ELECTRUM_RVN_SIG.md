# Migrating from Electrum-RVN-SIG ElectrumX

This repository is a community-maintained fork. It preserves the upstream MIT
licence and does not claim official Ravencoin status.

## Before the server upgrade

1. Stop the public ElectrumX listener cleanly. Do not delete its database.
2. Upgrade the private backend to Ravencoin Core 4.8.0 or later.
3. Let Core finish recovery and synchronization. Verify mainnet, peers, equal
   block/header heights, and block 4,487,775:

   ```text
   000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd
   ```

4. Back up ElectrumX configuration and record its indexed height and tip hash.
   Keep Core RPC bound to localhost/private networking.

## Install and validate

Create a fresh Python 3.10 to 3.12 virtual environment, install this source, and
retain the existing database initially. At startup the maintained fork compares
the indexed database tip to Core's canonical hash and, when present in the DB,
checks the incident checkpoint. A mismatch causes startup to fail with a clear
rewind/rebuild message; the server does not silently publish stale-fork data.

If a mismatch is reported, preserve a backup and follow a reviewed ElectrumX
rewind procedure. Rebuild only when a safe rewind is unavailable or validation
still fails. Never delete the sole database copy as an exploratory step.

For a new containerized deployment, follow
[`DOCKER_COMPOSE.md`](DOCKER_COMPOSE.md). When migrating an existing database into
the named Compose volume, stop both old and new services first, preserve the
original, and copy it with ownership suitable for the image's unprivileged
`electrumx` user. Never start two ElectrumX processes against the same LevelDB.

After startup, query `server.features` and `server.ravencoin_backend`. Confirm:

- ElectrumX and backend Core versions are reported separately;
- `minimumSafeCore` is `4.8.0` and `coreSafe` is true;
- network is `main`, checkpoint status is true, and heights are current;
- sampled Electrum headers match Core at several heights;
- Ravencoin asset queries and transaction broadcast work in a controlled test.

## Production publication

Use a non-root service account, persistent local storage, CA-valid TLS, and a
restart policy that cannot storm. Do not expose Core RPC or credentials. Leave
`ALLOW_UNSAFE_RAVENCOIN_CORE` unset. Keep an independent observer and alert on
backend version, checkpoint, synchronization, database mismatch, memory, swap,
I/O wait, and indexing progress.
