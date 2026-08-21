# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "core-safety-watch.yml"


def _text():
    return WORKFLOW.read_text(encoding="utf-8")


def _section(start, end=None):
    text = _text()
    part = text.split(start, 1)[1]
    return part.split(end, 1)[0] if end else part


def test_watcher_builds_and_certifies_candidates_in_containers():
    text = _text()
    # The untrusted stage drives builds and certification only through the
    # orchestrator, which itself executes no candidate code.
    assert "core-safety/scripts/run_candidate_certification.py" in text
    orchestrator = (ROOT / "core-safety/scripts/run_candidate_certification.py"
                    ).read_text(encoding="utf-8")
    assert "build_candidate.sh" in orchestrator


def test_certification_reports_carry_a_container_confinement_record():
    orchestrator = (ROOT / "core-safety/scripts/run_candidate_certification.py"
                    ).read_text(encoding="utf-8")
    assert '"--network", "none"' in orchestrator
    assert '"--cap-drop", "ALL"' in orchestrator
    assert '"no-new-privileges"' in orchestrator
    assert ":ro" in orchestrator
    assert "REPORT_STDOUT_PREFIX" in orchestrator


def test_authoritative_decision_comes_from_the_trusted_evaluator():
    text = _text()
    evaluate_section = _section("  evaluate:", "  propose_policy:")
    assert "core-safety/scripts/evaluate_certification.py" in evaluate_section
    # The evaluator job must not build or execute candidate code.
    assert "build_candidate.sh" not in evaluate_section
    assert "run_candidate_certification.py" not in evaluate_section
    assert "docker build" not in evaluate_section
    # Watcher state is authored by the trusted job only.
    assert "core-safety-state-" in evaluate_section
    certify_section = _section("  certify:", "  evaluate:")
    assert "core-safety-state-" not in certify_section


def test_signing_consumes_only_evaluator_bound_reports():
    text = _text()
    signing_section = _section("  propose_policy:")
    assert "evaluation-summary.json" in signing_section
    assert "canonicalDigest" in signing_section
    assert "derivedOverall" in signing_section


def test_missing_evidence_artifacts_fail_closed():
    text = _text()
    # Evidence and canonical-report artifacts are security evidence: their
    # absence must fail the run.  (The optional policy-proposal upload may
    # legitimately be absent when no candidate was conclusively certified.)
    evidence_uploads = text.split("Upload raw certification evidence", 1)[1] \
        .split("  evaluate:", 1)[0]
    assert "if-no-files-found: error" in evidence_uploads
    canonical_uploads = text.split("Upload canonical certification reports", 1)[1] \
        .split("  propose_policy:", 1)[0]
    assert "if-no-files-found: error" in canonical_uploads


def test_watcher_never_signs_in_candidate_build_job():
    text = _text()
    untrusted = _section("  certify:", "  evaluate:")
    assert "POLICY_SIGNING_KEY" not in untrusted
    assert "core-safety-signing" not in untrusted
    evaluator = _section("  evaluate:", "  propose_policy:")
    assert "POLICY_SIGNING_KEY" not in evaluator
    assert "core-safety-signing" not in evaluator


def test_inconclusive_candidate_is_not_marked_processed():
    # The evaluator marks only terminal derived verdicts; BUILD_FAILED and
    # REVIEW_REQUIRED never enter processed state (asserted functionally in
    # core-safety/scripts/test_evaluate_certification.py).
    evaluator = (ROOT / "core-safety/scripts/evaluate_certification.py"
                 ).read_text(encoding="utf-8")
    assert 'TERMINAL_STATES = {"CERTIFICATION_PASSED", "CERTIFICATION_FAILED", "KNOWN_UNSAFE"}' \
        in evaluator
    assert 'if item["overall"] in TERMINAL_STATES:' in evaluator


def test_signing_stays_in_protected_job_and_uses_signed_baseline():
    signing_section = _section("  propose_policy:")
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
