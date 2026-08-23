# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""GLM53-RVN-002 / REAUDIT-002 regression tests.

The root monitor-controller systemd unit must execute only a root-owned copy of
the exact controller bytes authenticated by the signed release bundle.  A
same-user race against the extracted install tree must never cross into root
execution.
"""

import hashlib
import importlib.util
import io
import os
import pathlib
import subprocess
import tarfile
from types import SimpleNamespace

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"

spec = importlib.util.spec_from_file_location("electrumx_ravencoin_install", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def _unit(root=pathlib.Path("/home/operator/electrumx-ravencoin")):
    return installer.controller_unit_body(root)


def _bundle_with_controller(payload: bytes) -> bytes:
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        info = tarfile.TarInfo(installer.CONTROLLER_SCRIPT)
        info.size = len(payload)
        info.mode = 0o755
        archive.addfile(info, io.BytesIO(payload))
    return output.getvalue()


def test_c_execstart_points_at_root_owned_trusted_copy():
    unit = _unit()
    exec_start = next(line for line in unit.splitlines()
                      if line.startswith("ExecStart="))
    assert str(installer.TRUSTED_CONTROLLER_PATH) in exec_start
    assert str(installer.TRUSTED_CONTROLLER_PATH).startswith("/usr/local/lib/")


def test_d_unit_never_executes_the_operator_writable_vendor_copy():
    """Modifying the vendor checkout after install cannot change root code."""
    unit = _unit()
    assert "vendor/ravencoin-node-monitor" not in unit
    assert installer.CONTROLLER_SCRIPT not in unit


def test_signed_bundle_member_digest_is_independent_of_extracted_tree():
    payload = b"#!/usr/bin/env python3\nprint('trusted controller')\n"
    bundle = _bundle_with_controller(payload)
    expected = hashlib.sha256(payload).hexdigest()
    assert installer._bundle_member_sha256(
        bundle, installer.CONTROLLER_SCRIPT) == expected


def test_bundle_member_digest_rejects_missing_controller():
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        payload = b"unrelated"
        info = tarfile.TarInfo("unrelated.txt")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(installer.InstallError, match="cannot derive trusted digest"):
        installer._bundle_member_sha256(output.getvalue(), installer.CONTROLLER_SCRIPT)


def test_a_trusted_copy_is_not_user_writable_after_install(monkeypatch, tmp_path):
    """After installation the executed script must be root:root and immutable
    to the invoking user, group and world."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    script = trusted / "controller.py"
    script.write_text("# controller\n")

    real_stat = pathlib.Path.stat
    mode_overrides = {str(script): 0o755, str(trusted): 0o755}

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if str(self) in mode_overrides:
            return os.stat_result(
                (mode_overrides[str(self)], result.st_ino, result.st_dev,
                 0, 0, 0, result.st_size, result.st_atime, result.st_mtime,
                 result.st_ctime))
        return result

    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", trusted)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH", script)
    # Patch Path.stat (not os.stat) so the fake applies on every Python
    # version: 3.10's pathlib does not resolve os.stat dynamically.
    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    installer.verify_trusted_controller()

    mode_overrides[str(script)] = 0o775
    with pytest.raises(installer.InstallError):
        installer.verify_trusted_controller()
    mode_overrides[str(script)] = 0o755


def test_verify_rejects_non_root_owner(monkeypatch, tmp_path):
    """The pre-remediation user-owned state must fail verification."""
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o755)
    script = trusted / "controller.py"
    script.write_text("# controller\n")
    script.chmod(0o755)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", trusted)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH", script)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    with pytest.raises(installer.InstallError, match="owned by root"):
        installer.verify_trusted_controller()


def test_verify_rejects_missing_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", tmp_path)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH",
                        tmp_path / "absent.py")
    with pytest.raises(installer.InstallError):
        installer.verify_trusted_controller()


def test_verify_rejects_wrong_final_digest(monkeypatch, tmp_path):
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    script = trusted / "controller.py"
    script.write_bytes(b"malicious replacement")

    real_stat = pathlib.Path.stat

    def fake_stat(self, *args, **kwargs):
        result = real_stat(self, *args, **kwargs)
        if self in (trusted, script):
            return os.stat_result(
                (0o755, result.st_ino, result.st_dev, 0, 0, 0,
                 result.st_size, result.st_atime, result.st_mtime,
                 result.st_ctime))
        return result

    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", trusted)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH", script)
    monkeypatch.setattr(pathlib.Path, "stat", fake_stat)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    expected = hashlib.sha256(b"signed controller").hexdigest()
    with pytest.raises(installer.InstallError, match="does not match signed bundle"):
        installer.verify_trusted_controller(expected)


