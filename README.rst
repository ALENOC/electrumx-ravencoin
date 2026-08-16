============================================
ElectrumX for Ravencoin
============================================

The maintained community edition of the Ravencoin ElectrumX server. It
combines Ravencoin Core and ElectrumX deployment tooling, asset-aware wallet
queries, and additional safety checks introduced after the August 2026
consensus incident.

If this is your first node, start here
======================================

This guide explains the pieces before asking you to run commands. If you
already operate Ravencoin Core and ElectrumX, use the `Documentation index`_
to jump to the operator and reference guides.

What is Ravencoin?
------------------

Ravencoin is a public blockchain for recording ownership and transfers. Every
transaction is part of a shared history called the blockchain. Many computers
keep copies of that history and check new blocks against the network rules.
That independent checking is what makes the network decentralized: no single
server gets to declare which history is true.

What is a node?
---------------

A Ravencoin *full node* is a computer running Ravencoin Core. Core downloads
the blockchain, verifies blocks and transactions against the consensus rules,
and exchanges the result with other nodes. It does not simply ask another
server what is true; it checks the data itself.

A node is not a wallet. A wallet manages keys and signs transactions for its
owner. Core can run with its wallet disabled, and this project deliberately
does so. Running this server does not give it your seed, private keys or coins.

What is ElectrumX?
------------------

A full node has the complete chain, but it is not designed to answer every
light wallet's address-history question quickly. ElectrumX reads Core's
validated chain and builds a lookup database. A wallet can then ask for
transactions, balances, history and Ravencoin asset data without downloading
and indexing the whole blockchain itself.

ElectrumX answers those queries; it does not hold wallet keys, sign payments or
take custody of funds. The layers look like this::

   wallet (keys and signing)
          |
          v
   ElectrumX (fast wallet-query index)
          |
          v
   Ravencoin Core (full verified chain)
          |
          v
   Ravencoin peer-to-peer network

Why run an Electrum server?
---------------------------

More independent Electrum servers improve the network in three practical ways:

* **Privacy:** an Electrum server can see which addresses a wallet asks about.
  More unrelated operators spread that knowledge instead of concentrating it.
* **Availability:** wallets have alternatives when one host is offline or
  overloaded.
* **Resilience:** independent servers let wallets compare answers and make it
  harder for one operator or outage to shape everyone's view of the chain.

Several hostnames run by one company are still one operator. This project
tracks operator groups rather than counting endpoints as if each were an
independent authority. Discovery is useful, but discovery is not trust; see
the `Electrum monitor`_ guide.

Why this maintained fork exists
-------------------------------

In August 2026, a consensus-critical weakness in Ravencoin's KAWPOW header
validation was exploited on mainnet. A header carries a declared ``nHeight``.
Vulnerable software did not consistently check that declaration against the
block's real position in the chain, even though the value affects proof-of-work
validation behavior. The affected history also created restart, index-reload
and header-synchronization problems.

The 2miners 4.8.0 response rejects the height mismatch from the affected
boundary, preserves the last unaffected checkpoint, and adds recovery behavior
for damaged index history. The maintained server adds defense in depth: exact
Core identity, behavioral release certification, signed policy evidence,
backend checks and independent chain validation. Read the detailed `August
2026 incident guide`_ for the boundary, consequences and primary source.

This is a community-maintained fork, not a publication of the Ravencoin
Foundation or the original Electrum maintainers. Lineage and licensing are
described in `Upstream and credits`_.

Documentation menu
==================

Start with whichever describes your goal:

* `Getting started`_: beginner concepts, bundled Core and existing-Core paths.
* `Hardware`_: Raspberry Pi, Orange Pi, x86, storage and cooling decisions.
* `Public node`_: dynamic DNS, DuckDNS, CGNAT, forwarding and TLS.
* `Operations`_: lifecycle commands, progress checks, backups and upgrades.
* `Security model`_: what release and server evidence can, and cannot, prove.
* `August 2026 incident guide`_: technical incident background and recovery.
* `Core certification`_: candidate releases, profile and signed policy.
* `Electrum monitor`_: discovery, health, operator groups and vantage points.
* `Architecture`_: service boundaries and data flow.
* `Troubleshooting`_: symptoms, checks and safe fixes.
* `Validation status`_: the single source for current release/live status.
* `Documentation index`_: the complete user-oriented map, including protocol
  and RPC references.

