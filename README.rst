============================================================
ElectrumX for Ravencoin — maintained for Ravencoin Core 4.8+
============================================================

This is the maintained ALENOC fork of ElectrumX-RVN.  It is designed for
modern Ravencoin operation after the August 2026 consensus incident, preserves
Ravencoin asset indexing and RPC methods, and adds explicit validation of the
Ravencoin Core backend.  **Ravencoin Core 4.8.0 or newer is the production
safety baseline. Core 4.6.x and 4.7.x are rejected by default.**

The recommended Docker Compose deployment supplies both an exact, verified
Ravencoin Core 4.8.0 binary and ElectrumX.  A second mode connects ElectrumX to
an operator's existing compatible Core.  ElectrumX itself is
``ElectrumX-RVN 1.13.0.dev1``; it is not “ElectrumX 4.8.0”.

Security status
===============

Production mainnet operation fails closed when the daemon is older than 4.8.0,
is on the wrong network, does not match checkpoint 4,487,775, or conflicts with
the existing ElectrumX database.  Validation repeats while the server runs and
immediately before transaction broadcast.  Never set
``ALLOW_UNSAFE_RAVENCOIN_CORE`` in production.

The bundled Core artifact is currently **Linux amd64 only** because that is the
only binary published by the maintainer release.  The ElectrumX image is tested
for amd64 and arm64.  An arm64 operator can use ``--existing-core`` with a
separately verified Core 4.8.0+ installation; this repository does not pretend
that an unverified arm64 Core artifact exists.

Current validation status
=========================

This table reports what has actually been observed, not what is intended.
It is updated as live validation progresses.

==========================================  ======================================
Item                                        Evidence as of 2026-08-15
==========================================  ======================================
Implementation and deterministic tests      complete; 176 passed, 5 skipped
Bundled Core 4.8.0 container                smoke validated, Linux amd64
Compose models (bundled/TLS/existing-Core)  validated by ``docker compose config``
Live Core reindex with txindex+assetindex   in progress, Linux amd64
Full ElectrumX mainnet historical index     in progress, not complete
Live Raven asset RPC validation             pending the completed index
Client end-to-end ``SAFE_CORE_VERIFIED``    pending the completed index
Public CA-valid Electrum TLS endpoint       pending operator network action
ElectrumX arm64 container                   build validated only
Bundled Core on arm64                       not published; use existing-Core mode
==========================================  ======================================

An initial Core reindex plus a full ElectrumX historical index is a
multi-hour to multi-day job on any hardware.  Do not advertise a server
until both finish and the checks in this document pass.

Why this fork exists
====================

The August 2026 Ravencoin incident exploited missing validation between the
``nHeight`` declared by a post-KAWPOW header and its actual chain position.
Patched Core behavior rejects the affected construction from height 4,487,776
and anchors the last unaffected block at 4,487,775.  This fork applies those
invariants at the Electrum server boundary as defense in depth and requires the
patched Core generation.

Primary reference: the `2miners Ravencoin 4.8.0 maintainer release
<https://github.com/2miners/Ravencoin/releases/tag/v4.8.0>`_.

What changed in this fork
=========================

The maintained code and tests implement:

* a numeric ``>= 4.8.0`` Core floor, including acceptance of exactly 4.8.0;
* default rejection of 4.6.x, 4.7.x, malformed versions, and wrong networks;
* periodic backend validation and downgrade detection after startup;
* fresh backend validation immediately before transaction broadcast;
* the exact mainnet checkpoint at height 4,487,775;
* post-KAWPOW ``nHeight`` validation from height 4,487,776;
* canonical Core-chain validation of the ElectrumX database tip and checkpoint;
* sanitized backend evidence through ``server.ravencoin_backend``;
* preserved Ravencoin asset indexing and asset-aware RPC methods;
* current Python packaging/runtime support and non-root containers;
* amd64 and arm64 ElectrumX image builds; and
* a complete, health-gated Core + ElectrumX Compose deployment.

Recommended hardware
====================

Read this before buying anything.  A *recommended target* is not the same
as a *runtime-validated configuration*, and this section keeps the two
apart.

