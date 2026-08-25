# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Regression tests for SRV-02: the monitor must verify the signed
safe-Core policy's Ed25519 signature, against a pinned key and a
persisted anti-rollback high-water mark, before using it -- and a
verification failure must never be silently upgraded to a SAFE
classification.
"""

import base64
import importlib.util
import json
import pathlib

import pytest

from network_observer import cli
from network_observer import directory as directory_mod
from network_observer.classify import Security, classify_backend
from network_observer.store import Store

ROOT = pathlib.Path(__file__).resolve().parents[1]
CERTIFIED_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"


def _load_module(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


policy_mod = _load_module("policy_for_monitor_policy_tests", "core-safety/scripts/policy.py")


def _backend_payload(commit=CERTIFIED_COMMIT):
    core = {
        "name": "Ravencoin Core", "version": "4.8.0", "versionNumber": 4_080_000,
        "subversion": "/Ravencoin:4.8.0/", "network": "main",
        "blocks": 4_494_000, "headers": 4_494_000, "initialBlockDownload": False,
        "identity": {"evidence": "BUILD_IDENTITY_VERIFIED",
                     "sourceRepository": "RavenProject/Ravencoin", "sourceCommit": commit},
    }
    return {
        "server": "ElectrumX-RVN", "serverVersion": "ElectrumX-RVN 1.13.0.dev1",
        "backend": core,
        "compatibility": {
            "minimumSafeCore": "4.8.0", "safetyProfile": "rvn-consensus-2026-08-v1",
            "coreSafe": True, "networkMatches": True,
            "backendSynchronized": True, "kawpowHeightValidation": True,
            "checkpoint4487775": True,
        },
        "observedAt": 1786790000,
    }


def _signed_policy(private_key, key_id, *, version=1):
    body = policy_mod.build_policy(
        policy_version=version, safety_profile="rvn-consensus-2026-08-v1",
        releases=[{
            "repository": "RavenProject/Ravencoin", "tag": "v4.8.0", "version": "4.8.0",
            "commit": CERTIFIED_COMMIT, "status": "KNOWN_SAFE",
            "certification": {"result": "PASS"},
        }])
    return policy_mod.sign_policy(body, private_key, key_id=key_id)


def _write(tmp_path, name, document):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


@pytest.fixture
def keypair():
    private_key, public_bytes = policy_mod.generate_keypair()
    key_id = policy_mod.key_id_for(public_bytes)
    return private_key, public_bytes, key_id


@pytest.fixture
def pinned_key_file(tmp_path, keypair):
    _private_key, public_bytes, _key_id = keypair
    path = tmp_path / "trusted.hex"
    path.write_text(public_bytes.hex() + "\n")
    return path


def test_valid_current_signed_policy_is_accepted(tmp_path, keypair, pinned_key_file):
    private_key, _public_bytes, key_id = keypair
    path = _write(tmp_path, "policy.json", _signed_policy(private_key, key_id))
    trusted = cli.load_trusted_policy_keys(pinned_key_file)

    body = cli.load_policy(str(path), trusted_keys=trusted)

    assert body["policyVersion"] == 1
    assert body["releases"][0]["status"] == "KNOWN_SAFE"
    # Certified, but chain evidence is a separate gate; policy alone never
    # grants SAFE.
    security, _reason = classify_backend(_backend_payload(), body)
    assert security is Security.UNVERIFIED


def test_one_byte_tamper_is_rejected(tmp_path, keypair, pinned_key_file):
    private_key, _public_bytes, key_id = keypair
    document = _signed_policy(private_key, key_id)
    document["policy"]["releases"][0]["version"] = "9.9.9"  # tamper, no re-sign
    path = _write(tmp_path, "policy.json", document)
    trusted = cli.load_trusted_policy_keys(pinned_key_file)

    body = cli.load_policy(str(path), trusted_keys=trusted)

    assert body == cli.EMPTY_POLICY
    security, _reason = classify_backend(_backend_payload(), body)
    assert security is Security.UNREVIEWED_CORE


def test_valid_payload_wrong_signature_is_rejected(tmp_path, keypair, pinned_key_file):
    private_key, _public_bytes, key_id = keypair
    document = _signed_policy(private_key, key_id)
    other_private_key, _other_public = policy_mod.generate_keypair()
    forged = other_private_key.sign(policy_mod.canonical_bytes(document["policy"]))
    # keyId still claims the pinned key, but the signature was made with a
    # different key entirely.
    document["signature"]["value"] = base64.b64encode(forged).decode("ascii")
    path = _write(tmp_path, "policy.json", document)
    trusted = cli.load_trusted_policy_keys(pinned_key_file)

    body = cli.load_policy(str(path), trusted_keys=trusted)
    assert body == cli.EMPTY_POLICY


def test_directory_signing_domain_is_rejected(tmp_path, keypair, pinned_key_file):
    """A payload signed under the directory's domain separator (a different
    purpose, same key) must not verify as a policy: domain separation must
    hold even when a single key is reused across roles."""
    private_key, _public_bytes, key_id = keypair
    body = policy_mod.build_policy(
        policy_version=1, safety_profile="rvn-consensus-2026-08-v1",
        releases=[{
            "repository": "RavenProject/Ravencoin", "tag": "v4.8.0", "version": "4.8.0",
            "commit": CERTIFIED_COMMIT, "status": "KNOWN_SAFE",
            "certification": {"result": "PASS"},
        }])
    signature = private_key.sign(directory_mod.canonical_bytes(body))
    document = {"policy": body, "signature": {
        "algorithm": "ed25519", "keyId": key_id,
        "value": base64.b64encode(signature).decode("ascii"),
    }}
    path = _write(tmp_path, "policy.json", document)
    trusted = cli.load_trusted_policy_keys(pinned_key_file)

    body_out = cli.load_policy(str(path), trusted_keys=trusted)
    assert body_out == cli.EMPTY_POLICY


def test_unknown_signing_key_is_rejected(tmp_path, pinned_key_file):
    other_private_key, other_public = policy_mod.generate_keypair()
    other_id = policy_mod.key_id_for(other_public)
    path = _write(tmp_path, "policy.json", _signed_policy(other_private_key, other_id))
    trusted = cli.load_trusted_policy_keys(pinned_key_file)  # only the fixture key

    body = cli.load_policy(str(path), trusted_keys=trusted)
    assert body == cli.EMPTY_POLICY


def test_rolled_back_policy_is_rejected_via_persisted_high_water_mark(
        tmp_path, keypair, pinned_key_file):
    private_key, _public_bytes, key_id = keypair
    trusted = cli.load_trusted_policy_keys(pinned_key_file)
    store = Store(str(tmp_path / "network-observer.sqlite3"))
    try:
        newer_path = _write(tmp_path, "newer.json", _signed_policy(private_key, key_id, version=2))
        body = cli.load_policy(
            str(newer_path), trusted_keys=trusted,
            minimum_policy_version=store.load_minimum_policy_version())
        assert body["policyVersion"] == 2
        store.record_policy_version(body["policyVersion"])

        older_path = _write(tmp_path, "older.json", _signed_policy(private_key, key_id, version=1))
        rolled_back = cli.load_policy(
            str(older_path), trusted_keys=trusted,
            minimum_policy_version=store.load_minimum_policy_version())
        assert rolled_back == cli.EMPTY_POLICY
    finally:
        store.close()


def test_malformed_policy_is_rejected(tmp_path, pinned_key_file):
    path = _write(tmp_path, "policy.json", {"not": "a policy document"})
    trusted = cli.load_trusted_policy_keys(pinned_key_file)
    body = cli.load_policy(str(path), trusted_keys=trusted)
    assert body == cli.EMPTY_POLICY


def test_missing_signature_is_rejected(tmp_path, keypair, pinned_key_file):
    private_key, _public_bytes, key_id = keypair
    document = _signed_policy(private_key, key_id)
    del document["signature"]
    path = _write(tmp_path, "policy.json", document)
    trusted = cli.load_trusted_policy_keys(pinned_key_file)
    body = cli.load_policy(str(path), trusted_keys=trusted)
    assert body == cli.EMPTY_POLICY


def test_missing_policy_file_fails_closed_not_safe(tmp_path, pinned_key_file):
    trusted = cli.load_trusted_policy_keys(pinned_key_file)
    body = cli.load_policy(str(tmp_path / "does-not-exist.json"), trusted_keys=trusted)
    assert body == cli.EMPTY_POLICY
    security, _reason = classify_backend(_backend_payload(), body)
    assert security is Security.UNREVIEWED_CORE


def test_no_policy_argument_fails_closed():
    assert cli.load_policy(None, trusted_keys={}) == cli.EMPTY_POLICY


def test_load_trusted_policy_keys_matches_the_real_production_key():
    """The default pinned key file this monitor ships with must actually
    resolve to the key id the production policy is signed with."""
    trusted = cli.load_trusted_policy_keys(cli.DEFAULT_POLICY_TRUSTED_KEY)
    assert "a6b89849cec9eab7" in trusted