Quick start
===========

The bundled Core artifact is qualified for both **Linux x86-64/amd64** and
**Linux ARM64/aarch64** (including Raspberry Pi 5 and Orange Pi 5-class
boards). Docker selects the matching architecture automatically; the commands
below are the same for both.

For the bundled path, use a 64-bit Linux host with Docker Engine, Compose v2,
Git and OpenSSL:

.. code-block:: sh

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot
   docker compose up -d --build
   docker compose ps

What each command does:

* ``git clone`` downloads this repository to the host;
* ``setup.sh`` checks Docker and architecture, creates local configuration and
  ignored RPC credentials, and validates the Compose model;
* ``docker compose up`` starts the bundled Core plus ElectrumX services;
* ``docker compose ps`` shows service state and health.

The script does not print credentials or delete existing data. ``--enable-reboot``
is optional and installs a user service for reboot recovery. Read `Getting
started`_ before using an existing Core instead of the bundled mode.

Nothing is ready immediately after Docker starts, and that is normal
----------------------------------------------------------------------

Startup happens in stages:

1. Ravencoin Core starts and becomes JSON-RPC healthy.
2. Core downloads the chain, or scans/rebuilds indexes from existing block
   files. Required ``txindex``, ``assetindex`` and REST behavior are part of
   the deployment model.
3. ElectrumX waits for usable Core and then builds its historical database.
4. Core catches up to the network and ElectrumX catches up to Core.
5. Read-only backend, checkpoint, asset and chain checks are performed.
6. Only then should you consider publishing a public TLS endpoint.

An initial sync or index can take hours or days. A healthy container only means
that a process answers its health check; it does not mean the chain is current.
During Core initial synchronization, ``blocks < headers`` is normal. During a
block-file reindex, some progress fields can remain at zero while the log is
advancing. Do not restart Core or delete chain data because of that phase.

The complete path from first purchase to public service
========================================================

Use the following as a mental map. The first seven steps produce a useful
private server; publishing is optional:

1. Choose hardware from the `Hardware`_ guide.
2. Install a current 64-bit Linux, Docker Engine and Compose v2.
3. Clone this repository and run the setup script.
4. Start Core and ElectrumX.
5. Wait for Core to synchronize and build its indexes.
6. Wait for ElectrumX to finish its historical index.
7. Test the stack privately on the host or LAN.
8. If you want to serve others, choose a stable hostname such as DuckDNS.
9. Check for CGNAT, reserve a stable LAN address and forward only TCP 50002.
10. Obtain a CA-valid TLS certificate for the hostname.
11. Test DNS, TCP, TLS and the Electrum protocol from outside your LAN.
12. Advertise the endpoint only after the live validation gates pass.

Private node first, public node later
-------------------------------------

A **private node** serves your own wallets on your LAN or VPN. It needs no
router port forwarding and is the recommended first milestone.

A **public node** serves other wallets. It needs reliable inbound networking,
TLS, monitoring and ongoing maintenance. Neither mode gives the server your
wallet keys. Get the private mode working and synchronized before taking on
public networking; see `Public node`_.

Recommended hardware, briefly
=============================

* **Raspberry Pi 5, 8 GB or more, plus NVMe:** recommended low-power target;
  use active cooling, a reliable supply and a 64-bit OS. The bundled certified
  Core artifact is qualified for ARM64 and runs on the Pi 5 directly.
* **Orange Pi 5-class, 8 GB or more, plus NVMe:** useful lower-cost target;
  verify the exact 5/5B/5 Plus/5 Pro board before buying a carrier or drive.
  These ARM64 boards use the same bundled Core deployment as the Pi 5.
