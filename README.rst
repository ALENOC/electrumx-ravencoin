============================================
ElectrumX for Ravencoin 1.13.7
============================================

Community-maintained ElectrumX server for Ravencoin, with Ravencoin-specific
asset support, an exact official Ravencoin Core trust model, verified bootstrap
options, deployment tooling and additional safety checks introduced after the
August 2026 consensus incident.

**Current release: 1.13.7.** It supersedes 1.13.1, which is no longer the
recommended release. New installations should use the 1.13.7 installer, and
existing 1.13.1 nodes should move to 1.13.7. A 1.13.1 node installed with the
historical ``setup.sh`` path needs the one-time adoption step in
`Legacy adoption`_ before the normal updater can be used; a 1.13.1 node
installed with the release installer updates directly with
``electrumx-update apply``. Later releases keep the same Ravencoin Core 4.8.0
identity, so this is a software update and not a change of Core trust root.

ElectrumX-RVN 1.13.1 is the first release line in this repository that combines
all of the following in one maintained deployment path:

* official ``RavenProject/Ravencoin`` Core 4.8.0 pinning and certification;
* ChainStrap Fast Verified Bootstrap with a mandatory full Core reindex;
* the signed safe-Core policy architecture;
* the signed single-file installer/update architecture;
* deterministic Docker Compose project isolation;
* database crash-consistency hardening;
* the Ravencoin Node Monitor integration;
* Linux amd64 and ARM64 build/test coverage.

ChainStrap: fast verified bootstrap in 1.13.1
==============================================

`ChainStrap <https://chainstrap.com>`_ support is available in ElectrumX-RVN
starting with **version 1.13.1** and is the default bootstrap choice in the
guided fresh installer.

**Special thanks to Tron Black for making ChainStrap available to the Ravencoin
ecosystem and enabling this faster bootstrap path.**

ChainStrap is used only as a transport/bootstrap accelerator. It is **not** a
replacement for Ravencoin Core validation and it is not treated as a source of
consensus trust.

