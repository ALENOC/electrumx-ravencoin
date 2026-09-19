# Fast Verified Bootstrap with ChainStrap

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Fork features](fork-features.md) · [Security model](security-model.md)

ChainStrap can reduce the time spent obtaining Ravencoin's historical block
data. In this project it is deliberately treated as a **transport source, not a
consensus authority**. ElectrumX never indexes a ChainStrap database directly.

## Trust model

The integration is intentionally narrower than the generic ChainStrap client:

1. The repository ships a reviewed RVN **mainnet-only** manifest pinned to a
   specific `chainstrap/chainstrap.github.io` commit.
2. Every IPFS part has a fixed CID, byte length, and SHA-256 in that local
   manifest.
3. The downloader writes only safe regular `blocks/blkNNNNN.dat` members into
   the Ravencoin datadir. Other safe regular members are ignored rather than
   trusted or installed.
4. Downloaded chainstate, block indexes, undo databases, asset databases,
   configuration, wallets, and unrelated files are never accepted as validated
   node state.
5. Block files must be contiguous from `blk00000.dat`; otherwise bootstrap
   stops.
6. The bundled, pinned Ravencoin Core 4.8.0 runs a full
   `-reindex -assumevalid=0` with `txindex=1` and `assetindex=1` while network
   access is disabled.
7. A second offline verification stage proves the resulting active chain and
   required indexes before the bootstrap completion marker is written.
8. Normal Core startup is allowed only after the complete validation gate exits
   successfully.
9. ElectrumX continues to depend on the normal Core health gate and builds its
   own database.

A malicious or broken snapshot can therefore waste bandwidth or fail bootstrap,
but it is not accepted as validated chain state. Consensus validation remains
the responsibility of the local Core binary qualified by this repository.

## Failure semantics are deliberately fail-closed

Selecting ChainStrap is an explicit bootstrap choice. If the downloader, digest
checks, archive validation, local reindex, or post-reindex verification fail,
the single-file installer **does not silently switch to P2P**. Changing
bootstrap mode behind the operator's back would hide the failure that was
actually selected and make validation results ambiguous.

For a failed *fresh* single-file install, the installer prints the recent
ChainStrap bootstrap log, tears down the just-created Compose project including
its named volumes, removes the partial install directory, and returns an error.
Because a fresh install first refuses pre-existing Compose-labelled resources
for the fixed `electrumx-ravencoin` project name, this cleanup cannot silently
delete an older node that existed before the attempt.

After reviewing the failure, an operator who wants traditional synchronization
can start a new clean attempt explicitly with `--p2p-bootstrap`. No automatic
fallback is treated as success.

## Archive member handling

ChainStrap archives may contain data other than raw block files. Location alone
is not a trust signal: a safe regular file under `blocks/` is not automatically
accepted as a Ravencoin block file.

The extractor classifies members structurally:

- allowlisted raw `blocks/blkNNNNN.dat` members may be extracted;
- other safe regular members are ignored regardless of directory;
- path traversal, unsafe links, device/special entries, malformed names, and
  other unsafe archive structures fail closed.

This means upstream snapshots can contain their own indexes without those
indexes ever becoming local trusted state.

## Resume and gateway behavior

Use fast bootstrap only for a fresh bundled-Core deployment. Do not enable it on
a data volume that already contains Core data without a matching bootstrap
marker. The downloader fails closed rather than overlaying an existing node.

If a vetted bootstrap is interrupted, a manifest-bound progress marker lets the
same snapshot resume without re-downloading parts already extracted
successfully. In-progress archive bytes can survive transient gateway failures,
but another allowlisted HTTPS gateway may continue only when it returns an
exact, manifest-consistent HTTP `Content-Range`.

A SHA-256 or size-integrity failure discards the unsafe partial rather than
reusing it. Gateway selection is adaptive: a gateway that successfully delivers
a verified part becomes preferred for later parts, while a gateway that fails
before delivering payload bytes is temporarily circuit-broken instead of being
retried repeatedly for every part.

The `dweb.link` and `w3s.link` transports use DNS-safe CIDv1 subdomain gateway
URLs directly. Redirects, when present, are accepted only inside the original
release-allowlisted gateway family. All transports remain untrusted until the
pinned part size and SHA-256 are verified.

