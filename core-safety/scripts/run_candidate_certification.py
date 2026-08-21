#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Untrusted-stage orchestrator for candidate certification.

This script runs in the CI job that builds and exercises candidate code, but
it never executes candidate code itself: every candidate binary runs inside
the candidate build container, with no network, no privileged flags, and no
writable mount of the job workspace or of the evidence directory.  The raw
certification report travels over the container's stdout pipe (a channel the
candidate cannot write to except by breaking out of the container), and is
written to the evidence directory only by this orchestrator.

The evidence this stage produces is deliberately NOT an authoritative
decision.  The authoritative PASS/FAIL decision is derived later, in a
separate trusted job, by core-safety/scripts/evaluate_certification.py, which
never executes candidate code.

Usage:
  run_candidate_certification.py --candidates <new-candidates.json> \
      --evidence-dir core-safety/state/evidence
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from candidate import ALLOWED_SOURCE_REPOSITORIES  # noqa: E402

REPORT_STDOUT_PREFIX = "CERTIFICATION_REPORT_JSON="
ALLOWED_OUTCOMES = {"CERTIFICATION_PASSED", "CERTIFICATION_FAILED",
                    "REVIEW_REQUIRED", "BUILD_FAILED", "KNOWN_UNSAFE"}
EVIDENCE_SCHEMA_VERSION = 1


def _repo_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def _download_source_archive(repository: str, commit: str,
                             destination: pathlib.Path) -> str:
    """Pin the exact bytes the reproducible candidate build will fetch."""
    source_url = f"https://github.com/{repository}/archive/{commit}.tar.gz"
    subprocess.run([
        "curl", "--fail", "--location", "--proto", "=https", "--tlsv1.2",
        "--output", str(destination), source_url,
    ], check=True)
    digest = hashlib.sha256()
    with destination.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_certification_container(image_tag: str, commit: str,
                                 record_path: pathlib.Path) -> dict:
    """Run certify_core.py inside the candidate container and capture its
    report from stdout.  The job workspace is mounted read-only; datadirs live
    in container-local tmpfs; there is no network and no report path shared
    with the container."""
    repo_root = _repo_root()
    try:
        record_container_path = "/harness/" + str(record_path.relative_to(repo_root))
    except ValueError as exc:
        raise RuntimeError(
            f"candidate record {record_path} is not inside the repository "
            "workspace and cannot be mounted read-only into the container") from exc
    argv = [
        "docker", "run", "--rm",
        "--network", "none",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--tmpfs", "/tmp:rw,size=4g,exec",
        "--volume", f"{repo_root}:/harness:ro",
        "--workdir", "/harness",
        image_tag,
        "python3", "core-safety/scripts/certify_core.py",
        "--candidate-file", record_container_path,
        "--profile", "core-safety/profiles/rvn-consensus-2026-08-v1.json",
        "--fixtures", "core-safety/fixtures/mainnet-incident-headers.json",
        "--source-dir", "/out/source",
        "--bin-dir", "/out/bin",
        "--candidate-probe", "/out/bin/test_raven",
        "--candidate-test-binary", "/out/bin/test_raven",
        "--report", "-",
    ]
    completed = subprocess.run(argv, check=False, capture_output=True,
                               text=True, timeout=7200)
    report = None
    for line in completed.stdout.splitlines():
        if line.startswith(REPORT_STDOUT_PREFIX):
            report = json.loads(line[len(REPORT_STDOUT_PREFIX):])
    if completed.returncode not in (0, 1):
        raise RuntimeError(
            f"certification infrastructure failure (exit {completed.returncode}) "
            f"for {commit}: {completed.stderr[-1500:]}")
    if report is None:
        raise RuntimeError(
            f"certification container produced no report on stdout for {commit}")
    if report.get("overall") not in ALLOWED_OUTCOMES:
        raise RuntimeError(f"invalid certification outcome: {report.get('overall')!r}")
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "capture": {
            "containerized": True,
            "network": "none",
            "workspaceMount": "read-only",
            "reportChannel": "container-stdout",
            "exitCode": completed.returncode,
            "imageTag": image_tag,
        },
        "report": report,
    }


def _build_failure_evidence(commit: str, image_tag: str,
                            reason: str) -> dict:
    """Trusted-code record of an inconclusive build.  Not terminal state: the
    candidate stays retryable and never enters the policy."""
    return {
        "schemaVersion": EVIDENCE_SCHEMA_VERSION,
        "capture": {
            "containerized": True,
            "network": "none",
            "workspaceMount": "read-only",
            "reportChannel": "container-stdout",
            "buildFailed": True,
            "imageTag": image_tag,
            "reason": reason[:500],
        },
        "report": None,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidates", required=True,
                        help="new-candidates.json produced by discover_releases.py")
    parser.add_argument("--evidence-dir", required=True)
    arguments = parser.parse_args(argv)

    candidates = json.loads(
        pathlib.Path(arguments.candidates).read_text(encoding="utf-8"))
    evidence_dir = pathlib.Path(arguments.evidence_dir)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    build_root = _repo_root() / "build" / "candidate"
    build_root.mkdir(parents=True, exist_ok=True)

    produced = 0
    for entry in candidates:
        repository = entry.get("repository")
        commit = entry.get("commit")
        state = entry.get("state")
        if state != "PROVENANCE_VALIDATED":
            print(f"leaving candidate retryable: {state} {repository} "
                  f"{entry.get('tag', '')} {entry.get('reason', '')}")
            continue
        if not commit:
            raise RuntimeError("PROVENANCE_VALIDATED candidate has no commit")
        if repository not in ALLOWED_SOURCE_REPOSITORIES:
            raise RuntimeError(
                f"refusing candidate outside official source: {repository}@{commit}")

        # The candidate record consumed inside the container is written by this
        # orchestrator into a read-only-mounted workspace, and its identity is
        # re-verified by the trusted evaluator against this run's discovery.
        record = evidence_dir / f"candidate-{commit[:12]}.json"
        candidate_fields = {
            "repository", "tag", "commit", "version", "tag_object",
            "tag_verified", "commit_verified", "published_at", "release_url",
            "artifact_name", "artifact_sha256", "notes",
        }
        record.write_text(json.dumps({
            key: entry[key] for key in candidate_fields if key in entry
        }), encoding="utf-8")

        archive = build_root / f"source-{commit}.tar.gz"
        try:
            source_sha256 = _download_source_archive(repository, commit, archive)
            build_dir = build_root / commit
            subprocess.run([
                "core-safety/scripts/build_candidate.sh",
                commit, source_sha256, repository, str(build_dir),
            ], check=True, cwd=_repo_root())
        except (subprocess.CalledProcessError, RuntimeError) as exc:
            print(f"candidate build unavailable ({exc}); recording an "
                  "inconclusive certification")
            evidence = _build_failure_evidence(
                commit, f"chainstrap-candidate-build:{commit}", str(exc))
        else:
            evidence = _run_certification_container(
                f"chainstrap-candidate-build:{commit}", commit, record)
        finally:
            archive.unlink(missing_ok=True)

        evidence_path = evidence_dir / f"evidence-{commit[:12]}.json"
        evidence_path.write_text(
            json.dumps(evidence, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        overall = (evidence["report"] or {}).get("overall", "BUILD_FAILED")
        print(f"{repository}@{commit[:12]} -> {overall} "
              f"(raw evidence: {evidence_path.name})")
        produced += 1

    print(f"captured raw evidence for {produced} candidate(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
