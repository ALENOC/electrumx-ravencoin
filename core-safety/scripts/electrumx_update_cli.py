# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""electrumx-update: detect / verify ElectrumX releases automatically;
install only on an explicit operator command.

    electrumx-update check
    electrumx-update status
    electrumx-update show
    electrumx-update apply [--approve-consensus-change]

``check`` never installs anything: it only updates ``pendingCandidate`` after
verifying the release signature, the actual downloaded artifact digest, and
the bundled Core identity against the verified signed safe-Core policy.
``apply`` is a separate operator action. Silence, restart, reboot, or timer
expiry are never interpreted as consent to install.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional, Sequence

from packaging.version import Version

import policy as core_policy
import update_apply
import update_audit
from update_decision import (
    EligibilityVerdict, HealthGateResult, HostFacts, VerificationVerdict,
    evaluate_eligibility, evaluate_verification,
)
from update_manifest import ManifestError, load_trusted_key, verify_manifest
from update_state import (
    UpdateState, effective_core_policy_floor, load_state, record_check_result,
    record_verified_core_policy, save_state,
)

DEFAULT_STATE_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_STATE_PATH", "/var/lib/electrumx-ravencoin/update-state.json")
DEFAULT_AUDIT_LOG_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_AUDIT_LOG_PATH", "/var/lib/electrumx-ravencoin/update-audit.log")
DEFAULT_TRUSTED_KEY_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_PUBLIC_KEY_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "production", "update-signing-public-key.hex"))
DEFAULT_CORE_POLICY_PATH = os.environ.get(
    "ELECTRUMX_CORE_POLICY_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "production", "safe-core-policy.json"))
DEFAULT_CORE_POLICY_KEY_PATH = os.environ.get(
    "ELECTRUMX_CORE_POLICY_PUBLIC_KEY_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "production", "core-policy-signing-public-key.hex"))
RELEASES_API_URL = "https://api.github.com/repos/ALENOC/electrumx-ravencoin/releases"
MAX_UPDATE_ARTIFACT_BYTES = 4 * 1024 * 1024 * 1024

# Production apply is deliberately blocked until the Docker switch and all
# twelve health gates are wired to real probes. Leaving the old placeholder
# path callable would stop/restart a live node even though every health result
# is synthetic False. Decision/apply logic remains unit-testable independently.
PRODUCTION_APPLY_READY = False


@dataclasses.dataclass
class ReleaseCandidate:
    """One concrete, tagged GitHub Release under consideration."""
    version: str
    channel: str
    is_prerelease: bool
    signed_manifest_document: Optional[dict]
    artifact_bytes: Optional[bytes]
    artifact_digest: Optional[str]


@dataclasses.dataclass
class ReleaseSource:
    """Injected boundary between update decision logic and GitHub Releases."""
    list_candidates: Callable[[], Sequence[ReleaseCandidate]]
    reachable: bool = True


def run_check(*, state: UpdateState, source: ReleaseSource, host: HostFacts,
              trusted_keys: dict, safe_core_certified_commits: frozenset,
              auto_update_mode: str, pre_pull: Callable[[dict], None] = None,
              safe_core_certification_digests: Optional[dict] = None) -> UpdateState:
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
        )
        eligible.append((candidate, eligibility, verification, manifest_body))

    if not eligible:
        record_check_result(state, pending_candidate=None,
                            failure_reason="no eligible candidate release found")
        return state

    # Prefer the newest VERIFIED release. An unverifiable newest GitHub release
    # must not hide a slightly older release that is still newer than installed
    # and fully verifies. If none verify, retain the newest failure for operator
    # diagnostics without treating it as installable.
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
    }

    if verification.verdict == VerificationVerdict.VERIFIED and pre_pull is not None:
        pre_pull(manifest_body)

    record_check_result(
        state, pending_candidate=pending,
        failure_reason=(None if verification.verdict == VerificationVerdict.VERIFIED
                        else verification.reason))
    return state


def format_status(state: UpdateState) -> str:
    return json.dumps(state.to_dict(), indent=2, sort_keys=True)


