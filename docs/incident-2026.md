# August 2026 Ravencoin consensus incident

Documentation: [Home](../README.rst) · [Docs index](README.md) ·
[Security model](security-model.md) · [Core certification](core-certification.md)

This document explains why the maintained server treats Core release identity
and chain evidence as security boundaries. It separates facts reported by the
primary release source from the additional defense-in-depth checks implemented
in this repository.

## Short version

In August 2026, a consensus vulnerability involving Ravencoin's KAWPOW block
header height was exploited on mainnet. Vulnerable software accepted a header
whose declared `nHeight` did not match the block's real position in the chain.
That value influences proof-of-work validation, so the mismatch was consensus
critical rather than cosmetic.

The 2miners v4.8.0 release identifies block 4,487,775 as the last unaffected
checkpoint and rejects the affected construction from block 4,487,776. It also
contains recovery behavior for block-index and chainstate problems caused by
the malformed history. The maintained ElectrumX stack requires a certified
Core identity and independently checks the chain served to wallets.

## What the primary source says

The [2miners/Ravencoin v4.8.0 release notes](https://github.com/2miners/Ravencoin/releases/tag/v4.8.0)
say that exploitation had been observed since 2026-08-07. They describe the
first affected block as 4,487,776 and the last unaffected mainnet checkpoint as
4,487,775. They also report 96 affected blocks among 2,089 blocks measured from
height 4,489,527 through 4,491,615.

The release notes are the primary source for the incident-specific numbers in
this document. The exact candidate identity, certification report and profile
digest are maintained separately in [Core certification](core-certification.md)
and the public `core-safety/production/` artifacts.

## Background: KAWPOW and `nHeight`

KAWPOW is Ravencoin's proof-of-work system. A block header includes a declared
height, `nHeight`, alongside fields such as the previous block, timestamp and
proof-of-work data. The height is used when selecting the relevant DAG epoch
and ProgPoW period, and therefore affects how the proof of work is interpreted.

The vulnerable contextual path treated the declared height as if it were the
block's real chain height. It did not enforce the relationship:

```text
declared header nHeight == contextual chain height
```

That allowed a block in one position to claim another position and influence
which proof-of-work path was taken. The release notes explain that the cheap
path below the checkpoint checked only the final hash over the supplied
`mix_hash`, not genuine ProgPoW work, and that the decision depended on the
height the block declared about itself.

This is a consensus rule. It cannot safely be replaced with a server-version
check or a string search.

## Boundary and checkpoint

The maintained profile uses the following boundary:

| Height | Meaning |
|---|---|
| 4,487,775 | Last-unaffected mainnet checkpoint used to anchor history |
| 4,487,776 | First affected boundary; the post-incident height binding applies |

The release's checkpoint is useful for two different purposes:

1. **Release certification:** deterministic candidate fixtures and candidate
   validation code prove that the checkpoint data and boundary behavior are
   preserved without downloading mainnet.
2. **Live node validation:** a synchronized deployment must actually observe
   the canonical checkpoint hash at height 4,487,775.

The first is reproducible in CI. The second remains a deployment gate and is
not implied by a software release passing certification.

## Consequences seen on mainnet

The release notes describe two operational consequences of the malformed
history:

* **Block-index reload failures:** on restart, the index's chain-derived height
  could cause proof-of-work recomputation to fail, producing an apparent block
  database loading error even though the raw block data was still on disk.
* **Header synchronization failures:** headers rebuilt from the affected index
  could hash differently from the value expected by a child. A peer then saw a
  non-continuous header sequence, scored or banned the peer, and could not catch
  up through the affected sequence.

The release also says that the patched node can skip unverifiable block-index
entries, drop entries with broken ancestry, and rebuild chainstate when the
coins database is ahead of the repaired block index. These operations can take
hours because they replay a large portion of the chain. A node doing that work
is not necessarily hung.

## Asset database and `transfer_overflow` context

The release notes state that asset databases are wiped together with chainstate
during the recovery path. Asset data is derived from the chain; retaining an
old asset registry while replaying a repaired history can cause duplicate
issuance failures and stop recovery. This is why the deployment keeps
`assetindex` and asset RPC checks as explicit live gates.

The release also describes asset transfer quantity-overflow checks as the
`transfer_overflow` BIP9 soft fork on bit 11. Release certification tests the
candidate's behavioral rule. Live validation separately confirms that the
deployed mainnet node reports the activation state and serves the expected
asset data.

## Recovery implications for an operator

Follow the release-specific recovery instructions for the exact Core build and
protect your operational data before changing a live node. In particular:

* plan downtime and keep the host powered and cooled;
* preserve configuration and credentials separately from regenerable chain and
  index data;
* do not delete raw blocks or chainstate as a first reaction to a slow restart;
* do not start a second reindex on a node already performing recovery;
* validate the canonical checkpoint, indexes, asset RPC and ElectrumX history
  after Core recovery completes.

The maintained deployment guide deliberately keeps release certification
independent from a full-mainnet recovery. That makes the software gate
reproducible while leaving real chain state to the protected live-node gate.

## What ALENOC adds

The following are ALENOC defense-in-depth measures, not claims that ALENOC
discovered the original vulnerability:

* Core releases are identified by exact repository and commit, not by a version
  floor alone.
* A candidate must pass the immutable behavioral safety profile before entering
  the signed safe-Core policy.
* The server reports backend evidence through `server.ravencoin_backend`, but
  that self-report is not treated as remote binary attestation.
* The deployment checks network, synchronization, checkpoint, KAWPOW, asset and
  chain evidence independently.
* The client fails closed for an unknown, stale, contradictory or revoked
  release and continues validating the chain independently.

## References

* [Ravencoin 4.8.0 release notes — 2miners/Ravencoin](https://github.com/2miners/Ravencoin/releases/tag/v4.8.0)
* [Ravencoin source repository — RavenProject/Ravencoin](https://github.com/RavenProject/Ravencoin)
* [Core certification guide](core-certification.md)
* [Security model](security-model.md)
* [Live validation status](validation-status.md)
