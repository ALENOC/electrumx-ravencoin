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

The bundled Core artifact is qualified for **Linux x86-64/amd64**, backed by a
persisted certification report. **Linux ARM64/aarch64** (including Raspberry
Pi 5 and Orange Pi 5-class boards) builds from the same pinned source commit
on native ARM64 GitHub Actions runners, with the build-time consensus test
suite (``make check``) and a basic startup/RPC/REST/txindex/restart smoke
suite passing; it has not been run through the incident-specific consensus
probes that back the amd64 status, and no report is persisted in this
repository. See `Validation status`_ for the current per-architecture
evidence. Docker selects the matching architecture automatically; the
commands below are the same for both.

Deploying on a Raspberry Pi 5? Read `Raspberry Pi 5 / ARM64: running from an
SSD or NVMe`_ before the first ``docker compose up -d --build``. It explains
how to move Docker's data-root, the blockchain and the ElectrumX database
onto an SSD or NVMe so the microSD only ever holds the operating system.

For the bundled path, use a 64-bit Linux host with Docker Engine, Compose v2,
Git and OpenSSL. If Docker is not installed yet, see `Installing Docker when
it is missing`_ below before running these commands:

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

Single-file installer (alternative to git clone)
--------------------------------------------------

Instead of cloning the repository, a host can fetch and run one signed,
self-contained Python file:

.. code-block:: sh

   curl -fL -O https://github.com/ALENOC/electrumx-ravencoin/releases/latest/download/electrumx-ravencoin-install.py
   python3 electrumx-ravencoin-install.py

Never pipe the download straight into an interpreter (``curl ... | python3``
or ``curl ... | bash``). Fetch the file, read it if you want to, then run it.

The installer verifies a signed release manifest against a pinned ElectrumX
release public key before installing anything; this key is a separate trust
root from the Core certification signing key used elsewhere in this
repository, and the installer never has access to either private key. Run
``python3 electrumx-ravencoin-install.py --check-only`` to detect the host and
validate release trust metadata without making any persistent change.

On a fresh, empty bundled-Core install the installer defaults to the ChainStrap
Fast Verified Bootstrap described in the `August 2026 incident guide`_; press
Enter to accept it, or pass ``--p2p-bootstrap`` for traditional Ravencoin P2P
synchronization instead.
ChainStrap is transport only: Ravencoin Core independently validates every
downloaded block before ElectrumX ever reads it. The installer never
re-triggers ChainStrap against an existing Core or ChainStrap datadir, and a
normal ElectrumX upgrade never re-triggers it either.

The installer also offers the optional `Electrum monitor`_ dashboard, enabled
by default on a fresh interactive install (``--without-monitor`` to skip,
``--with-monitor`` to force it non-interactively). The monitor dashboard binds
to ``127.0.0.1:8899`` by default and runs unprivileged, with no Docker socket
access. Its optional privileged host controller (bandwidth and connection
management) is disabled by default and only turns on with the explicit
``--with-monitor-controller`` flag.

If the installer detects an existing installation, it does not reinstall or
re-bootstrap anything; it hands off to ``electrumx-update check`` /
``status`` / ``show`` / ``apply``. Applying an update always requires an
explicit operator command, and a release marked as a consensus-affecting
change is never installed by an ordinary ``electrumx-update apply``.

Installing Docker when it is missing
------------------------------------

On a Debian 13 (trixie) ARM64 host, install Docker Engine and Compose v2 from
Docker's official repository:

.. code-block:: sh

   sudo apt update
   sudo apt install -y ca-certificates curl

   sudo install -m 0755 -d /etc/apt/keyrings
   sudo curl -fsSL https://download.docker.com/linux/debian/gpg \
       -o /etc/apt/keyrings/docker.asc
   sudo chmod a+r /etc/apt/keyrings/docker.asc

   sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<EOF
   Types: deb
   URIs: https://download.docker.com/linux/debian
   Suites: trixie
   Components: stable
   Architectures: arm64
   Signed-By: /etc/apt/keyrings/docker.asc
   EOF

   sudo apt update

   sudo apt install -y \
       docker-ce \
       docker-ce-cli \
       containerd.io \
       docker-buildx-plugin \
       docker-compose-plugin