=========================================  =============================  ============  ==============================================================
Platform                                   RAM                            Data storage  Status
=========================================  =============================  ============  ==============================================================
Raspberry Pi 5                             8 GB minimum, 16 GB better     NVMe SSD      recommended low-power target; runtime validation pending
Orange Pi 5-class (RK3588/RK3588S)         8 GB minimum, 16 GB preferred  NVMe SSD      recommended low-cost target; runtime validation pending
x86-64 mini PC / NUC                       16 GB or more                  NVMe SSD      fastest initial indexing; bundled Core path validated on amd64
Dedicated server or VPS                    16 GB or more                  NVMe SSD      optional, for a long-lived public node
Orange Pi Zero 3 and other ~1-2 GB boards  insufficient                   n/a           not recommended; see the warning below
=========================================  =============================  ============  ==============================================================

Raspberry Pi 5 — first choice for a low-power node
--------------------------------------------------

* an 8 GB board is the sensible minimum for Core plus ElectrumX; the 16 GB
  variant leaves more room for a larger ElectrumX cache;
* an NVMe SSD, attached through the official Raspberry Pi M.2 HAT+ or an
  equivalent PCIe adapter.  Raspberry Pi documents the Pi 5 interface as a
  single-lane PCIe 2.0 connection, and states that the board "is not
  certified for Gen 3.0 speeds" and that "PCIe Gen 3.0 connections may be
  unstable".  Plan on the certified Gen 2 setting for an unattended node;
* check the form factor before ordering: the official M.2 HAT+ M Key takes
  2230 or 2242 drives, and the Compact version takes 2230 only.  A 2280
  drive needs a different carrier;
* active cooling.  Raspberry Pi states the Pi 5 "will perform best with
  active cooling", and the initial index runs the CPU hard for hours;
* a quality 5 V / 5 A USB-C Power Delivery supply.  An underpowered supply
  plus a hungry NVMe drive is a classic source of mid-index corruption;
* a 64-bit OS with Docker and Compose v2.

The bundled Core artifact is amd64-only, so an arm64 board runs ElectrumX in
``--existing-core`` mode against a Core 4.8.0+ build you verify yourself.

Orange Pi 5 class — second choice, lower cost
---------------------------------------------

An RK3588 or RK3588S board with 8 GB or more (16 GB preferred when the price
difference is small), an NVMe SSD, active cooling and a reliable supply is a
suitable low-power target.  **Verify your exact variant before buying
storage.**  The Orange Pi 5, 5B, 5 Plus and 5 Pro differ in M.2 connector,
lane count, supported drive length and boot behaviour, so follow the vendor
documentation for your board rather than a generic guide.

x86-64 mini PC or NUC — fastest initial build
---------------------------------------------

16 GB or more with an NVMe SSD.  This is the only class on which the bundled
Core 4.8.0 + ElectrumX deployment path itself has been exercised, and more
cores shorten the one-off Core reindex and historical index considerably.

Not suitable: very low memory boards
------------------------------------

Boards in the Orange Pi Zero 3 class, around 1-2 GB of RAM, are **not**
suitable for a combined Core + ElectrumX node.  A controlled attempt on a
~1.45 GiB board of that class produced severe memory, swap and I/O pressure
before any index existed.  Such a board can still run Ravencoin Core alone.
Model names vary, so treat this as a memory-size limit rather than a claim
about every board in a product family.

Storage: use NVMe or SSD, not microSD
-------------------------------------

Three separate database workloads stack up here:

1. Core downloads or reindexes raw blocks and rebuilds chainstate;
2. ``txindex`` and ``assetindex`` add further index writes;
3. ElectrumX then builds its own historical LevelDB index on top, with its
   own compaction.

The initial build is far heavier than steady state, and it is dominated by
storage latency and write endurance rather than by raw CPU.  Once both
indexes exist, ongoing load is much lighter.

* **Do not put the Ravencoin chain or the ElectrumX database on a microSD
  card.**  microSD is fine for boot or recovery media, not for this data;