def format_show(state: UpdateState) -> str:
    """The full identity table the operator must see before ``apply``."""
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
        f"update manifest signature status: {candidate.get('_verificationVerdict')}",
        f"architecture compatibility:       {manifest.get('architecture')}",
        f"DB compatibility:                 {manifest.get('dbCompatibility')}",
        f"rollbackSafe:                     {manifest.get('rollbackSafe')}",
        f"consensusImpact:                  {manifest.get('consensusImpact')}",
    ]
    return "\n".join(lines)


def _stream_sha256(url: str, *, timeout_seconds: float) -> Optional[str]:
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
    """Fetch a signed manifest and hash actual release artifact bytes.

    The unverified manifest body is used only as a digest-selection hint. Trust
    is granted later by ``verify_manifest``. We never copy ``artifactDigest``
    from JSON into the observed-digest field; at least one concrete release
    asset must hash to the signed value.
    """
    assets = release.get("assets") or []
    manifest_asset = next(
        (a for a in assets if a.get("name") == "release-manifest.json"), None)
    if manifest_asset is None:
        return None, None, None
    try:
        request = urllib.request.Request(
            manifest_asset["browser_download_url"],
            headers={"User-Agent": "electrumx-update"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            manifest_document = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError):
        return None, None, None

    body = (manifest_document.get("manifest")
            if isinstance(manifest_document, dict) else None)
    expected_digest = body.get("artifactDigest") if isinstance(body, dict) else None
    if not isinstance(expected_digest, str) or not expected_digest.startswith("sha256:"):
        return manifest_document, None, None
    expected_hex = expected_digest[7:]
    if len(expected_hex) != 64 or any(c not in "0123456789abcdef" for c in expected_hex):
        return manifest_document, None, None

    installer_name = body.get("installerFilename") if isinstance(body, dict) else None
    for asset in assets:
        name = asset.get("name")
        url = asset.get("browser_download_url")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        if name == "release-manifest.json" or name == installer_name:
            continue
        try:
            observed = _stream_sha256(url, timeout_seconds=timeout_seconds)
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
        if observed == expected_digest:
            return manifest_document, None, observed
    return manifest_document, None, None


def github_release_source(*, repository: str = "ALENOC/electrumx-ravencoin",
                          channel: str = "stable",
                          timeout_seconds: float = 10.0) -> ReleaseSource:
    """Real GitHub Releases wiring used only by ``check``."""

    def _list_candidates() -> Sequence[ReleaseCandidate]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases",
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
            manifest_document, artifact_bytes, artifact_digest = _fetch_release_assets(
                release, timeout_seconds=timeout_seconds)
            candidates.append(ReleaseCandidate(
                version=str(release.get("tag_name", "")).lstrip("v"),
                channel=channel,
                is_prerelease=bool(release.get("prerelease")),
                signed_manifest_document=manifest_document,
                artifact_bytes=artifact_bytes,
                artifact_digest=artifact_digest,
            ))
        return candidates

    try:
        probe = urllib.request.Request(
            f"https://api.github.com/repos/{repository}",
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
    """Verify the signed safe-Core policy and return active RavenProject trust.

    Historical 2miners entries may remain cryptographically valid evidence but
    are never returned as trusted release identities.
    """
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

    commits = set()
    digests = {}
    for release in body.get("releases", []):
        if release.get("repository") != "RavenProject/Ravencoin":
            continue
        if release.get("status") != "KNOWN_SAFE":
            continue
        certification = release.get("certification") or {}
        if certification.get("result") != "PASS":
            continue
        commit = release.get("commit")
        report_digest = release.get("reportDigest")
        if not commit or not report_digest:
            raise core_policy.PolicyError(
                "KNOWN_SAFE RavenProject release lacks commit/reportDigest")
        commits.add(commit)
        digests[commit] = report_digest
    return frozenset(commits), digests, body["policyVersion"]


def production_apply_hooks(*, compose_files: Sequence[str] = ("compose.yaml",),
                           project_directory: Optional[str] = None) -> "update_apply.ApplyHooks":
    """Docker Compose hooks retained for development/tests.

    The public CLI does not call them while ``PRODUCTION_APPLY_READY`` is false.
    """
    args_prefix = ["docker", "compose"]
    for filename in compose_files:
        args_prefix += ["-f", filename]

    def _run(*extra_args: str) -> None:
        subprocess.run(list(args_prefix) + list(extra_args), check=True,
                       cwd=project_directory)

    def stop_services() -> None:
        _run("stop")

    def switch_atomically(manifest: dict) -> None:
        _run("pull")

    def start_services() -> None:
        _run("up", "-d")

    def run_health_checks(manifest: dict) -> update_apply.HealthGateResult:
        return HealthGateResult(*([False] * 12))

    def rollback_to(previous: Optional[dict]) -> None:
        _run("down")
        if previous is not None:
            _run("up", "-d")

    return update_apply.ApplyHooks(
        stop_services=stop_services, switch_atomically=switch_atomically,
        start_services=start_services, run_health_checks=run_health_checks,
        rollback_to=rollback_to,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="electrumx-update")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("status")
    sub.add_parser("show")
    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--approve-consensus-change", action="store_true")
    return parser


def _load_current_host_facts(state: UpdateState) -> HostFacts:
    current = state.current_release or {}
    return HostFacts(
        architecture=os.environ.get("ELECTRUMX_UPDATE_ARCH", "linux/amd64"),
        installed_updater_version=current.get("requiredUpdaterVersion", "1.0.0"),
        current_electrumx_version=current.get("electrumxVersion", "0.0.0"),
        current_core_commit=current.get("coreCommit", ""),
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
            # load_trusted_key already returns {keyId: raw_public_key}; do not
            # derive a key id from the returned dict a second time.
            trusted_keys = load_trusted_key(DEFAULT_TRUSTED_KEY_PATH)
            minimum_policy = effective_core_policy_floor(
                state, _configured_policy_floor())
            certified_commits, certification_digests, policy_version = \
                load_safe_core_certifications(minimum_policy_version=minimum_policy)
            # Advance the persisted floor only after the policy's signature,
            # schema, expiry and monotonic version checks have all succeeded.
            record_verified_core_policy(state, policy_version)
        except (OSError, ValueError, ManifestError, core_policy.PolicyError,
                json.JSONDecodeError) as exc:
            print(f"cannot load updater trust state: {exc}", file=sys.stderr)
            return 1

        host = _load_current_host_facts(state)
        source = github_release_source()
        state = run_check(
            state=state,
            source=source,
            host=host,
            trusted_keys=trusted_keys,
            safe_core_certified_commits=certified_commits,
            safe_core_certification_digests=certification_digests,
            auto_update_mode=os.environ.get("ELECTRUMX_UPDATE_CHANNEL", "stable"),
        )
        save_state(DEFAULT_STATE_PATH, state)
        print(format_status(state))
        return 0

    if args.command == "apply":
        if not PRODUCTION_APPLY_READY:
            print(
                "production apply is intentionally disabled: the real atomic "
                "Compose switch and all health probes are not yet wired; no "
                "services were stopped or changed",
                file=sys.stderr,
            )
            return 1
        if not state.pending_candidate:
            print("no pending candidate; run 'electrumx-update check' first", file=sys.stderr)
            return 1
        hooks = production_apply_hooks()
        old_version = (state.current_release or {}).get("electrumxVersion", "unknown")
        new_version = state.pending_candidate.get("version", "unknown")
        manifest_digest = (state.pending_candidate.get("manifest") or {}).get(
            "certificationReportDigest", "")
        result = update_apply.apply_pending_candidate(
            state, hooks, approve_consensus_change=args.approve_consensus_change)
        save_state(DEFAULT_STATE_PATH, state)
        update_audit.record(
            DEFAULT_AUDIT_LOG_PATH, initiator="operator-cli", action="apply",
            old_version=old_version, new_version=new_version,
            manifest_digest=manifest_digest,
            result=getattr(result.verdict, "value", str(result.verdict)),
            detail=result.detail,
        )
        print(f"{result.verdict}: {result.detail}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
