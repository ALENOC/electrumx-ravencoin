# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""electrumx-update: detect and verify releases; install only on explicit apply.

Manifest-v2 candidates are ordered by semantic version and signed
``artifact_revision``. Same-version higher revisions are discoverable; lower
revisions and equal-revision/different-digest equivocation fail closed. The
host-wide anti-rollback state is separate from the install directory and is
advanced only after a successful promotion.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Sequence

from packaging.version import Version

from electrumx_core_safety import artifact_revision
import policy as core_policy
import update_apply
import update_audit
import update_manifest
import update_policy
import update_runtime
from update_decision import (
    EligibilityVerdict, HostFacts, VerificationVerdict,
    evaluate_eligibility, evaluate_verification,
)
from update_manifest import ManifestError, load_trusted_key, verify_manifest
from update_state import (
    UpdateState, effective_core_policy_floor, load_state, record_check_result,
    record_verified_core_policy, save_state,
)

UPDATER_VERSION = "2.0.0"
REPOSITORY = "ALENOC/electrumx-ravencoin"
RELEASES_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
BUNDLE_FILENAME = "electrumx-ravencoin-bundle.tar.gz"
MANIFEST_FILENAME = "release-manifest.json"
PROVENANCE_FILENAME = "release-provenance.json"
MAX_UPDATE_ARTIFACT_BYTES = update_runtime.MAX_ARTIFACT_BYTES

SCRIPT_PATH = Path(__file__).resolve()
DEFAULT_INSTALL_ROOT = Path(os.environ.get(
    "ELECTRUMX_INSTALL_ROOT", str(SCRIPT_PATH.parents[2]))).resolve()
DEFAULT_STATE_DIR = Path(os.environ.get(
    "ELECTRUMX_UPDATE_STATE_DIR",
    str(DEFAULT_INSTALL_ROOT.parent / f".{DEFAULT_INSTALL_ROOT.name}.state"))).resolve()
DEFAULT_STATE_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_STATE_PATH", str(DEFAULT_STATE_DIR / "update-state.json"))
DEFAULT_AUDIT_LOG_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_AUDIT_LOG_PATH", str(DEFAULT_STATE_DIR / "update-audit.log"))
DEFAULT_TRUSTED_KEY_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_PUBLIC_KEY_PATH",
    str(DEFAULT_INSTALL_ROOT / "core-safety" / "production" /
        "update-signing-public-key.hex"))
DEFAULT_CORE_POLICY_PATH = os.environ.get(
    "ELECTRUMX_CORE_POLICY_PATH",
    str(DEFAULT_INSTALL_ROOT / "core-safety" / "production" /
        "safe-core-policy.json"))
DEFAULT_CORE_POLICY_KEY_PATH = os.environ.get(
    "ELECTRUMX_CORE_POLICY_PUBLIC_KEY_PATH",
    str(DEFAULT_INSTALL_ROOT / "core-safety" / "production" /
        "core-policy-signing-public-key.hex"))
DEFAULT_CORE_POLICY_CACHE_PATH = os.environ.get(
    "ELECTRUMX_CORE_POLICY_CACHE_PATH", str(DEFAULT_STATE_DIR / "safe-core-policy.json"))
DEFAULT_CORE_POLICY_URL = os.environ.get(
    "ELECTRUMX_CORE_POLICY_URL", update_policy.DEFAULT_POLICY_URL)
PRODUCTION_APPLY_READY = True


@dataclasses.dataclass
class ReleaseCandidate:
    version: str
    channel: str
    is_prerelease: bool
    signed_manifest_document: Optional[dict]
    artifact_bytes: Optional[bytes]
    artifact_digest: Optional[str]
    manifest_url: Optional[str] = None
    artifact_url: Optional[str] = None
    release_tag: Optional[str] = None


@dataclasses.dataclass
class ReleaseSource:
    list_candidates: Callable[[], Sequence[ReleaseCandidate]]
    reachable: bool = True


