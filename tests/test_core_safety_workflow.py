# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "core-safety-watch.yml"


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_watcher_builds_exact_candidate_before_certification():
    text = _text()
    assert "core-safety/scripts/build_candidate.sh" in text
    assert '"--source-dir", str(build_dir / "source")' in text
    assert '"--bin-dir", str(build_dir / "bin")' in text
    assert 'candidate_test = build_dir / "bin" / "test_raven"' in text
    assert '"--candidate-probe", str(candidate_test)' in text
    assert '"--candidate-test-binary", str(candidate_test)' in text
    assert 'repository != "RavenProject/Ravencoin"' in text
    assert 'state != "PROVENANCE_VALIDATED"' in text
    assert "source_sha256" in text


def test_watcher_never_signs_in_candidate_build_job():
    text = _text()
    certify_section = text.split("  certify:", 1)[1].split("  propose_policy:", 1)[0]
    assert "POLICY_SIGNING_KEY" not in certify_section
    assert "core-safety-signing" not in certify_section


def test_inconclusive_candidate_is_not_marked_processed():
    text = _text()
    certify_section = text.split("  certify:", 1)[1].split("  propose_policy:", 1)[0]
    assert 'terminal = {"CERTIFICATION_PASSED", "CERTIFICATION_FAILED", "KNOWN_UNSAFE"}' \
        in certify_section
    assert 'if report.get("overall") not in terminal:' in certify_section
    assert "BUILD_FAILED" not in certify_section.split(
        "Record only terminal candidate identities", 1)[1].split(
            "Save watcher state", 1)[0]
    assert "REVIEW_REQUIRED" not in certify_section.split(
        "Record only terminal candidate identities", 1)[1].split(
            "Save watcher state", 1)[0]


def test_signing_stays_in_protected_job_and_uses_signed_baseline():
    text = _text()
    signing_section = text.split("  propose_policy:", 1)[1]
    assert "environment: core-safety-signing" in signing_section
    assert "POLICY_SIGNING_KEY" in signing_section
    assert "minimum_policy_version=3" in signing_section
    assert "policy.verify_policy" in signing_section
    assert "--previous-policy core-safety/production/safe-core-policy.json" in signing_section
    assert 'entry.get("repository") != "RavenProject/Ravencoin"' in signing_section
    assert 'proposed["policyVersion"] != previous["policyVersion"] + 1' in signing_section


def test_watcher_does_not_publish_or_mutate_repository():
    text = _text()
    # The scheduled watcher may produce report/policy artifacts, but cannot
    # merge, tag, create a GitHub Release, or push generated trust state.
    assert "git push" not in text
    assert "gh release create" not in text
    assert "git tag" not in text
    assert "contents: write" not in text