On an amd64 host, set ``Architectures: amd64`` in the ``docker.sources`` entry.
On another distribution or release, use the matching commands from Docker's
official installation documentation.

Then let your own user manage Docker without ``sudo``:

.. code-block:: sh

   sudo usermod -aG docker "$USER"
   newgrp docker

Membership in the ``docker`` group is root-equivalent on the host, so only add
accounts you fully trust. Log out and back in, or run ``newgrp docker``, for
the change to take effect.

Verify before continuing with the quick start:

.. code-block:: sh

   docker version
   docker compose version
   docker run --rm hello-world

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

Initial synchronization or indexing can take many hours or longer,
depending on storage, network conditions, peer availability and host
performance. A healthy container only means that a process answers its
health check; it does not mean the chain is current.
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
  use active cooling, a reliable supply and a 64-bit OS. Core builds for ARM64
  from the same pinned source commit as the certified amd64 artifact on
  native ARM64 CI hardware, with ``make check`` and a basic RPC/REST/txindex
  smoke suite passing, but without the incident-specific probes the amd64
  status rests on; a physical Raspberry Pi 5 deployment has validated the
  documented SSD build and startup path on real hardware; see `Validation
  status`_ for current per-architecture evidence.
* **Orange Pi 5-class, 8 GB or more, plus NVMe:** useful lower-cost target;
  verify the exact 5/5B/5 Plus/5 Pro board before buying a carrier or drive.
  These ARM64 boards use the same bundled Core deployment path as the Pi 5,
  with the same partially-validated-ARM64 caveat above.
* **x86-64 mini-PC or NUC, 16 GB or more, plus NVMe:** the fastest and most
  straightforward bundled certified-Core deployment path.
* **Dedicated server/VPS:** suitable for a long-lived public node when storage,
  memory and raw TCP networking are appropriate.

NVMe/SSD is strongly preferred because raw blocks, chainstate, ``txindex``,
``assetindex`` and the ElectrumX database are all written during the initial
build. Do not place chain or index data on microSD. The `Raspberry Pi 5 /
ARM64: running from an SSD or NVMe`_ section below walks through that layout
step by step. See `Hardware`_ for RAM, cooling, TLC/QLC, free-space and
board-specific guidance. Recommended hardware is not the same as completed
runtime validation.

Raspberry Pi 5 / ARM64: running from an SSD or NVMe
===================================================

This section documents a complete Raspberry Pi 5 deployment that keeps the
microSD card for the operating system only, and puts Docker, the Ravencoin
blockchain and the ElectrumX database on an external SSD or NVMe drive. Two
attachment methods are supported:

* a USB 3.x SSD, or an NVMe drive in a USB enclosure; or
* an NVMe drive connected through a Raspberry Pi PCIe/M.2 HAT.

The workflow was validated on real hardware on 2026-08-16: a Raspberry
Pi 5 (8 GB, ``aarch64``) running Raspberry Pi OS Lite 64-bit (Debian 13
trixie) from the microSD with zram enabled, and an external 2 TB NVMe/SSD
in a USB enclosure holding Docker, the Ravencoin blockchain and the
ElectrumX database. See `Real Raspberry Pi 5 validation`_ for the recorded
evidence.

Do the storage steps below before the first ``docker compose up -d --build``.
On ARM64 that command compiles Ravencoin Core from source, and the build
cache alone needs several GB. With Docker's default storage location, the
build cache, the blockchain and the ElectrumX database would all be written
to the microSD.

