# ElectrumX-RVN 1.13.1

ElectrumX-RVN 1.13.1 is the first maintained release line that combines the official RavenProject Ravencoin Core 4.8.0 trust identity, ChainStrap Fast Verified Bootstrap, the hardened Core certification pipeline, crash-consistency protections, the signed installer/update architecture, and the Ravencoin Node Monitor integration.

## Highlights

- **ChainStrap Fast Verified Bootstrap** is integrated for fresh installs and is the guided installer's default bootstrap choice.
- **Special thanks to Tron Black** for making ChainStrap available to the Ravencoin ecosystem. ChainStrap support is available in ElectrumX-RVN starting with version 1.13.1.
- ChainStrap is treated as transport only: Ravencoin Core performs a local `-reindex -assumevalid=0` validation before normal operation, with digest-bound marker state preventing accidental bypass.
- Bundled Core trust is pinned to the exact official `RavenProject/Ravencoin` v4.8.0 commit `22549129888d02e0e08fcdb9f96f3c699167e774`.
- Signed safe-Core policy v3 certifies that exact official commit and revokes the historical 2miners build identity.
- Core readiness supports the official v4.8.0 response that omits `initialblockdownload`, while still requiring synchronized heights, peers and a fresh tip and rejecting explicit IBD.
- Candidate Core execution, trusted evaluation and protected policy signing are separated into distinct trust domains.
- ElectrumX database startup now validates the transaction-hash extent globally and fails closed on unsafe historical corruption.
- Docker Compose uses a deterministic project identity and safer fresh-install cleanup semantics.
- The single-file installer/update path uses independent Ed25519 trust domains for safe-Core policy and ElectrumX release/update manifests.
- Ravencoin Node Monitor is integrated with a loopback-only dashboard by default. The root-owned bandwidth/controller path is separate and explicit opt-in.
- Native ARM64 Core build/test coverage is maintained alongside amd64 artifact validation.
- README and operator guidance were rewritten to match the 1.13.1 architecture and remove obsolete historical Core pins from active installation instructions.

## ChainStrap

Project: https://chainstrap.com

On a fresh install the release installer defaults to ChainStrap. Traditional P2P synchronization remains available explicitly with:

```sh
python3 electrumx-ravencoin-install.py --p2p-bootstrap --storage-root /path/to/data
```

The installer never silently changes from ChainStrap to P2P after a failed bootstrap.

## Official Ravencoin Core identity

- Repository: `RavenProject/Ravencoin`
- Version/tag: `v4.8.0`
- Commit: `22549129888d02e0e08fcdb9f96f3c699167e774`

Version strings alone are not accepted as trust evidence; the deployment pins and verifies the exact source/release identity.

## Installation

The production installation interface is the stable single-file URL:

`https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py`

Download first, then execute locally. Do not pipe the network response directly into Python or a shell.

```sh
curl --fail --location --remote-name \
  https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py
python3 electrumx-ravencoin-install.py
```

For verification without persistent installation:

```sh
python3 electrumx-ravencoin-install.py --check-only
```

## Release-security gate

The GitHub release must only be published after the exact final release candidate satisfies the protected production gates: RavenProject-only signed safe-Core policy promotion, dedicated ElectrumX update/release key provisioning, clean fresh-install qualification, and final release audit. Development or test keys must never be substituted to make these gates pass.
