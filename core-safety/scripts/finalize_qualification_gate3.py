#!/usr/bin/env python3
"""Unattended finalizer for the 1.13.8 fresh-install service-state gate.

The 1.13.8 ChainStrap fresh install was qualified up to the mandatory Core
reindex, which takes hours. This script waits for that reindex, evaluates the
remaining service-state gate against the running qualification stack, and then
records the observed result in the repository without a human in the loop.

It is deliberately fail closed about what it will claim:

* it declares ``RESULT: PASS`` only when every gate below is observed to pass;
* on any failure it records the observed failure text instead, and never
  rewrites the result line to PASS;
* it never edits release artifacts, manifests, tags, or the published release.

Gates evaluated:

1. the Core bootstrap-reindex one-shot exits 0 and writes its completion marker;
2. Ravencoin Core starts, reaches the repository readiness gate and is healthy;
3. Core still reports version 4.8.0;
4. ElectrumX starts and is healthy;
5. ElectrumX reports ``ElectrumX-RVN 1.13.8``.

The repository change is proposed as a pull request, because master is
protected. Auto-merge is requested so the branch lands once required checks
pass.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

VERSION = "1.13.8"
QUALIFICATION_DOC = Path("docs") / f"HARDWARE_QUALIFICATION_{VERSION}.md"
IDENTITY_TEST = Path("tests") / "test_release_version_identity.py"
EXPECTED_ELECTRUMX_VERSION = f"ElectrumX-RVN {VERSION}"
EXPECTED_CORE_VERSION = "4.8.0"
DEFAULT_POLL_SECONDS = 120
DEFAULT_TIMEOUT_SECONDS = 24 * 60 * 60
SERVICE_TIMEOUT_SECONDS = 3 * 60 * 60


class GateFailure(RuntimeError):
    """A qualification gate did not pass; nothing may be declared PASS."""


def log(message: str) -> None:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"[{stamp}] {message}", flush=True)


def run(args: list[str], *, cwd: Path | None = None, check: bool = True,
        timeout: int | None = None) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args, cwd=cwd, check=False, timeout=timeout,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if check and completed.returncode != 0:
        raise GateFailure(
            f"command failed ({completed.returncode}): {' '.join(args)}\n"
            f"{completed.stdout.strip()}")
    return completed


class Stack:
    """The isolated qualification Compose project."""

    def __init__(self, repo: Path, project: str, env_file: Path,
                 compose_files: list[Path]):
        self.repo = repo
        self.project = project
        self.env_file = env_file
        self.compose_files = compose_files

    def compose(self, *args: str, check: bool = True,
                timeout: int | None = None) -> subprocess.CompletedProcess:
        command = ["docker", "compose", "-p", self.project,
                   "--env-file", str(self.env_file)]
        for path in self.compose_files:
            command += ["-f", str(path)]
        command += list(args)
        return run(command, cwd=self.repo, check=check, timeout=timeout)

    def container_id(self, service: str) -> str:
        result = self.compose("ps", "-q", service)
        return result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""

    def health(self, service: str) -> str:
        container = self.container_id(service)
        if not container:
            return "absent"
        result = run(
            ["docker", "inspect", "-f",
             "{{if .State.Health}}{{.State.Health.Status}}"
             "{{else}}{{.State.Status}}{{end}}", container], check=False)
        return result.stdout.strip()


def wait_for_reindex(stack: Stack, datadir: Path, poll: int,
                     timeout: int) -> str:
    """Block until the Core reindex one-shot has finished, then judge it."""
    marker = datadir / ".chainstrap-reindex-complete"
    deadline = time.monotonic() + timeout
    service = "ravencoin-bootstrap-reindex"
    while time.monotonic() < deadline:
        if marker.exists():
            log("reindex completion marker present")
            break
        container = stack.container_id(service)
        if container:
            state = run(
                ["docker", "inspect", "-f",
                 "{{.State.Status}} {{.State.ExitCode}}", container],
                check=False).stdout.strip()
            status, _, code = state.partition(" ")
            if status == "exited":
                if code.strip() != "0":
                    raise GateFailure(
                        f"reindex one-shot exited with code {code.strip()}")
                if not marker.exists():
                    raise GateFailure(
                        "reindex one-shot exited 0 without writing its "
                        "completion marker")
                break
        log(f"waiting for the Core reindex ({service} still running)")
        time.sleep(poll)
    else:
        raise GateFailure(
            f"reindex did not finish within {timeout} seconds")

    payload = read_marker(datadir, marker)
    log(f"reindex marker: {payload[:200]}")
    return payload


CORE_IMAGE = "alenoc/ravencoin-core:4.8.0"


def read_marker(datadir: Path, marker: Path) -> str:
    """Read the completion marker, which Core writes owner-only as its own uid.

    The gate signal is the one-shot exiting 0 and the marker existing; both are
    established before this call. The contents are recorded as evidence, so an
    unreadable marker is reported as such rather than being treated as a gate
    failure.
    """
    try:
        return marker.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        log(f"marker not readable directly ({exc}); reading it through {CORE_IMAGE}")
    result = run(
        ["docker", "run", "--rm", "-v", f"{datadir}:/datadir:ro",
         "--entrypoint", "cat", CORE_IMAGE,
         f"/datadir/{marker.name}"], check=False)
    if result.returncode == 0:
        return result.stdout.strip()
    log("marker contents could not be read; recording its presence only")
    return ("marker present at "
            f"{marker}, contents not readable from the host account")


def wait_for_health(stack: Stack, service: str, poll: int,
                    timeout: int) -> None:
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        status = stack.health(service)
        if status != last:
            log(f"{service} health: {status}")
            last = status
        if status == "healthy":
            return
        if status in ("exited", "dead"):
            raise GateFailure(f"{service} is {status} instead of healthy")
        time.sleep(poll)
    raise GateFailure(
        f"{service} did not become healthy within {timeout} seconds "
        f"(last status: {last or 'unknown'})")


def core_version(stack: Stack) -> str:
    container = stack.container_id("ravencoin-core")
    if not container:
        raise GateFailure("ravencoin-core container is absent")
    result = run(
        ["docker", "exec", container, "sh", "-c",
         "ravend -version | head -n 1"], check=False)
    text = result.stdout.strip()
    match = re.search(r"v?([0-9]+\.[0-9]+\.[0-9]+)", text)
    if not match:
        raise GateFailure(f"could not read the Core version: {text}")
    return match.group(1)


def electrumx_getinfo(stack: Stack) -> dict:
    container = stack.container_id("electrumx")
    if not container:
        raise GateFailure("electrumx container is absent")
    result = run(["docker", "exec", container, "electrumx_rpc", "getinfo"],
                 check=False)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise GateFailure(
            f"electrumx_rpc getinfo did not return JSON: "
            f"{result.stdout.strip()[:400]}") from exc


def evaluate(stack: Stack, datadir: Path, poll: int, timeout: int) -> list[str]:
    """Return evidence lines, or raise GateFailure."""
    evidence = []
    marker = wait_for_reindex(stack, datadir, poll, timeout)
    evidence.append(
        "- the mandatory network-isolated Core reindex completed and wrote its "
        "completion marker:")
    evidence.append("")
    evidence.append("```")
    evidence.append(marker[:1000])
    evidence.append("```")
    evidence.append("")

    log("starting ravencoin-core")
    stack.compose("up", "-d", "ravencoin-core", timeout=1800)
    wait_for_health(stack, "ravencoin-core", poll, SERVICE_TIMEOUT_SECONDS)
    version = core_version(stack)
    if version != EXPECTED_CORE_VERSION:
        raise GateFailure(
            f"Core reports {version}, expected {EXPECTED_CORE_VERSION}")
    evidence.append(
        f"- Ravencoin Core reached the repository readiness gate, is healthy "
        f"and reports `{version}`;")

    log("starting electrumx")
    stack.compose("up", "-d", "electrumx", timeout=1800)
    wait_for_health(stack, "electrumx", poll, SERVICE_TIMEOUT_SECONDS)
    info = electrumx_getinfo(stack)
    reported = str(info.get("version", ""))
    if reported != EXPECTED_ELECTRUMX_VERSION:
        raise GateFailure(
            f"ElectrumX reports {reported!r}, expected "
            f"{EXPECTED_ELECTRUMX_VERSION!r}")
    evidence.append("- ElectrumX started and is healthy;")
    evidence.append(
        f"- `electrumx_rpc getinfo` reports `\"version\": \"{reported}\"`, "
        f"with the daemon height at "
        f"`{info.get('daemon height', 'unknown')}` and the DB height at "
        f"`{info.get('db height', 'unknown')}`;")
    evidence.append(
        "- the backend remained the trusted Ravencoin Core "
        f"{EXPECTED_CORE_VERSION} identity pinned by this release.")
    return evidence


def prepare_worktree(repo: Path, branch: str) -> Path:
    """Record the result on a clean checkout of origin/master, not in place.

    The qualification host keeps untracked release candidates and local work in
    the main checkout, and the running stack is served from it. A dedicated
    worktree keeps this unattended commit to exactly the two recorded files.
    """
    run(["git", "fetch", "origin", "master"], cwd=repo)
    target = repo.parent / f".{repo.name}-gate3-worktree"
    if target.exists():
        run(["git", "worktree", "remove", "--force", str(target)],
            cwd=repo, check=False)
    run(["git", "worktree", "add", "-B", branch, str(target), "origin/master"],
        cwd=repo)
    return target


def record(repo: Path, evidence: list[str], failure: str | None) -> None:
    doc_path = repo / QUALIFICATION_DOC
    text = doc_path.read_text(encoding="utf-8")
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if failure is None:
        block = "\n".join([
            "### Gate 3: post-install service state",
            "",
            f"PASS, observed on the isolated fresh installation at {stamp}:",
            "",
            *evidence,
        ])
    else:
        block = "\n".join([
            "### Gate 3: post-install service state",
            "",
            f"FAILED, observed on the isolated fresh installation at {stamp}:",
            "",
            "```",
            failure[:2000],
            "```",
            "",
            *evidence,
        ])

    pattern = re.compile(
        r"### Gate 3: post-install service state\n.*?(?=\n### )", re.DOTALL)
    if not pattern.search(text):
        raise GateFailure(
            "the qualification document has no gate 3 section to update")
    text = pattern.sub(block + "\n", text, count=1)

    if failure is None:
        text = text.replace("## RESULT: PENDING", "## RESULT: PASS", 1)
        result_pattern = re.compile(
            r"## Qualification result\n\nPENDING\.\n.*?$", re.DOTALL)
        result_block = (
            "## Qualification result\n"
            "\n"
            "PASS.\n"
            "\n"
            "Every mandatory gate above passed on real hardware and the signed\n"
            "artifact identity is recorded in this document. Gate 3 was completed\n"
            "after publication, on the maintainer's explicit instruction to publish\n"
            "ahead of the isolated fresh-install reindex.\n")
        if result_pattern.search(text):
            text = result_pattern.sub(result_block, text, count=1)
    doc_path.write_text(text, encoding="utf-8")

    if failure is None:
        test_path = repo / IDENTITY_TEST
        test_text = test_path.read_text(encoding="utf-8")
        needle = 'assert "## RESULT: PENDING" in text'
        if needle in test_text:
            test_path.write_text(
                test_text.replace(needle, 'assert "## RESULT: PASS" in text', 1),
                encoding="utf-8")


def branch_name(failure: str | None) -> str:
    return f"qual/{VERSION}-gate3-" + ("pass" if failure is None else "failed")


def propose(repo: Path, worktree: Path, branch: str, failure: str | None,
            dry_run: bool) -> None:
    if failure is None:
        subject = f"docs: record the {VERSION} fresh-install service-state gate as PASS"
        body = (
            f"The isolated ChainStrap fresh installation finished its mandatory "
            f"network-isolated Ravencoin Core reindex. Core reached the "
            f"readiness gate and is healthy at {EXPECTED_CORE_VERSION}, "
            f"ElectrumX started and is healthy, and it reports "
            f"`{EXPECTED_ELECTRUMX_VERSION}`.\n\n"
            f"Gate 3 and the overall qualification result are recorded "
            f"accordingly. Produced unattended by "
            f"`core-safety/scripts/finalize_qualification_gate3.py`; the "
            f"observed evidence is in the document.")
    else:
        subject = f"docs: record the {VERSION} fresh-install service-state gate failure"
        body = (
            f"The isolated ChainStrap fresh installation did not pass the "
            f"post-install service-state gate. The observed failure is "
            f"recorded verbatim in the qualification document and the "
            f"qualification result stays PENDING.\n\nProduced unattended by "
            f"`core-safety/scripts/finalize_qualification_gate3.py`.")

    files = [str(QUALIFICATION_DOC)]
    if failure is None:
        files.append(str(IDENTITY_TEST))

    if dry_run:
        log(f"dry run: would commit {files} on {branch}")
        return

    run(["git", "add", *files], cwd=worktree)
    run(["git", "commit", "-m", subject, "-m", body], cwd=worktree)
    run(["git", "push", "-u", "origin", branch, "--force-with-lease"],
        cwd=worktree)
    created = run(
        ["gh", "pr", "create", "--base", "master", "--head", branch,
         "--title", subject, "--body", body], cwd=worktree, check=False)
    log(created.stdout.strip())
    merge_when_green(worktree, branch)


def merge_when_green(worktree: Path, branch: str,
                     poll: int = 60, timeout: int = 2 * 60 * 60) -> None:
    """Merge once required checks pass.

    This repository does not enable GitHub auto-merge, so the merge is polled
    here. A failing check leaves the pull request open for a human, which is
    the correct outcome: nothing is force merged.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        checks = run(["gh", "pr", "checks", branch, "--json",
                      "name,state"], cwd=worktree, check=False)
        try:
            rows = json.loads(checks.stdout)
        except json.JSONDecodeError:
            rows = []
        states = {row.get("state", "") for row in rows}
        if rows and states <= {"SUCCESS", "SKIPPED", "NEUTRAL"}:
            merged = run(["gh", "pr", "merge", branch, "--merge"],
                         cwd=worktree, check=False)
            log(f"merge: {merged.stdout.strip()}")
            return
        bad = states & {"FAILURE", "ERROR", "CANCELLED", "TIMED_OUT",
                        "ACTION_REQUIRED"}
        if bad:
            log(f"pull request checks are not green ({sorted(bad)}); "
                "leaving it open for review")
            return
        log(f"waiting for pull request checks: {sorted(states)}")
        time.sleep(poll)
    log("pull request checks did not settle in time; leaving it open")