* prefer a reputable TLC SSD or NVMe drive.  QLC drives do work, but TLC
  generally holds up better under a long index build;
* keep free-space headroom, enable normal Linux TRIM, and watch SMART or
  NVMe health counters;
* do not substitute a large swap file for missing RAM.

Capacity, from measurements rather than predictions:

* a synchronized mainnet Core datadir without extra indexes measured about
  **45 GB** in August 2026, of which the raw ``blk*.dat`` payload was about
  **37 GB**;
* ``txindex``, ``assetindex`` and the ElectrumX index are additional, and
  the chain keeps growing.

A 1 TB-class NVMe drive is a reasonable starting point at this scale if you
monitor free space; 2 TB gives materially more headroom and is the better
choice for a long-lived public node.  Verify actual sizes on your own
deployment with the disk-usage commands below instead of trusting a fixed
number in a document.

Quick start — Core 4.8.0 + ElectrumX
====================================

Requirements are a current Linux Docker Engine, Docker Compose v2, Git, and
OpenSSL.  On a fresh amd64 server:

.. code-block:: sh

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot
   docker compose up -d --build
   docker compose ps

``setup.sh`` verifies Docker/Compose and the CPU architecture, creates
``.env`` without overwriting an existing file, generates strong RPC credentials
under the Git-ignored ``.secrets/`` directory, restricts their host permissions,
and validates the Compose model.  It never displays a credential or deletes
existing data.  ``--enable-reboot`` creates a non-secret user systemd unit,
refuses to overwrite an existing unit, and enables it for the next boot.  User
lingering must be enabled for boot before login; the script warns when an
administrator still needs to run ``loginctl enable-linger <operator-user>``.

The initial Electrum TCP listener is bound to ``127.0.0.1:50001``.  This lets
Core synchronize and ElectrumX index without accidentally publishing an
unencrypted production service.  Enable public TLS only after the checks below
pass.

Architecture
============

::

                          Internet
                             |
                             |  Electrum TCP / TLS  (operator opt-in)
                             v
                  +--------------------------+
                  |    electrumx-ravencoin   |   non-root, persistent index
                  +--------------------------+
                             |
                             |  private Docker network only:
                             |  Ravencoin JSON-RPC :8766
                             |  Ravencoin REST     :8766/rest/...
                             v
                  +--------------------------+
                  |  Ravencoin Core >= 4.8.0 |   non-root, persistent chain
                  +--------------------------+
                             |
                             |  P2P :8767  (operator opt-in inbound)
                             v
                       Ravencoin network

Public, only if the operator enables it:

* the Electrum service, TCP 50001 or preferably TLS 50002;
* Ravencoin P2P inbound on 8767.

Private, never published:

* Ravencoin JSON-RPC on 8766;
* Ravencoin REST on the same 8766 listener.

Core JSON-RPC and Core REST share one HTTP listener.  REST has **no
authentication at all**, so the ``rpcbind``/``rpcallowip`` pair is the only
access control.  The shipped Compose model binds that listener to the container
loopback and one private bridge address and gives it no host port mapping.

Core provenance and integrity
==============================

The bundled image downloads exactly:

* repository: ``https://github.com/2miners/Ravencoin``;
* release/tag: ``v4.8.0``;
* tag object: ``9f553fcbcd6929acf24c0dfea456398dc6455dae``;
* source commit: ``b60f50e04f1fba425b28804e61be2694faaf3469``;
* archive: ``ravencoin-4.8.0-x86_64-linux-gnu.tar.gz``;
* archive SHA-256:
  ``966cf8978af1f2e3f36e9733d011eb92f4116750af6f8e77c5a5ced525577c4c``.

The image build also verifies the published hashes of ``ravend`` and
``raven-cli``.  The archive digest matches both the release API digest and the
published ``SHA256SUMS`` file.  The release tag and checksum file are not GPG
signed; exact hash pinning detects substitution relative to the reviewed
release but is not a maintainer signature.  No ``latest`` tag or third-party
Core container image is used.

Host and storage requirements
=============================

