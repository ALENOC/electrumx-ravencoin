#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Compatibility alias for the canonical updater security module.

Security-critical types and state live only in
``electrumx_core_safety.artifact_revision``. This file intentionally defines no
verdict enums or policy logic. Legacy imports are rebound to that one module
object so loading this path cannot create a second EligibilityVerdict class.
"""
from __future__ import annotations

import sys as _sys

from electrumx_core_safety import artifact_revision as _canonical


def __getattr__(name):
    return getattr(_canonical, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_canonical)))


# Make ordinary ``import artifact_revision`` return the canonical module object.
_sys.modules[__name__] = _canonical
