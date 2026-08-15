#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""Turn certification reports into a signed safe-Core policy.

Promotion happens here and nowhere else, and only a report whose overall result
is CERTIFICATION_PASSED can produce a KNOWN_SAFE entry.  Everything else is
carried forward as an explicit non-safe status so the policy records why a
release is refused rather than staying silent about it.

The signing key is supplied as a file path or through the POLICY_SIGNING_KEY
environment variable, in raw or base64 form.  It is never written to the policy,
never logged, and never taken from the policy being generated.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from candidate import CandidateState, KNOWN_UNSAFE_VERSIONS  # noqa: E402
from policy import (  # noqa: E402
    PolicyError, build_policy, key_id_for, sign_policy, validate_body, verify_policy,
)

STATUS_FROM_REPORT = {
    CandidateState.CERTIFICATION_PASSED.value: "KNOWN_SAFE",
    CandidateState.CERTIFICATION_FAILED.value: "KNOWN_UNSAFE",
    CandidateState.KNOWN_UNSAFE.value: "KNOWN_UNSAFE",
}


def load_private_key(source: str) -> Ed25519PrivateKey:
    """Load a raw 32 byte Ed25519 private key from a file or base64 string."""
    material = None
    path = pathlib.Path(source)
    if path.exists():
        material = path.read_bytes().strip()
    else:
        material = source.strip().encode()
    if len(material) != 32:
        try:
            material = base64.b64decode(material, validate=True)
        except Exception as exc:  # noqa: BLE001
            raise PolicyError("signing key is neither raw 32 bytes nor base64") from exc
    if len(material) != 32:
        raise PolicyError("signing key must be 32 bytes of Ed25519 private material")
    return Ed25519PrivateKey.from_private_bytes(material)


def entry_from_report(report: dict) -> dict:
    """Build one policy release entry from a certification report."""
    candidate = report["candidate"]
    overall = report["overall"]
    status = STATUS_FROM_REPORT.get(overall)
    if status is None:
        raise PolicyError(
            f"report for {candidate.get('repository')}@{candidate.get('commit')} is "
            f"{overall}; only a passed or failed certification may enter the policy, "
            f"an ambiguous result must be resolved by a human first")
    entry = {
        "repository": candidate["repository"],
        "tag": candidate["tag"],
        "version": candidate["version"],
        "commit": candidate["commit"],
        "status": status,
        "publishedAt": candidate.get("published_at"),
        "reportDigest": report.get("reportDigest"),
        "certification": {
            "profile": report["profile"],
            "harnessVersion": report.get("harnessVersion"),
            "result": "PASS" if status == "KNOWN_SAFE" else "FAIL",
            "finishedAt": report.get("finishedAt"),
        },
    }
    if candidate.get("artifact_sha256"):
        entry["artifactSha256"] = candidate["artifact_sha256"]
    if status == "KNOWN_UNSAFE":
        entry["certification"]["result"] = "FAIL"
    return entry


def known_unsafe_baseline_entries() -> list:
    """The generations that are unsafe by policy, independent of certification."""
    entries = []
    for version in KNOWN_UNSAFE_VERSIONS:
        entries.append({
            "repository": "RavenProject/Ravencoin",
            "tag": f"v{version}",
            "version": version,
            "commit": "0" * 40,
            "status": "KNOWN_UNSAFE",
            "certification": {"profile": "rvn-consensus-2026-08-v1", "result": "FAIL"},
            "note": "generation predates the August 2026 nHeight binding fix; listed "
                    "for diagnostics, and never matched by identity lookup",
        })
    return entries


def merge_previous(previous_body: dict, new_entries: list, revocations: list) -> list:
    """Carry forward existing entries, apply new ones, then apply revocations."""
    merged = {}
    for entry in previous_body.get("releases", []):
        merged[(entry["repository"], entry["commit"])] = dict(entry)
    for entry in new_entries:
        merged[(entry["repository"], entry["commit"])] = entry
    for revocation in revocations:
        key = (revocation["repository"], revocation["commit"])
        existing = merged.get(key, {
            "repository": revocation["repository"],
            "tag": revocation.get("tag", "unknown"),
            "version": revocation.get("version", "0.0.0"),
            "commit": revocation["commit"],
        })
        existing = dict(existing)
        existing["status"] = "REVOKED"
        existing["revocationReason"] = revocation["reason"]
        existing["revokedAt"] = revocation.get("revokedAt")
        existing.pop("certification", None)
        merged[key] = existing
    return sorted(merged.values(), key=lambda item: (item["repository"], item["commit"]))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", action="append", default=[],
                        help="certification report JSON; may be repeated")
    parser.add_argument("--previous-policy", default=None,
                        help="the currently published signed policy, to carry forward")
    parser.add_argument("--revoke", action="append", default=[],
                        help="JSON object with repository, commit and reason")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--signing-key", default=os.environ.get("POLICY_SIGNING_KEY", ""))
    parser.add_argument("--valid-for-days", type=int, default=90)
    parser.add_argument("--include-unsafe-baseline", action="store_true")
    arguments = parser.parse_args(argv)

    previous_body = {"releases": [], "policyVersion": 0}
    if arguments.previous_policy:
        document = json.loads(
            pathlib.Path(arguments.previous_policy).read_text(encoding="utf-8"))
        previous_body = document.get("policy", document)
        validate_body(previous_body)

    new_entries = []
    for report_path in arguments.report:
        report = json.loads(pathlib.Path(report_path).read_text(encoding="utf-8"))
        new_entries.append(entry_from_report(report))
    if arguments.include_unsafe_baseline:
        new_entries.extend(known_unsafe_baseline_entries())

    revocations = [json.loads(item) for item in arguments.revoke]
    for revocation in revocations:
        for key in ("repository", "commit", "reason"):
            if key not in revocation:
                raise PolicyError(f"revocation is missing {key!r}")

    releases = merge_previous(previous_body, new_entries, revocations)
    body = build_policy(
        policy_version=int(previous_body.get("policyVersion", 0)) + 1,
        safety_profile=json.loads(
            pathlib.Path(arguments.profile).read_text(encoding="utf-8"))["profileId"],
        releases=releases,
        valid_for_days=arguments.valid_for_days,
    )

    if not arguments.signing_key:
        unsigned = pathlib.Path(arguments.output).with_suffix(".unsigned.json")
        unsigned.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
        print(f"no signing key supplied; wrote unsigned candidate policy to {unsigned}")
        print("the unsigned document is not usable by a client and must be signed by "
              "the protected signing step")
        return 2

    private_key = load_private_key(arguments.signing_key)
    public_bytes = private_key.public_key().public_bytes_raw()
    document = sign_policy(body, private_key, key_id=key_id_for(public_bytes))

    # Self-check: the artifact we are about to publish must verify against the
    # public half of the key that just signed it.
    verify_policy(document, {key_id_for(public_bytes): public_bytes})

    output = pathlib.Path(arguments.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n",
                      encoding="utf-8")
    print(f"wrote signed policy version {body['policyVersion']} with "
          f"{len(releases)} release entries to {output}")
    print(f"signing key id: {key_id_for(public_bytes)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
