# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression coverage for the standalone 1.13.2 embedded-module boundary."""

from __future__ import annotations

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402


def test_embedded_revision_module_cannot_clobber_installer_globals(tmp_path):
    _private, public = update_manifest.generate_keypair()
    rendered = tmp_path / "electrumx-ravencoin-install.py"
    render_installer_v2.render(output=rendered, public_key_hex=public.hex())

    spec = importlib.util.spec_from_file_location(
        "rendered_installer_namespace_regression", rendered)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)

    # The historical installer verifier relies on the capture group. The
    # embedded high-water module intentionally uses a different SHA256_RE;
    # those globals must remain isolated from one another.
    assert module.SHA256_RE.groups == 1
    assert module._REVISION_MODULE.SHA256_RE.groups == 0
    assert module.SHA256_RE is not module._REVISION_MODULE.SHA256_RE

    digest = "sha256:" + "a" * 64
    assert module.SHA256_RE.fullmatch(digest).group(1) == "a" * 64
