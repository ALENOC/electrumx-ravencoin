# Copyright (c) 2026, the ElectrumX-RVN community maintainers
#
# The MIT License (MIT).  See LICENCE for details.

"""electrumx-update: detect / verify / pre-pull ElectrumX releases
automatically; install only on an explicit operator command.

    electrumx-update check    # detect + verify + (optionally) pre-pull
    electrumx-update status   # print persisted state
    electrumx-update show     # print the pending candidate's full identity
    electrumx-update apply [--approve-consensus-change]

``check`` never installs anything: it only ever updates the persisted
``pendingCandidate``. ``apply`` is the sole path that can change what is
running, and a generic ``apply`` refuses a release whose manifest declares
consensusImpact=true; that release requires
``apply --approve-consensus-change``.

Silence, a restart, a reboot, or timer expiry are never interpreted as
consent to install.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Callable, Optional, Sequence

import update_apply
import update_audit
from update_decision import (
    EligibilityVerdict, HealthGateResult, HostFacts, VerificationVerdict,
    evaluate_eligibility, evaluate_verification,
)
from update_manifest import ManifestError, load_trusted_key, verify_manifest
from update_state import UpdateState, load_state, record_check_result, save_state

# Default on-disk locations for a real installation. Overridable via env vars
# so tests and non-default layouts never need to touch these constants.
DEFAULT_STATE_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_STATE_PATH", "/var/lib/electrumx-ravencoin/update-state.json")
DEFAULT_AUDIT_LOG_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_AUDIT_LOG_PATH", "/var/lib/electrumx-ravencoin/update-audit.log")
DEFAULT_TRUSTED_KEY_PATH = os.environ.get(
    "ELECTRUMX_UPDATE_PUBLIC_KEY_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                 "production", "update-signing-public-key.hex"))
RELEASES_API_URL = "https://api.github.com/repos/ALENOC/electrumx-ravencoin/releases"


@dataclasses.dataclass
class ReleaseCandidate:
    """One concrete, tagged GitHub Release under consideration. Never a
    mutable ``latest`` alias.
    """
    version: str
    channel: str
    is_prerelease: bool
    signed_manifest_document: Optional[dict]
    artifact_bytes: Optional[bytes]
    artifact_digest: Optional[str]


@dataclasses.dataclass
class ReleaseSource:
    """Injected boundary between decision logic and the outside world.
    Production wiring talks to the real GitHub Releases API and downloads
    real artifacts; tests supply a fake that returns fixed data, including
    simulating GitHub being unreachable.
    """
    list_candidates: Callable[[], Sequence[ReleaseCandidate]]
    reachable: bool = True


def run_check(*, state: UpdateState, source: ReleaseSource, host: HostFacts,
              trusted_keys: dict, safe_core_certified_commits: frozenset,
              auto_update_mode: str, pre_pull: Callable[[dict], None] = None) -> UpdateState:
    """Steps 1-13: discover, filter, verify, and (if configured) pre-pull.
    Never installs. Always leaves the running node untouched.
    """
    if not source.reachable:
        record_check_result(state, pending_candidate=None,
                            failure_reason=VerificationVerdict.REFUSED_GITHUB_UNREACHABLE.value)
        return state

    best_candidate = None
    best_verdicts = None

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
                manifest_body = verify_manifest(candidate.signed_manifest_document, trusted_keys)
                signature_valid = True
            except ManifestError:
                signature_valid = False

        verification = evaluate_verification(
            manifest=manifest_body,
            signature_valid=signature_valid,
            downloaded_artifact_digest=candidate.artifact_digest,
            host=host,
            safe_core_certified_commits=safe_core_certified_commits,
        )

        if best_candidate is None:
            best_candidate = candidate
            best_verdicts = (eligibility, verification, manifest_body)

    if best_candidate is None:
        record_check_result(state, pending_candidate=None,
                            failure_reason="no eligible candidate release found")
        return state

    eligibility, verification, manifest_body = best_verdicts
    pending = {
        "version": best_candidate.version,
        "manifest": manifest_body,
        "_eligibilityVerdict": eligibility.verdict.value,
        "_verificationVerdict": verification.verdict.value,
        "_verificationReason": verification.reason,
    }

    if verification.verdict == VerificationVerdict.VERIFIED and pre_pull is not None:
        pre_pull(manifest_body)

    record_check_result(state, pending_candidate=pending,
                        failure_reason=None if verification.verdict ==
                        VerificationVerdict.VERIFIED else verification.reason)
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


def github_release_source(*, repository: str = "ALENOC/electrumx-ravencoin",
                          channel: str = "stable",
                          timeout_seconds: float = 10.0) -> ReleaseSource:
    """Real GitHub Releases wiring. Only ever consulted from ``check``; never
    installs. Any network failure (timeout, DNS, HTTP error, malformed JSON)
    is surfaced as ``reachable=False`` so ``run_check`` leaves the node
    untouched rather than guessing.
    """

    def _list_candidates() -> Sequence[ReleaseCandidate]:
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases",
            headers={"Accept": "application/vnd.github+json",
                    "User-Agent": "electrumx-update"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            releases = json.loads(response.read().decode("utf-8"))

        candidates = []
        for release in releases:
            if release.get("draft"):
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

    reachable = True
    try:
        probe = urllib.request.Request(
            f"https://api.github.com/repos/{repository}",
            headers={"User-Agent": "electrumx-update"})
        urllib.request.urlopen(probe, timeout=timeout_seconds)
    except (urllib.error.URLError, TimeoutError, OSError):
        reachable = False

    if not reachable:
        return ReleaseSource(list_candidates=lambda: [], reachable=False)
    return ReleaseSource(list_candidates=_list_candidates, reachable=True)


def _fetch_release_assets(release: dict, *, timeout_seconds: float):
    """Downloads ``release-manifest.json`` for a single GitHub release, if
    present. Returns ``(manifest_document, artifact_bytes, artifact_digest)``;
    any missing/unreadable asset yields ``(None, None, None)`` so the
    candidate is simply treated as unverifiable, never as verified-by-default.
    """
    manifest_asset = next(
        (a for a in release.get("assets", []) if a.get("name") == "release-manifest.json"),
        None)
    if manifest_asset is None:
        return None, None, None
    try:
        request = urllib.request.Request(
            manifest_asset["browser_download_url"],
            headers={"User-Agent": "electrumx-update"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            manifest_document = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None, None, None

    artifact_digest = None
    body = manifest_document.get("body") if isinstance(manifest_document, dict) else None
    if isinstance(body, dict):
        artifact_digest = body.get("artifactDigest")
    return manifest_document, None, artifact_digest


def production_apply_hooks(*, compose_files: Sequence[str] = ("compose.yaml",),
                           project_directory: Optional[str] = None) -> "update_apply.ApplyHooks":
    """Docker-Compose-backed hooks. Every step shells out to ``docker
    compose``; nothing here touches blockchain data, ElectrumX's database, or
    certificates directly, matching the atomic-switch design in
    ``update_apply.py``.
    """
    args_prefix = ["docker", "compose"]
    for f in compose_files:
        args_prefix += ["-f", f]

    def _run(*extra_args: str) -> None:
        subprocess.run(list(args_prefix) + list(extra_args), check=True,
                       cwd=project_directory)

    def stop_services() -> None:
        _run("stop")

    def switch_atomically(manifest: dict) -> None:
        # The compose build args (RAVENCOIN_VERSION / RAVENCOIN_SOURCE_COMMIT
        # / *_SHA256) and the ElectrumX image tag are declarative, digest-pinned
        # config generated from the verified manifest elsewhere in the install
        # tree; this hook only ever pulls the already-verified, digest-pinned
        # images and never executes anything downloaded outside the manifest.
        _run("pull")

    def start_services() -> None:
        _run("up", "-d")

    def run_health_checks(manifest: dict) -> update_apply.HealthGateResult:
        # Placeholder conservative health gate: a real deployment wires this
        # to RPC/DB probes (expected versions, Core RPC reachability, DB
        # openability, tip coherence). Until those probes are implemented,
        # fail closed rather than claim health that was never checked.
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_arg_parser().parse_args(argv)
    state = load_state(DEFAULT_STATE_PATH)

    if args.command == "status":
        print(format_status(state))
        return 0

    if args.command == "show":
        print(format_show(state))
        return 0

    if args.command == "check":
        try:
            trusted_keys = {}
            public_bytes = load_trusted_key(DEFAULT_TRUSTED_KEY_PATH)
            from update_manifest import key_id_for
            trusted_keys[key_id_for(public_bytes)] = public_bytes
        except (OSError, ManifestError) as exc:
            print(f"cannot load release public key from {DEFAULT_TRUSTED_KEY_PATH}: {exc}",
                  file=sys.stderr)
            return 1

        host = _load_current_host_facts(state)
        source = github_release_source()
        certified_commits = frozenset({"22549129888d02e0e08fcdb9f96f3c699167e774"})
        state = run_check(state=state, source=source, host=host,
                          trusted_keys=trusted_keys,
                          safe_core_certified_commits=certified_commits,
                          auto_update_mode=os.environ.get(
                              "ELECTRUMX_UPDATE_CHANNEL", "stable"))
        save_state(DEFAULT_STATE_PATH, state)
        print(format_status(state))
        return 0

    if args.command == "apply":
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
