# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""GLM53-RVN-008 regression tests: every installer/updater Compose invocation
must pin the project name so an exported COMPOSE_PROJECT_NAME cannot detach
the tooling from the project namespace its preflights and container-name
assumptions rely on."""

import importlib.util
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location(
    "electrumx_ravencoin_install", ROOT / "electrumx-ravencoin-install.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

sys.path.insert(0, str(ROOT / "core-safety" / "scripts"))
import update_runtime  # noqa: E402

PROJECT = "electrumx-ravencoin"


def test_installer_compose_prefix_pins_project_name():
    prefix = installer._compose_prefix(["compose.yaml", "compose.monitor.yaml"])
    assert prefix[:6] == ["docker", "compose", "-p", PROJECT, "-f",
                          "compose.yaml"]
    # The pin precedes every file argument, so no environment default can
    # override it.
    assert prefix.count("-p") == 1


def test_updater_compose_prefix_pins_project_name():
    prefix = update_runtime._compose_prefix(pathlib.Path("/opt/install"),
                                            ["compose.yaml"])
    assert prefix[:4] == ["docker", "compose", "-p", PROJECT]


def test_docker_preflight_filters_on_the_pinned_project():
    # The command construction is internal; assert the label filter constant
    # used by the preflight matches the pinned name instead of an
    # environment-derived one.
    assert installer.COMPOSE_PROJECT_NAME == PROJECT


def test_environment_override_cannot_change_the_pin(monkeypatch):
    monkeypatch.setenv("COMPOSE_PROJECT_NAME", "evil")
    prefix = installer._compose_prefix(["compose.yaml"])
    assert prefix[3] == PROJECT
    updater_prefix = update_runtime._compose_prefix(
        pathlib.Path("/opt/install"), ["compose.yaml"])
    assert updater_prefix[3] == PROJECT