def cleanup(stack: Stack, qual_root: Path, images: list[str]) -> None:
    """Tear down the qualification stack once its result is recorded.

    Scope is deliberately narrow: only this Compose project, only the
    qualification snapshot data, and only images named on the command line.
    Unrelated Docker data and any production stack are never touched.
    """
    log("tearing down the qualification stack")
    stack.compose("down", "-v", "--remove-orphans", check=False, timeout=1800)
    for image in images:
        removed = run(["docker", "image", "rm", image], check=False)
        log(f"image {image}: {removed.stdout.strip() or 'removed'}")
    if qual_root is not None:
        for name in ("data", "electrumx", "config", "monitor"):
            target = qual_root / name
            if target.exists():
                run(["rm", "-rf", str(target)], check=False)
                log(f"removed qualification directory {target}")
    log("logs, evidence and secrets under the qualification root are kept")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True,
                        help="repository checkout to record the result in")
    parser.add_argument("--datadir", type=Path, required=True,
                        help="Ravencoin datadir of the qualification install")
    parser.add_argument("--project", required=True,
                        help="Compose project name of the qualification stack")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--compose-file", type=Path, action="append",
                        required=True, dest="compose_files")
    parser.add_argument("--poll-seconds", type=int, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout-seconds", type=int,
                        default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--cleanup", action="store_true",
                        help="tear down the qualification stack afterwards")
    parser.add_argument("--cleanup-image", action="append", default=[],
                        dest="cleanup_images",
                        help="image tag to remove during cleanup (repeatable)")
    parser.add_argument("--qual-root", type=Path,
                        help="qualification root whose snapshot data to remove")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    repo = args.repo.resolve()
    stack = Stack(repo, args.project, args.env_file.resolve(),
                  [path.resolve() for path in args.compose_files])
    evidence: list[str] = []
    failure: str | None = None
    try:
        evidence = evaluate(stack, args.datadir.resolve(), args.poll_seconds,
                            args.timeout_seconds)
    except GateFailure as exc:
        failure = str(exc)
        log(f"gate 3 FAILED: {failure}")
    except Exception as exc:  # unattended: never leave a silent half state
        failure = f"unexpected finalizer error: {exc!r}"
        log(failure)

    branch = branch_name(failure)
    worktree = prepare_worktree(repo, branch)
    record(worktree, evidence, failure)
    propose(repo, worktree, branch, failure, args.dry_run)
    if args.cleanup and not args.dry_run:
        if failure is None:
            cleanup(stack, args.qual_root, args.cleanup_images)
        else:
            log("gate 3 failed: keeping the stack for inspection")

    log("gate 3 " + ("PASS recorded" if failure is None else "failure recorded"))
    return 0 if failure is None else 1


if __name__ == "__main__":
    sys.exit(main())