def run_check(*, state: UpdateState, source: ReleaseSource, host: HostFacts,
              trusted_keys: dict, safe_core_certified_commits: frozenset,
              auto_update_mode: str, artifact_high_water: dict,
              pre_pull: Callable[[dict], None] = None,
              safe_core_certification_digests: Optional[dict] = None,
              verified_core_policy_version: Optional[int] = None) -> UpdateState:
    """Discover and verify candidates. Never installs or stops services.

    Revision ordering is evaluated only after a schema-v2 manifest has
    authenticated the candidate revision and digests. The host-wide high-water
    is mandatory and is checked before operational HostFacts are consulted.
    """
    if not source.reachable:
        record_check_result(
            state, pending_candidate=None,
            failure_reason=VerificationVerdict.REFUSED_GITHUB_UNREACHABLE.value)
        return state

    considered = []
    refused_reason = None
    for candidate in source.list_candidates():
        manifest_body = None
        if candidate.signed_manifest_document is not None:
            try:
                manifest_body = verify_manifest(
                    candidate.signed_manifest_document, trusted_keys)
            except ManifestError as exc:
                refused_reason = f"manifest verification failed: {exc}"
        if manifest_body is None:
            continue

        if manifest_body.get("electrumxVersion") != candidate.version:
            refused_reason = "release tag version disagrees with signed manifest"
            continue
        try:
            artifact_revision.enforce_high_water(artifact_high_water, manifest_body)
        except artifact_revision.RevisionSecurityError as exc:
            refused_reason = str(exc)
            continue

        eligibility = evaluate_eligibility(
            auto_update_mode=auto_update_mode,
            channel=manifest_body.get("channel"),
            host=host,
            candidate_version=manifest_body.get("electrumxVersion"),
            candidate_is_prerelease=candidate.is_prerelease,
            candidate_revision=manifest_body.get("artifact_revision"),
            candidate_artifact_digest=manifest_body.get("artifactDigest"),
            candidate_provenance_digest=manifest_body.get("provenanceDigest"),
        )
        if eligibility.verdict != EligibilityVerdict.ELIGIBLE:
            refused_reason = eligibility.reason or eligibility.verdict.value
            continue

        verification = evaluate_verification(
            manifest=manifest_body,
            signature_valid=True,
            downloaded_artifact_digest=candidate.artifact_digest,
            host=host,
            safe_core_certified_commits=safe_core_certified_commits,
            safe_core_certification_digests=safe_core_certification_digests,
            verified_core_policy_version=verified_core_policy_version,
        )
        considered.append((candidate, eligibility, verification, manifest_body))

    if not considered:
        record_check_result(
            state, pending_candidate=None,
            failure_reason=refused_reason or "no eligible candidate release found")
        return state

    verified = [item for item in considered
                if item[2].verdict == VerificationVerdict.VERIFIED]
    pool = verified or considered
    best_candidate, eligibility, verification, manifest_body = max(
        pool,
        key=lambda item: (
            Version(item[3]["electrumxVersion"]),
            item[3]["artifact_revision"],
        ))

    pending = {
        "version": best_candidate.version,
        "manifest": manifest_body,
        "_eligibilityVerdict": eligibility.verdict.value,
        "_verificationVerdict": verification.verdict.value,
        "_verificationReason": verification.reason,
        "_manifestUrl": best_candidate.manifest_url,
        "_artifactUrl": best_candidate.artifact_url,
        "_releaseTag": best_candidate.release_tag,
    }
    if verification.verdict == VerificationVerdict.VERIFIED and pre_pull is not None:
        pre_pull(manifest_body)
    record_check_result(
        state, pending_candidate=pending,
        failure_reason=(None if verification.verdict == VerificationVerdict.VERIFIED
                        else verification.reason or verification.verdict.value))
    return state


def format_status(state: UpdateState) -> str:
    return json.dumps(state.to_dict(), indent=2, sort_keys=True)