Moving Docker's data-root is the key step. The Compose stack keeps the
blockchain, Core's configuration and the ElectrumX database in Docker named
volumes (``ravencoin-data``, ``ravencoin-config``, ``electrumx-data`` and
``rpc-secrets``). Named volumes live under Docker's data-root, which is
``/var/lib/docker`` on a standard install, and ``/var/lib/docker`` is on the
microSD. Moving the data-root to the SSD moves every heavy write in one
step, and no Compose volume configuration needs to change.

Recommended hardware
--------------------

* Raspberry Pi 5 with 8 GB or more; 16 GB is more comfortable.
* Active cooling. The ARM64 Core build is a burst of sustained CPU load,
  and the initial synchronization and ElectrumX indexing that follow keep
  the host busy for much longer than the build itself.
* A reliable power supply; see `Power`_ below.
* A USB 3.x SSD/NVMe enclosure, or an M.2 HAT, with a drive large enough
  for the chain, the Core indexes and the ElectrumX database plus headroom.
  See `Hardware`_ for RAM, TLC/QLC endurance and free-space guidance.
* A 64-bit operating system; the tested image was Raspberry Pi OS Lite
  64-bit (Debian 13 trixie).

Target storage layout
---------------------

.. code-block:: text

   microSD
     Raspberry Pi OS (boot and system only)

   SSD/NVMe, mounted at /srv/ravencoin
     docker/                  Docker data-root:
       image layers and build cache
       volumes/               Compose named volumes:
         ...ravencoin-data       Ravencoin blockchain
         ...ravencoin-config     Core raven.conf
         ...electrumx-data       ElectrumX database
         ...rpc-secrets          prepared RPC credential copies

The goal of the next three steps is that, before anything is built,
``docker info`` reports ``DockerRootDir=/srv/ravencoin/docker``.

Step 1: identify the disk
-------------------------

Identify which disk is the SSD before changing anything:

.. code-block:: sh

   lsblk -o NAME,SIZE,FSTYPE,LABEL,UUID,MODEL,TRAN,MOUNTPOINTS

Confirm the model, size and transport (``usb`` for an enclosure, ``nvme``
for a HAT) so you act on the intended drive and not on the microSD or
another disk.

Formatting is only needed on a brand-new drive. Skip it if the drive
already contains a filesystem you want to keep.

.. warning::

   **Optional and destructive.** ``mkfs`` erases everything on the chosen
   partition. Run it only on a new or empty drive whose partition name you
   just verified with ``lsblk``, substituting your own device:

   .. code-block:: sh

      sudo mkfs.ext4 /dev/sdX1

Step 2: mount the SSD persistently by UUID
------------------------------------------

Create the mount point and mount the filesystem:

.. code-block:: sh

   sudo mkdir -p /srv/ravencoin
   sudo mount UUID=<SSD-UUID> /srv/ravencoin

Verify before saving the mount:

.. code-block:: sh

   findmnt /srv/ravencoin
   df -hT /srv/ravencoin

Then add the filesystem to ``/etc/fstab`` by UUID:

.. code-block:: text

   UUID=<SSD-UUID> /srv/ravencoin ext4 defaults,noatime 0 2

Mounting by UUID makes the attachment method transparent. The same drive in
a USB enclosure may appear as ``/dev/sda1`` and later, after moving it to an
M.2 HAT, as ``/dev/nvme0n1p1``. The filesystem UUID does not change, so the
mount point ``/srv/ravencoin`` and everything under it stay stable, and no
Docker or Compose configuration needs to change.

Do not add ``nofail`` for the primary Ravencoin storage. If the SSD were
missing at boot and boot continued silently, ``/srv/ravencoin`` would be an
ordinary directory on the microSD, and Docker could write the blockchain
and the database onto the microSD card. Without ``nofail``, a missing SSD
produces a loud boot failure instead of silent data placement on the wrong
disk.

Step 3: move the Docker data-root to the SSD
--------------------------------------------

1. Check where Docker currently keeps its data:

   .. code-block:: sh

      docker info --format 'DockerRootDir={{.DockerRootDir}}'