Use a dedicated, current Linux host with a multi-core CPU, enough RAM for
Ravencoin Core plus the configured ElectrumX cache and operating system, and
ample SSD/NVMe storage for both databases.  HDD-only initial indexing is not
recommended.  Chain size, index size, sync time, and resource demand grow, so
measure current requirements before ordering hardware instead of relying on a
stale fixed number.

Core synchronizes chain state first while ElectrumX builds its own index from
Core.  Either operation can take significant time.  Do not advertise the
server until Core is synchronized, ElectrumX is caught up, TLS validates, and
backend/chain/asset checks all pass.

Persistent state
================

The Compose project creates:

* ``ravencoin-data`` — blocks, chainstate, indexes, and peer state;
* ``ravencoin-config`` — the generated Core configuration and RPC credential;
* ``electrumx-data`` — the ElectrumX LevelDB index; and
* ``rpc-secrets`` — container-readable copies of generated RPC credentials.

The host source credentials remain mode ``0600`` below ``.secrets/``.  A
network-isolated, one-shot initializer copies them to the private volume without
logging their values.  Core RPC is reachable only on the dedicated bridge and
has no host ``ports`` mapping.

Required Core settings
======================

The generated configuration enables ``server=1``, ``txindex=1``,
``assetindex=1`` and ``rest=1``.  ElectrumX needs historical raw transactions
while indexing, and ``blockchain.asset.list_addresses_by_asset`` delegates to
Core's asset index.  Wallet loading is disabled because ElectrumX does not
require a Core wallet.  Address, timestamp, and spent indexes are not enabled
without a server requirement.

``rest=1`` is mandatory, not optional: ElectrumX downloads every block from
Core's REST endpoint ``rest/block/<hash>.bin`` and has no JSON-RPC fallback.
Without it, Core answers each block request with ``Not Found``, ElectrumX logs
``daemon service refused: Not Found``, and the index never advances past
height ``-1``.  Core's REST interface is unauthenticated, so its only access
control is the ``rpcbind``/``rpcallowip`` pair; keep it on loopback and the
private bridge, and never publish port 8766.

Verify Ravencoin Core
=====================

.. code-block:: sh

   docker compose exec ravencoin-core ravend --version
   docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getnetworkinfo
   docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getblockchaininfo
   docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getconnectioncount
   docker compose exec ravencoin-core raven-cli -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin getblockhash 4487775

Require ``version`` at least ``4080000``, subversion resembling
``/Ravencoin:4.8.0/``, chain ``main``, blocks approximately equal to headers,
IBD false once synchronized, and checkpoint hash
``000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd``.

Verify ElectrumX
================

.. code-block:: sh

   docker compose ps
   docker compose logs --tail=100 ravencoin-core
   docker compose logs --tail=100 electrumx
   docker compose exec electrumx electrumx_rpc getinfo

The Core container becomes healthy when JSON-RPC is ready; that does not mean
IBD is complete.  ElectrumX retries temporary Core unavailability and does not
open its external session service until its own catch-up path permits it.

After the loopback listener is ready, query the exact server contract without
installing netcat:

.. code-block:: sh

   python3 - <<'PY'
   import json, socket
   calls = [
       ["server.version", ["operator-check", "1.10"]],
       ["server.features", []],
       ["server.ravencoin_backend", []],
       ["blockchain.headers.subscribe", []],
   ]
   with socket.create_connection(("127.0.0.1", 50001), timeout=10) as sock:
       stream = sock.makefile("rwb")
       for request_id, (method, params) in enumerate(calls, 1):
           stream.write((json.dumps({"id": request_id, "method": method,
                                     "params": params}) + "\n").encode())
           stream.flush()
           print(stream.readline().decode().rstrip())
   PY

``server.version`` is the ElectrumX software identity, for example
``ElectrumX-RVN 1.13.0.dev1``.  It is never the Ravencoin Core version.  Backend
Core evidence comes only from ``server.ravencoin_backend``, whose sanitized
schema is:

