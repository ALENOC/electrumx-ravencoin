#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Derive the authoritative certification decision from raw evidence.

This script runs in a trusted CI job that never executes candidate code and
never mounts anything a candidate could write.  The untrusted certification
stage (run_candidate_certification.py) may be fully attacker-controlled as far
as this script is concerned: its raw evidence is validated, never believed.

Security properties, each of which fails closed (non-zero exit, no output):

* completeness: every provenance-validated candidate of this discovery run
  must have exactly one raw evidence record;
* identity: the evidence must describe exactly the discovered candidate;
* integrity: the report's declared digest must match the digest recomputed
  from its body (post-capture tampering is detectable);
* independence: the overall verdict is re-derived from the per-test results
  and the pinned profile.  A forged ``overall: CERTIFICATION_PASSED`` whose
  per-test results do not support it is refused, not signed;
* schema: unknown fields, wrong versions, malformed profiles, duplicate or
  conflicting records are all refused.

The canonical reports and the evaluation summary this script writes are the
only certification output the protected signing job may consume.

Usage:
  evaluate_certification.py --candidates <new-candidates.json> \
      --evidence-dir core-safety/state/evidence \
      --profile core-safety/profiles/rvn-consensus-2026-08-v1.json \
      --output-dir core-safety/state/reports \
      --state-output core-safety/state/processed.json \
      --previous-policy core-safety/production/safe-core-policy.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from candidate import (  # noqa: E402
    Candidate, CandidateState, TestResult, aggregate_state, load_profile,
    required_test_ids,
)

EVALUATION_SCHEMA_VERSION = 1
REPORT_SCHEMA_VERSION = 1
TERMINAL_STATES = {"CERTIFICATION_PASSED", "CERTIFICATION_FAILED", "KNOWN_UNSAFE"}
REPORT_TOP_LEVEL_FIELDS = {
    "schemaVersion", "candidate", "profile", "profileRevision", "profileSha256",
    "harnessVersion", "buildEnvironment", "requiredTests", "liveNodeValidation",
    "results", "overall", "startedAt", "finishedAt", "reportDigest",
}


class EvaluationError(RuntimeError):
    """The raw evidence is not trustworthy; the run must fail closed."""


