# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Shared security primitives for the ElectrumX updater.

Security-critical shared types must be imported through this package so Python
cannot create distinct enum classes by loading the same source under unrelated
module names.
"""

from . import artifact_revision

__all__ = ("artifact_revision",)
