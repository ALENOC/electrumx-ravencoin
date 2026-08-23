#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Build non-secret inputs for a production release candidate.

CI may execute this builder because it never receives a private key and never
publishes a GitHub Release. It produces deterministic release bytes, provenance,
an unsigned manifest-v2 body and an offline-signing handoff. The release/update
private key is intentionally unavailable to this process.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "core-safety" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import build_release_bundle  # noqa: E402
import policy  # noqa: E402
import render_installer_v2  # noqa: E402
import update_manifest  # noqa: E402

VERSION_SOURCE = ROOT / "electrumx" / "__init__.py"
CORE_POLICY = ROOT / "core-safety" / "production" / "safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY = (
    ROOT / "core-safety" / "production" / "core-policy-signing-public-key.hex"
)
CORE_REPOSITORY = "RavenProject/Ravencoin"
CORE_COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"
BUNDLE_NAME = "electrumx-ravencoin-bundle.tar.gz"
INSTALLER_NAME = "electrumx-ravencoin-install.py"
PROVENANCE_NAME = "release-provenance.json"
UNSIGNED_MANIFEST_NAME = "unsigned-release-manifest.json"
SIGNING_INPUTS_NAME = "offline-signing-inputs.json"
RAW_KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class ReleaseBuildError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def electrumx_version() -> str:
    match = re.search(
        r"^version = 'ElectrumX-RVN ([^']+)'$",
        VERSION_SOURCE.read_text(encoding="utf-8"), re.MULTILINE)
    if match is None:
        raise ReleaseBuildError("cannot determine ElectrumX release version")
    return match.group(1)


def verified_policy_entry() -> tuple[dict, dict]:
    public = bytes.fromhex(CORE_POLICY_PUBLIC_KEY.read_text(encoding="ascii").strip())
    document = json.loads(CORE_POLICY.read_text(encoding="utf-8"))
    body = policy.verify_policy(
        document, {policy.key_id_for(public): public}, minimum_policy_version=3)
    matches = [
        entry for entry in body["releases"]
        if entry.get("repository") == CORE_REPOSITORY
        and entry.get("commit") == CORE_COMMIT
        and entry.get("status") == "KNOWN_SAFE"
    ]
    if len(matches) != 1:
        raise ReleaseBuildError(
            "signed policy does not contain exactly one approved release identity")
    if [entry for entry in body["releases"] if entry.get("status") == "KNOWN_SAFE"] != matches:
        raise ReleaseBuildError("signed policy authorizes an unexpected additional Core")
    entry = matches[0]
    if (entry.get("certification") or {}).get("result") != "PASS":
        raise ReleaseBuildError("approved Core entry has no passing certification")
    return body, entry


def parse_revision(value: str) -> int:
    if not re.fullmatch(r"0|[1-9][0-9]*", value or ""):
        raise ReleaseBuildError("artifact revision must be canonical non-negative decimal")
    return int(value)


