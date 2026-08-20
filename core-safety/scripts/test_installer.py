# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for electrumx-ravencoin-install.py.

The installer is a single standalone file at the repository root (not a
package, filename contains hyphens), so it is loaded here via importlib
rather than a normal import. Every test exercises the real pure logic in
that file; network, Docker, and filesystem side effects are isolated behind
injectable callables so nothing here needs a real Docker daemon or network
access.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import sys
import unittest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INSTALLER_PATH = os.path.join(REPO_ROOT, "electrumx-ravencoin-install.py")

_spec = importlib.util.spec_from_file_location("electrumx_ravencoin_install", INSTALLER_PATH)
installer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installer)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import update_manifest as um  # noqa: E402


def _signed_release_manifest(key_pair, **overrides):
    private_key, public_bytes = key_pair
    kwargs = dict(
        electrumx_version="1.3.0", channel="stable",
        artifact_digest="sha256:" + "a" * 64, architecture="linux/amd64",
        core_version="4.8.0", core_repository="RavenProject/Ravencoin",
        core_tag="v4.8.0", core_commit="c" * 40,
        certification_report_digest="sha256:" + "b" * 64, safe_core_policy_version=3,
        required_updater_version="1.0.0", config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True, consensus_impact=False, auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + "d" * 64,
    )
    kwargs.update(overrides)
    body = um.build_manifest(**kwargs)
    key_id = um.key_id_for(public_bytes)
    document = um.sign_manifest(body, private_key, key_id=key_id)
    return document, public_bytes.hex()


class CliTests(unittest.TestCase):

    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as ctx:
            installer.parse_args(["--help"])
        self.assertEqual(ctx.exception.code, 0)

    def test_version_flag_parses(self):
        args = installer.parse_args(["--version"])
        self.assertTrue(args.version)

    def test_check_only_parses(self):
        args = installer.parse_args(["--check-only"])
        self.assertTrue(args.check_only)

    def test_chainstrap_and_p2p_conflict_fails(self):
        with self.assertRaises(SystemExit):
            installer.parse_args(["--chainstrap", "--p2p-bootstrap"])

    def test_with_and_without_monitor_conflict_fails(self):
        with self.assertRaises(SystemExit):
            installer.parse_args(["--with-monitor", "--without-monitor"])

    def test_controller_without_monitor_fails(self):
        with self.assertRaises(SystemExit):
            installer.parse_args(["--with-monitor-controller", "--without-monitor"])

    def test_chainstrap_alone_parses(self):
        args = installer.parse_args(["--chainstrap"])
        self.assertTrue(args.chainstrap)

    def test_monitor_with_controller_parses(self):
        args = installer.parse_args(["--with-monitor", "--with-monitor-controller"])
        self.assertTrue(args.with_monitor)
        self.assertTrue(args.with_monitor_controller)


class DetectionTests(unittest.TestCase):

    def test_amd64_detected(self):
        self.assertEqual(installer.detect_architecture("x86_64"), "amd64")

    def test_arm64_detected(self):
        self.assertEqual(installer.detect_architecture("aarch64"), "arm64")

    def test_unsupported_architecture_rejected(self):
        with self.assertRaises(installer.InstallError):
            installer.detect_architecture("mips")

    def test_python_version_too_old_rejected(self):
        with self.assertRaises(installer.InstallError):
            installer.check_python_version((3, 7, 0, "final", 0))

    def test_python_version_ok_accepted(self):
        installer.check_python_version((3, 12, 0, "final", 0))  # must not raise

    def test_missing_docker_returns_none(self):
        self.assertIsNone(installer.detect_docker(which=lambda name: None))

    def test_docker_present_returns_path(self):
        self.assertEqual(
            installer.detect_docker(which=lambda name: "/usr/bin/docker"),
            "/usr/bin/docker")

    def test_missing_compose_returns_none(self):
        result = installer.detect_compose(which=lambda name: None)
        self.assertIsNone(result)

    def test_compose_plugin_detected(self):
        class _Result:
            returncode = 0
        result = installer.detect_compose(
            which=lambda name: "/usr/bin/docker",
            run=lambda *a, **k: _Result())
        self.assertEqual(result, ["docker", "compose"])


