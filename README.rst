========================================
ElectrumX-RVN community-maintained fork
========================================

This is a community-maintained fork of
`Electrum-RVN-SIG/electrumx-ravencoin
<https://github.com/Electrum-RVN-SIG/electrumx-ravencoin>`_.  It is not an
official Ravencoin release.  The fork preserves the original MIT licence,
copyright notices, and attribution.

Security baseline
=================

The server is designed for Ravencoin Core 4.8.0 and later.  On startup it
queries the local daemon and refuses, by default, to serve when any of these
conditions hold:

* the backend Core version is older than 4.8.0;
* the configured network and daemon network differ;
* mainnet checkpoint 4,487,775 does not match; or
* an existing ElectrumX database tip is not on Core's canonical chain.

The checks repeat periodically.  Transaction broadcast performs an additional
fresh check so a backend downgrade cannot retain a broadcast window.  The
``server.ravencoin_backend`` method exposes sanitized backend-version evidence;
clients must still verify genesis, headers, chain history, and operator
independence themselves.

The mainnet KAWPOW header ``nHeight`` is checked against its indexed height from
block 4,487,776 onward as defense in depth for the August 2026 incident.  The
last-unaffected checkpoint is:

::

   4487775 000000000002d64509e06e76ddbbe418c725291687ec62b41ecfc40386a091fd

Install
-------

Python 3.10 through 3.12, a C/C++ toolchain, CMake, Git, and LevelDB headers are
required to install the native Ravencoin hashing dependencies.

::

   python3 -m venv venv
   venv/bin/pip install --upgrade pip
   venv/bin/pip install .

Set ``COIN=Ravencoin``, ``DB_DIRECTORY``, and a localhost/private
``DAEMON_URL``.  Never expose Ravencoin Core RPC to the Internet.  The explicit
development escape hatch ``ALLOW_UNSAFE_RAVENCOIN_CORE=1`` is disabled by
default and must not be used for production.

Container and service templates
===============================

Build the multi-architecture-compatible source image from the repository root:

::

   docker build -f contrib/Dockerfile -t electrumx-rvn:dev .

The image runs as a non-root user and does not generate TLS keys.  Mount a
persistent database and operator-managed CA-valid certificate/key when enabling
public TLS.  A hardened systemd example is under ``contrib/systemd``.

Operations and migration
========================

Read ``docs/MIGRATING_FROM_ELECTRUM_RVN_SIG.md`` before reusing a database that
may have indexed the affected chain.  ``SECURITY.md`` documents the threat model
and reporting guidance.  Software ARM64 compatibility is exercised by the
container workflow; resource suitability still depends on the host and a full
initial-index soak.

Attribution
===========

Neil Booth wrote the original ElectrumX implementation.  Ravencoin conversion
and asset support came from the Electrum-RVN-SIG community, including
kralverde.  See ``LICENCE``, ``docs/ACKNOWLEDGEMENTS``, and repository history.
