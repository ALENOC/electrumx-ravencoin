# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression coverage for the standalone 1.13.2 embedded-module boundary."""

from __future__ import annotations

import base64
import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402


def _load_rendered_installer(tmp_path):
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
    return module


def test_embedded_revision_module_cannot_clobber_installer_globals(tmp_path):
    module = _load_rendered_installer(tmp_path)

    # The historical installer verifier relies on the capture group. The
    # embedded high-water module intentionally uses a different SHA256_RE;
    # those globals must remain isolated from one another.
    assert module.SHA256_RE.groups == 1
    assert module._REVISION_MODULE.SHA256_RE.groups == 0
    assert module.SHA256_RE is not module._REVISION_MODULE.SHA256_RE

    digest = "sha256:" + "a" * 64
    assert module.SHA256_RE.fullmatch(digest).group(1) == "a" * 64


def test_embedded_revision_payload_is_byte_identical_to_source_tree_module(tmp_path):
    module = _load_rendered_installer(tmp_path)

    # The renderer must embed the canonical module bytes directly. There is no
    # independently maintained copy: any source change is therefore reflected
    # in the rendered installer, and this assertion prevents a future renderer
    # refactor from silently introducing drift.
    embedded = base64.b64decode(module._REVISION_MODULE_B64, validate=True)
    assert embedded == render_installer_v2.REVISION_MODULE.read_bytes()
