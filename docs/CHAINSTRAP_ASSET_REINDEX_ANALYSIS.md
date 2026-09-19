# ChainStrap reindex analysis — consolidated

The source-level conclusions from this working paper are now part of the
maintained [Fast Verified Bootstrap](fast-bootstrap.md) guide, especially the
**Local Core reindex** and **Post-reindex verification gate** sections.

The active documentation now records the security-relevant conclusion directly:
a successful reindex process exit is not sufficient; the offline verification
stage must prove the expected active-chain height/hash and real asset/index
reads before the bootstrap completion marker is written.

The original detailed working paper remains recoverable from Git history.