Starting with **1.13.7**, current upstream ChainStrap archives may contain
additional derived Ravencoin datadir material alongside the raw blockchain.
ElectrumX-RVN intentionally ignores that material and extracts **only**
allowlisted ``blocks/blk*.dat`` files. Files such as ``assets/*.ldb``,
``assets/CURRENT``, ``assets/LOCK``, chainstate or other derived indexes are
never imported. Ravencoin Core then performs the normal full local reindex and
validation of the raw blockchain data. The 1.13.1 flow is deliberately fail-closed:

1. the installer verifies the signed ElectrumX release metadata;
2. the ChainStrap manifest and downloaded parts are verified before staging;
3. downloaded block files are staged into the isolated Core data path;
4. Ravencoin Core performs a local ``-reindex -assumevalid=0`` validation pass;
5. marker state is digest-bound so normal Core startup cannot consume a staged
   but incompletely validated bootstrap;
6. ElectrumX is allowed to consume Core only after the Core readiness gate
   succeeds.

If ChainStrap fails, the installer does **not** silently fall back to P2P. The
operator must explicitly choose traditional synchronization instead:

.. code-block:: sh

   python3 electrumx-ravencoin-install.py --p2p-bootstrap --storage-root /path/to/data

For implementation and threat-model details see `Fast bootstrap`_ and
`Security model`_.

Why run an ElectrumX server?
============================

A Ravencoin full node validates the blockchain, but it is not optimized for the
address-history and asset queries required by lightweight wallets. ElectrumX
builds a query index from Core's validated chain and serves those requests
without holding wallet private keys or signing transactions.

The normal trust/data path is::

   wallet / Electrum client
            |
            v
   ElectrumX-RVN 1.13.7
            |
            v
   Ravencoin Core 4.8.0
   RavenProject/Ravencoin
   22549129888d02e0e08fcdb9f96f3c699167e774
            |
            v
   Ravencoin peer-to-peer network

More independently operated Electrum servers improve privacy, availability and
network resilience. Discovery of a server is not the same as trusting it; the
project keeps discovery, capability checks, backend evidence and operator trust
as separate concepts.

Official Ravencoin Core identity
================================

The bundled deployment is pinned to the official Ravencoin repository:

.. code-block:: text

   repository : RavenProject/Ravencoin
   tag        : v4.8.0
   version    : 4.8.0
   commit     : 22549129888d02e0e08fcdb9f96f3c699167e774

The Docker build also pins the expected source/release archive and binary
SHA-256 values. The running ElectrumX service receives this build identity from
verified deployment configuration rather than trusting a self-reported daemon
string.

Historical emergency/community builds may still be useful evidence when
investigating the August 2026 incident, but they are **not** the production
Core trust root. Current production trust is designed around the
exact official ``RavenProject/Ravencoin`` identity above.

Installation: one stable link
==============================

For a published production release, the recommended installation entry point
is one stable URL:

`Download the latest verified installer <https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py>`_

Download the installer first, then execute the local file:

.. code-block:: sh

   curl --fail --location --remote-name \
     https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py

   python3 electrumx-ravencoin-install.py

Do not use ``curl ... | python3`` or ``curl ... | bash``. Keeping the bootstrap
file on disk makes the initial executable inspectable before it is run.

To verify the host and available signed release metadata without making a
persistent installation:

.. code-block:: sh

   python3 electrumx-ravencoin-install.py --check-only

Fresh-install defaults
----------------------

The guided installer defaults to:

* bundled official Ravencoin Core 4.8.0;
* ChainStrap Fast Verified Bootstrap;
* Ravencoin Node Monitor enabled;
* the advanced root-owned bandwidth/connection controller **disabled** unless
  explicitly requested;
* explicit project storage selection;
* deterministic Compose project name ``electrumx-ravencoin``.

Useful choices include:

.. code-block:: sh

   # Traditional Ravencoin P2P sync instead of ChainStrap
   python3 electrumx-ravencoin-install.py \
     --p2p-bootstrap --storage-root /path/to/data

   # Do not deploy the optional Node Monitor
   python3 electrumx-ravencoin-install.py \
     --without-monitor --storage-root /path/to/data

   # Explicitly enable the privileged host controller
   python3 electrumx-ravencoin-install.py \
     --with-monitor-controller --storage-root /path/to/data

A failed fresh install is cleaned up rather than being silently adopted on the
next attempt. Existing installations are not silently reinstalled or
re-bootstrapped; maintenance is handled through the explicit updater commands.

Repository checkout installation
--------------------------------

Operators who prefer to work directly from source can still use:

.. code-block:: sh

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot
   docker compose up -d --build
   docker compose ps

The release installer is recommended for production because it is designed to
bind the downloaded bundle, release manifest and independent Core policy to
separate verification keys.

Installation FAQ
----------------

Where does the installer put things?
""""""""""""""""""""""""""""""""""""

Three independent locations, and only one of them is large:

* the **installation directory** holds the Compose files, ``.env`` and the
  install marker. It defaults to ``electrumx-ravencoin`` resolved against the
  *current working directory*, not against the home directory, so running the
  installer from ``~/electrumx-deploy`` creates
  ``~/electrumx-deploy/electrumx-ravencoin``. Override it with
  ``--install-dir``;
* the **project data directory** holds the Ravencoin chain data and the
  ElectrumX index. It is selected explicitly with ``--storage-root`` and is the
  only location that grows to hundreds of gigabytes;
* **Docker images** stay in the existing Docker data-root. The installer does
  not move them, so the filesystem holding ``/var/lib/docker`` still needs
  free space even when ``--storage-root`` points at another disk.

The installer prints all three on success.

``host anti-rollback preflight failed: root-owned security-state locator is missing``
"""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

The full message is:

.. code-block:: text

   error: host anti-rollback preflight failed: root-owned security-state locator is missing: /var/lib/electrumx-ravencoin/security-state.locator

This is expected on a first installation performed as an unprivileged user, and
it is not a defect. The host-wide anti-rollback floor lives outside the
installation directory so that reinstalling into another directory cannot reset
it. An unprivileged process is deliberately never allowed to create that
root-owned locator itself, because that would let a local user lower the floor
and re-offer a withdrawn release. An administrator provisions it once.

Note that ``--check-only`` does not touch persistent state, so a successful
preflight does not imply that the locator exists.

To keep the node owned by the current unprivileged user, provision the locator
once and then re-run the installer unchanged:

.. code-block:: sh

   sudo install -d -o root -g root -m 0755 /var/lib/electrumx-ravencoin

   printf '{\n  "schemaVersion": 1,\n  "ownerUid": %s,\n  "path": "%s"\n}\n' \
     "$(id -u)" \
     "${XDG_STATE_HOME:-$HOME/.local/state}/electrumx-ravencoin/security-state.json" \
     | sudo tee /var/lib/electrumx-ravencoin/security-state.locator >/dev/null

   sudo chmod 0644 /var/lib/electrumx-ravencoin/security-state.locator

The locator must end up as a regular non-symlink file, owned by root, mode
``0644``. The state file it names is created later by the installer itself,
owned by that same user and mode ``0600``.

Running the installer under ``sudo`` instead is also supported. Root
provisions the locator itself, but it binds the root namespace
``/var/lib/electrumx-ravencoin/security-state.json``, and the installation and
its data become root-owned, so every later ``docker compose`` and
``electrumx-update apply`` needs ``sudo`` as well.

The two options are mutually exclusive. The locator fixes exactly one owning
UID, so a locator provisioned for an unprivileged user makes a later root
invocation fail with:

.. code-block:: text

   security-state namespace belongs to uid 1000, not caller uid 0

Changing that decision afterwards means removing the root-owned locator and its
state file, which discards the recorded anti-rollback high-water. Choose the
owning identity before installing.

``mkdir: invalid option -- 'o'`` when advanced host controls are enabled
""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: text

   error: command failed with exit code 1: /usr/bin/sudo mkdir -p -o root -g root -m 0755 /usr/local/lib/electrumx-ravencoin

This affects the 1.13.5 installer only. It creates the root-owned controller
directory with ``mkdir`` and ownership flags that ``mkdir`` does not accept, so
a fresh 1.13.5 install aborts whenever the advanced bandwidth/connection
controller is requested. The failed install is cleaned up: no installation
directory, no project data and no recorded anti-rollback state are left behind,
and the run can simply be repeated.

1.13.6 replaces that call with ``install -d`` and creates the directory
correctly, so the advanced controller can be enabled during a normal
installation. Operators still on the 1.13.5 installer should move to the current
1.13.7 installer, or install without the advanced controller, which is the default and
is not required for normal monitoring:

.. code-block:: sh

   python3 electrumx-ravencoin-install.py --storage-root /path/to/data
   # answer N at step 4/4

``fresh install storage root already exists``
""""""""""""""""""""""""""""""""""""""""""""

.. code-block:: text

   error: fresh install storage root already exists: /mnt/data/electrumx-ravencoin-storage; preserve or remove it explicitly before retrying

A fresh installation never writes into an existing storage root, so a directory
left by an earlier attempt is refused instead of being reused or overwritten.
The installer suggests ``<mountpoint>/electrumx-ravencoin-storage`` on each
writable mounted filesystem it discovers, and that suggestion is what collides.

Decide explicitly what the old directory is. If it holds chain data worth
keeping, rename it and reuse the suggested path:

.. code-block:: sh

   mv /mnt/data/electrumx-ravencoin-storage /mnt/data/electrumx-storage-old

If it is disposable, remove it. Subdirectories are owned by the container UIDs,
so this needs ``sudo`` and it discards any synced chain data:

.. code-block:: sh

   sudo rm -rf /mnt/data/electrumx-ravencoin-storage

Alternatively choose ``C`` at step 1/4, or pass ``--storage-root DIR``, and
name a different directory. The path must be a dedicated child directory: not
``/``, not ``$HOME``, and not a filesystem mountpoint itself.

Supported deployment targets
============================

The maintained container path targets 64-bit Linux hosts.

Linux amd64 / x86-64
--------------------

The bundled Core image uses the exact pinned official Ravencoin 4.8.0 release
artifact and verifies the expected archive and binary SHA-256 values before the
runtime image is produced.

Linux ARM64 / aarch64
---------------------

ARM64, including Raspberry Pi 5 and Orange Pi 5-class systems, builds Ravencoin
Core from the **same official RavenProject commit**
``22549129888d02e0e08fcdb9f96f3c699167e774``. Native ARM64 GitHub Actions
builds run Core's ``make check`` plus the repository's startup/RPC/REST/txindex
smoke coverage.

ARM64 source-build binaries are architecture-specific and must not be compared
to the amd64 binary hashes. See `Validation status`_ for the exact evidence and
remaining architecture-specific qualification limits.

For SBC deployments use SSD/NVMe storage rather than microSD for Docker, Core
and the ElectrumX database. Detailed Raspberry Pi/Orange Pi, storage, cooling
and migration procedures live in `Hardware`_ and `Storage selection`_ rather
than being duplicated in this README.

Ravencoin Node Monitor
======================

The optional bundled dashboard comes from
`ravencoin-node-monitor <https://github.com/ALENOC/ravencoin-node-monitor>`_.
It is separate from the ElectrumX peer/network monitor used by this repository.

Default deployment properties include:

* dashboard published only on ``127.0.0.1:8899``;
* no Docker socket inside the monitor container;
* read-only container filesystem where practical;
* ``no-new-privileges`` and dropped Linux capabilities;
* a private internal admin path to ElectrumX;
* no host ``CAP_NET_ADMIN`` in the dashboard container.

The optional bandwidth/connection controller is a **separate privileged security
domain**. It is disabled by default. When explicitly enabled, the root-executed
controller is copied to a root-owned path and its bytes are SHA-256-bound to the
controller authenticated inside the verified release bundle before systemd is
allowed to execute it.

Security model
==============

ElectrumX-RVN separates several trust decisions that older deployment
models often collapse together.

Core release trust
------------------

A Core version string is not enough. The intended decision chain is::

   exact RavenProject repository + commit
       -> behavioral certification evidence
       -> trusted evaluator
       -> signed safe-Core policy
       -> exact deployment identity
       -> live backend and chain checks

The candidate Core code is isolated from the authoritative certification
verdict. The evaluator derives the verdict from bounded evidence, and the
protected signing stage consumes only canonical digest-bound results.

Release/update trust
--------------------

ElectrumX release/update signatures use a separate Ed25519 trust domain from
the safe-Core policy. The two private keys must never be reused. Missing,
malformed, stale, mismatched or rollback evidence fails closed.

Database integrity
------------------

Startup verifies the global transaction-hash-file extent against the committed
transaction count and rejects unsafe historical corruption rather than creating
sparse holes. Bounded crash-tail recovery remains separate from full historical
repair.

Container/network isolation
---------------------------

The default Compose model keeps Core JSON-RPC private, publishes ElectrumX TCP
on loopback by default, uses a dedicated backend network and keeps the Node
Monitor admin path on an internal network. Core P2P remains the intentionally
public Core-facing port when the operator wants a normal peer node.

See `Security model`_, `Core certification`_ and `Crash consistency`_ for the
full rationale and limits.

August 2026 incident
====================

The project carries additional safeguards because of the Ravencoin consensus
incident disclosed in August 2026. The maintained deployment now uses the
official RavenProject 4.8.0 release line and binds the exact official commit
rather than accepting a version label alone.

For incident boundaries, affected heights, recovery behavior and primary
references see the `August 2026 incident guide`_.

Storage and hardware
====================

Use SSD or NVMe storage for a long-lived node. Raw blocks, chainstate,
``txindex``, ``assetindex`` and the ElectrumX database all create sustained
random and sequential I/O.

Typical targets:

* Raspberry Pi 5, 8 GB or more, active cooling + SSD/NVMe;
* Orange Pi 5-class, 8 GB or more + NVMe;
* x86-64 mini-PC/NUC, 16 GB or more + NVMe;
* dedicated Linux server/VPS with adequate persistent storage and inbound TCP.

For SBCs, keeping the operating system on microSD is acceptable if Docker's
data-root and all node databases are moved to SSD/NVMe before the first heavy
build/sync. See `Hardware`_ and `Storage selection`_.

Startup and synchronization
===========================

A healthy container is not the same thing as a synchronized node.

Normal first-start progression is:

1. Core configuration and private RPC credentials are prepared;
2. Core starts, or ChainStrap stages verified blocks and Core performs its
   mandatory validation reindex;
3. Core reaches a usable/fresh peer-backed chain state;
4. ElectrumX starts indexing the validated Core chain;
5. ElectrumX catches up to Core;
6. backend/checkpoint/asset evidence becomes meaningful;
7. only then should an operator consider publishing a public TLS endpoint.

Useful checks:

.. code-block:: sh

   docker compose ps
   docker compose logs --tail=100 ravencoin-core
   docker compose logs --tail=100 electrumx
   docker compose exec electrumx electrumx_rpc getinfo
   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf \
       -datadir=/var/lib/ravencoin getblockchaininfo

During initial synchronization ``blocks < headers`` is normal. During a local
reindex, progress fields can temporarily look static while block-file processing
continues in the logs.

Private first, public later
===========================

A private ElectrumX server can serve wallets over a LAN or VPN without router
port forwarding. This is the recommended first milestone.

For a public service, configure a stable hostname, external TLS and only the
ports intentionally required by the service. Never expose Ravencoin Core
JSON-RPC or unauthenticated Core REST to the public Internet.

The normal public Electrum service is TLS on TCP 50002. Follow `Public node`_
for dynamic DNS, CGNAT, firewall/forwarding, certificates and external testing.

Updates
=======

The updater is deliberately operator-driven. Update availability does not imply
silent installation, and restarts do not implicitly replace the running source
or database.

Normal maintenance uses::

   electrumx-update check
   electrumx-update status
   electrumx-update show
   electrumx-update apply

A candidate must satisfy the release manifest, architecture, safe-Core policy,
compatibility and rollback checks before it can be applied.

An update is transactional. The updater proves that the existing storage stays
attached before it stops anything, switches the release directory, then either
promotes the new release or restores the previous one exactly. If it cannot
prove an exact restore it stops and asks for operator intervention instead of
starting an ambiguous stack.

Two properties matter for existing deployments:

* Storage is preserved as it is. Installations created by the release installer
  use bind-backed project storage. Installations adopted from an older
  ``setup.sh`` deployment keep their original Docker named volumes. The updater
  reads which model an installation uses from its own install marker and never
  converts, recreates or deletes storage.
* Compose overlays selected through ``COMPOSE_FILE`` in ``.env`` are preserved
  across promotion and rollback, so a TLS deployment stays a TLS deployment.
  ChainStrap is a one-shot bootstrap and is never re-run by an update.

Adoption of an older ``setup.sh`` deployment is a separate one-time step, and
the operator is prompted for it explicitly. After adoption has completed, every
later update is an ordinary ``electrumx-update apply``.

Release readiness
=================

A Git merge and a production release are different security events.

The 1.13.1 source integration has passed the pre-merge adversarial audit,
remediation, focused micro-round and CI gates. Publishing a production release
still requires the repository's protected release gates to be satisfied on the
exact final release commit/tree and artifacts, including:

* promotion of the RavenProject-only signed safe-Core policy;
* provisioning of the dedicated ElectrumX release/update public-key trust root
  through its recorded key ceremony;
* clean interactive fresh-install qualification;
* final release/audit gate on the exact release candidate.

The implementation intentionally fails closed if those production trust roots
are absent. A development/test key must never be substituted merely to make a
release succeed.

Documentation
=============

Start with the guide that matches the task:

* `Getting started`_: concepts and deployment paths;
* `Fast bootstrap`_: ChainStrap staging and mandatory Core validation;
* `Hardware`_: SBC/x86 sizing, storage and cooling;
* `Storage selection`_: placement and disk-safety guidance;
* `Operations`_: lifecycle, progress, backups and updates;
* `Public node`_: DNS, CGNAT, TLS and exposure;
* `Security model`_: trust boundaries and fail-closed behavior;
* `Core certification`_: exact Core identity and policy evidence;
* `Crash consistency`_: database recovery model;
* `Electrum monitor`_: peer discovery and backend/operator evidence;
* `August 2026 incident guide`_: incident and recovery background;
* `Validation status`_: current test, architecture and release evidence;
* `Legacy adoption`_: one-time adoption of a ``setup.sh`` 1.13.1 node;
* `Troubleshooting`_: operational diagnosis;
* `Documentation index`_: full documentation map.

Credits
=======

This repository is maintained by ALENOC as a community fork. ElectrumX,
Ravencoin support and the surrounding ecosystem have many earlier authors and
maintainers; the original MIT notices remain in ``LICENCE`` and additional
lineage is recorded in `Upstream and credits`_.

**Special thanks to Tron Black for ChainStrap and for making the fast bootstrap
feature available to the Ravencoin ecosystem. ElectrumX-RVN integrates this
feature starting with version 1.13.1.**

Security reports should use the maintained repository's security advisory
process documented in ``SECURITY.md``.

.. _Documentation index: docs/README.md
.. _Getting started: docs/getting-started.md
.. _Fast bootstrap: docs/fast-bootstrap.md
.. _Hardware: docs/hardware.md
.. _Storage selection: docs/storage-selection.md
.. _Operations: docs/operations.md
.. _Public node: docs/public-node.md
.. _Security model: docs/security-model.md
.. _Core certification: docs/core-certification.md
.. _Crash consistency: docs/crash-consistency.md
.. _Electrum monitor: docs/electrum-monitor.md
.. _August 2026 incident guide: docs/incident-2026.md
.. _Validation status: docs/validation-status.md
.. _Legacy adoption: docs/LEGACY_1.13.1_ADOPTION.md
.. _Troubleshooting: docs/troubleshooting.md
.. _Upstream and credits: docs/upstream-and-credits.md
