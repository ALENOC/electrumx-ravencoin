import pathlib
import sys

import pytest

SCRIPTS = pathlib.Path(__file__).parents[1] / "core-safety/scripts"
sys.path.insert(0, str(SCRIPTS))
import update_manifest as um  # noqa: E402


def body(**overrides):
    values = dict(
        electrumx_version="1.13.3",
        artifact_revision=0,
        channel="stable",
        artifact_digest="sha256:" + "a" * 64,
        provenance_digest="sha256:" + "b" * 64,
        architecture="linux/amd64,linux/arm64",
        core_version="4.8.0",
        core_repository="RavenProject/Ravencoin",
        core_tag="v4.8.0",
        core_commit="22549129888d02e0e08fcdb9f96f3c699167e774",
        certification_report_digest="c" * 64,
        safe_core_policy_version=3,
        required_updater_version="1.13.3",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename="electrumx-ravencoin-install.py",
        installer_digest="sha256:" + "d" * 64,
        release_timestamp="2026-08-22T00:00:00Z",
    )
    values.update(overrides)
    return um.build_manifest(**values)


def test_schema_v2_requires_artifact_revision_and_provenance_digest():
    candidate = body()
    assert candidate["schemaVersion"] == 2
    assert candidate["artifact_revision"] == 0
    assert candidate["provenanceDigest"].startswith("sha256:")


def test_negative_artifact_revision_is_rejected():
    with pytest.raises(um.ManifestError, match="artifact_revision"):
        body(artifact_revision=-1)


def test_malformed_provenance_digest_is_rejected():
    with pytest.raises(um.ManifestError, match="provenanceDigest"):
        body(provenance_digest="sha256:bad")


def test_signature_domain_is_v2_and_round_trip_verifies():
    private, public = um.generate_keypair()
    candidate = body(artifact_revision=4)
    signed = um.sign_manifest(candidate, private, key_id=um.key_id_for(public))
    assert um.SIGNATURE_DOMAIN.endswith(b"v2\x00")
    assert um.verify_manifest(signed, {um.key_id_for(public): public}) == candidate
