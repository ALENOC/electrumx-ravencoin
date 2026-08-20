#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Gate for the RavenProject trust migration: verify before signing.

This script is the pre-signing check for the atomic v2 -> v3 migration that
revokes the 2miners/Ravencoin baseline and promotes the certified
RavenProject/Ravencoin release in a single signed transition.  It never reads
POLICY_SIGNING_KEY and never signs anything; it only proves that an unsigned
v3 candidate is the deterministic, trustworthy result of:

  * a validly signed v2 policy,
  * an explicit revocation of the known 2miners/Ravencoin baseline identity,
  * a genuine CERTIFICATION_PASSED report for the RavenProject/Ravencoin
    release, with its reportDigest recomputed from the report itself rather
    than trusted from any other source.

Signing happens afterwards, in the protected step, using the ordinary
generate_policy.py --signing-key flow with the exact same inputs this script
verified.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from generate_policy import entry_from_report, merge_previous  # noqa: E402
from policy import (  # noqa: E402
    PolicyError, TRUSTED_RELEASE_REPOSITORIES, build_policy, validate_body, verify_policy,
)

EXPECTED_BASELINE_REPOSITORY = "2miners/Ravencoin"
EXPECTED_BASELINE_COMMIT = "b60f50e04f1fba425b28804e61be2694faaf3469"
EXPECTED_CANDIDATE_REPOSITORY = "RavenProject/Ravencoin"
EXPECTED_CANDIDATE_COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"
EXPECTED_CANDIDATE_TAG = "v4.8.0"


def recompute_report_digest(report: dict) -> str:
    """Reproduce certify_core.py's digest: sha256 of the report body with the
    reportDigest field itself excluded, canonical json, sorted keys."""
    body = {key: value for key, value in report.items() if key != "reportDigest"}
    return hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def verify_report_identity(report: dict) -> None:
    candidate = report.get("candidate", {})
    if candidate.get("repository") != EXPECTED_CANDIDATE_REPOSITORY:
        raise PolicyError(
            f"certification report repository {candidate.get('repository')!r} is not "
            f"{EXPECTED_CANDIDATE_REPOSITORY!r}")
    if candidate.get("tag") != EXPECTED_CANDIDATE_TAG:
        raise PolicyError(
            f"certification report tag {candidate.get('tag')!r} is not "
            f"{EXPECTED_CANDIDATE_TAG!r}")
    if candidate.get("commit") != EXPECTED_CANDIDATE_COMMIT:
        raise PolicyError(
            f"certification report commit {candidate.get('commit')!r} is not "
            f"{EXPECTED_CANDIDATE_COMMIT!r}")
    if report.get("overall") != "CERTIFICATION_PASSED":
        raise PolicyError(
            f"certification report overall status is {report.get('overall')!r}, "
            f"not CERTIFICATION_PASSED; refusing to promote")

    declared_digest = report.get("reportDigest")
    if not isinstance(declared_digest, str) or len(declared_digest) != 64:
        raise PolicyError(
            "certification report reportDigest must be exactly 64 hexadecimal "
            f"characters, got {declared_digest!r}")
    recomputed_digest = recompute_report_digest(report)
    if recomputed_digest != declared_digest:
        raise PolicyError(
            "certification report reportDigest does not match the digest recomputed "
            f"from the report body on disk (declared={declared_digest!r}, "
            f"recomputed={recomputed_digest!r})")

    required_tests = report.get("requiredTests", [])
    if not required_tests:
        raise PolicyError("certification report has no requiredTests to enforce")
    results = report.get("results", {})
    for test_id in required_tests:
        outcome = results.get(test_id, {})
        if outcome.get("result") != "PASS":
            raise PolicyError(
                f"mandatory certification test {test_id!r} did not PASS "
                f"(result={outcome.get('result')!r})")


def verify_baseline(previous_body: dict) -> None:
    baseline = None
    for entry in previous_body.get("releases", []):
        if entry["repository"] == EXPECTED_BASELINE_REPOSITORY:
            baseline = entry
            break
    if baseline is None:
        raise PolicyError(
            f"v2 policy does not contain a {EXPECTED_BASELINE_REPOSITORY!r} baseline entry")
    if baseline["status"] != "KNOWN_SAFE":
        raise PolicyError(
            f"v2 baseline entry for {EXPECTED_BASELINE_REPOSITORY!r} is "
            f"{baseline['status']!r}, expected KNOWN_SAFE prior to migration")
    if baseline["commit"] != EXPECTED_BASELINE_COMMIT:
        raise PolicyError(
            f"v2 baseline commit {baseline['commit']!r} does not match the expected "
            f"identity {EXPECTED_BASELINE_COMMIT!r}")


