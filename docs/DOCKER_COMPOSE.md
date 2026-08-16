# Docker Compose deployment notes

The canonical beginner deployment and operations guide is the repository
[README](../README.rst). It documents both supported modes:

- recommended `compose.yaml`: pinned Ravencoin Core 4.8.0 plus ElectrumX;
- `compose.existing-core.yaml`: ElectrumX only, for a private existing Core
  deployment. Exact backend identity and signed-policy/live checks still apply.

## Compose invariants

The bundled stack deliberately has these properties:

- on amd64, Core is built from the exact 2miners v4.8.0 release artifact,
  verifying the archive, `ravend`, and `raven-cli` SHA-256 values during the
  image build; on ARM64 there is no equivalent prebuilt release asset, so the
  image build compiles from the pinned source archive, verified by SHA-256,
  with `make check` run as part of the build;
- Core RPC has no host port mapping and is bound only to loopback plus the
  dedicated `172.29.80.0/24` bridge;
- Core P2P 8767 is public;
- the initial Electrum TCP service is host-loopback only;
- public Electrum TLS is an explicit `compose.tls.yaml` overlay;
- Core chain/config, ElectrumX DB, and prepared RPC secrets use separate named
  volumes;
- both long-running services execute as unprivileged image users with a
  read-only root filesystem, all capabilities dropped, and no Docker socket;
- the network-isolated secret initializer has only the `DAC_OVERRIDE`
  capability needed to read mode-0600 host source files;
- Core health means JSON-RPC is available, not that IBD is finished;
- ElectrumX waits on Core health and retains its own RPC retry behavior; and
- automatic crash restarts stop after five failed attempts.

`depends_on` is not treated as proof that Core is ready. The dependency uses
`condition: service_healthy`, and ElectrumX still handles later temporary RPC
loss.

## Credential flow

`./setup.sh` generates a URL-safe random username and 256-bit random password
under `.secrets/`, with host mode 0600. Values are never printed. Compose's
one-shot initializer copies them to a private volume because file-backed
Compose secrets preserve host ownership on some engines and otherwise cannot be
read by a non-root container. Core creates its persistent mode-0600 config from
that volume; ElectrumX constructs `DAEMON_URL` only inside its process.

Changing only one secret file or changing the source credentials without the
persistent Core config causes a fail-closed mismatch. No setup path silently
overwrites existing credential files or persistent data.

## Production verification

Before publication, require all of the following:

1. Core reports integer version at least `4080000`, subversion
   `/Ravencoin:4.8.0/` or newer, chain `main`, synchronized blocks/headers, and
   the exact incident checkpoint.
2. ElectrumX management RPC reports a caught-up database.
3. `server.features` advertises Ravencoin assets and the backend capability.
4. `server.ravencoin_backend` reports a safe, synchronized, current backend.
5. Several headers match an independently trusted Ravencoin chain using the
   correct post-KAWPOW block-ID rules.
6. Read-only asset methods work for known assets.
7. TLS validates from outside the host.

Do not advertise the endpoint or run a broadcast test with real funds until
these checks pass.