def format_show(state: UpdateState) -> str:
    candidate = state.pending_candidate
    if not candidate or not candidate.get("manifest"):
        return "no verified pending candidate"
    manifest = candidate["manifest"]
    current = state.current_release or {}
    lines = [
        f"installed ElectrumX version:      {current.get('electrumxVersion', '(unknown)')}",
        f"installed artifact revision:     {current.get('artifact_revision', '(unknown)')}",
        f"candidate ElectrumX version:      {manifest.get('electrumxVersion')}",
        f"candidate artifact revision:      {manifest.get('artifact_revision')}",
        f"candidate artifact digest:        {manifest.get('artifactDigest')}",
        f"candidate provenance digest:      {manifest.get('provenanceDigest')}",
        f"bundled Ravencoin Core version:   {manifest.get('coreVersion')}",
        f"RavenProject tag:                 {manifest.get('coreTag')}",
        f"exact Core commit:                {manifest.get('coreCommit')}",
        f"Core certification report digest: {manifest.get('certificationReportDigest')}",
        f"signed policy version:            {manifest.get('safeCorePolicyVersion')}",
        f"update verification status:       {candidate.get('_verificationVerdict')}",
        f"architecture compatibility:       {manifest.get('architecture')}",
        f"DB compatibility:                 {manifest.get('dbCompatibility')}",
        f"rollbackSafe:                     {manifest.get('rollbackSafe')}",
        f"consensusImpact:                  {manifest.get('consensusImpact')}",
        f"release tag:                      {candidate.get('_releaseTag')}",
    ]
    return "\n".join(lines)


