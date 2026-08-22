#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Compatibility alias for the canonical updater security module.

Security-critical types and state live only in
``electrumx_core_safety.artifact_revision``. This file intentionally defines no
verdict enums or policy logic. Legacy imports are rebound to that one module
object so loading this path cannot create a second EligibilityVerdict class.

The explicit namespace re-export is intentional: some historical tests/tools
load this compatibility path with ``spec_from_file_location`` and retain the
loader-created module object even after ``sys.modules`` is rebound. Those
callers must still receive the exact canonical classes/functions, never a
second implementation or a second enum definition.
"""
from __future__ import annotations

import sys as _sys

from electrumx_core_safety import artifact_revision as _canonical


for _name in dir(_canonical):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_canonical, _name)


def __getattr__(name):
    return getattr(_canonical, name)


def __dir__():
    return sorted(set(globals()) | set(dir(_canonical)))


# Make ordinary ``import artifact_revision`` return the canonical module object.
_sys.modules[__name__] = _canonical
