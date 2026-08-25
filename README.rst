================================
ElectrumX for Ravencoin
================================

Production-oriented ElectrumX infrastructure for Ravencoin with verified
Ravencoin Core 4.8.0, Fast Verified Bootstrap, signed transactional updates,
optional node monitoring, Network Observer tooling, and maintained Linux amd64
/ ARM64 deployment paths.

**Current release: ElectrumX-RVN 1.13.11**

`Install`_ · `What's new`_ · `Architecture`_ · `Security`_ ·
`Network Observer`_ · `Documentation`_ ·
`Latest release <https://github.com/ALENOC/electrumx-ravencoin/releases/latest>`_

.. _Install:

Quick install
=============

Download the published installer first, then run the local file:

.. code-block:: sh

   curl --fail --location --remote-name \
     https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py

   python3 electrumx-ravencoin-install.py

The guided installer lets you choose storage, bootstrap method, and optional
monitoring. Do not use ``curl ... | python3`` or ``curl ... | bash``: keeping
the installer on disk makes the initial executable inspectable before it runs.

To verify the host and signed release metadata without making a persistent
installation:

.. code-block:: sh

   python3 electrumx-ravencoin-install.py --check-only

Default fresh install
---------------------

The guided installer defaults to:

* official ``RavenProject/Ravencoin`` Core 4.8.0;
* ChainStrap Fast Verified Bootstrap;
* a mandatory full local Core validation/reindex;
* Ravencoin Node Monitor enabled;
* the privileged bandwidth/connection controller disabled unless requested;
* explicit storage selection;
* deterministic Compose project name ``electrumx-ravencoin``.

Why run ElectrumX-RVN?
======================

Ravencoin Core validates the blockchain, but lightweight wallets need efficient
address-history and asset queries. ElectrumX builds that query index from the
chain already validated by Core and serves wallet requests without holding
wallet private keys or signing transactions.

More independently operated Electrum servers improve availability, privacy,
infrastructure decentralization, and network resilience.

.. _What's new:

What's new in 1.13.11
=====================

Since 1.13.1, the project has added revision-aware signed releases,
host-wide anti-rollback state, transactional update/rollback, hardened
ChainStrap staging, and explicit migration of legacy persistent state.

Release 1.13.11 also introduces the optional Ravencoin Network Observer:
Chain Quorum 2.0, signed multi-vantage observations, operator-aware diversity,
active asset capability probes, and height-bound Asset Data Quorum. A tested
N-of-M governance/succession framework is included but production threshold
governance is **not activated**; the project is founder-independence capable,
not founder-independent.

See `1.13.11 overview`_ for the technical changes and compatibility guarantees.

How it works
============

The normal data and trust path is::

   Wallet / Electrum client
             |
             v
   ElectrumX-RVN 1.13.11
             |
             v
   Ravencoin Core 4.8.0
   RavenProject/Ravencoin
             |
             v
   Ravencoin peer-to-peer network

Official Ravencoin Core identity
--------------------------------

The bundled deployment is pinned to the official Ravencoin repository:

.. code-block:: text

   repository : RavenProject/Ravencoin
   tag        : v4.8.0
   version    : 4.8.0
   commit     : 22549129888d02e0e08fcdb9f96f3c699167e774

A daemon version string alone is not treated as sufficient proof of backend
identity. The deployment and validation paths bind the expected official Core
source/release identity explicitly.

See `Core certification`_ and `Validation status`_ for the detailed evidence.

Fast Verified Bootstrap
=======================

Fresh installations use `ChainStrap <https://chainstrap.com>`_ Fast Verified
Bootstrap by default. ChainStrap accelerates acquisition of historical raw
block data; it is **not** a consensus trust source and it does not replace Core
validation. Only verified raw block files may enter staging; downloaded
chainstate and indexes are never installed. The pinned Core performs a complete
local ``-reindex -assumevalid=0`` before ElectrumX starts. Failures remain
fail-closed rather than silently switching to P2P synchronization.

See `Fast bootstrap`_ for the archive rules, resume behavior, storage needs,
P2P alternative, and complete threat model.