def _stream_sha256(url: str, *, timeout_seconds: float) -> Optional[str]:
    try:
        update_runtime.validate_release_asset_url(url, expected_filename=BUNDLE_FILENAME)
    except update_runtime.UpdateRuntimeError:
        return None
    request = urllib.request.Request(url, headers={"User-Agent": "electrumx-update"})
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        declared = response.headers.get("Content-Length")
        if declared is not None:
            try:
                if int(declared) > MAX_UPDATE_ARTIFACT_BYTES:
                    return None
            except ValueError:
                return None
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_UPDATE_ARTIFACT_BYTES:
                return None
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _fetch_release_assets(release: dict, *, timeout_seconds: float):
    assets = release.get("assets") or []
    manifest_asset = next(
        (asset for asset in assets if asset.get("name") == MANIFEST_FILENAME), None)
    bundle_asset = next(
        (asset for asset in assets if asset.get("name") == BUNDLE_FILENAME), None)
    if manifest_asset is None or bundle_asset is None:
        return None, None, None, None
    manifest_url = manifest_asset.get("browser_download_url")
    artifact_url = bundle_asset.get("browser_download_url")
    if not isinstance(manifest_url, str) or not isinstance(artifact_url, str):
        return None, None, None, None
    try:
        release_tag = update_runtime.validate_release_asset_url(
            manifest_url, expected_filename=MANIFEST_FILENAME)
        bundle_tag = update_runtime.validate_release_asset_url(
            artifact_url, expected_filename=BUNDLE_FILENAME)
        if release_tag != bundle_tag:
            return None, None, None, None
        request = urllib.request.Request(
            manifest_url, headers={"User-Agent": "electrumx-update"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return None, None, None, None
        manifest_document = json.loads(raw.decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
            update_runtime.UpdateRuntimeError):
        return None, None, None, None
    body = manifest_document.get("manifest") if isinstance(manifest_document, dict) else None
    expected_digest = body.get("artifactDigest") if isinstance(body, dict) else None
    if not isinstance(expected_digest, str) or not expected_digest.startswith("sha256:"):
        return manifest_document, None, manifest_url, artifact_url
    expected_hex = expected_digest[7:]
    if len(expected_hex) != 64 or any(c not in "0123456789abcdef" for c in expected_hex):
        return manifest_document, None, manifest_url, artifact_url
    try:
        observed = _stream_sha256(artifact_url, timeout_seconds=timeout_seconds)
    except (urllib.error.URLError, TimeoutError, OSError):
        observed = None
    return manifest_document, observed, manifest_url, artifact_url


def github_release_source(*, repository: str = REPOSITORY,
                          channel: str = "stable",
                          timeout_seconds: float = 10.0) -> ReleaseSource:
    if repository != REPOSITORY:
        return ReleaseSource(list_candidates=lambda: [], reachable=False)

    def _list_candidates() -> Sequence[ReleaseCandidate]:
        request = urllib.request.Request(
            RELEASES_API_URL,
            headers={"Accept": "application/vnd.github+json",
                     "User-Agent": "electrumx-update"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            releases = json.loads(response.read().decode("utf-8"))
        if not isinstance(releases, list):
            raise ValueError("GitHub Releases response is not a list")
        candidates = []
        for release in releases:
            if not isinstance(release, dict) or release.get("draft"):
                continue
            tag = release.get("tag_name")
            if not isinstance(tag, str) or not tag:
                continue
            manifest_document, artifact_digest, manifest_url, artifact_url = \
                _fetch_release_assets(release, timeout_seconds=timeout_seconds)
            candidates.append(ReleaseCandidate(
                version=tag.lstrip("v"), channel=channel,
                is_prerelease=bool(release.get("prerelease")),
                signed_manifest_document=manifest_document,
                artifact_bytes=None, artifact_digest=artifact_digest,
                manifest_url=manifest_url, artifact_url=artifact_url,
                release_tag=tag))
        return candidates

    try:
        probe = urllib.request.Request(
            f"https://api.github.com/repos/{REPOSITORY}",
            headers={"User-Agent": "electrumx-update"})
        with urllib.request.urlopen(probe, timeout=timeout_seconds):
            pass
    except (urllib.error.URLError, TimeoutError, OSError):
        return ReleaseSource(list_candidates=lambda: [], reachable=False)
    return ReleaseSource(list_candidates=_list_candidates, reachable=True)


def load_safe_core_certifications(
        *, policy_path: str = DEFAULT_CORE_POLICY_PATH,
        key_path: str = DEFAULT_CORE_POLICY_KEY_PATH,
        minimum_policy_version: int = 0) -> tuple[frozenset, dict, int]:
    with open(key_path, "r", encoding="ascii") as handle:
        key_hex = handle.read().strip()
    try:
        public_bytes = bytes.fromhex(key_hex)
    except ValueError as exc:
        raise core_policy.PolicyError("Core policy public key is malformed") from exc
    if len(public_bytes) != 32:
        raise core_policy.PolicyError("Core policy public key must be exactly 32 bytes")
    trusted = {core_policy.key_id_for(public_bytes): public_bytes}
    with open(policy_path, "r", encoding="utf-8") as handle:
        document = json.load(handle)
    body = core_policy.verify_policy(
        document, trusted, minimum_policy_version=minimum_policy_version)
    commits, digests = update_policy.extract_ravenproject_certifications(body)
    return commits, digests, body["policyVersion"]


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electrumx-update")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("status")
    sub.add_parser("show")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--approve-consensus-change", action="store_true")
    return parser


def _detected_architecture() -> str:
    override = os.environ.get("ELECTRUMX_UPDATE_ARCH")
    if override:
        return override
    machine = platform.machine().lower()
    if machine in ("x86_64", "amd64"):
        return "linux/amd64"
    if machine in ("aarch64", "arm64"):
        return "linux/arm64"
    return f"linux/{machine}"


def _load_current_host_facts(state: UpdateState) -> HostFacts:
    current = state.current_release or {}
    db = current.get("dbCompatibility") or {}
    schema = db.get("schemaVersion", 1)
    if not isinstance(schema, int) or isinstance(schema, bool) or schema < 1:
        schema = 1
    return HostFacts(
        architecture=_detected_architecture(),
        installed_updater_version=UPDATER_VERSION,
        current_electrumx_version=current.get("electrumxVersion", "0.0.0"),
        current_core_commit=current.get("coreCommit", ""),
        current_db_schema=schema,
        current_artifact_revision=current.get("artifact_revision"),
        current_artifact_digest=current.get("artifactDigest"),
        current_provenance_digest=current.get("provenanceDigest"),
    )


def _configured_policy_floor() -> int:
    raw = os.environ.get("ELECTRUMX_MIN_CORE_POLICY_VERSION", "0")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("ELECTRUMX_MIN_CORE_POLICY_VERSION must be an integer") from exc
    if value < 0:
        raise ValueError("ELECTRUMX_MIN_CORE_POLICY_VERSION cannot be negative")
    return value


def resolve_production_core_policy(
        state: UpdateState, *, configured_floor: Optional[int] = None,
        resolver: Callable[..., update_policy.ResolvedPolicy] =
        update_policy.resolve_safe_core_policy) -> update_policy.ResolvedPolicy:
    configured = (_configured_policy_floor()
                  if configured_floor is None else configured_floor)
    floor = effective_core_policy_floor(state, configured)
    resolved = resolver(
        bundled_path=DEFAULT_CORE_POLICY_PATH,
        cache_path=DEFAULT_CORE_POLICY_CACHE_PATH,
        key_path=DEFAULT_CORE_POLICY_KEY_PATH,
        minimum_policy_version=floor,
        remote_url=DEFAULT_CORE_POLICY_URL,
    )
    record_verified_core_policy(state, resolved.version)
    return resolved


def _resolve_high_water() -> tuple[Path, dict]:
    path = artifact_revision.resolve_host_high_water_path(
        provision_root_locator=False)
    return path, artifact_revision.load_high_water(path)


def _load_json_manifest_from_tagged_url(url: str) -> dict:
    update_runtime.validate_release_asset_url(url, expected_filename=MANIFEST_FILENAME)
    raw = update_runtime.fetch_small_release_asset(url)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("tagged release manifest is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ManifestError("tagged release manifest is not an object")
    return document


def _revalidate_pending_for_apply(state: UpdateState, resolved_policy,
                                  trusted_keys: dict, high_water: dict) -> tuple[dict, str]:
    pending = state.pending_candidate or {}
    if pending.get("_verificationVerdict") != VerificationVerdict.VERIFIED.value:
        raise ManifestError("pending candidate was not VERIFIED by check")
    manifest_url = pending.get("_manifestUrl")
    artifact_url = pending.get("_artifactUrl")
    release_tag = pending.get("_releaseTag")
    if not all(isinstance(item, str) and item
               for item in (manifest_url, artifact_url, release_tag)):
        raise ManifestError(
            "pending candidate predates exact tagged-asset binding; run check again")
    manifest_tag = update_runtime.validate_release_asset_url(
        manifest_url, expected_filename=MANIFEST_FILENAME)
    artifact_tag = update_runtime.validate_release_asset_url(
        artifact_url, expected_filename=BUNDLE_FILENAME)
    if manifest_tag != release_tag or artifact_tag != release_tag:
        raise ManifestError("pending tagged release URLs disagree with release identity")

    fresh_document = _load_json_manifest_from_tagged_url(manifest_url)
    fresh_body = verify_manifest(fresh_document, trusted_keys)
    previous_body = pending.get("manifest")
    if fresh_body != previous_body:
        raise ManifestError(
            "signed tagged manifest changed since check; run electrumx-update check again")
    if fresh_body.get("electrumxVersion") != pending.get("version"):
        raise ManifestError("pending release version disagrees with signed manifest")
    artifact_revision.enforce_high_water(high_water, fresh_body)

    host = _load_current_host_facts(state)
    eligibility = evaluate_eligibility(
        auto_update_mode="stable",
        channel=fresh_body.get("channel"),
        host=host,
        candidate_version=fresh_body.get("electrumxVersion"),
        candidate_is_prerelease=False,
        candidate_revision=fresh_body.get("artifact_revision"),
        candidate_artifact_digest=fresh_body.get("artifactDigest"),
        candidate_provenance_digest=fresh_body.get("provenanceDigest"),
    )
    if eligibility.verdict != EligibilityVerdict.ELIGIBLE:
        raise ManifestError(
            f"candidate is no longer eligible: {eligibility.verdict.value} {eligibility.reason}")

    verification = evaluate_verification(
        manifest=fresh_body, signature_valid=True,
        downloaded_artifact_digest=fresh_body.get("artifactDigest"),
        host=host, safe_core_certified_commits=resolved_policy.commits,
        safe_core_certification_digests=resolved_policy.certification_digests,
        verified_core_policy_version=resolved_policy.version,
    )
    if verification.verdict != VerificationVerdict.VERIFIED:
        raise ManifestError(
            f"candidate no longer verifies: {verification.verdict.value} {verification.reason}")

    pending["manifest"] = fresh_body
    pending["_eligibilityVerdict"] = EligibilityVerdict.ELIGIBLE.value
    pending["_verificationVerdict"] = VerificationVerdict.VERIFIED.value
    pending["_verificationReason"] = ""
    state.pending_candidate = pending
    return fresh_body, artifact_url


def _verify_bundle_provenance(path: Path, manifest: dict) -> None:
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            matches = [member for member in archive.getmembers()
                       if member.name == PROVENANCE_FILENAME]
            if len(matches) != 1 or not matches[0].isfile() or matches[0].size > 256 * 1024:
                raise ManifestError("release provenance member is missing, duplicate or unsafe")
            handle = archive.extractfile(matches[0])
            if handle is None:
                raise ManifestError("cannot read release provenance member")
            observed = "sha256:" + hashlib.sha256(handle.read()).hexdigest()
    except tarfile.TarError as exc:
        raise ManifestError("cannot inspect release provenance") from exc
    if observed != manifest.get("provenanceDigest"):
        raise ManifestError("release provenance digest differs from signed manifest")


def _record_apply_result(state: UpdateState, *, old_version: str,
                         new_version: str, manifest: Optional[dict],
                         result, detail: str) -> None:
    try:
        digest = ("sha256:" + update_manifest.manifest_digest(manifest)) \
            if manifest else ""
        update_audit.record(
            DEFAULT_AUDIT_LOG_PATH, initiator="operator-cli", action="apply",
            old_version=old_version, new_version=new_version,
            manifest_digest=digest,
            result=getattr(result, "value", str(result)), detail=detail)
    except OSError as exc:
        print(f"warning: could not append updater audit log: {exc}", file=sys.stderr)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        state = load_state(DEFAULT_STATE_PATH)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"cannot load updater state from {DEFAULT_STATE_PATH}: {exc}", file=sys.stderr)
        return 1

    if args.command == "status":
        print(format_status(state))
        return 0
    if args.command == "show":
        print(format_show(state))
        return 0

    if args.command == "check":
        try:
            trusted_keys = load_trusted_key(DEFAULT_TRUSTED_KEY_PATH)
            resolved_policy = resolve_production_core_policy(state)
            _high_water_path, high_water = _resolve_high_water()
        except (OSError, ValueError, ManifestError, core_policy.PolicyError,
                update_policy.PolicyResolutionError,
                artifact_revision.RevisionSecurityError,
                json.JSONDecodeError) as exc:
            print(f"cannot load updater trust state: {exc}", file=sys.stderr)
            return 1
        host = _load_current_host_facts(state)
        state = run_check(
            state=state, source=github_release_source(), host=host,
            trusted_keys=trusted_keys,
            safe_core_certified_commits=resolved_policy.commits,
            safe_core_certification_digests=resolved_policy.certification_digests,
            verified_core_policy_version=resolved_policy.version,
            artifact_high_water=high_water,
            auto_update_mode=os.environ.get("ELECTRUMX_UPDATE_CHANNEL", "stable"))
        try:
            save_state(DEFAULT_STATE_PATH, state)
        except OSError as exc:
            print(f"cannot persist updater state: {exc}", file=sys.stderr)
            return 1
        print(format_status(state))
        return 0

    if args.command == "apply":
        if not PRODUCTION_APPLY_READY:
            print("production apply is disabled", file=sys.stderr)
            return 1
        if not state.pending_candidate:
            print("no pending candidate; run 'electrumx-update check' first", file=sys.stderr)
            return 1

        old_version = (state.current_release or {}).get("electrumxVersion", "unknown")
        new_version = state.pending_candidate.get("version", "unknown")
        manifest = state.pending_candidate.get("manifest")
        result_obj = None
        try:
            trusted_keys = load_trusted_key(DEFAULT_TRUSTED_KEY_PATH)
            resolved_policy = resolve_production_core_policy(state)
            high_water_path, high_water = _resolve_high_water()
            save_state(DEFAULT_STATE_PATH, state)
            manifest, artifact_url = _revalidate_pending_for_apply(
                state, resolved_policy, trusted_keys, high_water)

            DEFAULT_STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
            with tempfile.TemporaryDirectory(
                    prefix=".apply-artifact-", dir=DEFAULT_STATE_DIR) as temporary:
                artifact = update_runtime.download_verified_artifact(
                    artifact_url, expected_digest=manifest["artifactDigest"],
                    directory=Path(temporary))
                _verify_bundle_provenance(artifact, manifest)
                update_runtime.validate_bundle_file(artifact, manifest)
                switch = update_runtime.TransactionalComposeSwitch(
                    install_root=DEFAULT_INSTALL_ROOT,
                    artifact_path=artifact, manifest=manifest)
                hooks = update_apply.ApplyHooks(
                    stop_services=switch.stop_services,
                    switch_atomically=switch.switch_atomically,
                    start_services=switch.start_services,
                    run_health_checks=switch.run_health_checks,
                    rollback_to=switch.rollback_to,
                    finalize_success=switch.finalize_success)
                try:
                    result_obj = update_apply.apply_pending_candidate(
                        state, hooks,
                        approve_consensus_change=args.approve_consensus_change)
                finally:
                    switch.cleanup_unactivated()

            promoted = getattr(result_obj.verdict, "value", "") == "PROMOTE_TO_CURRENT"
            if promoted:
                # Runtime/no-op promotion has succeeded. Advance the host-wide
                # floor before saving operational state; failure is fail-closed.
                artifact_revision.advance_high_water(high_water_path, manifest)
            save_state(DEFAULT_STATE_PATH, state)
            if promoted and hooks.finalize_success is not None and \
                    old_version != new_version:
                hooks.finalize_success()
            _record_apply_result(
                state, old_version=old_version, new_version=new_version,
                manifest=manifest, result=result_obj.verdict, detail=result_obj.detail)
            print(f"{result_obj.verdict}: {result_obj.detail}")
            return 0 if promoted else 1

        except (OSError, ValueError, ManifestError, core_policy.PolicyError,
                update_policy.PolicyResolutionError,
                update_runtime.UpdateRuntimeError,
                artifact_revision.RevisionSecurityError,
                json.JSONDecodeError) as exc:
            detail = f"apply refused before promotion: {type(exc).__name__}: {exc}"
            try:
                save_state(DEFAULT_STATE_PATH, state)
            except OSError:
                pass
            _record_apply_result(
                state, old_version=old_version, new_version=new_version,
                manifest=manifest, result="REFUSED", detail=detail)
            print(detail, file=sys.stderr)
            return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