Progress output shows `measuring speed...` during the initial transfer sample;
rate and ETA appear only after enough bytes or time have been observed, avoiding
misleading startup estimates.

## Local Core reindex

After raw blocks are staged, the bundled Core runs offline with networking
disabled and rebuilds chainstate and the required indexes locally using:

```text
-reindex -assumevalid=0
```

The project does not patch or suppress Ravencoin Core consensus behavior to make
ChainStrap succeed. Historical transfer-script deserialization warnings can
appear while Core scans old raw block files because Ravencoin's loader contains
an upstream historical compatibility path. Their presence is not treated as
proof of success or as proof of failure by itself.

The decisive trust boundary is the resulting **active chain and indexes**, not
the absence of log warnings.

## Post-reindex verification gate

A successful `ravend -reindex` process exit is necessary but not sufficient for
bootstrap completion. Before `.chainstrap-reindex-complete` is written, the
verification Core remains offline and must prove all of the following against
the snapshot marker:

1. `getblockcount` equals the snapshot height;
2. `getbestblockhash` equals the snapshot block hash;
3. `getblockhash(snapshot_height)` equals the same snapshot block hash;
4. the asset metadata database answers real `listassets` / `getassetdata`
   read-only queries for an asset present at the snapshot tip; and
5. the address-by-asset index answers `listaddressesbyasset` without reporting
   that `-assetindex` is disabled or unusable.

The verification process inherits the isolated bootstrap network model, so this
stage does not repair an incomplete local state by fetching missing network data.
If any proof fails, bootstrap remains incomplete and normal serving does not
start.

This is why the project does not rely on process exit code alone and why no
ChainStrap-generated chainstate or indexes are imported.

## Running the source-checkout bootstrap

```sh
git clone https://github.com/ALENOC/electrumx-ravencoin.git
cd electrumx-ravencoin
./fast-bootstrap.sh --enable-reboot
docker compose up -d --build
```

Compose can display `Waiting` for a long time while gated one-shot stages are
active. That is expected. Use the status helper instead of interpreting
`Waiting` as a hang:

```sh
./fast-bootstrap-status.sh
./fast-bootstrap-status.sh --follow
```

The first form shows the current phase, container state, live resource counters,
and recent logs. The second follows ChainStrap, reindex, Core, and ElectrumX logs
across the complete bootstrap. `Ctrl-C` stops only log following; it does not
stop the containers.

Then inspect the normal services:

```sh
docker compose ps
docker compose logs -f ravencoin-core electrumx
```

The ChainStrap download can finish much earlier than the complete bootstrap.
Core must still parse and validate all downloaded blocks and rebuild its
indexes, and ElectrumX must still build its historical query database.

For the currently pinned snapshot, the downloader requires about **85 GiB of
free space** before a fresh bootstrap starts. That check is intentionally
conservative and does not replace normal free-space headroom for the later
ElectrumX database and chain growth.

After a Fast Verified Bootstrap has completed its full Core reindex and
post-reindex gate, the completed state is tied to the exact bootstrap marker
hash. A later release may pin a newer ChainStrap snapshot for new nodes without
forcing an already validated node to bootstrap again.

## Pinned snapshot

The current integration pins the RVN mainnet snapshot published at block
**4,501,329**, block hash
`000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48`, from
ChainStrap commit `c4ed0750603ea59823cdd21854d7eb75fe365928` (snapshot
timestamp 2026-08-19T22:16:38Z). The manifest records 17 IPFS parts and their
SHA-256 values.

A newer ChainStrap snapshot is **not consumed automatically**. Updating the
pinned manifest is a reviewed repository change, so an upstream metadata change
cannot silently expand what a release downloads.

## Testnet

ChainStrap testnet bootstrap is intentionally unsupported. The integration
accepts only `RVN` `mainnet`; adding another network requires a separately
reviewed manifest and validation path.

## Attribution

ChainStrap is an independent Ravencoin community project. This repository does
not copy its downloader implementation; it consumes the public snapshot
format/CIDs and credits the upstream project as the data transport source.
