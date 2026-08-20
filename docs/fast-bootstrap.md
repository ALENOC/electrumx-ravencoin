# Fast Verified Bootstrap with ChainStrap

ChainStrap can reduce the time spent obtaining Ravencoin's historical block data. In this
project it is deliberately treated as a **transport source, not a consensus authority**.
ElectrumX never indexes a ChainStrap database directly.

## Trust model

The integration is intentionally narrower than the generic ChainStrap client:

1. The repository ships a reviewed RVN **mainnet-only** manifest pinned to a specific
   `chainstrap/chainstrap.github.io` commit.
2. Every IPFS part has a fixed CID, byte length and SHA-256 in that local manifest.
3. The downloader accepts only `blocks/blkNNNNN.dat`. It ignores downloaded chainstate,
   block indexes, undo files, asset databases, configuration and wallet files.
4. Block files must be contiguous from `blk00000.dat`; otherwise bootstrap stops.
5. The bundled, pinned Ravencoin Core 4.8.0 runs a full `-reindex -assumevalid=0` with
   `txindex=1` and `assetindex=1` while network access is disabled.
6. Normal Core startup is allowed only after that one-shot reindex exits successfully.
7. ElectrumX continues to depend on the normal Core health gate and builds its own database.

A malicious or broken snapshot can therefore waste bandwidth or fail bootstrap, but it is not
accepted as validated chain state. Consensus validation remains the responsibility of the local
Core binary qualified by this repository.

## Use it only for a fresh bundled-Core deployment

Do not enable fast bootstrap on a data volume that already contains Core data without a matching
bootstrap marker. The downloader fails closed rather than overlaying an existing node. If a vetted
bootstrap is interrupted, a manifest-bound progress marker lets the same snapshot resume without
re-downloading parts that were already extracted successfully. In-progress archive bytes are also
preserved across transient gateway failures: another allowlisted HTTPS gateway may continue only
when it returns an exact, manifest-consistent HTTP `Content-Range`. A SHA-256 or size-integrity
failure discards the unsafe partial instead of attempting to reuse it. Gateway selection is adaptive:
a gateway that successfully delivers a verified part becomes preferred for later parts, while a gateway
that fails before delivering any payload bytes is temporarily circuit-broken instead of being retried
repeatedly for every part. The `dweb.link` and `w3s.link` transports use DNS-safe CIDv1 subdomain
gateway URLs directly; redirects, when present, are accepted only inside the original release-allowlisted
gateway family. All transports remain untrusted until the pinned part size and SHA-256 are verified.
Progress output deliberately shows `measuring speed...` during the initial transfer sample; rate and ETA
appear only after enough bytes or time have been observed, avoiding misleading multi-hour startup ETAs.

```sh
git clone https://github.com/ALENOC/electrumx-ravencoin.git
cd electrumx-ravencoin
./fast-bootstrap.sh --enable-reboot
docker compose up -d --build
```

Compose can display `Waiting` for a long time while the gated one-shot stages are active.
That is expected: the downloader now emits per-part percentage, MiB/GiB transferred, current
throughput, ETA, overall snapshot progress, gateway fallback, SHA-256 verification and extraction
progress. Use the status helper instead of interpreting `Waiting` as a hang:

```sh
./fast-bootstrap-status.sh
./fast-bootstrap-status.sh --follow
```

The first form shows the current phase, container state, live resource counters and recent logs.
The second follows ChainStrap, reindex, Core and ElectrumX logs across the complete bootstrap.
`Ctrl-C` stops only log following; it does not stop the containers.

Then inspect the normal services:

```sh
docker compose ps
docker compose logs -f ravencoin-core electrumx
```

The ChainStrap download can finish much earlier than the complete bootstrap. Core must still
parse and validate all downloaded blocks and rebuild its indexes, and ElectrumX must still build
its historical query database. For the pinned snapshot below, the downloader requires about
**85 GiB of free space** before a fresh bootstrap starts. That check is intentionally conservative
and does not replace normal free-space headroom for the later ElectrumX database and chain growth.

After a Fast Verified Bootstrap has completed its full Core reindex, that completed state is tied
to the exact bootstrap marker hash. A later release may pin a newer ChainStrap snapshot for new
nodes without forcing an already validated node to bootstrap again.

## Pinned snapshot

The initial integration pins the RVN mainnet snapshot published at block **4,501,329**, block
hash `000000000004967a3501a0e5edca06f6a88f3a6b4af7b4688160e2b63a4a7e48`, from ChainStrap
commit `c4ed0750603ea59823cdd21854d7eb75fe365928` (snapshot timestamp 2026-08-19T22:16:38Z).
The manifest records 17 IPFS parts and their SHA-256 values.

A newer ChainStrap snapshot is **not consumed automatically**. Updating the pinned manifest is a
reviewed repository change, so an upstream metadata change cannot silently expand what a release
downloads.

## Testnet

ChainStrap testnet bootstrap is intentionally unsupported. The integration accepts only `RVN`
`mainnet`; adding another network requires a separately reviewed manifest and validation path.

## Attribution

ChainStrap is an independent project by the Ravencoin community. This repository does not copy
its downloader implementation; it consumes the public snapshot format/CIDs and credits the
upstream project as the data transport source.