class DatadirSafetyTests(unittest.TestCase):

    def test_empty_datadir(self):
        state = installer.classify_datadir(
            "/tmp/nope", exists=lambda p: False, listdir=lambda p: [])
        self.assertEqual(state, "empty")

    def test_populated_core_datadir_is_core_valid(self):
        state = installer.classify_datadir(
            "/x", exists=lambda p: True,
            listdir=lambda p: ["blocks", "chainstate", "debug.log"])
        self.assertEqual(state, "core_valid")

    def test_chainstrap_validated_datadir(self):
        state = installer.classify_datadir(
            "/x", exists=lambda p: True,
            listdir=lambda p: ["blocks", "chainstate", "chainstrap.blocks.json"])
        self.assertEqual(state, "chainstrap_validated")

    def test_ambiguous_populated_datadir_fails_closed(self):
        state = installer.classify_datadir(
            "/x", exists=lambda p: True,
            listdir=lambda p: ["some_random_file.txt"])
        self.assertEqual(state, "ambiguous")

    def test_existing_installation_detected(self):
        self.assertTrue(
            installer.detect_existing_installation("/x", exists=lambda p: True))

    def test_no_existing_installation(self):
        self.assertFalse(
            installer.detect_existing_installation("/x", exists=lambda p: False))


class ManifestVerificationTests(unittest.TestCase):

    def setUp(self):
        self.key_pair = um.generate_keypair()

    def test_valid_manifest_accepted(self):
        document, public_key_hex = _signed_release_manifest(self.key_pair)
        body = installer.verify_manifest_signature(document, public_key_hex)
        self.assertEqual(body["electrumxVersion"], "1.3.0")

    def test_invalid_signature_rejected(self):
        document, public_key_hex = _signed_release_manifest(self.key_pair)
        document["signature"]["value"] = base64.b64encode(b"\x00" * 64).decode()
        with self.assertRaises(installer.InstallError):
            installer.verify_manifest_signature(document, public_key_hex)

    def test_malformed_manifest_rejected(self):
        with self.assertRaises(installer.InstallError):
            installer.verify_manifest_signature({"not": "a manifest"}, "aa" * 32)

    def test_malformed_base64_signature_rejected(self):
        document, public_key_hex = _signed_release_manifest(self.key_pair)
        document["signature"]["value"] = "not-base64!!"
        with self.assertRaises(installer.InstallError):
            installer.verify_manifest_signature(document, public_key_hex)

    def test_unsupported_signature_algorithm_rejected(self):
        document, public_key_hex = _signed_release_manifest(self.key_pair)
        document["signature"]["algorithm"] = "rsa"
        with self.assertRaises(installer.InstallError):
            installer.verify_manifest_signature(document, public_key_hex)

    def test_unsupported_architecture_rejected(self):
        document, public_key_hex = _signed_release_manifest(self.key_pair)
        body = installer.verify_manifest_signature(document, public_key_hex)
        with self.assertRaises(installer.InstallError):
            installer.verify_architecture(body, "arm64")

    def test_matching_architecture_accepted(self):
        document, public_key_hex = _signed_release_manifest(
            self.key_pair, architecture="linux/arm64")
        body = installer.verify_manifest_signature(document, public_key_hex)
        installer.verify_architecture(body, "arm64")  # must not raise

    def test_wrong_artifact_digest_rejected(self):
        expected = "sha256:" + "a" * 64
        with self.assertRaises(installer.InstallError):
            installer.verify_artifact_digest(b"actual bytes", expected)

    def test_correct_artifact_digest_accepted(self):
        data = b"the artifact"
        import hashlib
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        installer.verify_artifact_digest(data, digest)  # must not raise

    def test_consensus_change_cannot_be_auto_update_eligible(self):
        with self.assertRaises(um.ManifestError):
            _signed_release_manifest(
                self.key_pair, consensus_impact=True, auto_update_eligible=True)

    def test_missing_pinned_key_refuses(self):
        with self.assertRaises(installer.InstallError):
            installer.require_pinned_release_key("")