def recompute_report_digest(report: dict) -> str:
    """Reproduce certify_core.py's digest: sha256 of the report body with the
    reportDigest field itself excluded, canonical json, sorted keys."""
    body = {key: value for key, value in report.items() if key != "reportDigest"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvaluationError(message)


def _load_json(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationError(f"{path}: unreadable or malformed JSON ({exc})") from exc


def derive_overall(report: dict, profile: dict,
                   candidate: Candidate) -> CandidateState:
    """Re-derive the verdict from per-test results, ignoring the untrusted
    ``overall`` field."""
    results = report.get("results")
    _require(isinstance(results, dict), "report has no per-test result mapping")
    for test_id, value in results.items():
        _require(isinstance(test_id, str) and isinstance(value, dict),
                 f"malformed result entry for {test_id!r}")
        observed = value.get("result")
        _require(observed in {item.value for item in TestResult},
                 f"unknown test result {observed!r} for {test_id!r}")

    # The required-test list is itself re-derived from the pinned profile and
    # the discovered candidate, never taken from the report.
    artifact_pinned = candidate.artifact_sha256 is not None
    expected_required = required_test_ids(
        profile, have_chain_data=False, artifact_pinned=artifact_pinned)
    declared_required = report.get("requiredTests")
    _require(isinstance(declared_required, list),
             "report has no requiredTests list")
    _require(tuple(declared_required) == tuple(expected_required),
             "report requiredTests disagree with the pinned profile")

    parsed = {test_id: TestResult(value["result"])
              for test_id, value in results.items()}
    state = aggregate_state(parsed, required_ids=expected_required)
    if candidate.is_known_unsafe_version:
        state = CandidateState.KNOWN_UNSAFE
    return state


def evaluate_candidate(entry: dict, evidence: dict, profile: dict) -> dict:
    """Validate one raw evidence record and return the canonical report."""
    capture = evidence.get("capture") or {}
    _require(isinstance(capture, dict), "evidence has no capture record")
    _require(capture.get("containerized") is True,
             "evidence was not captured from a containerized execution")
    _require(capture.get("network") == "none",
             "evidence execution was not network-isolated")

    if capture.get("buildFailed"):
        # Inconclusive build recorded by trusted orchestrator code.  Not a
        # decision, never terminal, never enters the policy.
        return {"overall": "BUILD_FAILED", "report": None}

    report = evidence.get("report")
    _require(isinstance(report, dict), "evidence carries no report")
    _require(report.get("schemaVersion") == REPORT_SCHEMA_VERSION,
             f"unsupported report schema version {report.get('schemaVersion')!r}")
    unknown = set(report) - REPORT_TOP_LEVEL_FIELDS
    _require(not unknown, f"report carries unknown fields: {sorted(unknown)}")

    # Integrity: the body must still hash to its declared digest.
    declared_digest = report.get("reportDigest")
    _require(isinstance(declared_digest, str) and len(declared_digest) == 64,
             "report reportDigest must be 64 hex characters")
    _require(recompute_report_digest(report) == declared_digest,
             "report body does not match its declared digest")

    # Identity: the report must describe exactly the discovered candidate.
    reported_candidate = report.get("candidate") or {}
    _require(isinstance(reported_candidate, dict), "report has no candidate")
    try:
        parsed = Candidate.from_dict(dict(reported_candidate))
    except ValueError as exc:
        raise EvaluationError(f"report candidate record is invalid: {exc}") from exc
    discovered = Candidate.from_dict({
        key: entry[key] for key in (
            "repository", "tag", "commit", "version", "tag_object",
            "tag_verified", "commit_verified", "published_at", "release_url",
            "artifact_name", "artifact_sha256", "notes")
        if key in entry})
    _require(parsed.identity == discovered.identity,
             f"report identity {parsed.identity} != discovered {discovered.identity}")
    for field in ("tag", "version", "tag_object", "artifact_sha256"):
        _require(getattr(parsed, field) == getattr(discovered, field),
                 f"report candidate field {field!r} disagrees with discovery")

    # Profile binding.
    _require(report.get("profile") == profile["profileId"],
             "report was not produced against the pinned profile")
    revision = profile.get("profileRevision")
    _require(report.get("profileRevision") == revision,
             "report profile revision disagrees with the pinned profile")
    declared_profile_digest = profile.get("profileSha256")
    definition = dict(profile)
    definition.pop("profileRevision", None)
    definition.pop("profileSha256", None)
    computed = hashlib.sha256(json.dumps(
        definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _require(declared_profile_digest == computed,
             "pinned profile does not hash to its declared digest")
    _require(report.get("profileSha256") == declared_profile_digest,
             "report profile digest disagrees with the pinned profile")

    # Independence: derive the verdict, never trust the reported one.
    derived = derive_overall(report, profile, discovered)
    claimed = report.get("overall")
    _require(claimed == derived.value,
             f"report claims overall={claimed!r} but its own per-test results "
             f"derive {derived.value!r}; refusing to certify this evidence")

    canonical = dict(report)
    canonical["overall"] = derived.value
    canonical["evaluation"] = {
        "schemaVersion": EVALUATION_SCHEMA_VERSION,
        "evaluator": "evaluate_certification.py",
        "derivedOverall": derived.value,
        "derivedFrom": "per-test results and the pinned profile, independently "
                       "of the untrusted stage",
    }
    canonical["reportDigest"] = recompute_report_digest(canonical)
    return {"overall": derived.value, "report": canonical}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--evidence-dir", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--state-output", required=True)
    parser.add_argument("--previous-policy", required=True)
    arguments = parser.parse_args(argv)

    try:
        candidates = _load_json(pathlib.Path(arguments.candidates))
        profile = load_profile(arguments.profile)
        previous = _load_json(pathlib.Path(arguments.previous_policy))
    except (EvaluationError, ValueError) as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        return 1

    evidence_dir = pathlib.Path(arguments.evidence_dir)
    output_dir = pathlib.Path(arguments.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    expected = {}
    for entry in candidates:
        if (entry.get("state") == "PROVENANCE_VALIDATED"
                and entry.get("repository") and entry.get("commit")):
            key = f"{entry['repository']}@{entry['commit']}"
            _require(key not in expected, f"duplicate candidate identity {key}")
            expected[key] = entry

    summary_entries = []
    seen_identities = set()
    try:
        for key, entry in sorted(expected.items()):
            commit = entry["commit"]
            evidence_path = evidence_dir / f"evidence-{commit[:12]}.json"
            _require(evidence_path.is_file(),
                     f"missing raw evidence for {key} ({evidence_path})")
            evidence = _load_json(evidence_path)
            _require(evidence.get("schemaVersion") == 1,
                     f"{evidence_path}: unsupported evidence schema")
            verdict = evaluate_candidate(entry, evidence, profile)
            identity = (entry["repository"], entry["commit"])
            _require(identity not in seen_identities,
                     f"conflicting evidence for {identity}")
            seen_identities.add(identity)
            if verdict["report"] is not None:
                report_path = output_dir / f"report-{commit[:12]}.json"
                report_path.write_text(
                    json.dumps(verdict["report"], indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
            summary_entries.append({
                "repository": entry["repository"],
                "commit": commit,
                "tag": entry.get("tag"),
                "overall": verdict["overall"],
                "canonicalDigest": (verdict["report"] or {}).get("reportDigest"),
            })
    except EvaluationError as exc:
        print(f"evaluation refused: {exc}", file=sys.stderr)
        print("no policy may be generated from this run", file=sys.stderr)
        return 1

    summary = {
        "schemaVersion": EVALUATION_SCHEMA_VERSION,
        "candidates": summary_entries,
        "digest": hashlib.sha256(json.dumps(
            summary_entries, sort_keys=True,
            separators=(",", ":")).encode()).hexdigest(),
    }
    summary_path = output_dir / "evaluation-summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    # Watcher state: seed from the signed baseline, then mark exactly the
    # identities whose derived verdict is terminal.  Computed here, in the
    # trusted job, so untrusted code cannot author processed state.
    body = previous.get("policy", previous)
    processed = {}
    for release in body.get("releases", []):
        repository = release.get("repository")
        release_commit = release.get("commit")
        if repository and release_commit:
            processed[f"{repository}@{release_commit}"] = {"tag": release.get("tag")}
    for item in summary_entries:
        if item["overall"] in TERMINAL_STATES:
            processed[f"{item['repository']}@{item['commit']}"] = {"tag": item["tag"]}
    pathlib.Path(arguments.state_output).write_text(
        json.dumps({"processed": processed}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    for item in summary_entries:
        print(f"{item['repository']}@{item['commit'][:12]} -> {item['overall']}")
    print(f"evaluation summary: {summary_path} ({len(summary_entries)} candidate(s))")
    return 0


if __name__ == "__main__":
    sys.exit(main())