2. Create the target directory:

   .. code-block:: sh

      sudo mkdir -p /srv/ravencoin/docker

3. Stop Docker and containerd:

   .. code-block:: sh

      sudo systemctl stop docker.socket
      sudo systemctl stop docker.service
      sudo systemctl stop containerd.service

   ``docker.socket`` is stopped first so that a stray ``docker`` command
   cannot restart the daemon through socket activation mid-migration.

4. If Docker has already been used on this host, copy its existing data to
   the SSD (install ``rsync`` first if it is missing). Confirm first that
   the SSD is still mounted: if it were not, the copy would write into a
   plain directory on the microSD instead:

   .. code-block:: sh

      findmnt /srv/ravencoin
      sudo rsync -aHAX --numeric-ids /var/lib/docker/ /srv/ravencoin/docker/

5. Configure the new data-root in ``/etc/docker/daemon.json``:

   .. code-block:: json

      {
        "data-root": "/srv/ravencoin/docker",
        "log-driver": "local",
        "log-opts": {
          "max-size": "10m",
          "max-file": "2"
        }
      }

   If ``/etc/docker/daemon.json`` already exists, merge these keys into the
   existing JSON instead of overwriting the whole file.

6. Prevent Docker from starting before the SSD is mounted:

   .. code-block:: sh

      sudo mkdir -p /etc/systemd/system/docker.service.d
      printf '[Unit]\nRequiresMountsFor=/srv/ravencoin\n' \
          | sudo tee /etc/systemd/system/docker.service.d/storage.conf >/dev/null
      sudo systemctl daemon-reload

   The drop-in makes systemd refuse to start Docker until ``/srv/ravencoin``
   is mounted.

7. Start Docker again:

   .. code-block:: sh

      sudo systemctl start containerd
      sudo systemctl start docker

8. Verify:

   .. code-block:: sh

      docker info --format 'DockerRootDir={{.DockerRootDir}}'

   Expected output::

      DockerRootDir=/srv/ravencoin/docker

Compose named volumes now live under the SSD-backed data-root, so the
blockchain, Core's configuration and the ElectrumX database will be written
to the SSD without any Compose change.

Step 4: install and start the stack
-----------------------------------

Install Docker Engine and Compose v2 first if they are missing; the
instructions in `Installing Docker when it is missing`_ above match
Raspberry Pi OS trixie. Then run the normal repository installation:

.. code-block:: sh

   git clone https://github.com/ALENOC/electrumx-ravencoin.git
   cd electrumx-ravencoin
   ./setup.sh --enable-reboot

If setup reports that user lingering is disabled, the reboot-recovery user
service cannot start at boot until it is enabled:

.. code-block:: sh

   sudo loginctl enable-linger "$USER"
   loginctl show-user "$USER" -p Linger

Expected output::

   Linger=yes

Then build and start:

.. code-block:: sh

   docker compose up -d --build
   docker compose ps

The Compose file builds the Core image locally through the
``ravencoin-core`` service and tags it ``alenoc/ravencoin-core:4.8.0``. No
registry image has to be pulled.

What the ARM64 build does
-------------------------

On an ARM64 host, Docker detects the target architecture and
``docker/core/Dockerfile`` takes its source-build path. During
``docker compose up -d --build``, the ``ravencoin-core`` image build:

* starts from the pinned ``debian:bullseye-slim`` base image, by digest;
* installs the build toolchain, including ``build-essential``, autotools,
  Boost, Berkeley DB (``libdb++-dev``), libevent and OpenSSL development
  libraries;
* downloads the Ravencoin source archive from the pinned ``2miners/Ravencoin``
  commit ``b60f50e04f1fba425b28804e61be2694faaf3469`` and verifies its
  SHA-256 against the pinned value in ``compose.yaml``;
* runs ``./autogen.sh`` and ``./configure --disable-wallet --without-gui
  --without-miniupnpc --disable-bench``, matching this deployment's
  wallet-disabled model;