class BootstrapChoiceTests(unittest.TestCase):

    def test_fresh_no_flags_defaults_to_chainstrap(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=False,
            existing_datadir_state="empty", interactive=False)
        self.assertEqual(choice, "chainstrap")

    def test_explicit_chainstrap_flag(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=True, p2p_flag=False,
            existing_datadir_state="empty", interactive=False)
        self.assertEqual(choice, "chainstrap")

    def test_explicit_p2p_flag(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=True,
            existing_datadir_state="empty", interactive=False)
        self.assertEqual(choice, "p2p")

    def test_conflicting_flags_fail(self):
        with self.assertRaises(installer.InstallError):
            installer.resolve_bootstrap_choice(
                chainstrap_flag=True, p2p_flag=True,
                existing_datadir_state="empty", interactive=False)

    def test_existing_core_datadir_preserved_no_auto_chainstrap(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=False,
            existing_datadir_state="core_valid", interactive=False)
        self.assertEqual(choice, "preserve_existing")

    def test_existing_chainstrap_validated_datadir_preserved(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=False,
            existing_datadir_state="chainstrap_validated", interactive=False)
        self.assertEqual(choice, "preserve_existing")

    def test_ambiguous_datadir_fails_closed(self):
        with self.assertRaises(installer.InstallError):
            installer.resolve_bootstrap_choice(
                chainstrap_flag=False, p2p_flag=False,
                existing_datadir_state="ambiguous", interactive=False)

    def test_interactive_enter_selects_chainstrap(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=False,
            existing_datadir_state="empty", interactive=True,
            prompt=lambda text: "")
        self.assertEqual(choice, "chainstrap")

    def test_interactive_2_selects_p2p(self):
        choice = installer.resolve_bootstrap_choice(
            chainstrap_flag=False, p2p_flag=False,
            existing_datadir_state="empty", interactive=True,
            prompt=lambda text: "2")
        self.assertEqual(choice, "p2p")


class MonitorChoiceTests(unittest.TestCase):

    def test_fresh_interactive_default_yes(self):
        enabled = installer.resolve_monitor_choice(
            with_monitor_flag=False, without_monitor_flag=False,
            interactive=True, prompt=lambda text: "")
        self.assertTrue(enabled)

    def test_non_interactive_default_yes(self):
        enabled = installer.resolve_monitor_choice(
            with_monitor_flag=False, without_monitor_flag=False,
            interactive=False)
        self.assertTrue(enabled)

    def test_without_monitor_flag_disables(self):
        enabled = installer.resolve_monitor_choice(
            with_monitor_flag=False, without_monitor_flag=True,
            interactive=False)
        self.assertFalse(enabled)

    def test_with_monitor_flag_enables(self):
        enabled = installer.resolve_monitor_choice(
            with_monitor_flag=True, without_monitor_flag=False,
            interactive=False)
        self.assertTrue(enabled)

    def test_conflicting_monitor_flags_fail(self):
        with self.assertRaises(installer.InstallError):
            installer.resolve_monitor_choice(
                with_monitor_flag=True, without_monitor_flag=True,
                interactive=False)

    def test_controller_default_disabled(self):
        enabled = installer.resolve_monitor_controller_choice(
            monitor_enabled=True, with_controller_flag=False, interactive=False)
        self.assertFalse(enabled)

    def test_controller_explicit_opt_in(self):
        enabled = installer.resolve_monitor_controller_choice(
            monitor_enabled=True, with_controller_flag=True, interactive=False)
        self.assertTrue(enabled)

    def test_controller_never_asked_when_monitor_disabled(self):
        enabled = installer.resolve_monitor_controller_choice(
            monitor_enabled=False, with_controller_flag=False, interactive=True,
            prompt=lambda text: (_ for _ in ()).throw(AssertionError("must not prompt")))
        self.assertFalse(enabled)

    def test_interactive_controller_enter_selects_no(self):
        enabled = installer.resolve_monitor_controller_choice(
            monitor_enabled=True, with_controller_flag=False, interactive=True,
            prompt=lambda text: "")
        self.assertFalse(enabled)