* **x86-64 mini-PC or NUC, 16 GB or more, plus NVMe:** the fastest and most
  straightforward bundled certified-Core deployment path.
* **Dedicated server/VPS:** suitable for a long-lived public node when storage,
  memory and raw TCP networking are appropriate.

NVMe/SSD is strongly preferred because raw blocks, chainstate, ``txindex``,
``assetindex`` and the ElectrumX database are all written during the initial
build. Do not place chain or index data on microSD. See `Hardware`_ for RAM,
cooling, TLC/QLC, free-space and board-specific guidance. Recommended hardware
is not the same as completed runtime validation.

Private checks after startup
============================

Run these from the repository directory:

.. code-block:: sh

   docker compose ps
   docker compose logs --tail=100 ravencoin-core
   docker compose logs --tail=100 electrumx
   docker compose exec electrumx electrumx_rpc getinfo
   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf \
       -datadir=/var/lib/ravencoin getblockchaininfo

Core's ``blocks`` and ``headers`` show chain progress. ElectrumX's ``getinfo``
shows its own indexed height. Read `Operations`_ for the configured RPC paths,
disk checks and a safe interpretation of waiting versus failure.

Publishing later
================

For a changing residential IP, dynamic DNS keeps a hostname pointed at your
current address. DuckDNS is one beginner-friendly example, and the setup script
has an optional ``--configure-ddns`` path. DNS alone does not bypass CGNAT or
open a router port. The full `Public node`_ guide explains stable LAN addresses,
port forwarding, ACME/TLS, renewal and external testing.

Never publish Core JSON-RPC or REST. Core REST is unauthenticated. The normal
public service is Electrum TLS on TCP 50002; the management interface and
unencrypted listener remain private unless you have a deliberate, reviewed
reason to expose them.

Security in one page
====================

The project separates software-release evidence from deployment evidence:

``exact Core repository + commit``
   -> behavioral release certification
   -> signed safe-Core policy
   -> fresh ``server.ravencoin_backend`` evidence
   -> independent chain validation
   -> wallet/server eligibility

The current certified release is documented in `Core certification`_. A version
number by itself does not establish trust, and the release certification does
not prove that an unrelated third-party Electrum server is running that exact
binary. Missing, stale, contradictory or unknown evidence fails closed. The
full explanation, including anti-rollback, revocation and operator diversity,
is in `Security model`_.

Current project status
======================

The authoritative status distinction is:

* **Core release certification:** the maintained v4.8.0 candidate passed all
  mandatory release tests and a signed policy exists.
* **Live mainnet deployment validation:** still in progress. Core synchronization,
  ElectrumX historical indexing, live asset/index checks, backend evidence,
  public TLS and client ``SAFE_CORE_VERIFIED`` remain deployment gates.

Do not call the live deployment fully validated or publish a wallet release
until the gates in `Validation status`_ are complete.

Contributing and credits
========================

This repository is maintained by ALENOC as a community fork. ElectrumX and the
Ravencoin adaptation have earlier authors and maintainers; the original MIT
notices remain in ``LICENCE``. See `Upstream and credits`_ and ``NOTICE.md``.
Security reports should use the maintained repository's security advisory
channel described in ``SECURITY.md``.

.. _Documentation index: docs/README.md
.. _Getting started: docs/getting-started.md
.. _Hardware: docs/hardware.md
.. _Public node: docs/public-node.md
.. _Operations: docs/operations.md
.. _Security model: docs/security-model.md
.. _August 2026 incident guide: docs/incident-2026.md
.. _Core certification: docs/core-certification.md
.. _Electrum monitor: docs/electrum-monitor.md
.. _Architecture: docs/architecture.md
.. _Troubleshooting: docs/troubleshooting.md
.. _Validation status: docs/validation-status.md
.. _Upstream and credits: docs/upstream-and-credits.md