* compiles with ``make -j"$(nproc)"`` and runs the Core test suite with
  ``make check`` as part of the image build;
* installs only ``ravend`` and ``raven-cli`` into the final runtime image.

On amd64 the same Dockerfile instead downloads the pinned 2miners Ravencoin
4.8.0 release archive, verifies the archive and binary SHA-256 hashes, and
skips compilation entirely. The ARM64 source build is therefore substantially
slower than the amd64 prebuilt-binary path. Build time depends on CPU
cooling, storage, Docker cache state and host load: a real Raspberry Pi 5
(8 GB) deployment completed the Core ARM64 compile-and-test stage in about
951 seconds, with the full ``docker compose up -d --build`` finishing 35/35
steps in 967.9 seconds, roughly 16 minutes. That deployment reused some
cached Docker dependency layers, so treat it as one observed measurement,
not a guaranteed clean-build time. Sustained CPU load on all cores is
normal during the build; run it with adequate cooling, since thermal
throttling slows it further.

The image build is not the long part of a first deployment. Initial
blockchain synchronization and ElectrumX indexing can take many hours or
longer, depending on storage, network conditions, peer availability and
host performance (see `Nothing is ready immediately after Docker starts,
and that is normal`_).

The ARM64 image is a local build from the pinned source commit, not the
certified amd64 release binary, and its binary hashes necessarily differ
from the amd64 release hashes. See `Validation status`_ for what is and is
not proven for each architecture.

To rebuild the Core image from scratch, discarding cached build layers:

.. code-block:: sh

   docker compose build --no-cache ravencoin-core
   docker compose up -d

Monitoring the build and the host
---------------------------------

``vcgencmd`` is part of Raspberry Pi OS. During the build, in a second
terminal:

.. code-block:: sh

   watch -n 5 'vcgencmd measure_temp; vcgencmd get_throttled'

``throttled=0x0`` is the healthy state. Any nonzero value means the
firmware has flagged undervoltage, frequency capping, throttling or a
temperature limit, currently or earlier since boot, and deserves
investigation before you continue the build or the much longer initial
synchronization that follows it.

Watch storage during and after the build:

.. code-block:: sh

   docker system df
   df -h /srv/ravencoin

USB enclosure troubleshooting
-----------------------------

Prefer a USB 3.x enclosure and cable, and check the negotiated speed:

.. code-block:: sh

   lsusb -t

A healthy USB 3.x link reports ``5000M`` or faster. ``480M`` means the
drive negotiated USB 2.0. That technically works, but it is much slower
and should be treated as a temporary or fallback configuration for the
initial sync and ElectrumX indexing. The preference order for a permanent
node is: direct NVMe through an M.2 HAT first, a stable USB 3.x SSD/NVMe
second, and USB 2.0-class throughput only as a fallback while you arrange
one of the better options.

UAS (USB Attached SCSI) is normally the faster protocol and preferable when
it is stable. However, some USB-to-NVMe bridge chips, including certain
JMicron controllers, can exhibit UAS instability on some Raspberry Pi
setups. Observed symptoms on the real test deployment included device
offline events, Read Capacity failures, Buffer I/O and JBD2 errors, and
USB resets or disconnects under sustained load. If, and only if, you
observe such instability, you can disable UAS for that specific bridge:

1. Find your bridge's actual USB vendor and product ID with ``lsusb``. For
   example, a JMicron-based bridge may report ``152d:a580``.
2. Add a quirk to the kernel command line using your own VID:PID, appended
   to the single existing line in ``/boot/firmware/cmdline.txt``
   (all kernel parameters must stay on that one line, separated by spaces):

   .. code-block:: text

      usb-storage.quirks=152d:a580:u

3. Reboot, then verify the quirk and the driver in use:

   .. code-block:: sh

      cat /proc/cmdline | grep -o 'usb-storage.quirks=[^ ]*'
      lsusb -t

