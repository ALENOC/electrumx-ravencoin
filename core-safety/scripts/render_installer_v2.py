#!/usr/bin/env python3
# Copyright (c) 2026, the ElectrumX-RVN community maintainers
# The MIT License (MIT). See LICENCE for details.
"""Render the standalone 1.13.9 installer from the reviewed source template.

The checked-in installer remains the historical v1-schema bootstrap template so
1.13.1 behavior is explicit and reviewable. Release 1.13.9 is rendered with
strict, cardinality-checked transformations plus the audited host high-water
module. Any drift in an expected source marker fails the release build.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
INSTALLER = ROOT / "electrumx-ravencoin-install.py"
REVISION_MODULE = (
    ROOT / "core-safety" / "scripts" / "electrumx_core_safety" /
    "artifact_revision.py"
)
RETIRED_UPDATE_PUBLIC_KEY_HEX = (
    "4dbeb6131495015b1c44d2d61f80d527217623e1b12dee8f34664509ee3d2b35"
)
RETIRED_UPDATE_KEY_ID = "288e85d43f792f83"
KEY_RE = re.compile(r"^[0-9a-f]{64}$")


class RenderError(RuntimeError):
    pass


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RenderError(f"{label} marker cardinality is {count}, expected exactly one")
    return source.replace(old, new, 1)


def _embedded_revision_block() -> str:
    encoded = base64.b64encode(REVISION_MODULE.read_bytes()).decode("ascii")
    return f'''\n# --- BEGIN generated 1.13.9 revision/high-water extension ---\n_REVISION_MODULE_B64 = "{encoded}"\n_REVISION_MODULE_NAME = "_electrumx_installer_artifact_revision"\n_REVISION_MODULE = type(sys)(_REVISION_MODULE_NAME)\n_REVISION_MODULE.__file__ = "<artifact_revision>"\nsys.modules[_REVISION_MODULE_NAME] = _REVISION_MODULE\ntry:\n    exec(\n        compile(base64.b64decode(_REVISION_MODULE_B64), "<artifact_revision>", "exec"),\n        _REVISION_MODULE.__dict__,\n    )\nexcept BaseException:\n    sys.modules.pop(_REVISION_MODULE_NAME, None)\n    raise\n\n# Export only the narrowly required high-water API. The embedded module has its\n# own constants/imports (including SHA256_RE); none may overwrite installer\n# globals or alter validation behavior inherited from the reviewed template.\nRevisionSecurityError = _REVISION_MODULE.RevisionSecurityError\nresolve_host_high_water_path = _REVISION_MODULE.resolve_host_high_water_path\nload_high_water = _REVISION_MODULE.load_high_water\nenforce_high_water = _REVISION_MODULE.enforce_high_water\nadvance_high_water = _REVISION_MODULE.advance_high_water\n\nREQUIRED_BUNDLE_PATHS = frozenset(set(REQUIRED_BUNDLE_PATHS) | {{\n    "release-provenance.json",\n    "contrib/bootstrap/chainstrap_bootstrap.py",\n    "contrib/bootstrap/chainstrap_runtime.py",\n    "docker/bootstrap/Dockerfile",\n}})\n_V2_HIGH_WATER_PATH = None\n_v1_fetch_and_verify_bundle = fetch_and_verify_bundle\n\ndef fetch_and_verify_bundle(body: dict, *, fetch=None, public_key_hex=RELEASE_PUBLIC_KEY_HEX,\n                            core_policy_public_key_hex=PRODUCTION_CORE_POLICY_PUBLIC_KEY_HEX):\n    bundle, metadata = _v1_fetch_and_verify_bundle(\n        body, fetch=fetch, public_key_hex=public_key_hex,\n        core_policy_public_key_hex=core_policy_public_key_hex)\n    try:\n        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:gz") as archive:\n            matches = [member for member in archive.getmembers()\n                       if member.name == "release-provenance.json"]\n            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 256 * 1024:\n                raise InstallError("release provenance member is missing, duplicate or unsafe")\n            handle = archive.extractfile(matches[0])\n            if handle is None:\n                raise InstallError("cannot read release provenance member")\n            provenance = handle.read()\n    except tarfile.TarError as exc:\n        raise InstallError("cannot inspect release provenance") from exc\n    observed = "sha256:" + hashlib.sha256(provenance).hexdigest()\n    if observed != body.get("provenanceDigest"):\n        raise InstallError("release provenance digest does not match signed manifest")\n    return bundle, metadata\n\n_v1_install_fresh = install_fresh\n\ndef install_fresh(target: Path, data: bytes, *, body: dict, metadata: dict,\n                  bootstrap: str, monitor: bool, controller: bool,\n                  storage_root: Path) -> None:\n    global _V2_HIGH_WATER_PATH\n    try:\n        path = resolve_host_high_water_path(provision_root_locator=True)\n        enforce_high_water(load_high_water(path), body)\n    except RevisionSecurityError as exc:\n        raise InstallError(f"host anti-rollback preflight failed: {{exc}}") from exc\n    _V2_HIGH_WATER_PATH = path\n    try:\n        return _v1_install_fresh(\n            target, data, body=body, metadata=metadata, bootstrap=bootstrap,\n            monitor=monitor, controller=controller, storage_root=storage_root)\n    finally:\n        _V2_HIGH_WATER_PATH = None\n\ndef write_initial_update_state(target: Path, body: dict) -> None:\n    state_dir = state_dir_for(target)\n    path = state_dir / "update-state.json"\n    if path.exists():\n        raise InstallError(f"refusing to overwrite existing updater state {{path}}")\n    _private_atomic_json(path, {{\n        "schemaVersion": 3,\n        "currentRelease": body,\n        "lastKnownGoodRelease": None,\n        "pendingCandidate": None,\n        "updateTimestamp": datetime.datetime.now(datetime.timezone.utc)\n            .replace(microsecond=0).isoformat(),\n        "failureReason": None,\n        "minimumCorePolicyVersion": body["safeCorePolicyVersion"],\n    }})\n    if _V2_HIGH_WATER_PATH is None:\n        raise InstallError("host anti-rollback path was not established")\n    try:\n        advance_high_water(_V2_HIGH_WATER_PATH, body)\n    except RevisionSecurityError as exc:\n        raise InstallError(f"cannot advance host anti-rollback state: {{exc}}") from exc\n\n# --- END generated 1.13.9 revision/high-water extension ---\n'''


def render(*, output: pathlib.Path, public_key_hex: str) -> None:
    public_key_hex = public_key_hex.strip().lower()
    if not KEY_RE.fullmatch(public_key_hex):
        raise RenderError("replacement update public key is malformed")
    if public_key_hex == RETIRED_UPDATE_PUBLIC_KEY_HEX:
        raise RenderError("retired CI-held update key is forbidden for 1.13.9")
    if hashlib.sha256(bytes.fromhex(public_key_hex)).hexdigest()[:16] == RETIRED_UPDATE_KEY_ID:
        raise RenderError("retired update key id is forbidden for 1.13.9")

    source = INSTALLER.read_text(encoding="utf-8")
    source = _replace_once(
        source, 'VERSION = "0.4.0"', 'VERSION = "0.5.0"', "installer version")
    source = _replace_once(
        source,
        'SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v1\\x00"',
        'SIGNATURE_DOMAIN = b"ALENOC-RVN-ELECTRUMX-UPDATE-MANIFEST-v2\\x00"',
        "manifest signature domain")
    source = _replace_once(
        source,
        '    "electrumxVersion", "channel", "releaseTimestamp", "artifactDigest",\n',
        '    "electrumxVersion", "artifact_revision", "channel", "releaseTimestamp", "artifactDigest",\n'
        '    "provenanceDigest",\n',
        "manifest field list")
    source = _replace_once(
        source,
        '    if not isinstance(body, dict) or body.get("schemaVersion") != 1:\n'
        '        raise InstallError("unsupported release manifest body/schema")\n',
        '    if not isinstance(body, dict) or body.get("schemaVersion") != 2:\n'
        '        raise InstallError("unsupported release manifest body/schema")\n',
        "release manifest schema")
    source = _replace_once(
        source,
        '    if not SHA256_RE.fullmatch(str(body["installerDigest"])):\n'
        '        raise InstallError("installerDigest is malformed")\n',
        '    if not SHA256_RE.fullmatch(str(body["installerDigest"])):\n'
        '        raise InstallError("installerDigest is malformed")\n'
        '    if not SHA256_RE.fullmatch(str(body["provenanceDigest"])):\n'
        '        raise InstallError("provenanceDigest is malformed")\n'
        '    revision = body.get("artifact_revision")\n'
        '    if not isinstance(revision, int) or isinstance(revision, bool) or revision < 0:\n'
        '        raise InstallError("artifact_revision must be a non-negative integer")\n',
        "revision/provenance validation")
    source = _replace_once(
        source, 'RELEASE_PUBLIC_KEY_HEX = ""',
        f'RELEASE_PUBLIC_KEY_HEX = "{public_key_hex}"', "public key injection")
    source = _replace_once(
        source, '"alenoc/electrumx-ravencoin:1.13.1", "-ec",',
        '"alenoc/electrumx-ravencoin:1.13.9", "-ec",', "release image version")
    source = _replace_once(
        source, '\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
        _embedded_revision_block() +
        '\nif __name__ == "__main__":\n    raise SystemExit(main())\n',
        "main extension insertion")

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(source, encoding="utf-8")
    output.chmod(0o755)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=pathlib.Path)
    parser.add_argument("--public-key-hex", required=True)
    args = parser.parse_args()
    render(output=args.output.resolve(), public_key_hex=args.public_key_hex)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