def verify_atomic_migration(*, previous_document: dict, trusted_keys: dict,
                            report: dict, revocation: dict,
                            reviewed_candidate: dict,
                            minimum_policy_version: int = 2) -> dict:
    """Independently regenerate v3 and prove it matches the reviewed candidate.

    Returns the regenerated, still-unsigned policy body on success. Raises
    PolicyError on any mismatch, missing precondition, or untrusted identity.
    """
    # Steps 1-3: v2 must verify, and must actually be policyVersion 2.
    previous_body = verify_policy(
        previous_document, trusted_keys, minimum_policy_version=minimum_policy_version)
    if previous_body["policyVersion"] != 2:
        raise PolicyError(
            f"expected the migration source to be policyVersion 2, got "
            f"{previous_body['policyVersion']}")

    # Step 4: the 2miners baseline identity must be exactly what we expect
    # to revoke; nothing here promotes RavenProject from a stale baseline.
    verify_baseline(previous_body)

    # Steps 5-8: certification identity, PASS status, reportDigest, mandatory tests.
    verify_report_identity(report)

    if revocation.get("repository") != EXPECTED_BASELINE_REPOSITORY or \
            revocation.get("commit") != EXPECTED_BASELINE_COMMIT:
        raise PolicyError("revocation entry does not target the expected 2miners identity")
    for key in ("repository", "commit", "reason"):
        if key not in revocation:
            raise PolicyError(f"revocation is missing {key!r}")

    # Step 9: generate v3 atomically: one new KNOWN_SAFE entry, one revocation,
    # nothing else. entry_from_report derives identity solely from `report`,
    # so a 2miners report can never be substituted to promote RavenProject.
    new_entry = entry_from_report(report)
    if new_entry["status"] != "KNOWN_SAFE":
        raise PolicyError(
            f"RavenProject/Ravencoin certification produced status "
            f"{new_entry['status']!r}, expected KNOWN_SAFE")

    releases = merge_previous(previous_body, [new_entry], [revocation])

    known_safe_repositories = {entry["repository"] for entry in releases
                               if entry["status"] == "KNOWN_SAFE"}
    if known_safe_repositories != {EXPECTED_CANDIDATE_REPOSITORY}:
        raise PolicyError(
            f"unexpected set of KNOWN_SAFE repositories in generated v3: "
            f"{sorted(known_safe_repositories)}")
    for repository in known_safe_repositories:
        if repository not in TRUSTED_RELEASE_REPOSITORIES:
            raise PolicyError(f"{repository!r} is not an approved Core trust source")

    baseline_after = next(
        entry for entry in releases if entry["repository"] == EXPECTED_BASELINE_REPOSITORY)
    if baseline_after["status"] != "REVOKED":
        raise PolicyError(
            f"2miners/Ravencoin is {baseline_after['status']!r} in generated v3, "
            f"expected REVOKED")

    if len(releases) != 2:
        raise PolicyError(
            f"generated v3 contains {len(releases)} release entries, expected exactly 2 "
            f"(one revocation, one promotion); an unexpected third trust source is present")

    body = build_policy(
        policy_version=previous_body["policyVersion"] + 1,
        safety_profile=previous_body["safetyProfile"],
        releases=releases,
    )
    validate_body(body)

    # Steps 10-12: canonical-body comparison against the reviewed candidate.
    # generatedAt/expiresAt are intentionally excluded: they are a function of
    # signing time, not of migration content, and are never trust-relevant.
    compared_fields = ("schemaVersion", "policyVersion", "safetyProfile", "releases")
    mismatches = []
    for field in compared_fields:
        if body.get(field) != reviewed_candidate.get(field):
            mismatches.append(field)
    if mismatches:
        raise PolicyError(
            "regenerated v3 does not match the reviewed unsigned candidate "
            f"safe-core-policy-v3.unsigned.json in field(s): {mismatches}; "
            "refusing to sign a policy that was not independently reproduced")

    return body


def _load_json(path: str) -> dict:
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def _load_public_key_hex(path: str) -> bytes:
    hex_text = pathlib.Path(path).read_text(encoding="utf-8").strip()
    return bytes.fromhex(hex_text)


def main(argv=None) -> int:
    import argparse

    from policy import key_id_for

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--previous-policy", required=True,
                        help="the currently published, signed safe-core-policy v2")
    parser.add_argument("--public-key", required=True,
                        help="pinned Ed25519 public key hex file for the v2 signer")
    parser.add_argument("--report", required=True,
                        help="RavenProject/Ravencoin certification report JSON")
    parser.add_argument("--revoke", required=True,
                        help="JSON object with repository, commit and reason for 2miners")
    parser.add_argument("--candidate", required=True,
                        help="reviewed unsigned v3 candidate to reproduce and match")
    arguments = parser.parse_args(argv)

    previous_document = _load_json(arguments.previous_policy)
    public_bytes = _load_public_key_hex(arguments.public_key)
    trusted_keys = {key_id_for(public_bytes): public_bytes}
    report = _load_json(arguments.report)
    revocation = json.loads(arguments.revoke)
    reviewed_candidate = _load_json(arguments.candidate)

    try:
        body = verify_atomic_migration(
            previous_document=previous_document,
            trusted_keys=trusted_keys,
            report=report,
            revocation=revocation,
            reviewed_candidate=reviewed_candidate,
        )
    except PolicyError as exc:
        print(f"migration verification FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"migration verified: v{previous_document['policy']['policyVersion']} -> "
          f"v{body['policyVersion']}, {len(body['releases'])} release entries, "
          "matches the reviewed unsigned candidate")
    return 0


if __name__ == "__main__":
    sys.exit(main())
