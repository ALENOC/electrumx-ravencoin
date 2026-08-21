#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.

"""Build the non-secret inputs for a production GitHub release.

This script never receives a private key.  It creates the deterministic source
bundle, injects the ceremonied public update key into the standalone installer,
and writes the strictly validated manifest body that a separate protected job
signs.
"""

from __future__ import annotations

import argparse
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
import update_manifest  # noqa: E402

INSTALLER = ROOT / "electrumx-ravencoin-install.py"
VERSION_SOURCE = ROOT / "electrumx" / "__init__.py"
UPDATE_PUBLIC_KEY = (
    ROOT / "core-safety" / "production" / "update-signing-public-key.hex"
)
CORE_POLICY = ROOT / "core-safety" / "production" / "safe-core-policy.json"
CORE_POLICY_PUBLIC_KEY = (
    ROOT / "core-safety" / "production" / "core-policy-signing-public-key.hex"
)
CORE_REPOSITORY = "RavenProject/Ravencoin"
CORE_COMMIT = "22549129888d02e0e08fcdb9f96f3c699167e774"
BUNDLE_NAME = "electrumx-ravencoin-bundle.tar.gz"
INSTALLER_NAME = "electrumx-ravencoin-install.py"
UNSIGNED_MANIFEST_NAME = "unsigned-release-manifest.json"


class ReleaseBuildError(RuntimeError):
    pass


def sha256(path: pathlib.Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    if [entry for entry in body["releases"] if entry.get("status") == "KNOWN_SAFE"] \
            != matches:
        raise ReleaseBuildError("signed policy authorizes an unexpected additional Core")
    entry = matches[0]
    if (entry.get("certification") or {}).get("result") != "PASS":
        raise ReleaseBuildError("approved Core entry has no passing certification")
    return body, entry


def build_installer(output: pathlib.Path, public_key_hex: str) -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    marker = 'RELEASE_PUBLIC_KEY_HEX = ""'
    if source.count(marker) != 1:
        raise ReleaseBuildError("installer update-key injection marker is not unique")
    rendered = source.replace(
        marker, f'RELEASE_PUBLIC_KEY_HEX = "{public_key_hex}"')
    output.write_text(rendered, encoding="utf-8")
    output.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-dir", required=True, type=pathlib.Path)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-timestamp", required=True)
    args = parser.parse_args()

    version = electrumx_version()
    if args.tag != f"v{version}":
        raise ReleaseBuildError(
            f"tag {args.tag!r} does not match source version v{version}")

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    bundle_path = output / BUNDLE_NAME
    installer_path = output / INSTALLER_NAME

    public_key_hex = UPDATE_PUBLIC_KEY.read_text(encoding="ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", public_key_hex):
        raise ReleaseBuildError("production update-signing public key is malformed")

    policy_body, core_entry = verified_policy_entry()
    bundle_digest, metadata = build_release_bundle.build_bundle(
        monitor_dir=args.monitor_dir.resolve(), output=bundle_path, version=version)
    build_installer(installer_path, public_key_hex)

    manifest_body = update_manifest.build_manifest(
        electrumx_version=version,
        channel="stable",
        artifact_digest="sha256:" + bundle_digest,
        architecture="linux/amd64,linux/arm64",
        core_version=core_entry["version"],
        core_repository=CORE_REPOSITORY,
        core_tag=core_entry["tag"],
        core_commit=CORE_COMMIT,
        certification_report_digest=core_entry["reportDigest"],
        safe_core_policy_version=policy_body["policyVersion"],
        required_updater_version="1.0.0",
        config_compatibility={},
        db_compatibility={"schemaVersion": 1},
        rollback_safe=True,
        consensus_impact=False,
        auto_update_eligible=True,
        installer_filename=INSTALLER_NAME,
        installer_digest="sha256:" + sha256(installer_path),
        release_timestamp=args.release_timestamp,
    )
    (output / UNSIGNED_MANIFEST_NAME).write_text(
        json.dumps(manifest_body, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")

    checksums = {
        BUNDLE_NAME: sha256(bundle_path),
        INSTALLER_NAME: sha256(installer_path),
        UNSIGNED_MANIFEST_NAME: sha256(output / UNSIGNED_MANIFEST_NAME),
    }
    (output / "SHA256SUMS.build").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in checksums.items()),
        encoding="ascii")
    print(f"version={version}")
    print(f"sourceCommit={metadata['sourceCommit']}")
    print(f"nodeMonitorCommit={metadata['nodeMonitor']['commit']}")
    print(f"bundleSha256={checksums[BUNDLE_NAME]}")
    print(f"installerSha256={checksums[INSTALLER_NAME]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