The affected drive should now show ``Driver=usb-storage`` instead of
``Driver=uas``.

On the tested ``152d:a580`` bridge, this workaround eliminated the storage
errors: no Buffer I/O errors, critical target errors, USB resets, device
offline events or Read Capacity errors were observed afterwards during the
test period. The measured fallback performance was approximately 30.8
MB/s direct read, which is workable but well below a stable USB 3.x link
or an M.2 HAT. This observed result is specific to that bridge and this
workaround; it is not a recommendation to run the node on USB-storage
fallback permanently.

This is a compatibility workaround, not a performance recommendation. It
disables UAS only for the specific VID:PID you list, so determine your own
bridge's VID:PID before copying anything, and do not apply a quirk
globally. Do not disable UAS preemptively; most enclosures work correctly
with it.

Power
-----

Bus-powered SSD and NVMe enclosures can draw significant USB current. For a
storage-heavy Raspberry Pi 5 deployment, a 5V/5A USB-C power supply is
preferred. Inadequate power can cause undervoltage resets, I/O errors,
disappearing disks or filesystem corruption. Monitor with:

.. code-block:: sh

   vcgencmd get_throttled
   vcgencmd get_config usb_max_current_enable

``get_config usb_max_current_enable`` reports whether the firmware is
allowing the elevated total USB current budget. Do not blindly force that
setting to work around a 5V/3A supply: if the input power is insufficient,
the extra USB budget makes undervoltage more likely, not less. Use an
adequate supply, or an enclosure with its own power supply.

Migrating the SSD from USB to an M.2 HAT
----------------------------------------

Because ``/etc/fstab`` mounts the filesystem by UUID and Docker only knows
``/srv/ravencoin``, moving the same physical drive from a USB enclosure to
a PCIe/M.2 HAT requires no Docker or Compose change:

.. code-block:: text

   USB enclosure   /dev/sda1       UUID=<SSD-UUID> -> /srv/ravencoin
   M.2 HAT later   /dev/nvme0n1p1  UUID=<SSD-UUID> -> /srv/ravencoin

Before physically moving the drive, stop everything cleanly so no process
is writing to the SSD:

.. code-block:: sh

   cd ~/electrumx-ravencoin
   docker compose down
   sudo systemctl stop docker docker.socket containerd
   sudo umount /srv/ravencoin
   sudo poweroff

Stopping Docker and containerd before ``umount`` matters: the daemon holds
the data-root open, and containers could otherwise be restarted mid-unmount
by their restart policy. If ``umount`` reports that the target is busy, a
process is still using the mount; confirm Docker and containerd are fully
stopped before retrying.

After moving the drive to the HAT and booting, verify that the same
filesystem is mounted and Docker still uses the SSD:

.. code-block:: sh

   findmnt /srv/ravencoin
   docker info --format 'DockerRootDir={{.DockerRootDir}}'
   cd ~/electrumx-ravencoin
   docker compose up -d

No Compose paths, volume names or image tags change: the drive keeps the
same filesystem UUID and the same ``/srv/ravencoin`` mount, so Docker and
Compose see exactly the same storage layout. Enabling the PCIe slot and
any firmware configuration for the HAT itself should follow the specific
HAT vendor's current instructions; that is board setup and deliberately
not part of this storage migration.

Optional: pre-seed the blockchain from an existing Core
-------------------------------------------------------

An existing non-pruned Ravencoin Core datadir with ``txindex=1`` can be
copied to the new SSD to avoid downloading and validating the whole
blockchain over the network again. Copy only Core chain data, and let
ElectrumX build its own database on the Pi: ElectrumX database portability
across machines, versions and CPU architectures is not assumed, so
pre-seeding Core and letting ElectrumX index locally is the safe path.

Copying a datadir that is actively changing does not produce a consistent
final state. Use a two-pass copy to keep the source node's downtime short:

1. Bulk-copy while the source Core keeps running.
2. Gracefully stop the source Core so its block and index files are closed
   and consistent.
3. Run a final incremental pass to pick up the remainder.

The destination is the ``ravencoin-data`` named volume. Compose prefixes
volume names with the project name declared in ``compose.yaml``
(``electrumx-ravencoin``), so the full volume name is
``electrumx-ravencoin_ravencoin-data``; confirm it with ``docker volume
ls``. The commands below use a short-lived helper container with that
volume mounted at ``/data``, so they never touch Docker's internal storage
paths on the host. If the stack has never been started, run ``docker
compose up -d`` once so the volumes are created, then ``docker compose
down`` before copying.

.. warning::

   The next command deletes everything already inside the named volume.
   Use it only on a fresh deployment whose volume contains nothing but the
   beginning of a new sync you have not invested time in.

.. code-block:: sh

   docker run --rm \
       -v electrumx-ravencoin_ravencoin-data:/data \
       debian:bookworm-slim \
       bash -c 'find /data -mindepth 1 -delete'

Put a readable copy of the source datadir where the Pi can access it by
absolute path. If the source Core runs on another machine, first copy
``blocks/``, ``chainstate/`` and ``indexes/`` to a staging directory on the
SSD, for example ``/srv/ravencoin/staging``, and use that path as the
source below; the same two-pass idea applies to that transfer as well.
Then run the bulk copy, substituting the absolute source path:

.. code-block:: sh

   docker run --rm \
       -v electrumx-ravencoin_ravencoin-data:/data \
       -v /path/to/source-datadir:/source:ro \
       debian:bookworm-slim \
       bash -c 'apt-get update -qq && apt-get install -y -qq rsync &&
                rsync -a /source/blocks /source/chainstate /source/indexes /data/ &&
                chown -R 10001:10001 /data'

The source is mounted read-only and only the ``blocks``, ``chainstate``
and ``indexes`` subdirectories are copied, so wallets, private keys,
``.cookie`` files, RPC credentials and ``raven.conf`` are never copied.
This deployment generates and manages its own credentials and writes its
own Core configuration. After gracefully stopping the source Core, run the
same ``docker run`` command again as the final incremental pass. The
trailing ``chown`` sets the copied files to the image's unprivileged Core
user (uid and gid 10001), which the Core container requires.

Then start the stack with ``docker compose up -d``. Core verifies the
copied index and continues from it. If the source was missing an index this
deployment needs, such as ``assetindex``, or the copy turned out
inconsistent, Core performs local block-file scanning or indexing work,
which is much faster than downloading the chain again; neither case is data
loss. ElectrumX then builds its database from Core as usual.

Real Raspberry Pi 5 validation
------------------------------

The procedure in this section was executed end to end on real hardware on
2026-08-16. The recorded evidence:

* Hardware and OS: Raspberry Pi 5, 8 GB, ``aarch64``, Raspberry Pi OS Lite
  64-bit (Debian 13 trixie), OS on the microSD, external 2 TB SSD/NVMe in
  a USB enclosure.
* Storage: ``docker info --format 'DockerRootDir={{.DockerRootDir}}'``
  reported ``DockerRootDir=/srv/ravencoin/docker``, with ``findmnt``
  showing ``/srv/ravencoin`` on ``/dev/sda1 ext4 rw,noatime``, and the
  Compose named volumes on the SSD-backed data-root.
* Build: ``docker compose up -d --build`` completed 35/35 build steps in
  967.9 seconds, with the Core ARM64 compile-and-test stage at about 951
  seconds. Some cached Docker dependency layers were reused, so this is
  one observed deployment, not a guaranteed clean-build time.
* Core identity: ``docker compose exec ravencoin-core ravend --version``
  reported ``Raven Core Daemon version v4.8.0.0-gb60f50e04f``, matching
  the pinned source commit ``b60f50e04f1fba425b28804e61be2694faaf3469``.
  RPC ``getnetworkinfo`` reported ``"version": 4080000`` and
  ``"subversion": "/Ravencoin:4.8.0/"``.
