# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Regression tests for ordinary-update trust-root continuity.

A normal ElectrumX software update is allowed to replace application source,
but it must not silently replace either production public key.  The bundled
safe-Core policy must also verify under the already-installed Core-policy key
and bind the exact Core identity and certification report declared by the
signed release manifest.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import pathlib
import sys
import tarfile

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import policy as core_policy  # noqa: E402
import update_manifest  # noqa: E402
import update_runtime  # noqa: E402

CORE_COMMIT = "c" * 40
REPORT_DIGEST = "d" * 64
UPDATE_KEY_HEX = "a" * 64
MONITOR_COMMIT = "e" * 40
PROVENANCE_BYTES = (
    json.dumps({
        "schemaVersion": 1,
        "electrumxVersion": "1.14.0",
        "artifact_revision": 0,
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": "f" * 40,
    }, sort_keys=True, separators=(",", ":")) + "\n"
).encode("utf-8")
PROVENANCE_DIGEST = "sha256:" + hashlib.sha256(PROVENANCE_BYTES).hexdigest()


def _policy_document(*, private_key=None, report_digest=REPORT_DIGEST):
    private_key = private_key or Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes_raw()
    body = core_policy.build_policy(
        policy_version=3,
        safety_profile="rvn-consensus-2026-08-v1",
        releases=[{
            "repository": "RavenProject/Ravencoin",
            "tag": "v4.8.0",
            "version": "4.8.0",
            "commit": CORE_COMMIT,
            "status": "KNOWN_SAFE",
            "reportDigest": report_digest,
            "certification": {"result": "PASS"},
        }],
    )
    return (
        core_policy.sign_policy(
            body, private_key, key_id=core_policy.key_id_for(public_bytes)),
        public_bytes.hex(),
    )


def _bundle_files(*, policy_document, core_key_hex,
                  update_key_hex=UPDATE_KEY_HEX):
    metadata = {
        "schemaVersion": 1,
        "electrumxVersion": "1.14.0",
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": "f" * 40,
        "nodeMonitor": {
            "repository": "ALENOC/ravencoin-node-monitor",
            "commit": MONITOR_COMMIT,
            "bundledPath": "vendor/ravencoin-node-monitor",
        },
    }
    return {
        "compose.yaml": (
            "RAVENCOIN_VERSION: 4.8.0\n"
            f"RAVENCOIN_SOURCE_COMMIT: {CORE_COMMIT}\n"
            "RAVENCOIN_SOURCE_REPOSITORY: RavenProject/Ravencoin\n"
        ).encode(),
        "compose.storage.yaml": (
            b"volumes:\n"
            b"  ravencoin-data:\n    driver: local\n"
            b"  ravencoin-config:\n    driver: local\n"
            b"  electrumx-data:\n    driver: local\n"
            b"  monitor-data:\n    driver: local\n"
        ),
        "compose.chainstrap.yaml": b"network_mode: none\n",
        "compose.monitor.yaml": (
            b"services:\n  monitor:\n"
            b"    security_opt:\n      - no-new-privileges:true\n"
            b"    cap_drop:\n      - ALL\n"
            b'    ports:\n      - "127.0.0.1:8899:8899/tcp"\n'
        ),
        "compose.monitor-controller.yaml": b"services:\n  monitor-controller: {}\n",
        "compose.tls.yaml": (
            b"services:\n"
            b"  electrumx:\n"
            b"    ports:\n"
            b'      - "50002:50002/tcp"\n'
        ),
        "compose.existing-core.yaml": (
            b"services:\n"
            b"  electrumx:\n"
            b"    environment:\n"
            b"      RAVENCOIN_DAEMON_HOST: host.docker.internal\n"
        ),
        "setup.sh": b"#!/bin/sh\nexit 0\n",
        ".env.example": b"EXAMPLE=1\n",
        "docker/core/bootstrap-reindex.sh": (
            b"#!/bin/sh\nravend -connect=0\nravend -connect=0\n"
            b"raven-cli getbestblockhash\nraven-cli getblockhash 1\n"
            b"raven-cli listassets\nraven-cli getassetdata\n"
            b"raven-cli listaddressesbyasset\n"
        ),
        "release-install-metadata.json": (
            json.dumps(metadata, sort_keys=True) + "\n").encode(),
        "release-provenance.json": PROVENANCE_BYTES,
        "core-safety/production/update-signing-public-key.hex":
            (update_key_hex + "\n").encode(),
        "core-safety/production/core-policy-signing-public-key.hex":
            (core_key_hex + "\n").encode(),
        "core-safety/production/safe-core-policy.json":
            (json.dumps(policy_document, sort_keys=True) + "\n").encode(),
        "vendor/ravencoin-node-monitor/Dockerfile": b"FROM scratch\n",
        "vendor/ravencoin-node-monitor/.env.example": b"BIND_PORT=8899\n",
    }


