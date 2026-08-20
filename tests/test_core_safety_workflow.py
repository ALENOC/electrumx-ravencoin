# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "core-safety-watch.yml"


def test_watcher_builds_exact_candidate_before_certification():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "core-safety/scripts/build_candidate.sh" in text
    assert '"--source-dir", str(build_dir / "source")' in text
    assert '"--bin-dir", str(build_dir / "bin")' in text
    assert '"--candidate-test-binary", str(build_dir / "bin" / "test_raven")' in text
    assert 'repository != "RavenProject/Ravencoin"' in text
    assert "source_sha256" in text


def test_watcher_never_signs_in_candidate_build_job():
    text = WORKFLOW.read_text(encoding="utf-8")
    certify_section = text.split("  certify:", 1)[1].split("  propose_policy:", 1)[0]
    assert "POLICY_SIGNING_KEY" not in certify_section
    assert "core-safety-signing" not in certify_section


def test_signing_stays_in_protected_job():
    text = WORKFLOW.read_text(encoding="utf-8")
    signing_section = text.split("  propose_policy:", 1)[1]
    assert "environment: core-safety-signing" in signing_section
    assert "POLICY_SIGNING_KEY" in signing_section