class MonitorSecurityTests(unittest.TestCase):

    def test_dashboard_binds_localhost_only(self):
        env = installer.build_monitor_environment(
            core_network_alias="ravencoin-core", core_rpc_port=8766,
            electrumx_network_alias="electrumx", electrumx_rpc_port=8000)
        self.assertEqual(env["MONITOR_DASHBOARD_BIND"], "127.0.0.1")

    def test_core_rpc_not_exposed_publicly(self):
        env = installer.build_monitor_environment(
            core_network_alias="ravencoin-core", core_rpc_port=8766,
            electrumx_network_alias="electrumx", electrumx_rpc_port=8000)
        self.assertEqual(env["CORE_RPC_HOST"], "ravencoin-core")
        service = installer.build_monitor_service_definition(
            environment=env, controller_enabled=False)
        self.assertNotIn("8766", " ".join(service.get("ports", [])))

    def test_no_docker_socket_no_cap_net_admin(self):
        env = installer.build_monitor_environment(
            core_network_alias="c", core_rpc_port=1, electrumx_network_alias="e",
            electrumx_rpc_port=2)
        service = installer.build_monitor_service_definition(
            environment=env, controller_enabled=False)
        self.assertNotIn("volumes", service)  # no docker.sock bind mount
        self.assertEqual(service["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", service["security_opt"])

    def test_controller_is_a_separate_service_not_folded_in(self):
        env = installer.build_monitor_environment(
            core_network_alias="c", core_rpc_port=1, electrumx_network_alias="e",
            electrumx_rpc_port=2)
        without = installer.build_monitor_service_definition(
            environment=env, controller_enabled=False)
        with_controller = installer.build_monitor_service_definition(
            environment=env, controller_enabled=True)
        self.assertEqual(without["cap_drop"], with_controller["cap_drop"])
        self.assertEqual(without["security_opt"], with_controller["security_opt"])

    def test_credentials_are_random_and_not_predictable(self):
        first = installer.generate_monitor_credentials()
        second = installer.generate_monitor_credentials()
        self.assertNotEqual(first, second)
        self.assertGreaterEqual(len(first), 32)


class ComposeOrchestrationTests(unittest.TestCase):

    def test_chainstrap_choice_layers_overlay(self):
        files = installer.compose_files_for_bootstrap_choice("chainstrap")
        self.assertEqual(files, ["compose.yaml", "compose.chainstrap.yaml"])

    def test_p2p_choice_uses_base_only(self):
        files = installer.compose_files_for_bootstrap_choice("p2p")
        self.assertEqual(files, ["compose.yaml"])

    def test_preserve_existing_uses_base_only(self):
        files = installer.compose_files_for_bootstrap_choice("preserve_existing")
        self.assertEqual(files, ["compose.yaml"])

    def test_unknown_choice_rejected(self):
        with self.assertRaises(installer.InstallError):
            installer.compose_files_for_bootstrap_choice("bogus")

    def test_compose_up_builds_correct_command(self):
        captured = {}

        class _Result:
            returncode = 0

        def fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            return _Result()

        installer.run_compose_up(
            ["docker", "compose"], ["compose.yaml", "compose.chainstrap.yaml"],
            run=fake_run)
        self.assertEqual(captured["cmd"], [
            "docker", "compose", "-f", "compose.yaml",
            "-f", "compose.chainstrap.yaml", "up", "-d"])

    def test_compose_up_failure_raises(self):
        class _Result:
            returncode = 1

        with self.assertRaises(installer.InstallError):
            installer.run_compose_up(
                ["docker", "compose"], ["compose.yaml"],
                run=lambda cmd, **k: _Result())

    def test_install_marker_written_atomically_readable(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            marker_path = os.path.join(tmp, "marker.json")
            installer.write_install_marker(
                marker_path, bootstrap_choice="chainstrap",
                monitor_enabled=True, controller_enabled=False)
            with open(marker_path, encoding="utf-8") as handle:
                payload = json.load(handle)
            self.assertEqual(payload["bootstrapChoice"], "chainstrap")
            self.assertTrue(payload["monitorEnabled"])
            self.assertFalse(payload["monitorControllerEnabled"])


def _fake_manifest_body(**overrides):
    kwargs = dict(
        electrumx_version="1.3.0", channel="stable",
        artifact_digest="sha256:" + "a" * 64,
        architecture="linux/amd64,linux/arm64",
        core_version="4.8.0", core_repository="RavenProject/Ravencoin",
        core_tag="v4.8.0", core_commit="c" * 40,
        certification_report_digest="sha256:" + "b" * 64, safe_core_policy_version=3,
        required_updater_version="1.0.0", config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True, consensus_impact=False, auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + "d" * 64,
    )
    kwargs.update(overrides)
    return um.build_manifest(**kwargs)


class EntryPointTests(unittest.TestCase):

    def setUp(self):
        self._original_fetch_and_verify = installer.fetch_and_verify_release_manifest
        installer.fetch_and_verify_release_manifest = lambda **k: _fake_manifest_body()
        self.addCleanup(self._restore_fetch_and_verify)

    def _restore_fetch_and_verify(self):
        installer.fetch_and_verify_release_manifest = self._original_fetch_and_verify

    def test_version_prints_and_exits_zero(self):
        rc = installer.main(["--version"])
        self.assertEqual(rc, 0)

    def test_missing_docker_fails_closed(self):
        original = installer.detect_docker
        installer.detect_docker = lambda **k: None
        try:
            rc = installer.main(["--check-only"])
        finally:
            installer.detect_docker = original
        self.assertEqual(rc, 1)

    def test_missing_pinned_release_key_fails_closed_before_install(self):
        """The unsigned/unpinned development build must never install
        anything: an empty RELEASE_PUBLIC_KEY_HEX is the fail-closed default,
        not a silently-skipped check."""
        import tempfile
        installer.fetch_and_verify_release_manifest = (
            self._original_fetch_and_verify)
        original_docker = installer.detect_docker
        original_compose = installer.detect_compose
        original_run_up = installer.run_compose_up
        installer.detect_docker = lambda **k: "/usr/bin/docker"
        installer.detect_compose = lambda **k: ["docker", "compose"]
        installer.run_compose_up = lambda argv, files, **k: self.fail(
            "must not invoke compose without a verified release manifest")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    rc = installer.main(["--chainstrap", "--without-monitor"])
                finally:
                    os.chdir(cwd)
        finally:
            installer.detect_docker = original_docker
            installer.detect_compose = original_compose
            installer.run_compose_up = original_run_up
        self.assertEqual(rc, 1)

    def test_fresh_install_invokes_compose_and_writes_marker(self):
        import tempfile
        original_docker = installer.detect_docker
        original_compose = installer.detect_compose
        original_run_up = installer.run_compose_up
        calls = {}
        installer.detect_docker = lambda **k: "/usr/bin/docker"
        installer.detect_compose = lambda **k: ["docker", "compose"]
        installer.run_compose_up = lambda argv, files, **k: calls.update(
            argv=argv, files=files)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    rc = installer.main(["--chainstrap", "--without-monitor"])
                    with open(installer.DEFAULT_INSTALL_MARKER, encoding="utf-8") as handle:
                        marker = json.load(handle)
                finally:
                    os.chdir(cwd)
        finally:
            installer.detect_docker = original_docker
            installer.detect_compose = original_compose
            installer.run_compose_up = original_run_up
        self.assertEqual(rc, 0)
        self.assertEqual(calls["files"], ["compose.yaml", "compose.chainstrap.yaml"])
        self.assertEqual(marker["bootstrapChoice"], "chainstrap")
        self.assertFalse(marker["monitorEnabled"])

    def test_existing_install_marker_prevents_rebootstrap(self):
        import tempfile
        original_docker = installer.detect_docker
        original_compose = installer.detect_compose
        original_run_up = installer.run_compose_up
        installer.detect_docker = lambda **k: "/usr/bin/docker"
        installer.detect_compose = lambda **k: ["docker", "compose"]
        installer.run_compose_up = lambda argv, files, **k: self.fail(
            "must not re-bootstrap an existing installation")
        try:
            with tempfile.TemporaryDirectory() as tmp:
                cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    with open(installer.DEFAULT_INSTALL_MARKER, "w",
                             encoding="utf-8") as handle:
                        handle.write("{}")
                    rc = installer.main(["--chainstrap"])
                finally:
                    os.chdir(cwd)
        finally:
            installer.detect_docker = original_docker
            installer.detect_compose = original_compose
            installer.run_compose_up = original_run_up
        self.assertEqual(rc, 0)

    def test_conflicting_cli_flags_exit_nonzero(self):
        with self.assertRaises(SystemExit) as ctx:
            installer.main(["--chainstrap", "--p2p-bootstrap"])
        self.assertNotEqual(ctx.exception.code, 0)

    def test_check_only_makes_zero_persistent_mutations(self):
        import tempfile
        original_docker = installer.detect_docker
        original_compose = installer.detect_compose
        installer.detect_docker = lambda **k: "/usr/bin/docker"
        installer.detect_compose = lambda **k: ["docker", "compose"]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                before = sorted(os.listdir(tmp))
                cwd = os.getcwd()
                os.chdir(tmp)
                try:
                    rc = installer.main(["--check-only"])
                finally:
                    os.chdir(cwd)
                after = sorted(os.listdir(tmp))
        finally:
            installer.detect_docker = original_docker
            installer.detect_compose = original_compose
        self.assertEqual(rc, 0)
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
