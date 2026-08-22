#!/usr/bin/env python3
"""Fail-closed protected-path gate for high-trust repository surfaces.

The gate reads Git's raw diff so file modes, deletions, and rename-as-delete/add
behavior remain visible. The only unaudited bootstrap exception is the original
single introducing range rooted at ``BOOTSTRAP_BASE_SHA``.

Compose files are protected. A reviewed change requires a logged PR comment by
an allowed maintainer which binds the exact base SHA and SHA-256 of the complete
protected diff. Any later protected-path edit changes that digest and invalidates
the approval. The verifier's own policy change is included in the same digest
when present so this migration is visible in the approval record.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass

BOOTSTRAP_BASE_SHA = "6074ae4130e2aa1327f2daeb431875debfe40d1f"
WORKFLOW_PATH = ".github/workflows/path-scope.yml"
SCRIPT_PATH = "core-safety/scripts/verify_path_scope.py"
TEST_PATH = "tests/test_path_scope.py"
BOOTSTRAP_ALLOWED_PATHS = frozenset((WORKFLOW_PATH, SCRIPT_PATH, TEST_PATH))

PROTECTED_COMPOSE_PATHS = frozenset((
    "compose.yaml",
    "compose.storage.yaml",
    "compose.chainstrap.yaml",
    "compose.monitor.yaml",
    "compose.monitor-controller.yaml",
))
APPROVAL_PROTECTED_PATHS = frozenset((*PROTECTED_COMPOSE_PATHS, SCRIPT_PATH))
PROTECTED_EXACT_PATHS = frozenset((
    WORKFLOW_PATH,
    "docker/core/Dockerfile",
    "core-safety/production/update-signing-public-key.hex",
    "core-safety/production/core-policy-signing-public-key.hex",
))

GATEWAY_POLICY_PATH = "contrib/bootstrap/chainstrap_bootstrap.py"
GATEWAY_SYMBOLS = ("DEFAULT_GATEWAYS", "ALLOWED_GATEWAY_HOSTS")

APPROVAL_MAINTAINERS = frozenset(("ALENOC",))
APPROVAL_ASSOCIATIONS = frozenset(("OWNER", "MEMBER"))
APPROVAL_COMMAND = "/approve-protected-paths"
APPROVAL_RE = re.compile(
    r"^/approve-protected-paths base=([0-9a-f]{40}) "
    r"digest=(sha256:[0-9a-f]{64})$")
MAX_COMMENT_PAGES = 5
MAX_COMMENT_RESPONSE_BYTES = 2 * 1024 * 1024


class ScopeViolation(RuntimeError):
    """A proposed diff crosses a protected repository boundary."""


@dataclass(frozen=True)
class RawChange:
    old_mode: str
    new_mode: str
    status: str
    path: str


@dataclass(frozen=True)
class MaintainerApproval:
    login: str
    url: str
    base: str
    digest: str


def git_bytes(*args: str) -> bytes:
    completed = subprocess.run(
        ["git", *args], check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if completed.returncode != 0:
        raise ScopeViolation(
            f"git {' '.join(args)} failed: "
            f"{completed.stderr.decode('utf-8', errors='replace').strip()}")
    return completed.stdout


def git_text(*args: str) -> str:
    return git_bytes(*args).decode("utf-8")


def raw_changes(base: str, head: str) -> list[RawChange]:
    raw = git_bytes("diff", "--raw", "-z", "--no-renames", f"{base}...{head}")
    fields = raw.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % 2:
        raise ScopeViolation("unexpected git --raw -z record shape")

    changes = []
    for offset in range(0, len(fields), 2):
        header = fields[offset].decode("ascii", errors="strict")
        path = fields[offset + 1].decode("utf-8", errors="strict")
        if not header.startswith(":"):
            raise ScopeViolation(f"unexpected raw-diff header: {header!r}")
        pieces = header[1:].split()
        if len(pieces) != 5:
            raise ScopeViolation(f"unexpected raw-diff metadata: {header!r}")
        old_mode, new_mode, _old_sha, _new_sha, status = pieces
        if status not in ("A", "D", "M", "T", "U"):
            raise ScopeViolation(f"unexpected raw-diff status {status!r} for {path}")
        changes.append(RawChange(old_mode, new_mode, status, path))
    return changes


def path_exists(ref: str, path: str) -> bool:
    completed = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{path}"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return completed.returncode == 0


def file_at(ref: str, path: str) -> str:
    return git_text("show", f"{ref}:{path}")


def _assignment_value(source: str, symbol: str):
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == symbol
                   for target in node.targets):
            continue
        value = node.value
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and \
                value.func.id == "frozenset" and len(value.args) == 1 and not value.keywords:
            value = value.args[0]
            return frozenset(ast.literal_eval(value))
        return ast.literal_eval(value)
    raise ScopeViolation(f"cannot resolve protected gateway symbol {symbol}")


def gateway_policy(ref: str) -> tuple:
    source = file_at(ref, GATEWAY_POLICY_PATH)
    return tuple(_assignment_value(source, symbol) for symbol in GATEWAY_SYMBOLS)


def is_bootstrap_introduction(base: str, head: str, changes: list[RawChange]) -> bool:
    if base != BOOTSTRAP_BASE_SHA:
        return False
    if path_exists(base, WORKFLOW_PATH) or not path_exists(head, WORKFLOW_PATH):
        return False
    changed_paths = {change.path for change in changes}
    if not changed_paths or not changed_paths <= BOOTSTRAP_ALLOWED_PATHS:
        return False
    workflow_change = next(
        (change for change in changes if change.path == WORKFLOW_PATH), None)
    return workflow_change is not None and workflow_change.status == "A" and \
        workflow_change.old_mode == "000000" and workflow_change.new_mode == "100644"


def protected_diff_digest(base: str, head: str, paths: set[str]) -> str:
    if not paths:
        raise ScopeViolation("cannot approve an empty protected diff")
    diff = git_bytes(
        "diff", "--binary", "--full-index", "--no-ext-diff",
        f"{base}...{head}", "--", *sorted(paths))
    payload = b"PATH-SCOPE-PROTECTED-DIFF-v1\x00" + base.encode("ascii") + b"\x00" + diff
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def approval_command(base: str, digest: str) -> str:
    return f"{APPROVAL_COMMAND} base={base} digest={digest}"


def _pr_context() -> tuple[str, int]:
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ScopeViolation(
            "protected diff requires GitHub PR context; GITHUB_REPOSITORY is invalid")
    if not event_path:
        raise ScopeViolation(
            "protected diff requires GitHub PR context; GITHUB_EVENT_PATH is missing")
    try:
        with open(event_path, "r", encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeViolation(f"cannot read GitHub PR event: {exc}") from exc
    number = event.get("number")
    if not isinstance(number, int) or number < 1 or not isinstance(event.get("pull_request"), dict):
        raise ScopeViolation(
            "protected diff approval is available only on pull_request events")
    return repository, number


def _fetch_comment_page(repository: str, number: int, page: int) -> list[dict]:
    url = (
        f"https://api.github.com/repos/{repository}/issues/{number}/comments"
        f"?per_page=100&page={page}")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "electrumx-ravencoin-path-scope",
            "X-GitHub-Api-Version": "2022-11-28",
        })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            declared = response.headers.get("Content-Length")
            if declared is not None and int(declared) > MAX_COMMENT_RESPONSE_BYTES:
                raise ScopeViolation("GitHub approval comment response exceeds size limit")
            raw = response.read(MAX_COMMENT_RESPONSE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise ScopeViolation(f"cannot read logged protected-path approvals: {exc}") from exc
    if len(raw) > MAX_COMMENT_RESPONSE_BYTES:
        raise ScopeViolation("GitHub approval comment response exceeds size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScopeViolation("GitHub approval comment response is malformed") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise ScopeViolation("GitHub approval comment response has unexpected schema")
    return payload


def find_maintainer_approval(base: str, digest: str) -> MaintainerApproval | None:
    repository, number = _pr_context()
    expected = approval_command(base, digest)
    for page in range(1, MAX_COMMENT_PAGES + 1):
        comments = _fetch_comment_page(repository, number, page)
        for comment in comments:
            user = comment.get("user") or {}
            login = str(user.get("login") or "")
            association = str(comment.get("author_association") or "")
            body = str(comment.get("body") or "").strip()
            url = str(comment.get("html_url") or "")
            if login not in APPROVAL_MAINTAINERS or association not in APPROVAL_ASSOCIATIONS:
                continue
            match = APPROVAL_RE.fullmatch(body)
            if match is None:
                continue
            if body == expected and match.group(1) == base and match.group(2) == digest:
                return MaintainerApproval(login=login, url=url, base=base, digest=digest)
        if len(comments) < 100:
            break
    return None


def verify(base: str, head: str) -> None:
    changes = raw_changes(base, head)
    bootstrap = is_bootstrap_introduction(base, head, changes)

    for change in changes:
        if "120000" in (change.old_mode, change.new_mode):
            raise ScopeViolation(f"symlink-mode change is forbidden: {change.path}")
        if change.old_mode not in ("000000", change.new_mode) and \
                change.new_mode != "000000":
            raise ScopeViolation(
                f"file mode change is forbidden: {change.path} "
                f"({change.old_mode} -> {change.new_mode})")
        if change.path in PROTECTED_EXACT_PATHS:
            if bootstrap and change.path == WORKFLOW_PATH:
                continue
            raise ScopeViolation(
                f"protected path may not be changed by this PR: {change.path}")

    if path_exists(base, GATEWAY_POLICY_PATH) and path_exists(head, GATEWAY_POLICY_PATH):
        if gateway_policy(base) != gateway_policy(head):
            raise ScopeViolation(
                "release-embedded IPFS gateway allowlist/policy changed in "
                f"{GATEWAY_POLICY_PATH}")

    if bootstrap:
        approval_paths: set[str] = set()
    else:
        approval_paths = {
            change.path for change in changes if change.path in APPROVAL_PROTECTED_PATHS
        }

    approval = None
    if approval_paths:
        digest = protected_diff_digest(base, head, approval_paths)
        approval = find_maintainer_approval(base, digest)
        if approval is None:
            command = approval_command(base, digest)
            print(f"path-scope: protected diff digest {digest}")
            print(f"path-scope: required maintainer approval comment: {command}")
            raise ScopeViolation(
                "protected Compose/security-policy change has no matching logged maintainer approval")

    if bootstrap:
        print("path-scope: audited one-time introduction range accepted")
    elif approval is not None:
        print(
            "path-scope: protected diff approved by "
            f"{approval.login} at {approval.url}; digest={approval.digest}")
    else:
        print("path-scope: protected repository boundaries unchanged")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verify(args.base, args.head)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ScopeViolation as exc:
        print(f"path-scope: REFUSED: {exc}")
        raise SystemExit(1) from exc
