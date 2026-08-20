# ElectrumX crash consistency and monitor failure isolation

After an unclean host shutdown, a live Ravencoin ElectrumX deployment was
observed with LevelDB state committed through a block while the corresponding
`meta/hashes` and `meta/txcounts` tail was still zero/unwritten.  The old
startup path asserted on that mismatch and crash-looped.

The hardened design has three layers:

1. `DB.flush_fs()` writes headers, cumulative tx counts and transaction hashes
   with an fsync barrier (including new segment directory entries) **before**
   History/LevelDB state can commit.  A power loss can therefore leave flat
   files ahead of LevelDB, which is safe because the committed DB height is the
   authoritative prefix; it must not leave LevelDB ahead of non-durable flat
   metadata.
2. Startup no longer treats a trailing tx-count mismatch as a Python assertion.
   It detects truncated/non-monotonic/mismatched metadata plus a missing/zero
   committed-tip hash slot.  Before indexing, a bounded recovery of at most 64
   trailing blocks may rebuild headers/txcounts/hashes from the trusted daemon.
   Recovery is allowed only when the daemon tip matches the committed LevelDB
   tip and the cumulative tx count immediately before the recovery window is a
   valid anchor.  Otherwise startup fails closed and does not guess.
3. Ravencoin Node Monitor is no longer tied to the ElectrumX container network
   namespace or `service_healthy` dependency.  It stays alive during an
   ElectrumX crash-loop, continues monitoring Core/host state, and reports
   ElectrumX as unavailable.  Its admin RPC travels over a dedicated internal
   Docker network and is not published to the host.

This is defense in depth; clean shutdown and stable power are still strongly
recommended, but correctness no longer depends on the kernel flushing unrelated
flat-file page-cache writes before LevelDB's WAL/state commit.

## Monitor network and host-port resilience

The Monitor admin network is no longer tied to a single hard-coded Docker
subnet.  `configure_monitor_admin_network.py` inspects existing Docker IPv4
subnets and host routes, chooses a free RFC1918 `/29`, and records the subnet,
ElectrumX address and Monitor address in `.env`.  Complete operator overrides
are preserved; partial overrides fail closed.

The Monitor container healthcheck intentionally remains an in-container liveness
check.  Because that cannot prove Docker actually installed the host-side 8899
mapping, the verified installer additionally executes the pinned Node Monitor
`contrib/verify-published-port.py` after activation.  It requires both Docker
port metadata and a real host-loopback `/healthz` response.  On the known reboot
failure it may force-recreate **only** the Monitor once, then verifies again and
fails instead of looping or touching Core/ElectrumX.