Installation options
====================

Running ``python3 electrumx-ravencoin-install.py`` starts the guided installer.
It supports explicit storage selection, traditional P2P bootstrap, optional
local monitoring, and an opt-in privileged controller. Persistent Core and
ElectrumX data should live on SSD or NVMe; a fresh install never silently adopts
an existing storage root.

The signed installer is the recommended production entry point. Source-checkout
deployments remain supported for development and deliberate operator workflows,
but ``setup.sh`` does not create signed-release updater state and the retired
tracked update key cannot become an active production root.

See `Getting started`_, `Storage selection`_, and `Troubleshooting`_ for the
commands, storage model, and source-checkout trust behavior.

Supported systems
=================

The maintained deployment targets 64-bit Linux on amd64 and ARM64. Raspberry
Pi 5 is the physically qualified low-power ARM64 path for v1.13.11; Orange Pi
5-class systems use the supported ARM64 build path but do not inherit that
complete physical qualification. Use SSD/NVMe, active cooling on SBCs, and
avoid placing Docker or databases on microSD. See `Hardware`_,
`Storage selection`_, and `Validation status`_.

Ravencoin Node Monitor
======================

The installer can deploy the optional
`Ravencoin Node Monitor <https://github.com/ALENOC/ravencoin-node-monitor>`_.
It is separate from the ElectrumX peer/network monitoring logic in this
repository.

The default dashboard is published only on ``127.0.0.1:8899``. The optional
bandwidth/connection controller is a separate privileged component and remains
disabled unless explicitly requested.

Update
======

Updates are explicit and operator-driven. Availability of a newer release does
not imply silent installation.

For installations made by the signed release installer, normal maintenance uses:

.. code-block:: sh

   electrumx-update check
   electrumx-update status
   electrumx-update show
   electrumx-update apply

The updater authenticates the candidate, proves the existing storage model,
switches the release transactionally, runs health gates, and then either
promotes the candidate or restores the previous release exactly. It refuses to
start an ambiguous stack if exact rollback cannot be proven.

Existing blockchain and ElectrumX storage are preserved. Compose overlays
selected through ``COMPOSE_FILE`` are preserved across promotion and rollback.
ChainStrap is a fresh-install bootstrap and is not re-run by an ordinary update.

Upgrading older 1.13.1 installations
------------------------------------

A 1.13.1 node cannot directly authenticate the manifest-v2/current-key release
line. The replacement public key must first be authenticated out of band. A
historical ``setup.sh`` 1.13.1 deployment then uses the explicit one-time
`Legacy adoption`_ procedure for the signed 1.13.11 candidate; only later
updates use the ordinary updater. Never treat a statement signed solely by the
retired 1.13.1 key as authorization for its replacement.

Security
========

ElectrumX-RVN deliberately separates trust domains instead of collapsing them
into one version check.

* **Core identity:** the deployment binds an exact official RavenProject
  repository/release/commit identity.
* **Release/update signatures:** ElectrumX releases use a dedicated Ed25519
  release trust domain separate from safe-Core policy signing.
* **Fail-closed bootstrap:** only allowlisted raw block files reach staging and
  Core performs mandatory local validation before ElectrumX consumes the chain.
* **Anti-rollback:** host security state prevents reinstalling into another
  directory from silently lowering the highest accepted release floor.
* **Container/network isolation:** Core RPC stays private by default and public,
  monitoring, and administrative paths are kept separate.
* **Database integrity:** startup checks reject unsafe historical extent
  corruption while bounded crash-tail recovery remains explicit.
* **Observation is not authority:** Network Observer evidence cannot authorize
  releases, rotate governance, or replace local Ravencoin validation.
* **Governance domains remain separate:** release and safe-Core policy
  authorization use distinct keys and domains; threshold activation is pending
  a future independent-maintainer ceremony.

See `Security model`_, `Core certification`_, and `Crash consistency`_ for the
full rationale and limits. Security reports should follow ``SECURITY.md``.

Startup and synchronization
===========================

A healthy container is not the same thing as a fully synchronized node. Normal
first-start progression is:

#. Core configuration and private RPC credentials are prepared.
#. Core starts, or ChainStrap stages verified raw blocks.
#. Core performs its mandatory validation/reindex and reaches a usable chain.
#. ElectrumX indexes the validated Core chain.
#. ElectrumX catches up to Core.
#. Backend/checkpoint/asset evidence becomes meaningful.
#. Only then should an operator publish a public TLS endpoint.

Useful checks:

.. code-block:: sh

   docker compose ps
   docker compose logs --tail=100 ravencoin-core
   docker compose logs --tail=100 electrumx
   docker compose exec electrumx electrumx_rpc getinfo
   docker compose exec ravencoin-core raven-cli \
     -conf=/var/lib/ravencoin-config/raven.conf \
     -datadir=/var/lib/ravencoin getblockchaininfo

During initial synchronization, ``blocks < headers`` is normal. During local
reindex, height fields can appear static while block-file processing continues
in the Core logs.

Private first, public later
===========================

A private ElectrumX server can serve wallets over a LAN or VPN without router
port forwarding. This is the recommended first milestone.

For a public service, configure a stable hostname, external TLS, and only the
ports intentionally required. Never expose Ravencoin Core JSON-RPC or
unauthenticated Core REST to the public Internet. The normal public Electrum TLS
service uses TCP 50002.

See `Public node`_ for DNS, CGNAT, firewall/forwarding, certificates, and
external testing.

August 2026 Ravencoin incident
==============================

The maintained deployment uses the official RavenProject Ravencoin Core 4.8.0
release line introduced following the August 2026 consensus incident and binds
the exact official identity rather than accepting a version label alone.

For incident boundaries, recovery behavior, and primary references, see the
`August 2026 incident guide`_.

Documentation
=============

Start with the guide that matches the task:

=============================  =================================================
Task                           Guide
=============================  =================================================
Install a first node           `Getting started`_
Review 1.13.11 changes         `1.13.11 overview`_
Understand the architecture    `Architecture guide`_
Understand ChainStrap          `Fast bootstrap`_
Choose hardware                `Hardware`_
Choose storage                 `Storage selection`_
Operate or update a node       `Operations`_
Publish a public server        `Public node`_
Understand trust boundaries    `Security model`_
Understand Network Observer    `Network Observer guide`_
Understand governance          `Governance guide`_
Verify Core identity           `Core certification`_
Diagnose a problem             `Troubleshooting`_
Check qualification status     `Validation status`_
Adopt an old 1.13.1 node       `Legacy adoption`_
Browse all documentation       `Documentation index`_
=============================  =================================================

Project and credits
===================

This repository is maintained by ALENOC as a community ElectrumX fork for
Ravencoin. The original ElectrumX/Ravencoin lineage and MIT notices are
preserved; see `Upstream and credits`_.

**Special thanks to Tron Black for ChainStrap and for making Fast Verified
Bootstrap available to the Ravencoin ecosystem.**

License
=======

See ``LICENCE``.

.. _Architecture: docs/architecture.md
.. _Network Observer: docs/network-observer.md
.. _1.13.11 overview: docs/release-1.13.11.md
.. _Architecture guide: docs/architecture.md
.. _Getting started: docs/getting-started.md
.. _Fast bootstrap: docs/fast-bootstrap.md
.. _Hardware: docs/hardware.md
.. _Storage selection: docs/storage-selection.md
.. _Operations: docs/operations.md
.. _Public node: docs/public-node.md
.. _Security model: docs/security-model.md
.. _Network Observer guide: docs/network-observer.md
.. _Governance guide: docs/GOVERNANCE_AND_SUCCESSION.md
.. _Core certification: docs/core-certification.md
.. _Crash consistency: docs/crash-consistency.md
.. _Validation status: docs/validation-status.md
.. _Legacy adoption: docs/LEGACY_1.13.1_ADOPTION.md
.. _Troubleshooting: docs/troubleshooting.md
.. _Documentation index: docs/README.md
.. _August 2026 incident guide: docs/incident-2026.md
.. _Upstream and credits: docs/upstream-and-credits.md
