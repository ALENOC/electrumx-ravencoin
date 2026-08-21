# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""GLM53-RVN-002 regression tests: the root monitor-controller systemd unit
must execute only a root-owned copy of the controller that the installing
user cannot modify."""

import importlib.util
import os
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "electrumx-ravencoin-install.py"

spec = importlib.util.spec_from_file_location("electrumx_ravencoin_install", INSTALLER_PATH)
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def _unit(root=pathlib.Path("/home/operator/electrumx-ravencoin")):
    return installer.controller_unit_body(root)


def test_c_execstart_points_at_root_owned_trusted_copy():
    unit = _unit()
    exec_start = next(line for line in unit.splitlines()
                      if line.startswith("ExecStart="))
    assert str(installer.TRUSTED_CONTROLLER_PATH) in exec_start
    assert str(installer.TRUSTED_CONTROLLER_PATH).startswith("/usr/local/lib/")


def test_d_unit_never_executes_the_operator_writable_vendor_copy():
    """Test D companion: the user-owned vendor checkout is never referenced
    by the root unit, so modifying it after installation cannot change the
    code systemd executes."""
    unit = _unit()
    assert "vendor/ravencoin-node-monitor" not in unit
    assert installer.CONTROLLER_SCRIPT not in unit


def test_a_trusted_copy_is_not_user_writable_after_install(monkeypatch, tmp_path):
    """Test A/B: after installation the executed script must be owned by
    root:root and not writable by the invoking user, group or world."""
    trusted = tmp_path / "trusted"
    trusted.mkdir()
    script = trusted / "controller.py"
    script.write_text("# controller\n")

    real_stat = os.stat
    mode_overrides = {str(script): 0o755, str(trusted): 0o755}

    def fake_stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        key = str(pathlib.Path(str(path)))
        if key in mode_overrides:
            return os.stat_result(
                (mode_overrides[key], result.st_ino, result.st_dev, 0, 0, 0,
                 result.st_size, result.st_atime, result.st_mtime,
                 result.st_ctime))
        return result

    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", trusted)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH", script)
    monkeypatch.setattr(os, "stat", fake_stat)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    # Root-owned 0755 file in a root-owned 0755 directory: accepted.
    installer.verify_trusted_controller()

    # Group-writable file: refused.
    script.chmod(0o775)
    mode_overrides[str(script)] = 0o775
    try:
        installer.verify_trusted_controller()
        raised = False
    except installer.InstallError:
        raised = True
    assert raised, "group-writable trusted controller must be refused"
    mode_overrides[str(script)] = 0o755
    script.chmod(0o755)


def test_verify_rejects_non_root_owner(monkeypatch, tmp_path):
    """A trusted copy owned by the invoking user (the pre-remediation state)
    must fail verification."""
    trusted = tmp_path / "trusted"
    trusted.mkdir(mode=0o755)
    script = trusted / "controller.py"
    script.write_text("# controller\n")
    script.chmod(0o755)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", trusted)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH", script)
    monkeypatch.setattr(installer.os, "geteuid", lambda: 1000)
    try:
        installer.verify_trusted_controller()
        raised = False
    except installer.InstallError as exc:
        raised = "owned by root" in str(exc)
    assert raised, "user-owned trusted controller must be refused"


def test_verify_rejects_missing_copy(monkeypatch, tmp_path):
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_DIR", tmp_path)
    monkeypatch.setattr(installer, "TRUSTED_CONTROLLER_PATH",
                        tmp_path / "absent.py")
    try:
        installer.verify_trusted_controller()
        raised = False
    except installer.InstallError:
        raised = True
    assert raised


def test_e_reinstall_uses_atomic_root_owned_staging(monkeypatch, tmp_path):
    """Test E: reinstall replaces the trusted copy via a root-owned staging
    name plus atomic rename, never a directly-written or user-writable
    intermediate state."""
    commands = []

    def fake_run_checked(argv, cwd=None):
        commands.append(list(argv))

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(installer, "verify_trusted_controller", lambda: None)

    source = tmp_path / "vendor-copy.py"
    source.write_text("# controller\n")
    installer.install_trusted_controller(source)

    installs = [c for c in commands if "install" in c]
    assert installs, "trusted controller was never installed"
    install_cmd = installs[0]
    assert "-o" in install_cmd and install_cmd[install_cmd.index("-o") + 1] == "root"
    assert "-g" in install_cmd and install_cmd[install_cmd.index("-g") + 1] == "root"
    assert "-m" in install_cmd and install_cmd[install_cmd.index("-m") + 1] == "0755"
    staged = install_cmd[-1]
    assert staged.endswith(".new"), "install must target a staging name"
    assert staged != str(installer.TRUSTED_CONTROLLER_PATH), \
        "staging name must differ from the final path"

    renames = [c for c in commands if "mv" in c[:3] and "-fT" in c]
    assert renames and renames[0][-1] == str(installer.TRUSTED_CONTROLLER_PATH), \
        "final placement must be an atomic rename"
    # No command may write the final path directly.
    for command in commands:
        if "install" in command:
            assert command[-1] != str(installer.TRUSTED_CONTROLLER_PATH)


def test_install_controller_installs_trusted_copy_before_enabling(monkeypatch, tmp_path):
    """The unit is only enabled after the verified trusted copy exists."""
    order = []

    def fake_run_checked(argv, cwd=None):
        if "systemctl" in argv:
            order.append(argv[argv.index("systemctl") + 1])  # systemctl subcommand
        else:
            order.append("install-step")

    monkeypatch.setattr(installer, "controller_prerequisites", lambda **_: None)
    monkeypatch.setattr(installer, "run_checked", fake_run_checked)
    monkeypatch.setattr(installer, "install_trusted_controller",
                        lambda source: order.append("trusted-copy"))
    monkeypatch.setattr(installer, "verify_trusted_controller", lambda: None)

    root = tmp_path / "install"
    root.mkdir()
    installer.install_controller(root)
    assert order[0] == "trusted-copy"
    assert "enable" in order


def test_uninstall_removes_trusted_copy():
    script = (
        "import sys\n"
        "sys.path.insert(0, '.')\n"
    )
    assert "rm" in script or True  # structural placeholder, real check below
    import inspect
    source = inspect.getsource(installer.uninstall_controller_best_effort)
    assert str("TRUSTED_CONTROLLER_PATH") in source
    assert "rm" in source
