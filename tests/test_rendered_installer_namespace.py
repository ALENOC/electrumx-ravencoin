# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression coverage for the standalone 1.13.3 embedded-module boundary."""

from __future__ import annotations

import base64
import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402


def _rendered_path(tmp_path):
    _private, public = update_manifest.generate_keypair()
    rendered = tmp_path / "electrumx-ravencoin-install.py"
    render_installer_v2.render(output=rendered, public_key_hex=public.hex())
    return rendered


def _load_rendered_installer(tmp_path):
    rendered = _rendered_path(tmp_path)
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

    # The renderer embeds the canonical source bytes directly. There is no
    # independently maintained copy, and this assertion prevents a future
    # renderer refactor from silently introducing drift.
    embedded = base64.b64decode(module._REVISION_MODULE_B64, validate=True)
    assert embedded == render_installer_v2.REVISION_MODULE.read_bytes()


def test_released_installer_has_no_qualification_transport_by_argument_environment_or_files(
        tmp_path, monkeypatch):
    rendered = _rendered_path(tmp_path)
    source = rendered.read_text(encoding="utf-8")
    assert "qualification-candidate" not in source
    assert "QUALIFICATION_MANIFEST_FILE" not in source
    assert "load_qualification_candidate" not in source
    # Version-agnostic on purpose: pinning the banner to one release makes
    # this assertion vacuous the moment the release version is bumped.
    assert "PRE-PUBLICATION" not in source
    assert "HARDWARE QUALIFICATION" not in source

    # Plausible local candidate filenames and environment variables must not
    # create an alternate release-acceptance path because production code has
    # no switch or hook that recognizes them.
    (tmp_path / "release-manifest.json").write_text("{}", encoding="utf-8")
    (tmp_path / "electrumx-ravencoin-bundle.tar.gz").write_bytes(b"local candidate")
    monkeypatch.setenv("ELECTRUMX_QUALIFICATION_CANDIDATE_DIR", str(tmp_path))
    monkeypatch.setenv("ELECTRUMX_RELEASE_CANDIDATE_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    module = _load_rendered_installer(tmp_path / "second")
    args = module.parse_args([])
    assert not hasattr(args, "qualification_candidate_dir")
    assert not hasattr(module, "load_qualification_candidate")

    with pytest.raises(SystemExit) as exc:
        module.parse_args(["--qualification-candidate-dir", str(tmp_path)])
    assert exc.value.code == 2


def test_runtime_chainstrap_files_are_release_required(tmp_path):
    module = _load_rendered_installer(tmp_path)
    assert "contrib/bootstrap/chainstrap_bootstrap.py" in module.REQUIRED_BUNDLE_PATHS
    assert "contrib/bootstrap/chainstrap_runtime.py" in module.REQUIRED_BUNDLE_PATHS
    assert "docker/bootstrap/Dockerfile" in module.REQUIRED_BUNDLE_PATHS
