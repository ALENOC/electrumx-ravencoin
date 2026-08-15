============================================
ElectrumX for Ravencoin
============================================

The maintained ALENOC ElectrumX server fork for Ravencoin wallets. It preserves
Ravencoin asset support and adds a fail-closed safety boundary around the Core
backend and the chain served to clients.

.. warning::

   This is not an official Ravencoin or Electrum release. The initial certified
   Core is ``2miners/Ravencoin`` tag ``v4.8.0`` at commit
   ``b60f50e04f1fba425b28804e61be2694faaf3469``. A version number alone never
   grants trust; future Core releases must pass certification and appear in a
   valid signed safe-Core policy.

Quick links
===========

==============================  ============================================================
Guide                           Purpose
==============================  ============================================================
`Documentation index`_          Complete guide map
`Getting started`_              First deployment and what to expect
`Hardware`_                     NVMe, memory, SBC and x86 guidance
`Public node`_                  DDNS, CGNAT, port forwarding and TLS
`Operations`_                   Start, stop, logs, backups and upgrades
`Security model`_               Certified Core, backend evidence and chain validation
`Core certification`_           Release certification and signed policy
`Electrum monitor`_             Discovery, health and operator groups
`Validation status`_             Current evidence and pending live gates
`Troubleshooting`_              Common failures and safe recovery
`Upstream and credits`_         Lineage, MIT license and attribution
==============================  ============================================================

Why this fork exists
====================

ElectrumX indexes the full Ravencoin chain so lightweight wallets can query
balances and history without storing the chain themselves. More independent
servers improve privacy and availability, but a reachable server is not
automatically trustworthy.

This fork separates software-release trust from deployment trust:

``exact repository + commit`` -> ``behavioural certification`` ->
``signed policy`` -> ``backend evidence`` -> ``independent chain validation``

The signed policy says that a Core release passed the release profile. It does
not attest that an unrelated third-party Electrum server is running that exact
binary. The server and wallet therefore remain fail-closed when evidence is
missing, stale, contradictory or on the wrong chain.

Quick start
===========

For a new Linux x86-64 host with Docker Engine and Compose v2::

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot
   docker compose up -d --build
   docker compose ps

The bundled path supplies the pinned certified Core and ElectrumX. Core first
synchronizes or rebuilds its indexes; ElectrumX then builds its historical
index. A private listener is the safe starting point. Do not publish the node
until both indexes and the live validation checks are complete. For an already
running compatible Core, use the `existing-Core guide`_.

What happens next
-----------------

``Core sync/reindex -> ElectrumX historical index -> read-only validation ->
optional public TLS``

The initial process can take hours or days and resumes after a restart. Never
delete chainstate, Core indexes, or the ElectrumX database to solve a temporary
sync problem; see `Operations`_.

Recommended hardware
====================

* **Raspberry Pi 5 + NVMe** — recommended low-power target; runtime validation
  pending.
* **Orange Pi 5-class + NVMe** — recommended low-cost target; runtime
  validation pending.
* **x86-64 mini-PC + NVMe** — fastest path; bundled amd64 deployment exercised.
* **Server/VPS + NVMe** — suitable for a long-lived public node.

Use 8 GB RAM as a practical SBC minimum and 16 GB+ for x86 or comfortable
indexing. Use TLC/enterprise-oriented NVMe storage with free-space headroom;
microSD is not suitable for chain or index data. See `Hardware`_ for board
variants, cooling, power and the Orange Pi Zero-class warning.

Current status
==============

* Core release certification: **CERTIFICATION_PASSED**, 12/12 mandatory tests.
* Certified identity: ``2miners/Ravencoin`` ``v4.8.0`` at the exact commit above.
* Signed safe-Core policy: persisted and verified; current policy version 2.
* Live Core reindex and ElectrumX historical indexing: **in progress**.
* Asset RPC, public endpoint and client ``SAFE_CORE_VERIFIED``: **pending live
  validation**.

Release certification is complete; live deployment validation is not. The
authoritative details are in `Validation status`_.

Public node
===========

Want to publish a node? First finish private validation, then follow the
`Public node`_ guide. It covers stable LAN addressing, dynamic DNS, CGNAT,
TCP 50002, certificates, renewal and external testing. Never expose Core JSON-
RPC or unauthenticated REST to the Internet.

License and credits
===================

The project remains MIT-licensed. Original ElectrumX, Electrum-RVN-SIG and
Ravencoin-related attribution is preserved; ALENOC maintains this fork and does
not claim authorship of the upstream software. See `Upstream and credits`_,
``LICENCE`` and ``NOTICE.md``.

.. _Documentation index: docs/README.md
.. _Getting started: docs/getting-started.md
.. _Hardware: docs/hardware.md
.. _Public node: docs/public-node.md
.. _Operations: docs/operations.md
.. _Security model: docs/security-model.md
.. _Core certification: docs/core-certification.md
.. _Electrum monitor: docs/electrum-monitor.md
.. _Validation status: docs/validation-status.md
.. _Troubleshooting: docs/troubleshooting.md
.. _Upstream and credits: docs/upstream-and-credits.md
.. _existing-Core guide: docs/getting-started.md#existing-core-mode
