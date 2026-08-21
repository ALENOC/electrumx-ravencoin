# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT). See LICENCE for details.

"""electrumx-update: automatically detect and verify releases; install only
on an explicit operator command.

    electrumx-update check
    electrumx-update status
    electrumx-update show
    electrumx-update apply [--approve-consensus-change]

``check`` may fetch and verify metadata and release bytes, but never stops or
changes services. ``apply`` is a separate operator action and re-fetches and
re-verifies the exact tagged signed manifest, the signed safe-Core policy and
the release bundle immediately before any switch. Silence, restart, reboot,
timer expiry, or a previous successful ``check`` are never installation
consent.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import platform
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional, Sequence

from packaging.version import Version

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

UPDATER_VERSION = "1.0.0"
REPOSITORY = "ALENOC/electrumx-ravencoin"
RELEASES_API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases"
BUNDLE_FILENAME = "electrumx-ravencoin-bundle.tar.gz"
MANIFEST_FILENAME = "release-manifest.json"
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

# This is True only because production apply is now wired to the transactional
# release-directory switch and all 12 concrete runtime health gates in
# update_runtime.py. It does not mean unattended install: main() reaches that
# path only for the explicit ``apply`` subcommand.
PRODUCTION_APPLY_READY = True


@dataclasses.dataclass
class ReleaseCandidate:
    """One concrete tagged GitHub Release under consideration."""
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
    """Injected boundary between decision logic and GitHub Releases."""
    list_candidates: Callable[[], Sequence[ReleaseCandidate]]
    reachable: bool = True


def run_check(*, state: UpdateState, source: ReleaseSource, host: HostFacts,
              trusted_keys: dict, safe_core_certified_commits: frozenset,
              auto_update_mode: str, pre_pull: Callable[[dict], None] = None,
              safe_core_certification_digests: Optional[dict] = None,
              verified_core_policy_version: Optional[int] = None) -> UpdateState:
    """Discover and verify candidates. Never installs or stops services."""
    if not source.reachable:
        record_check_result(
            state, pending_candidate=None,
            failure_reason=VerificationVerdict.REFUSED_GITHUB_UNREACHABLE.value)
        return state

    eligible = []
    for candidate in source.list_candidates():
        eligibility = evaluate_eligibility(
            auto_update_mode=auto_update_mode,
            channel=candidate.channel,
            current_version=host.current_electrumx_version,
            candidate_version=candidate.version,
            candidate_is_prerelease=candidate.is_prerelease,
        )
        if eligibility.verdict != EligibilityVerdict.ELIGIBLE:
            continue

        signature_valid = False
        manifest_body = None
        if candidate.signed_manifest_document is not None:
            try:
                manifest_body = verify_manifest(
                    candidate.signed_manifest_document, trusted_keys)
                signature_valid = True
            except ManifestError:
                signature_valid = False

        verification = evaluate_verification(
            manifest=manifest_body,
            signature_valid=signature_valid,
            downloaded_artifact_digest=candidate.artifact_digest,
            host=host,
            safe_core_certified_commits=safe_core_certified_commits,
            safe_core_certification_digests=safe_core_certification_digests,
            verified_core_policy_version=verified_core_policy_version,
        )
        eligible.append((candidate, eligibility, verification, manifest_body))

    if not eligible:
        record_check_result(state, pending_candidate=None,
                            failure_reason="no eligible candidate release found")
        return state

    # Prefer the newest VERIFIED release. An unverifiable newest release must
    # not hide a slightly older release that is still newer than installed and
    # fully verifies. If none verify, retain the newest failure for diagnostics.
    verified = [item for item in eligible
                if item[2].verdict == VerificationVerdict.VERIFIED]
    pool = verified or eligible
    best_candidate, eligibility, verification, manifest_body = max(
        pool, key=lambda item: Version(item[0].version))

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
        # pre_pull receives metadata only. The production CLI deliberately does
        # not install from pre-pulled data; apply re-downloads and re-verifies.
        pre_pull(manifest_body)

    record_check_result(
        state, pending_candidate=pending,
        failure_reason=(None if verification.verdict == VerificationVerdict.VERIFIED
                        else verification.reason or verification.verdict.value))
    return state


def format_status(state: UpdateState) -> str:
    return json.dumps(state.to_dict(), indent=2, sort_keys=True)


def format_show(state: UpdateState) -> str:
    """Identity table the operator sees before ``apply``."""
    candidate = state.pending_candidate
    if not candidate or not candidate.get("manifest"):
        return "no verified pending candidate"
    manifest = candidate["manifest"]
    lines = [
        f"installed ElectrumX version:      {state.current_release.get('electrumxVersion') if state.current_release else '(unknown)'}",
        f"candidate ElectrumX version:      {manifest.get('electrumxVersion')}",
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
    # Discovery may use browser_download_url, but only our own concrete tagged
    # release bundle is allowed into the observed digest field.
    try:
        update_runtime.validate_release_asset_url(
            url, expected_filename=BUNDLE_FILENAME)
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
    """Fetch signed manifest and independently hash its exact bundle asset."""
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
    """Real GitHub Releases wiring used by ``check`` only."""
    if repository != REPOSITORY:
        # Tests may inject ReleaseSource directly. Production discovery never
        # becomes a generic repository updater.
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
                version=tag.lstrip("v"),
                channel=channel,
                is_prerelease=bool(release.get("prerelease")),
                signed_manifest_document=manifest_document,
                artifact_bytes=None,
                artifact_digest=artifact_digest,
                manifest_url=manifest_url,
                artifact_url=artifact_url,
                release_tag=tag,
            ))
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
    """Verify exactly one local signed safe-Core policy (legacy/test helper)."""
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
    """Resolve signed Core trust and monotonically advance the local floor."""
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


def _load_json_manifest_from_tagged_url(url: str) -> dict:
    update_runtime.validate_release_asset_url(
        url, expected_filename=MANIFEST_FILENAME)
    raw = update_runtime.fetch_small_release_asset(url)
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("tagged release manifest is not valid UTF-8 JSON") from exc
    if not isinstance(document, dict):
        raise ManifestError("tagged release manifest is not an object")
    return document


def _revalidate_pending_for_apply(state: UpdateState, resolved_policy,
                                  trusted_keys: dict) -> tuple[dict, str]:
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

    host = _load_current_host_facts(state)
    verification = evaluate_verification(
        manifest=fresh_body,
        signature_valid=True,
        # download_verified_artifact below verifies the exact signed digest;
        # this decision input is therefore that observed verified value.
        downloaded_artifact_digest=fresh_body.get("artifactDigest"),
        host=host,
        safe_core_certified_commits=resolved_policy.commits,
        safe_core_certification_digests=resolved_policy.certification_digests,
        verified_core_policy_version=resolved_policy.version,
    )
    if verification.verdict != VerificationVerdict.VERIFIED:
        raise ManifestError(
            f"candidate no longer verifies under current trust state: "
            f"{verification.verdict.value} {verification.reason}")

    eligibility = evaluate_eligibility(
        auto_update_mode="stable",
        channel=fresh_body.get("channel"),
        current_version=host.current_electrumx_version,
        candidate_version=fresh_body.get("electrumxVersion"),
        candidate_is_prerelease=False,
    )
    if eligibility.verdict != EligibilityVerdict.ELIGIBLE:
        raise ManifestError(
            f"candidate is no longer eligible: {eligibility.verdict.value}")

    pending["manifest"] = fresh_body
    pending["_eligibilityVerdict"] = EligibilityVerdict.ELIGIBLE.value
    pending["_verificationVerdict"] = VerificationVerdict.VERIFIED.value
    pending["_verificationReason"] = ""
    state.pending_candidate = pending
    return fresh_body, artifact_url


def _record_apply_result(state: UpdateState, *, old_version: str,
                         new_version: str, manifest: Optional[dict],
                         result, detail: str) -> None:
    try:
        digest = ("sha256:" + update_manifest.manifest_digest(manifest)) \
            if manifest else ""
        update_audit.record(
            DEFAULT_AUDIT_LOG_PATH,
            initiator="operator-cli",
            action="apply",
            old_version=old_version,
            new_version=new_version,
            manifest_digest=digest,
            result=getattr(result, "value", str(result)),
            detail=detail,
        )
    except OSError as exc:
        # State correctness is primary, but failure to append the local audit
        # trail is still surfaced to the operator.
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
        except (OSError, ValueError, ManifestError, core_policy.PolicyError,
                update_policy.PolicyResolutionError, json.JSONDecodeError) as exc:
            print(f"cannot load updater trust state: {exc}", file=sys.stderr)
            return 1

        host = _load_current_host_facts(state)
        source = github_release_source()
        state = run_check(
            state=state,
            source=source,
            host=host,
            trusted_keys=trusted_keys,
            safe_core_certified_commits=resolved_policy.commits,
            safe_core_certification_digests=resolved_policy.certification_digests,
            verified_core_policy_version=resolved_policy.version,
            auto_update_mode=os.environ.get("ELECTRUMX_UPDATE_CHANNEL", "stable"),
        )
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
            # Persist a newly verified policy floor immediately. Even if a later
            # artifact or runtime check fails, an older signed policy must never
            # be able to restore trust on the next invocation.
            save_state(DEFAULT_STATE_PATH, state)
            manifest, artifact_url = _revalidate_pending_for_apply(
                state, resolved_policy, trusted_keys)

            DEFAULT_STATE_DIR.mkdir(parents=True, mode=0o700, exist_ok=True)
            with tempfile.TemporaryDirectory(
                    prefix=".apply-artifact-", dir=DEFAULT_STATE_DIR) as temporary:
                artifact = update_runtime.download_verified_artifact(
                    artifact_url,
                    expected_digest=manifest["artifactDigest"],
                    directory=Path(temporary),
                )
                # Validate tar structure/trust bindings before preparing any
                # Docker build. TransactionalComposeSwitch repeats this before
                # extraction as defence in depth against accidental mutation.
                update_runtime.validate_bundle_file(artifact, manifest)

                switch = update_runtime.TransactionalComposeSwitch(
                    install_root=DEFAULT_INSTALL_ROOT,
                    artifact_path=artifact,
                    manifest=manifest,
                )
                hooks = update_apply.ApplyHooks(
                    stop_services=switch.stop_services,
                    switch_atomically=switch.switch_atomically,
                    start_services=switch.start_services,
                    run_health_checks=switch.run_health_checks,
                    rollback_to=switch.rollback_to,
                    finalize_success=switch.finalize_success,
                )
                try:
                    result_obj = update_apply.apply_pending_candidate(
                        state, hooks,
                        approve_consensus_change=args.approve_consensus_change,
                    )
                finally:
                    # Removes staging only if activation never happened. A
                    # rollbackSafe=false switched failure deliberately preserves
                    # the live failed release + sibling backup + journal.
                    switch.cleanup_unactivated()

            save_state(DEFAULT_STATE_PATH, state)
            _record_apply_result(
                state, old_version=old_version, new_version=new_version,
                manifest=manifest, result=result_obj.verdict, detail=result_obj.detail)
            print(f"{result_obj.verdict}: {result_obj.detail}")
            return 0 if getattr(result_obj.verdict, "value", "") == "PROMOTE_TO_CURRENT" else 1

        except (OSError, ValueError, ManifestError, core_policy.PolicyError,
                update_policy.PolicyResolutionError,
                update_runtime.UpdateRuntimeError, json.JSONDecodeError) as exc:
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
