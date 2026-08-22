import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).parents[1] / "core-safety/scripts/verify_path_scope.py"
SPEC = importlib.util.spec_from_file_location("verify_path_scope", MODULE_PATH)
scope = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = scope
SPEC.loader.exec_module(scope)


def git(repo, *args):
    completed = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def write(repo, path, data):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(data, encoding="utf-8")


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repository(tmp_path, monkeypatch):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    git(tmp_path, "config", "user.name", "Path Scope Tests")
    write(tmp_path, "contrib/bootstrap/chainstrap_bootstrap.py", """
DEFAULT_GATEWAYS = (\"https://ipfs.io/ipfs/\",)
ALLOWED_GATEWAY_HOSTS = frozenset((\"ipfs.io\",))
""".lstrip())
    write(tmp_path, "docker/core/Dockerfile", "FROM scratch\n")
    write(tmp_path, "core-safety/production/update-signing-public-key.hex", "a" * 64 + "\n")
    write(tmp_path, "core-safety/production/core-policy-signing-public-key.hex", "b" * 64 + "\n")
    base = commit(tmp_path, "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path, base


def test_bootstrap_exception_is_bound_to_exact_base(repository, monkeypatch):
    repo, base = repository
    monkeypatch.setattr(scope, "BOOTSTRAP_BASE_SHA", base)
    write(repo, scope.WORKFLOW_PATH, "name: path-scope\n")
    write(repo, scope.SCRIPT_PATH, "# verifier\n")
    write(repo, scope.TEST_PATH, "# tests\n")
    head = commit(repo, "introduce gate")
    scope.verify(base, head)


def test_bootstrap_exception_does_not_rearm_after_deletion(repository, monkeypatch):
    repo, base = repository
    monkeypatch.setattr(scope, "BOOTSTRAP_BASE_SHA", base)
    write(repo, scope.WORKFLOW_PATH, "name: path-scope\n")
    write(repo, scope.SCRIPT_PATH, "# verifier\n")
    write(repo, scope.TEST_PATH, "# tests\n")
    introduced = commit(repo, "introduce gate")
    (repo / scope.WORKFLOW_PATH).unlink()
    removed = commit(repo, "remove gate")
    with pytest.raises(scope.ScopeViolation, match="protected path"):
        scope.verify(introduced, removed)


def test_rename_of_path_scope_is_refused(repository, monkeypatch):
    repo, base = repository
    monkeypatch.setattr(scope, "BOOTSTRAP_BASE_SHA", base)
    write(repo, scope.WORKFLOW_PATH, "name: path-scope\n")
    write(repo, scope.SCRIPT_PATH, "# verifier\n")
    write(repo, scope.TEST_PATH, "# tests\n")
    introduced = commit(repo, "introduce gate")
    git(repo, "mv", scope.WORKFLOW_PATH, ".github/workflows/path-scope-renamed.yml")
    renamed = commit(repo, "rename gate")
    with pytest.raises(scope.ScopeViolation, match="protected path"):
        scope.verify(introduced, renamed)


def test_core_dockerfile_is_protected(repository):
    repo, base = repository
    write(repo, "docker/core/Dockerfile", "FROM busybox\n")
    head = commit(repo, "change core pin surface")
    with pytest.raises(scope.ScopeViolation, match="docker/core/Dockerfile"):
        scope.verify(base, head)


def test_gateway_allowlist_change_is_refused(repository):
    repo, base = repository
    write(repo, "contrib/bootstrap/chainstrap_bootstrap.py", """
DEFAULT_GATEWAYS = (\"https://dweb.link/ipfs/\",)
ALLOWED_GATEWAY_HOSTS = frozenset((\"dweb.link\",))
""".lstrip())
    head = commit(repo, "change gateway policy")
    with pytest.raises(scope.ScopeViolation, match="gateway allowlist"):
        scope.verify(base, head)


def test_unrelated_regular_file_change_is_allowed(repository):
    repo, base = repository
    write(repo, "docs/example.md", "safe\n")
    head = commit(repo, "docs")
    scope.verify(base, head)
