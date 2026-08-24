# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""GLM53-RVN-004 regression tests: the Core container entrypoint itself must
refuse normal startup when ChainStrap block data is staged but has not
completed full validation, so the security property no longer depends solely
on the Compose dependency graph."""

import hashlib
import os
import pathlib
import subprocess

ROOT = pathlib.Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "docker" / "core" / "entrypoint.sh"

BLOCKS_MARKER = ".chainstrap-blocks-ready.json"
DONE_MARKER = ".chainstrap-reindex-complete"


def _run_entrypoint(tmp_path, *, blocks_marker=None, done_marker=None):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    secrets = tmp_path / "secrets"
    data_dir.mkdir()
    config_dir.mkdir()
    secrets.mkdir()
    (secrets / "user").write_text("rpcuser\n")
    (secrets / "password").write_text("rpcpassword\n")
    config = config_dir / "raven.conf"
    config.write_text("rpcuser=rpcuser\nrpcpassword=rpcpassword\nrest=1\n")

    if blocks_marker is not None:
        (data_dir / BLOCKS_MARKER).write_text(blocks_marker)
    if done_marker is not None:
        (data_dir / DONE_MARKER).write_text(done_marker)

    ravend_ran = tmp_path / "ravend-ran"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "ravend").write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$RAVEND_RAN\"\nexit 0\n")
    (fake_bin / "ravend").chmod(0o755)

    environment = dict(os.environ)
    environment.update({
        "PATH": f"{fake_bin}:{environment['PATH']}",
        "RAVEND_RAN": str(ravend_ran),
        "RAVENCOIN_DATA_DIR": str(data_dir),
        "RAVENCOIN_CONFIG_DIR": str(config_dir),
        "RAVEN_RPC_USER_FILE": str(secrets / "user"),
        "RAVEN_RPC_PASSWORD_FILE": str(secrets / "password"),
    })
    completed = subprocess.run(
        ["sh", str(ENTRYPOINT)],
        capture_output=True, text=True, env=environment)
    return completed, ravend_ran.exists()


MARKER_BODY = '{\n  "blockhash": "%s",\n  "height": 1234,\n  "manifestSha256": "%s"\n}\n'


def test_no_chainstrap_markers_allows_normal_startup(tmp_path):
    completed, ran = _run_entrypoint(tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert ran


def test_staged_blocks_without_completed_reindex_refuses_startup(tmp_path):
    marker = MARKER_BODY % ("a" * 64, "b" * 64)
    completed, ran = _run_entrypoint(tmp_path, blocks_marker=marker)
    assert completed.returncode != 0
    assert not ran
    assert "refusing" in completed.stderr.lower()


def test_matching_reindex_marker_allows_startup(tmp_path):
    marker = MARKER_BODY % ("a" * 64, "b" * 64)
    done = hashlib.sha256(marker.encode()).hexdigest() + "\n"
    completed, ran = _run_entrypoint(
        tmp_path, blocks_marker=marker, done_marker=done)
    assert completed.returncode == 0, completed.stderr
    assert ran


def test_validated_chainstrap_startup_forwards_no_stray_arguments(tmp_path):
    """The marker comparison must not clobber the container arguments.

    Reading the staged marker digest with "set --" replaced the positional
    parameters that are forwarded to ravend, so a fully validated ChainStrap
    installation crash looped on its first normal startup with
    "Command line contains unexpected token".
    """
    marker = MARKER_BODY % ("a" * 64, "b" * 64)
    done = hashlib.sha256(marker.encode()).hexdigest() + "\n"
    completed, ran = _run_entrypoint(
        tmp_path, blocks_marker=marker, done_marker=done)
    assert completed.returncode == 0, completed.stderr
    assert ran
    arguments = [
        line for line in
        (tmp_path / "ravend-ran").read_text().splitlines() if line]
    assert arguments == [
        f"-datadir={tmp_path / 'data'}",
        f"-conf={tmp_path / 'config' / 'raven.conf'}",
        "-printtoconsole",
    ], arguments


def test_mismatched_reindex_marker_refuses_startup(tmp_path):
    marker = MARKER_BODY % ("a" * 64, "b" * 64)
    completed, ran = _run_entrypoint(
        tmp_path, blocks_marker=marker, done_marker="c" * 64 + "\n")
    assert completed.returncode != 0
    assert not ran
    assert "does not match" in completed.stderr


def test_empty_blocks_marker_is_treated_as_no_staged_data(tmp_path):
    # An empty marker cannot be a completed bootstrap (the bootstrap writer
    # only writes complete, non-empty markers atomically); the gate treats
    # it as absent rather than blocking startup on unusable state.
    completed, ran = _run_entrypoint(tmp_path, blocks_marker="")
    assert completed.returncode == 0, completed.stderr
    assert ran
