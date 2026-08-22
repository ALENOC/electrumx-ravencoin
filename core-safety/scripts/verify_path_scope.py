#!/usr/bin/env python3
"""Fail-closed protected-path gate for immutable repository trust surfaces.

The gate reads Git's raw diff so file modes, deletions, and rename-as-delete/add
behavior remain visible. There is no approval path or feature-PR bypass. The
only bootstrap exception is the original single introducing range rooted at
the audited pre-gate commit below.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
from dataclasses import dataclass

BOOTSTRAP_BASE_SHA = "6074ae4130e2aa1327f2daeb431875debfe40d1f"
WORKFLOW_PATH = ".github/workflows/path-scope.yml"
SCRIPT_PATH = "core-safety/scripts/verify_path_scope.py"
TEST_PATH = "tests/test_path_scope.py"
BOOTSTRAP_ALLOWED_PATHS = frozenset((WORKFLOW_PATH, SCRIPT_PATH, TEST_PATH))

PROTECTED_EXACT_PATHS = frozenset((
    WORKFLOW_PATH,
    SCRIPT_PATH,
    "docker/core/Dockerfile",
    "core-safety/production/update-signing-public-key.hex",
    "core-safety/production/core-policy-signing-public-key.hex",
))
GATEWAY_POLICY_PATH = "contrib/bootstrap/chainstrap_bootstrap.py"
GATEWAY_SYMBOLS = ("DEFAULT_GATEWAYS", "ALLOWED_GATEWAY_HOSTS")


class ScopeViolation(RuntimeError):
    """A proposed diff crosses an immutable repository trust boundary."""


@dataclass(frozen=True)
class RawChange:
    old_mode: str
    new_mode: str
    status: str
    path: str


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
            if bootstrap and change.path in BOOTSTRAP_ALLOWED_PATHS:
                continue
            raise ScopeViolation(
                f"protected path may not be changed by this PR: {change.path}")

    if path_exists(base, GATEWAY_POLICY_PATH) and path_exists(head, GATEWAY_POLICY_PATH):
        if gateway_policy(base) != gateway_policy(head):
            raise ScopeViolation(
                "release-embedded IPFS gateway allowlist/policy changed in "
                f"{GATEWAY_POLICY_PATH}")

    if bootstrap:
        print("path-scope: audited one-time introduction range accepted")
    else:
        print("path-scope: immutable repository trust boundaries unchanged")


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
