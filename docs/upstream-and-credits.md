# Upstream, credits and license

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security](security-model.md)

## Lineage

```text
ElectrumX -> Ravencoin ElectrumX fork -> Electrum-RVN-SIG fork -> ALENOC fork
```

Neil Booth created the original ElectrumX implementation. Ravencoin conversion,
asset work and later maintenance came from the Electrum-RVN-SIG community,
including kralverde. ALENOC maintains this fork and does not claim authorship of
the upstream software or official Ravencoin status.

## ChainStrap

The optional Fast Verified Bootstrap uses snapshot metadata and IPFS content
published by the independent community project
`chainstrap/chainstrap.github.io`. No ChainStrap downloader source is copied
into this repository. The local integration intentionally accepts only raw
Ravencoin `blk*.dat` files from a release-pinned manifest and requires the
bundled Core to reindex and validate them before normal service startup.

## License

The repository remains MIT-licensed. `LICENCE` retains Neil Booth's original
copyright and permission notice. Existing source notices and
`docs/ACKNOWLEDGEMENTS` remain part of the distribution. `NOTICE.md` records
the maintained-fork relationship without adding legal restrictions or an
unsupported ownership claim.

See also [the original authors reference](authors.rst).