* ``server``, ``serverVersion`` — ElectrumX identity;
* ``backend.name``, ``backend.version``, ``backend.versionNumber``,
  ``backend.subversion``, ``backend.network`` — the daemon's own report, for
  example ``4.8.0``, ``4080000`` and ``/Ravencoin:4.8.0/``;
* ``backend.blocks``, ``backend.headers``, ``backend.initialBlockDownload``;
* ``compatibility.minimumSafeCore`` — ``4.8.0``;
* ``compatibility.coreSafe`` — version, network and checkpoint policy all hold;
* ``compatibility.networkMatches``, ``compatibility.backendSynchronized``;
* ``compatibility.kawpowHeightValidation`` — this server enforces the
  post-KAWPOW ``nHeight`` rule;
* ``compatibility.checkpoint4487775`` — see below;
* ``observedAt`` — when the evidence was collected.

It never contains RPC credentials, wallet data, or file paths.

Checkpoint semantics are deliberately strict, and two different questions are
kept apart internally:

* *known* — a backend still below height 4,487,775 cannot violate the
  checkpoint, so it is allowed to start and to keep syncing;
* *verified* — the server actually asked the daemon for the hash at 4,487,775
  and it matched.

Only the second is published.  ``compatibility.checkpoint4487775`` is therefore
``false`` on a backend that has not yet reached that height, together with
``backendSynchronized: false``, and becomes ``true`` once the comparison has
really been made.  A server must never advertise verification it has not
performed, and a client must not accept a server that has not performed it.

None of this replaces the client's own work: clients must still verify the
genesis hash and validate chain history independently.

Monitoring and reading the state
================================

Copy and paste, in this order:

.. code-block:: sh

   # containers, restart counts and health
   docker compose ps

   # Core identity, chain position and peers
   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin \
       getnetworkinfo | grep -E '"version"|subversion'
   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin \
       getblockchaininfo | grep -E 'chain|blocks|headers|verificationprogress|size_on_disk'
   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf -datadir=/var/lib/ravencoin \
       getconnectioncount

   # logs, most recent first-level problems only
   docker compose logs --tail=50 ravencoin-core
   docker compose logs --tail=50 electrumx

   # ElectrumX indexed height and cache state
   docker compose exec electrumx electrumx_rpc getinfo

   # storage growth of both databases
   docker system df -v | grep -E 'ravencoin-data|electrumx-data'
   df -h /var/lib/docker

Then read the state from that output:

==========================================  ==================================================  ======================================================
What you see                                What it means                                       What to do
==========================================  ==================================================  ======================================================
Core log ``Reindexing block file ...``      rebuilding the block index from local ``blk*.dat``  wait; ``blocks`` stays 0 during this phase
Core ``blocks`` far below ``headers``       downloading or connecting blocks                    wait; check peers and disk space
Core ``blocks`` == ``headers``, IBD false   Core is caught up                                   verify the indexes, then watch ElectrumX
ElectrumX ``db height`` 0 or far behind     historical index still building                     wait; this is the longest phase
``daemon service refused: Not Found``       Core REST is unavailable                            confirm ``rest=1`` in the Core configuration
ElectrumX ``db height`` ~= Core ``blocks``  index caught up                                     run the backend, chain and asset checks
``checkpoint4487775`` false                 backend has not verified the checkpoint yet         expected while syncing; must be true before publishing
==========================================  ==================================================  ======================================================

A healthy Core container only means Core answers JSON-RPC.  It does not mean
the chain is synchronized, and it certainly does not mean ElectrumX is ready.

Ports and TLS
=============

========================  =====  =====================================================
Service                   Port   Default exposure
========================  =====  =====================================================
Ravencoin P2P             8767   Public TCP if the operator forwards it
Ravencoin JSON-RPC        8766   Private bridge only; never publish
Ravencoin REST            8766   Same private listener; unauthenticated, never publish
Electrum TCP              50001  Host loopback only; indexing and diagnostics
Electrum TLS              50002  Public only with ``compose.tls.yaml``
ElectrumX management RPC  8000   Container loopback only
========================  =====  =====================================================

