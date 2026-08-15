==========================================================
ElectrumX for Ravencoin — maintained for Ravencoin Core 4.8+
==========================================================

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

   Electrum client
          |
          | Electrum TLS :50002
          v
   +----------------------+       private Docker bridge       +------------------+
   | ElectrumX-RVN        | --------------------------------> | Ravencoin Core    |
   | non-root             |       JSON-RPC :8766 (not         | 4.8.0, non-root  |
   | persistent index     |       published on the host)      | persistent chain |
   +----------------------+                                    +---------+--------+
                                                                       |
                                                                       | P2P :8767
                                                                       v
                                                               Ravencoin network

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

The generated configuration enables ``server=1``, ``txindex=1`` and
``assetindex=1``.  ElectrumX needs historical raw transactions while indexing,
and ``blockchain.asset.list_addresses_by_asset`` delegates to Core's asset
index.  Wallet loading is disabled because ElectrumX does not require a Core
wallet.  Address, timestamp, and spent indexes are not enabled without a server
requirement.

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

``server.version`` is the ElectrumX software identity.  Backend Core evidence
comes only from ``server.ravencoin_backend``.  The current sanitized schema
contains server identity, Core version/versionNumber/subversion/network,
blocks/headers/IBD, minimum safe Core, network/sync/checkpoint/KAWPOW flags, and
an observation time.  It never contains RPC credentials, wallet data, or file
paths.  Clients must still verify genesis and chain history.

Ports and TLS
=============

=================  =====  ================================================
Service            Port   Default exposure
=================  =====  ================================================
Ravencoin P2P       8767   Public TCP
Ravencoin JSON-RPC  8766   Private bridge only; never publish
Electrum TCP        50001  Host loopback only; indexing/diagnostics
Electrum TLS        50002  Public only with ``compose.tls.yaml``
Management RPC       8000  Container loopback only
=================  =====  ================================================

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
mainnet, non-pruned, have ``txindex=1`` and ``assetindex=1``, and pass every
version, checkpoint, KAWPOW, canonical-tip, and broadcast check.

Asset verification
==================

After indexing, make read-only Electrum calls for a real asset controlled or
known by the operator: ``blockchain.asset.get_meta``,
``blockchain.asset.get_assets_with_prefix``,
``blockchain.asset.list_addresses_by_asset``, asset-aware scripthash balance,
history, listunspent and mempool methods, plus owner/unique/restricted asset
queries as applicable.  Test ``blockchain.transaction.broadcast`` only with an
operator-controlled signed transaction; read-only checks do not prove
broadcast eligibility.

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
