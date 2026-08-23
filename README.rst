================================
ElectrumX for Ravencoin
================================

Production-oriented ElectrumX infrastructure for Ravencoin with verified
Ravencoin Core 4.8.0, Fast Verified Bootstrap, transactional updates, optional
node monitoring, and maintained Linux amd64 / ARM64 deployment paths.

**Current release: ElectrumX-RVN 1.13.8**

`Install`_ · `Update`_ · `How it works`_ · `Security`_ · `Documentation`_ ·
`Latest release <https://github.com/ALENOC/electrumx-ravencoin/releases/latest>`_

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

How it works
============

The normal data and trust path is::

   Wallet / Electrum client
             |
             v
   ElectrumX-RVN 1.13.8
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
validation.

The trust path is::

   ChainStrap verified transport
             |
             v
      raw blocks/blk*.dat
             |
             v
      Ravencoin Core 4.8.0
             |
             | full local reindex
             | -reindex -assumevalid=0
             v
      locally validated chain

Current upstream ChainStrap archives may contain additional derived Ravencoin
datadir/index material, both beside the raw blocks (``assets/*.ldb``) and inside
the blocks namespace (``blocks/index/*.ldb``). Since **1.13.8**, ElectrumX-RVN
ignores such safe derived members wherever they appear and allows only
``blocks/blk*.dat`` files to enter the raw-block staging path. Ignored members
are never extracted and never reach the Ravencoin datadir. Current snapshots
also split derived material into whole parts that carry no ``blk*.dat`` at all;
such a part is accepted and extracts nothing. Unsafe paths, unsafe entry types
and malformed archives remain fail-closed, and a snapshot that yields no raw
block file, or a gapped raw-block sequence, is still refused before the
blocks-ready marker is written.

If ChainStrap fails, the installer does not silently switch to another bootstrap
method. Traditional Ravencoin P2P synchronization can be selected explicitly:

.. code-block:: sh

   python3 electrumx-ravencoin-install.py \
     --p2p-bootstrap --storage-root /path/to/data

For the complete staging and threat model, see `Fast bootstrap`_.

Installation options
====================

Running ``python3 electrumx-ravencoin-install.py`` starts the interactive
installer. Common non-interactive choices include:

.. code-block:: sh

   # Traditional P2P synchronization instead of ChainStrap
   python3 electrumx-ravencoin-install.py \
     --p2p-bootstrap --storage-root /path/to/data

   # Do not deploy the optional Node Monitor
   python3 electrumx-ravencoin-install.py \
     --without-monitor --storage-root /path/to/data

   # Explicitly enable the privileged host controller
   python3 electrumx-ravencoin-install.py \
     --with-monitor-controller --storage-root /path/to/data

Storage
-------

Persistent Ravencoin and ElectrumX data should normally live on SSD or NVMe.
The installer keeps three locations distinct:

* the installation directory containing Compose files, ``.env``, and markers;
* the explicitly selected project data directory containing Core and ElectrumX
  persistent data;
* Docker's own image/data root.

A fresh install never silently adopts an existing storage root. Detailed path,
ownership, collision, and anti-rollback locator guidance lives in
`Troubleshooting`_ and `Storage selection`_.

Source checkout installation
----------------------------

Operators who intentionally work from a repository checkout can still use:

.. code-block:: sh

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot
   docker compose up -d --build
   docker compose ps

The signed release installer is the recommended production entry point because
it binds the downloaded bundle, release manifest, and independent Core policy
to their verification paths.

Supported systems
=================

The maintained container deployment targets 64-bit Linux hosts.

Linux amd64 / x86-64
--------------------

The bundled Core image uses the pinned official Ravencoin 4.8.0 release
identity and verifies the expected release artifacts before runtime use.

Linux ARM64 / aarch64
---------------------

ARM64, including Raspberry Pi 5 and Orange Pi 5-class systems, builds Ravencoin
Core from the same official RavenProject commit shown above. Architecture-
specific qualification details are recorded in `Validation status`_.

Recommended hardware
--------------------

* Raspberry Pi 5: 8 GB or more, active cooling, SSD/NVMe;
* Orange Pi 5-class: 8 GB or more, NVMe;
* x86-64 mini-PC/NUC: 16 GB or more, NVMe;
* dedicated Linux server/VPS: adequate persistent SSD/NVMe and inbound TCP.

For long-lived nodes, avoid placing Docker, Core, or the ElectrumX database on
microSD. See `Hardware`_ and `Storage selection`_.

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

Normal maintenance uses:

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

A 1.13.1 node installed with the signed release installer can use the normal
``electrumx-update apply`` path. A historical ``setup.sh`` 1.13.1 deployment
must first perform the explicit one-time `Legacy adoption`_ procedure; later
updates then use the ordinary updater.

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
Understand ChainStrap          `Fast bootstrap`_
Choose hardware                `Hardware`_
Choose storage                 `Storage selection`_
Operate or update a node       `Operations`_
Publish a public server        `Public node`_
Understand trust boundaries    `Security model`_
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

.. _Install: `Quick install`_
.. _How it works: `How it works`_
.. _Security: `Security`_
.. _Documentation: `Documentation`_
.. _Getting started: docs/getting-started.md
.. _Fast bootstrap: docs/fast-bootstrap.md
.. _Hardware: docs/hardware.md
.. _Storage selection: docs/storage-selection.md
.. _Operations: docs/operations.md
.. _Public node: docs/public-node.md
.. _Security model: docs/security-model.md
.. _Core certification: docs/core-certification.md
.. _Crash consistency: docs/crash-consistency.md
.. _Validation status: docs/validation-status.md
.. _Legacy adoption: docs/LEGACY_1.13.1_ADOPTION.md
.. _Troubleshooting: docs/troubleshooting.md
.. _Documentation index: docs/README.md
.. _August 2026 incident guide: docs/incident-2026.md
.. _Upstream and credits: docs/upstream-and-credits.md