* Services: ``docker compose ps`` showed ``ravencoin-core`` and
  ``electrumx`` healthy, with the one-shot ``rpc-secrets-init`` completed
  successfully.
* ARM64 binary SHA-256, observed from the physical build:

  .. code-block:: text

     ravend    7e23e00a470c05ac39921ef3548284f93befad7a174587d0118f4f941648991b
     raven-cli 4797c653d9a51eb27ed2b694f222ff145bae4fb24139b62c3ec733bba567891b

  The physical Raspberry Pi 5 build produced the same ravend and raven-cli
  SHA-256 values previously observed on the native ARM64 CI build. This is
  evidence of matching build outputs across the two tested ARM64
  environments, but is not a formal reproducible-build guarantee. The ARM64
  binaries remain distinct from, and not equivalent to, the amd64 release
  artifact.
* Power and thermals: ``vcgencmd get_throttled`` returned ``throttled=0x0``
  during operation, at roughly 67.5 degrees C.

What this run validated: repository setup, the Compose configuration, the
local ARM64 Core source build including ``make check``, final image
creation, Core startup, healthcheck and RPC access, ElectrumX startup and
healthcheck, the SSD-backed Docker data-root with the named volumes on the
SSD, no storage kernel errors after the USB compatibility workaround, and
no power or thermal throttling.

What this run did not validate: full initial blockchain synchronization,
full ElectrumX indexing to the chain tip, the incident-specific
KAWPOW/nHeight consensus probes, checkpoint validation around height
4,487,775, affected-chain validation from height 4,487,776,
``transfer_overflow`` activation around height 4,493,664, restart and
reboot persistence after complete synchronization, and full ARM64
consensus qualification. At observation time the node was in initial
synchronization (``blocks`` at 0, ``headers`` increasing), and ElectrumX,
while healthy, correctly remained at daemon height and database height 0
until Core feeds it validated blocks.

In short: native ARM64 build and physical Raspberry Pi 5 deployment
validated; full incident-specific consensus qualification remains pending.

Validation checklist
--------------------

After installation, and periodically during operation, verify the full
storage and service picture:

.. code-block:: sh

   uname -m
   docker info --format 'DockerRootDir={{.DockerRootDir}}'
   findmnt /srv/ravencoin
   df -h /srv/ravencoin
   docker system df
   docker compose ps
   docker compose logs --tail=100 ravencoin-core
   docker compose logs --tail=100 electrumx
   vcgencmd get_throttled

Expected results: ``aarch64`` from ``uname -m``;
``DockerRootDir=/srv/ravencoin/docker``; ``findmnt`` showing the SSD
filesystem mounted at ``/srv/ravencoin``; ``throttled=0x0``; and
``docker compose ps`` showing the ``ravencoin-core`` and ``electrumx``
services running and healthy (the one-shot ``rpc-secrets-init`` shows as
completed or exited successfully).

To confirm Core is actually responsive and progressing, use the private
in-container RPC path. Core JSON-RPC has no host port mapping in this stack
and must stay private:

.. code-block:: sh

   docker compose exec ravencoin-core raven-cli \
       -conf=/var/lib/ravencoin-config/raven.conf \
       -datadir=/var/lib/ravencoin getblockchaininfo

``blocks`` and ``headers`` show chain progress, and ``blocks < headers`` is
normal during initial synchronization. To confirm ElectrumX is indexing:

.. code-block:: sh

   docker compose exec electrumx electrumx_rpc getinfo

The height reported by ElectrumX should advance toward Core's height. A
healthy container only means the process answers its health check, not that
the chain or the index is current: in the real-hardware validation,
ElectrumX reported a healthy state at daemon height and database height 0
while Core was still synchronizing its first validated blocks. Healthy and
fully indexed are different milestones; see `Private checks after
startup`_ and `Operations`_.

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