def parse_timestamp(value: str) -> str:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        stamp = datetime.datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ReleaseBuildError("release timestamp must be ISO-8601") from exc
    if stamp.tzinfo is None:
        raise ReleaseBuildError("release timestamp must include a timezone")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--artifact-revision", required=True)
    parser.add_argument("--release-timestamp", required=True)
    parser.add_argument("--update-public-key-hex", required=True)
    args = parser.parse_args()

    version = electrumx_version()
    if args.tag != f"v{version}":
        raise ReleaseBuildError(
            f"tag {args.tag!r} does not match source version v{version}")
    revision = parse_revision(args.artifact_revision)
    timestamp = parse_timestamp(args.release_timestamp)
    public_key_hex = args.update_public_key_hex.strip().lower()
    if not RAW_KEY_RE.fullmatch(public_key_hex):
        raise ReleaseBuildError("replacement update-signing public key is malformed")
    if public_key_hex == render_installer_v2.RETIRED_UPDATE_PUBLIC_KEY_HEX:
        raise ReleaseBuildError("retired CI-held update-signing key is forbidden")
    public_bytes = bytes.fromhex(public_key_hex)
    public_key_id = update_manifest.key_id_for(public_bytes)
    if public_key_id == render_installer_v2.RETIRED_UPDATE_KEY_ID:
        raise ReleaseBuildError("retired update-signing key id is forbidden")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle_path = output / BUNDLE_NAME
    installer_path = output / INSTALLER_NAME
    provenance_path = output / PROVENANCE_NAME

    policy_body, core_entry = verified_policy_entry()
    repo_head = build_release_bundle.run_git(ROOT, "rev-parse", "HEAD")
    monitor_dir = args.monitor_dir.resolve()
    monitor_head = build_release_bundle.run_git(monitor_dir, "rev-parse", "HEAD")
    pin = build_release_bundle.load_pin()["nodeMonitor"]
    if monitor_head != pin["commit"]:
        raise ReleaseBuildError("Node Monitor checkout does not equal reviewed pin")

    provenance = {
        "schemaVersion": 1,
        "electrumxVersion": version,
        "artifact_revision": revision,
        "releaseTimestamp": timestamp,
        "sourceRepository": "ALENOC/electrumx-ravencoin",
        "sourceCommit": repo_head,
        "nodeMonitor": dict(pin),
        "ravencoinCore": {
            "repository": CORE_REPOSITORY,
            "version": core_entry["version"],
            "tag": core_entry["tag"],
            "commit": CORE_COMMIT,
            "certificationReportDigest": core_entry["reportDigest"],
            "safeCorePolicyVersion": policy_body["policyVersion"],
        },
        "updateSigningPublicKey": public_key_hex,
        "updateSigningKeyId": public_key_id,
        "releaseProcess": {
            "privateKeyInCI": False,
            "ciPublishesRelease": False,
            "offlineSigningRequired": True,
        },
    }
    provenance_bytes = canonical_json_bytes(provenance)
    provenance_path.write_bytes(provenance_bytes)
    provenance_digest = "sha256:" + hashlib.sha256(provenance_bytes).hexdigest()

    # Render once and ship those exact bytes both as the standalone installer
    # asset and as the installer copy inside the source bundle.
    render_installer_v2.render(output=installer_path, public_key_hex=public_key_hex)
    installer_bytes = installer_path.read_bytes()
    bundle_digest, metadata = build_release_bundle.build_bundle(
        monitor_dir=monitor_dir,
        output=bundle_path,
        version=version,
        update_public_key_hex=public_key_hex,
        provenance_bytes=provenance_bytes,
        installer_bytes=installer_bytes,
    )

    manifest_body = update_manifest.build_manifest(
        electrumx_version=version,
        artifact_revision=revision,
        channel="stable",
        artifact_digest="sha256:" + bundle_digest,
        provenance_digest=provenance_digest,
        architecture="linux/amd64,linux/arm64",
        core_version=core_entry["version"],
        core_repository=CORE_REPOSITORY,
        core_tag=core_entry["tag"],
        core_commit=CORE_COMMIT,
        certification_report_digest=core_entry["reportDigest"],
        safe_core_policy_version=policy_body["policyVersion"],
        required_updater_version="2.0.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename=INSTALLER_NAME,
        installer_digest="sha256:" + sha256(installer_path),
        release_timestamp=timestamp,
    )
    unsigned_path = output / UNSIGNED_MANIFEST_NAME
    unsigned_path.write_text(
        json.dumps(manifest_body, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    signing_inputs = {
        "schemaVersion": 1,
        "tag": args.tag,
        "electrumxVersion": version,
        "artifact_revision": revision,
        "expectedPublicKeyHex": public_key_hex,
        "expectedKeyId": public_key_id,
        "retiredKeyIdForbidden": render_installer_v2.RETIRED_UPDATE_KEY_ID,
        "unsignedManifestSha256": sha256(unsigned_path),
        "artifactDigest": manifest_body["artifactDigest"],
        "installerDigest": manifest_body["installerDigest"],
        "provenanceDigest": manifest_body["provenanceDigest"],
    }
    signing_path = output / SIGNING_INPUTS_NAME
    signing_path.write_text(
        json.dumps(signing_inputs, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    checksums = {
        BUNDLE_NAME: sha256(bundle_path),
        INSTALLER_NAME: sha256(installer_path),
        PROVENANCE_NAME: sha256(provenance_path),
        UNSIGNED_MANIFEST_NAME: sha256(unsigned_path),
        SIGNING_INPUTS_NAME: sha256(signing_path),
    }
    (output / "SHA256SUMS.build").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="ascii")

    print(f"version={version}")
    print(f"artifactRevision={revision}")
    print(f"sourceCommit={metadata['sourceCommit']}")
    print(f"nodeMonitorCommit={metadata['nodeMonitor']['commit']}")
    print(f"updateSigningKeyId={public_key_id}")
    print(f"bundleSha256={checksums[BUNDLE_NAME]}")
    print(f"installerSha256={checksums[INSTALLER_NAME]}")
    print(f"provenanceSha256={checksums[PROVENANCE_NAME]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