def _make_bundle(files):
    raw = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as zipped:
        with tarfile.open(fileobj=zipped, mode="w") as archive:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mode = 0o755 if name.endswith(".sh") else 0o644
                archive.addfile(info, io.BytesIO(data))
    return raw.getvalue()


def _manifest_for(data):
    return update_manifest.build_manifest(
        electrumx_version="1.14.0",
        artifact_revision=0,
        channel="stable",
        artifact_digest="sha256:" + hashlib.sha256(data).hexdigest(),
        provenance_digest=PROVENANCE_DIGEST,
        architecture="linux/amd64",
        core_version="4.8.0",
        core_repository="RavenProject/Ravencoin",
        core_tag="v4.8.0",
        core_commit=CORE_COMMIT,
        certification_report_digest=REPORT_DIGEST,
        safe_core_policy_version=3,
        required_updater_version="2.0.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + "f" * 64,
        release_timestamp="2026-08-22T00:00:00Z",
    )


def _write_bundle(tmp_path, files):
    data = _make_bundle(files)
    path = tmp_path / "bundle.tar.gz"
    path.write_bytes(data)
    return path, _manifest_for(data)


def test_ordinary_update_accepts_same_keys_and_exact_signed_core_policy(tmp_path):
    document, core_key_hex = _policy_document()
    path, manifest = _write_bundle(
        tmp_path, _bundle_files(policy_document=document, core_key_hex=core_key_hex))

    metadata = update_runtime.validate_bundle_file(
        path, manifest,
        trusted_update_public_key_hex=UPDATE_KEY_HEX,
        trusted_core_policy_public_key_hex=core_key_hex,
    )
    assert metadata["nodeMonitor"]["commit"] == MONITOR_COMMIT


def test_ordinary_update_cannot_rotate_release_update_key(tmp_path):
    document, core_key_hex = _policy_document()
    path, manifest = _write_bundle(
        tmp_path,
        _bundle_files(
            policy_document=document,
            core_key_hex=core_key_hex,
            update_key_hex="b" * 64,
        ),
    )

    with pytest.raises(update_runtime.UpdateRuntimeError, match="rotate.*release/update"):
        update_runtime.validate_bundle_file(
            path, manifest,
            trusted_update_public_key_hex=UPDATE_KEY_HEX,
            trusted_core_policy_public_key_hex=core_key_hex,
        )


def test_ordinary_update_cannot_rotate_core_policy_key(tmp_path):
    document, core_key_hex = _policy_document()
    path, manifest = _write_bundle(
        tmp_path,
        _bundle_files(
            policy_document=document,
            core_key_hex="b" * 64,
        ),
    )

    with pytest.raises(update_runtime.UpdateRuntimeError, match="rotate.*safe-Core"):
        update_runtime.validate_bundle_file(
            path, manifest,
            trusted_update_public_key_hex=UPDATE_KEY_HEX,
            trusted_core_policy_public_key_hex=core_key_hex,
        )


def test_bundle_policy_must_be_signed_by_already_trusted_core_key(tmp_path):
    trusted_private = Ed25519PrivateKey.generate()
    trusted_core_key_hex = trusted_private.public_key().public_bytes_raw().hex()
    untrusted_document, _ = _policy_document(private_key=Ed25519PrivateKey.generate())
    path, manifest = _write_bundle(
        tmp_path,
        _bundle_files(
            policy_document=untrusted_document,
            core_key_hex=trusted_core_key_hex,
        ),
    )

    with pytest.raises(update_runtime.UpdateRuntimeError, match="policy does not verify"):
        update_runtime.validate_bundle_file(
            path, manifest,
            trusted_update_public_key_hex=UPDATE_KEY_HEX,
            trusted_core_policy_public_key_hex=trusted_core_key_hex,
        )


def test_bundle_policy_report_digest_must_match_release_manifest(tmp_path):
    document, core_key_hex = _policy_document(report_digest="9" * 64)
    path, manifest = _write_bundle(
        tmp_path, _bundle_files(policy_document=document, core_key_hex=core_key_hex))

    with pytest.raises(update_runtime.UpdateRuntimeError, match="report digest disagrees"):
        update_runtime.validate_bundle_file(
            path, manifest,
            trusted_update_public_key_hex=UPDATE_KEY_HEX,
            trusted_core_policy_public_key_hex=core_key_hex,
        )


def test_trust_continuity_requires_both_installed_keys_together(tmp_path):
    document, core_key_hex = _policy_document()
    path, manifest = _write_bundle(
        tmp_path, _bundle_files(policy_document=document, core_key_hex=core_key_hex))

    with pytest.raises(update_runtime.UpdateRuntimeError, match="both updater trust roots"):
        update_runtime.validate_bundle_file(
            path, manifest,
            trusted_update_public_key_hex=UPDATE_KEY_HEX,
        )