Obtain a CA-valid certificate for the Electrum hostname.  Put
``fullchain.pem`` and ``privkey.pem`` in one host directory, keep the private
key non-world-readable, and set ``ELECTRUMX_PUBLIC_HOST`` and
``ELECTRUMX_TLS_DIRECTORY`` in ``.env``.  Then run:

.. code-block:: sh

   docker compose -f compose.yaml -f compose.tls.yaml config --quiet
   docker compose -f compose.yaml -f compose.tls.yaml up -d --build
   openssl s_client -connect electrum.example.org:50002 -servername electrum.example.org -verify_return_error </dev/null

Use a certificate-renewal deploy hook to recreate or restart ElectrumX after an
atomic certificate update.  A normal HTTP-only Cloudflare Tunnel does not proxy
raw Electrum TCP/TLS; use direct TCP, a suitable TCP proxy, or a product that
explicitly supports arbitrary TCP.

Existing Core mode
==================

An operator already running non-pruned Ravencoin Core 4.8.0+ can deploy only
ElectrumX:

.. code-block:: sh

   ./setup.sh --existing-core
   ${EDITOR:-vi} contrib/electrumx.env
   ${EDITOR:-vi} .env
   docker compose -f compose.existing-core.yaml config --quiet
   docker compose -f compose.existing-core.yaml up -d --build

This mode uses host networking so ``127.0.0.1:8766`` remains private and
reachable.  It does not start a second Core.  The existing daemon must be
mainnet, non-pruned, have ``txindex=1``, ``assetindex=1`` and ``rest=1``, and
pass every version, checkpoint, KAWPOW, canonical-tip, and broadcast check.

Adding ``txindex`` or ``assetindex`` to a node that lacks them requires a full
Core reindex, so confirm both before choosing this mode:

.. code-block:: sh

   raven-cli getrawtransaction <old-confirmed-txid> >/dev/null && echo txindex-ok
   raven-cli listaddressesbyasset <existing-asset> >/dev/null && echo assetindex-ok
   curl -sf http://127.0.0.1:8766/rest/chaininfo.json >/dev/null && echo rest-ok

An unindexed node answers ``Use -txindex to enable blockchain transaction
queries`` or ``not functional unless -assetindex is enabled``, and a node
without ``rest=1`` fails the third check.

Asset verification
==================

Ravencoin asset support is **implemented and covered by the automated test
suite**; end-to-end validation against a completed mainnet index is **still in
progress** and this section will be updated when it finishes.  The asset
handlers registered by this server are:

* ``blockchain.asset.get_meta``, ``blockchain.asset.get_meta_history``;
* ``blockchain.asset.get_assets_with_prefix`` — also how owner tokens
  (``NAME!``) and unique assets (``NAME#TAG``) are discovered;
* ``blockchain.asset.list_addresses_by_asset`` — delegates to Core's
  ``assetindex``;
* ``blockchain.asset.broadcasts``, ``blockchain.asset.is_frozen``,
  ``blockchain.asset.verifier_string``,
  ``blockchain.asset.restricted_associations``, each with a ``_history`` or
  subscription variant where implemented;
* ``blockchain.tag.check``, ``blockchain.tag.qualifier.list``,
  ``blockchain.tag.h160.list`` and their history/subscription variants;
* asset-aware ``blockchain.scripthash.get_balance``, ``listunspent``,
  ``get_history`` and ``get_mempool``;
* ``blockchain.asset.subscribe`` / ``unsubscribe``.

After indexing, exercise them read-only against a real asset you know.
``blockchain.asset.list_addresses_by_asset`` additionally requires Core to have
passed asset activation, height 435,456; before that Core answers ``THIS COMMAND
IS NOT YET ACTIVE!``.  Test ``blockchain.transaction.broadcast`` only with an
operator-controlled signed transaction; read-only checks do not prove broadcast
eligibility, and a malformed payload only proves the boundary rejects it.

Backups
-------

Back up wallets outside this stack.  Core wallet support is disabled here.
Back up ``.secrets`` and the ``ravencoin-config`` volume as sensitive material.
The block data and ElectrumX index can be regenerated, but rebuilding is slow;
volume snapshots taken after a clean stop reduce recovery time.  Never copy a
live LevelDB directory as if it were a consistent backup.

Start, stop, restart, logs, and reboot
======================================

.. code-block:: sh

   docker compose start
   docker compose stop
   docker compose restart electrumx
   docker compose logs --follow
   docker compose ps

Both services receive SIGTERM and have long stop grace periods.  Automatic
crash restarts are capped at five attempts to avoid an endless failure loop.
Docker's bounded ``on-failure`` policy does not itself start containers after a
daemon reboot.  The Quick Start's enabled user unit runs
``docker compose up -d`` from this directory at boot; after reboot verify with
``docker compose ps`` and the Core/backend checks above.  Do not combine an
unbounded Docker restart policy with a second unbounded supervisor.

Upgrade
-------

Read release notes and review diffs first.  Preserve snapshots, then:

.. code-block:: sh

   docker compose stop
   git pull --ff-only
   ./setup.sh
   docker compose build --pull
   docker compose up -d

Re-run Core version/network/checkpoint, backend capability, chain, TLS, and
asset checks.  Never change Core to an unpinned image or use
``ALLOW_UNSAFE_RAVENCOIN_CORE`` to make an upgrade appear healthy.

Migration from an old Core/ElectrumX-RVN
=========================================

Read `docs/MIGRATING_FROM_ELECTRUM_RVN_SIG.md
<docs/MIGRATING_FROM_ELECTRUM_RVN_SIG.md>`_ before reusing data from around the
incident.  Upgrade and validate Core first.  Stop the old public listener and
take consistent backups.  This fork checks the existing ElectrumX DB tip and
checkpoint against Core.  A mismatch is a quarantine signal: review a rewind or
rebuild plan.  Do not blindly reuse, automatically wipe, or silently replace a
conflicting database.

Removal and data deletion
=========================

``docker compose down`` removes containers and the private network but keeps
named volumes.  **Adding ``--volumes`` irreversibly deletes Core chainstate,
indexes, configuration, RPC-secret copies, and the ElectrumX database.**  This
repository's setup never runs that destructive form.  Inventory and back up
volumes before any deliberate deletion.

Troubleshooting
===============

* **Core not synchronized:** inspect ``getblockchaininfo`` and peers; IBD and a
  blocks/headers gap are normal during initial sync.
* **ElectrumX waiting for Core:** require a healthy Core RPC and matching
  generated secret/config files; do not publish 8766.
* **Old Core rejected:** install 4.8.0 or newer.  Exactly 4.8.0 is valid.
* **Wrong network:** both ``NET=mainnet`` and Core chain ``main`` are required.
* **Checkpoint mismatch:** stop publication and investigate the Core chain/data.
* **DB-tip conflict:** preserve the database and follow the migration guide;
  never auto-delete it.
* **Indexing still running:** monitor both logs and storage I/O; the health of
  Core alone is not ElectrumX readiness.
* **Disk full:** stop cleanly, expand storage, and verify both databases before
  restart.
* **Permissions:** keep host secrets mode ``0600``; rerun ``./setup.sh`` to
  validate them without overwriting.
* **Port collision:** identify the existing listener before changing mappings;
  never solve a collision by publishing Core RPC.
* **TLS failure:** verify hostname, chain, file access, expiry, and renewal hook.
* **Backend method failure:** inspect server logs and query
  ``server.ravencoin_backend``; do not substitute ``server.version``.

Development
===========

.. code-block:: sh

   python3 -m venv env
   env/bin/pip install --upgrade pip
   env/bin/pip install . pytest pytest-asyncio
   env/bin/pytest -q

Security reports and the threat model are in ``SECURITY.md``.

Attribution
===========

Neil Booth wrote the original ElectrumX implementation.  Ravencoin conversion
and asset support came from the Electrum-RVN-SIG community, including
kralverde.  ALENOC maintains this fork; it did not create the original
software.  The original MIT licence, copyright notices, repository history,
``LICENCE`` and ``docs/ACKNOWLEDGEMENTS`` are preserved.
