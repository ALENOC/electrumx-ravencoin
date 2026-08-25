=======================
ElectrumX for Ravencoin
=======================

A production-oriented Ravencoin ElectrumX deployment with signed releases,
verified Ravencoin Core, transactional updates, and optional Network Observer
tooling.

The current version is |release|.

Source Code
===========

This maintained fork is hosted on `GitHub <https://github.com/ALENOC/electrumx-ravencoin>`_.

Please submit an issue on the `bug tracker
<https://github.com/ALENOC/electrumx-ravencoin/issues>`_ if you have found a
bug or have a suggestion to improve the server.

Authors and License
===================

Neil Booth wrote the vast majority of the code; see :ref:`Authors`.
Python version at least 3.8 is required.

The code remains under the `MIT Licence <../LICENCE>`_. Maintainer and lineage
notes are in `NOTICE.md <../NOTICE.md>`_.

Getting Started
===============

Start with the repository `documentation hub <README.md>`_. The current
technical overview is `ElectrumX-RVN 1.13.11 <release-1.13.11.md>`_; the
Sphinx pages below retain the protocol and legacy deployment references.

There is a `Dockerfile`_ available .

.. _installer: https://github.com/bauerj/electrumx-installer
.. _Dockerfile: https://github.com/lukechilds/docker-electrumx

Documentation
=============

.. toctree::

   features
   changelog
   HOWTO
   environment
   protocol
   peer_discovery
   rpc-interface
   architecture
   authors

Indices and tables
==================

* :ref:`genindex`
* :ref:`search`
