# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Adversarial regression tests for the trusted certification evaluator.

Each test simulates one way a malicious or subverted untrusted certification
stage could try to obtain a signed KNOWN_SAFE policy entry (GLM53-RVN-001).
Every case must end in the evaluator refusing (exit 1, no canonical report
that could be signed), except the legitimate-certification tests which must
succeed end to end.
"""

import hashlib
import json
import pathlib
import subprocess
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
REPO = SCRIPTS.parents[1]
PROFILE = REPO / "core-safety/profiles/rvn-consensus-2026-08-v1.json"
PREVIOUS_POLICY = REPO / "core-safety/production/safe-core-policy.json"
EVALUATOR = SCRIPTS / "evaluate_certification.py"

COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"
COMMIT12 = COMMIT[:12]
OTHER_COMMIT = "0123456789abcdef0123456789abcdef01234567"


def _profile_dict():
    return json.loads(PROFILE.read_text(encoding="utf-8"))


def _required_ids(profile, artifact_pinned=True):
    required = []
    for test in profile["tests"]:
        if test["class"] == "mandatory":
            required.append(test["id"])
        elif test["class"] == "mandatory-when-artifact-pinned" and artifact_pinned:
            required.append(test["id"])
    return required


def _candidate_entry(commit=COMMIT, version="4.8.0", tag="v4.8.0"):
    return {
        "repository": "RavenProject/Ravencoin",
        "tag": tag,
        "commit": commit,
        "version": version,
        "tag_object": commit,
        "tag_verified": True,
        "commit_verified": True,
        "published_at": "2026-08-01T00:00:00Z",
        "release_url": f"https://github.com/RavenProject/Ravencoin/releases/tag/{tag}",
        "artifact_name": "ravencoin-4.8.0-x86_64-linux-gnu.tar.gz",
        "artifact_sha256": "a" * 64,
        "notes": [],
        "state": "PROVENANCE_VALIDATED",
    }


def _report_body(entry, results, overall, required):
    profile = _profile_dict()
    return {
        "schemaVersion": 1,
        "candidate": {**{k: v for k, v in entry.items() if k != "state"},
                      "identity": f"{entry['repository']}@{entry['commit']}"},
        "profile": profile["profileId"],
        "profileRevision": profile["profileRevision"],
        "profileSha256": profile["profileSha256"],
        "harnessVersion": "1.1.0",
        "buildEnvironment": {"platform": "Linux", "python": "3.9.2",
                             "machine": "x86_64"},
        "requiredTests": list(required),
        "liveNodeValidation": [],
        "results": {test_id: {"result": result, "detail": "simulated",
                              "evidence": {}, "class": "mandatory",
                              "scope": "core"}
                    for test_id, result in results.items()},
        "overall": overall,
        "startedAt": 1,
        "finishedAt": 2,
    }


def _digest(body):
    stripped = {k: v for k, v in body.items() if k != "reportDigest"}
    return hashlib.sha256(json.dumps(
        stripped, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _passing_report(entry):
    required = _required_ids(_profile_dict())
    results = {test_id: "PASS" for test_id in required}
    body = _report_body(entry, results, "CERTIFICATION_PASSED", required)
    body["reportDigest"] = _digest(body)
    return body


def _evidence(report, **capture):
    capture_record = {"containerized": True, "network": "none",
                      "workspaceMount": "read-only",
                      "reportChannel": "container-stdout", "exitCode": 0}
    capture_record.update(capture)
    return {"schemaVersion": 1, "capture": capture_record, "report": report}


def _run_evaluator(tmp_path, entries, evidence_files, expect_success):
    state_dir = tmp_path / "state"
    evidence_dir = state_dir / "evidence"
    reports_dir = state_dir / "reports"
    evidence_dir.mkdir(parents=True)
    candidates_file = state_dir / "new-candidates.json"
    candidates_file.write_text(json.dumps(entries), encoding="utf-8")
    for name, payload in evidence_files.items():
        (evidence_dir / name).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(EVALUATOR),
         "--candidates", str(candidates_file),
         "--evidence-dir", str(evidence_dir),
         "--profile", str(PROFILE),
         "--output-dir", str(reports_dir),
         "--state-output", str(state_dir / "processed.json"),
         "--previous-policy", str(PREVIOUS_POLICY)],
        capture_output=True, text=True)
    if expect_success:
        assert completed.returncode == 0, completed.stderr + completed.stdout
    else:
        assert completed.returncode != 0, (
            f"evaluator accepted untrusted evidence:\n{completed.stdout}")
        assert not (reports_dir / "evaluation-summary.json").exists(), (
            "a refused evaluation must not leave a signable summary")
    return completed, reports_dir, state_dir


def test_a_forged_overall_is_refused(tmp_path):
    """Test A: malicious candidate forges overall=CERTIFICATION_PASSED while
    its real per-test results contain failures.  Signing must be refused."""
    entry = _candidate_entry()
    required = _required_ids(_profile_dict())
    results = {test_id: "PASS" for test_id in required}
    results[required[0]] = "FAIL"
    body = _report_body(entry, results, "CERTIFICATION_PASSED", required)
    body["reportDigest"] = _digest(body)
    completed, _, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(body)},
        expect_success=False)
    assert "derive" in completed.stderr or "overall" in completed.stderr


def test_b_tampered_report_after_digest_is_refused(tmp_path):
    """Test B: report body altered after its digest was computed."""
    entry = _candidate_entry()
    body = _passing_report(entry)
    tampered = dict(body)
    tampered["finishedAt"] = 999
    completed, _, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(tampered)},
        expect_success=False)
    assert "digest" in completed.stderr


def test_c_missing_evidence_fails_closed(tmp_path):
    """Test C: candidate omits required evidence entirely."""
    entry = _candidate_entry()
    completed, _, _ = _run_evaluator(tmp_path, [entry], {}, expect_success=False)
    assert "missing raw evidence" in completed.stderr


def test_d_duplicate_conflicting_evidence_fails_closed(tmp_path):
    """Test D: two candidates whose 12-hex commit prefixes collide cannot be
    covered by a single evidence file; the evaluator must refuse rather than
    silently attribute one record to both identities."""
    entry_a = _candidate_entry(commit=OTHER_COMMIT)
    entry_b = _candidate_entry(commit=OTHER_COMMIT[:12] + "f" * 28)
    body_a = _passing_report(entry_a)
    completed, _, _ = _run_evaluator(
        tmp_path, [entry_a, entry_b],
        {f"evidence-{OTHER_COMMIT[:12]}.json": _evidence(body_a)},
        expect_success=False)
    # Either refusal reason is correct: the second identity cannot be
    # satisfied by the first identity's evidence file.
    assert ("missing raw evidence" in completed.stderr
            or "identity" in completed.stderr)


def test_e_report_for_another_identity_is_refused(tmp_path):
    """Test E: evidence describes a different candidate than discovery."""
    entry = _candidate_entry()
    other_report = _passing_report(_candidate_entry(commit=OTHER_COMMIT))
    completed, _, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(other_report)},
        expect_success=False)
    assert "identity" in completed.stderr


def test_f_legitimate_certification_produces_canonical_report(tmp_path):
    """Test F: a fully legitimate certification flows through: canonical
    report, derivation record, summary digest and terminal watcher state."""
    entry = _candidate_entry()
    body = _passing_report(entry)
    completed, reports_dir, state_dir = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(body)},
        expect_success=True)
    canonical_path = reports_dir / f"report-{COMMIT12}.json"
    assert canonical_path.is_file()
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    assert canonical["overall"] == "CERTIFICATION_PASSED"
    assert canonical["evaluation"]["derivedOverall"] == "CERTIFICATION_PASSED"
    assert canonical["reportDigest"] == _digest(canonical)
    summary = json.loads((reports_dir / "evaluation-summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["candidates"][0]["canonicalDigest"] == canonical["reportDigest"]
    processed = json.loads((state_dir / "processed.json").read_text(encoding="utf-8"))
    assert f"RavenProject/Ravencoin@{COMMIT}" in processed["processed"]


def test_non_containerized_evidence_is_refused(tmp_path):
    """Evidence claiming host execution (the pre-remediation design) is not
    acceptable input to the trusted evaluator."""
    entry = _candidate_entry()
    body = _passing_report(entry)
    completed, _, _ = _run_evaluator(
        tmp_path, [entry],
        {f"evidence-{COMMIT12}.json": _evidence(body, containerized=False)},
        expect_success=False)


def test_networked_evidence_is_refused(tmp_path):
    """Evidence captured from a container with network access is refused."""
    entry = _candidate_entry()
    body = _passing_report(entry)
    completed, _, _ = _run_evaluator(
        tmp_path, [entry],
        {f"evidence-{COMMIT12}.json": _evidence(body, network="default")},
        expect_success=False)


def test_required_list_divergence_is_refused(tmp_path):
    """The untrusted stage cannot shrink the required-test list to make
    missing evidence look like a pass."""
    entry = _candidate_entry()
    required = _required_ids(_profile_dict())
    results = {test_id: "PASS" for test_id in required}
    body = _report_body(entry, results, "CERTIFICATION_PASSED", required[:-1])
    body["reportDigest"] = _digest(body)
    completed, _, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(body)},
        expect_success=False)
    assert "requiredTests" in completed.stderr


def test_inconclusive_build_stays_retryable(tmp_path):
    """A trusted-code BUILD_FAILED record is accepted as evidence but never
    becomes terminal state or a signable report."""
    # Use an identity that is not already present in the signed production
    # baseline.  After policy v3 promotion the canonical RavenProject v4.8.0
    # identity is intentionally seeded into processed state before candidate
    # evaluation, so it cannot demonstrate retryability for a new build.
    entry = _candidate_entry(commit=OTHER_COMMIT)
    commit12 = OTHER_COMMIT[:12]
    completed, reports_dir, state_dir = _run_evaluator(
        tmp_path, [entry],
        {f"evidence-{commit12}.json": _evidence(None, buildFailed=True)},
        expect_success=True)
    assert not (reports_dir / f"report-{commit12}.json").exists()
    summary = json.loads((reports_dir / "evaluation-summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["candidates"][0]["overall"] == "BUILD_FAILED"
    assert summary["candidates"][0]["canonicalDigest"] is None
    processed = json.loads((state_dir / "processed.json").read_text(encoding="utf-8"))
    assert f"RavenProject/Ravencoin@{OTHER_COMMIT}" not in processed["processed"]


def test_unavailable_results_never_derive_a_pass(tmp_path):
    """An all-PASS claim with UNAVAILABLE per-test results is refused."""
    entry = _candidate_entry()
    required = _required_ids(_profile_dict())
    results = {test_id: "UNAVAILABLE" for test_id in required}
    body = _report_body(entry, results, "CERTIFICATION_PASSED", required)
    body["reportDigest"] = _digest(body)
    _run_evaluator(tmp_path, [entry],
                   {f"evidence-{COMMIT12}.json": _evidence(body)},
                   expect_success=False)


def test_unknown_report_fields_are_refused(tmp_path):
    """Unknown top-level report fields (schema confusion) are refused."""
    entry = _candidate_entry()
    body = _passing_report(entry)
    body["extraField"] = "surprise"
    completed, _, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(body)},
        expect_success=False)
    assert "unknown fields" in completed.stderr


def test_mutation_style_end_to_end(tmp_path):
    """Mutation-style validation companion: a report whose derived verdict
    differs from its claim (REVIEW_REQUIRED results under a PASSED claim)
    can never appear in canonical output."""
    entry = _candidate_entry()
    required = _required_ids(_profile_dict())
    results = {test_id: "PASS" for test_id in required}
    results[required[0]] = "REVIEW_REQUIRED"
    body = _report_body(entry, results, "CERTIFICATION_PASSED", required)
    body["reportDigest"] = _digest(body)
    completed, reports_dir, _ = _run_evaluator(
        tmp_path, [entry], {f"evidence-{COMMIT12}.json": _evidence(body)},
        expect_success=False)
    assert not list(reports_dir.glob("report-*.json"))
