# Selecting the node data disk

The verified installer separates **project data placement** from Docker's
global image store. On an interactive fresh install it lists writable mounted
block filesystems, their mount points, filesystem types and free space, then
asks which one should hold the node data.

The selected dedicated directory contains:

- `ravencoin-data/`: Ravencoin Core blockchain data and the ChainStrap bootstrap;
- `ravencoin-config/`: generated Core configuration state;
- `electrumx-data/`: the ElectrumX database;
- `monitor-data/`: Node Monitor persistent-data mount. History remains RAM-only
  by default; `/data/history.db` is only used if the operator later opts into
  SQLite history.

Docker images and writable image layers are deliberately **not moved**. They
remain under the Docker daemon's existing `DockerRootDir`; changing that is a
global Docker-host administration operation and is outside this installer.

For automation, pass a dedicated, not-yet-existing directory with
`--storage-root /mount/path/electrumx-ravencoin-storage`. Non-interactive fresh
installs fail closed if `--storage-root` is omitted. The installer refuses `/`,
`$HOME`, a filesystem mountpoint itself, an existing data root, or a path whose
parent is not writable by the invoking operator.

Compose continues to use named volumes, but `compose.storage.yaml` configures
them as local-driver bind volumes whose real bytes live under the selected
filesystem. This preserves the existing Core/ChainStrap sharing model while
keeping the large blockchain and index data off the Docker image disk.

If a fresh install fails after storage activation, the installer removes the
Compose volumes and returns ownership of the dedicated storage tree before
deleting it. It never falls back from ChainStrap to P2P silently.