def test_reinstall_uses_atomic_root_owned_staging_and_digest(monkeypatch, tmp_path):
    """Matching signed bytes are checked while root-owned, then atomically renamed."""
    commands = []
    verified = []
    expected = "a" * 64

    def fake_run_checked(argv, cwd=None):
        commands.append(list(argv))

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(installer, "_file_sha256", lambda path: expected)
    monkeypatch.setattr(installer, "verify_trusted_controller",
                        lambda digest=None: verified.append(digest))
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    source = tmp_path / "vendor-copy.py"
    source.write_text("# controller\n")
    installer.install_trusted_controller(source, expected)

    installs = [c for c in commands if "install" in c and "-d" not in c]
    assert installs, "trusted controller was never installed"
    install_cmd = installs[0]
    assert "-o" in install_cmd and install_cmd[install_cmd.index("-o") + 1] == "root"
    assert "-g" in install_cmd and install_cmd[install_cmd.index("-g") + 1] == "root"
    assert "-m" in install_cmd and install_cmd[install_cmd.index("-m") + 1] == "0755"
    staged = install_cmd[-1]
    assert ".new." in staged, "install must target an unpredictable staging name"
    assert staged != str(installer.TRUSTED_CONTROLLER_PATH)

    renames = [c for c in commands if "mv" in c[:3] and "-fT" in c]
    assert renames and renames[0][-1] == str(installer.TRUSTED_CONTROLLER_PATH)
    assert verified == [expected]
    for command in commands:
        if "install" in command and "-d" not in command:
            assert command[-1] != str(installer.TRUSTED_CONTROLLER_PATH)


def test_content_race_digest_mismatch_never_reaches_atomic_rename(monkeypatch, tmp_path):
    """REAUDIT-002: if same-user malware changes the extracted source before
    privileged copy, the root-owned staged bytes cannot be promoted."""
    commands = []
    expected = hashlib.sha256(b"signed controller").hexdigest()
    malicious = hashlib.sha256(b"malicious race winner").hexdigest()

    def fake_run_checked(argv, cwd=None):
        commands.append(list(argv))

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(installer, "_file_sha256", lambda path: malicious)
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    source = tmp_path / "vendor-copy.py"
    source.write_bytes(b"malicious race winner")
    with pytest.raises(installer.InstallError, match="mismatch after privileged copy"):
        installer.install_trusted_controller(source, expected)

    assert not any("mv" in c[:3] and "-fT" in c for c in commands), \
        "digest-mismatched staged content must never reach the final root path"


def test_install_controller_installs_digest_bound_copy_before_enabling(monkeypatch, tmp_path):
    """The unit is only enabled after the exact expected digest is verified."""
    order = []
    expected = "a" * 64

    def fake_run_checked(argv, cwd=None):
        if "systemctl" in argv:
            order.append(argv[argv.index("systemctl") + 1])
        else:
            order.append("install-step")

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(
        installer, "install_trusted_controller",
        lambda source, digest: order.append(("trusted-copy", digest)))
    monkeypatch.setattr(
        installer, "verify_trusted_controller",
        lambda digest=None: order.append(("verify", digest)))

    root = tmp_path / "install"
    root.mkdir()
    installer.install_controller(root, expected)
    assert order[0] == ("trusted-copy", expected)
    assert ("verify", expected) in order
    assert "enable" in order
    assert order.index(("verify", expected)) < order.index("enable")


def test_uninstall_removes_trusted_copy():
    import inspect
    source = inspect.getsource(installer.uninstall_controller_best_effort)
    assert "TRUSTED_CONTROLLER_PATH" in source
    assert "rm" in source


def test_trusted_controller_dir_creation_uses_a_real_ownership_capable_command(
        monkeypatch, tmp_path):
    """The privileged directory step must be a command that accepts -o/-g/-m.

    ``mkdir`` has no ownership flags, so ``mkdir -p -o root -g root`` aborts the
    whole installation with ``mkdir: invalid option -- 'o'`` as soon as the
    operator enables the advanced host controller.
    """
    commands = []
    expected = "b" * 64
    # The installer shares this process's subprocess module, and the patch below
    # replaces subprocess.run globally, so keep the real one for the rehearsal.
    real_run = subprocess.run

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked",
                        lambda argv, cwd=None: commands.append(list(argv)))
    monkeypatch.setattr(installer, "_file_sha256", lambda path: expected)
    monkeypatch.setattr(installer, "verify_trusted_controller", lambda digest=None: None)
    monkeypatch.setattr(installer.subprocess, "run",
                        lambda *args, **kwargs: SimpleNamespace(returncode=0))

    source = tmp_path / "vendor-copy.py"
    source.write_text("# controller\n")
    installer.install_trusted_controller(source, expected)

    directory = [c for c in commands if c[-1] == str(installer.TRUSTED_CONTROLLER_DIR)]
    assert directory, "the trusted controller directory was never created"
    command = directory[0]
    assert "mkdir" not in command
    # The command may carry a sudo prefix; the privileged program is what matters.
    command = command[command.index("install"):]
    assert command[0] == "install" and "-d" in command
    assert command[command.index("-o") + 1] == "root"
    assert command[command.index("-g") + 1] == "root"
    assert command[command.index("-m") + 1] == "0755"

    # Execute the same argv shape unprivileged, so the flags are validated by
    # the real coreutils binary rather than by this test's expectations.
    target = tmp_path / "trusted-dir"
    rehearsal = [arg for arg in command]
    rehearsal[rehearsal.index("-o") + 1] = str(os.getuid())
    rehearsal[rehearsal.index("-g") + 1] = str(os.getgid())
    rehearsal[-1] = str(target)
    completed = real_run(rehearsal, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert target.is_dir()
    assert target.stat().st_mode & 0o777 == 0o755
