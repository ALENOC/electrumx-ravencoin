# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

import pathlib
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import electrumx_update_cli as cli  # noqa: E402
from update_decision import (  # noqa: E402
    HostFacts, VerificationVerdict, evaluate_verification,
)


def _host():
    return HostFacts(
        architecture="linux/amd64",
        installed_updater_version="1.0.0",
        current_electrumx_version="1.0.0",
        current_core_commit="a" * 40,
    )


def _manifest(commit="c" * 40, cert_digest="sha256:cert"):
    return {
        "artifactDigest": "sha256:artifact",
        "architecture": "linux/amd64",
        "coreCommit": commit,
        "certificationReportDigest": cert_digest,
        "dbCompatibility": {"schemaVersion": 1},
    }


def test_certification_digest_must_match_verified_policy():
    commit = "c" * 40
    decision = evaluate_verification(
        manifest=_manifest(commit, "sha256:wrong"),
        signature_valid=True,
        downloaded_artifact_digest="sha256:artifact",
        host=_host(),
        safe_core_certified_commits=frozenset({commit}),
        safe_core_certification_digests={commit: "sha256:expected"},
    )
    assert decision.verdict is VerificationVerdict.REFUSED_CERTIFICATION_DIGEST_MISMATCH


def test_matching_certification_digest_can_verify():
    commit = "c" * 40
    decision = evaluate_verification(
        manifest=_manifest(commit, "sha256:expected"),
        signature_valid=True,
        downloaded_artifact_digest="sha256:artifact",
        host=_host(),
        safe_core_certified_commits=frozenset({commit}),
        safe_core_certification_digests={commit: "sha256:expected"},
    )
    assert decision.verdict is VerificationVerdict.VERIFIED


def test_production_apply_is_blocked_until_real_health_wiring_exists():
    assert cli.PRODUCTION_APPLY_READY is False
