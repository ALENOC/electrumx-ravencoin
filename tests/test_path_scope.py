import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MODULE_PATH = ROOT / "core-safety/scripts/verify_path_scope.py"
WORKFLOW_PATH = ROOT / ".github/workflows/path-scope.yml"
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
    write(tmp_path, scope.SCRIPT_PATH, "# trusted verifier\n")
    write(tmp_path, scope.WORKFLOW_PATH, "name: path-scope\n")
    write(tmp_path, "compose.yaml", "services: {}\n")
    base = commit(tmp_path, "base")
    monkeypatch.chdir(tmp_path)
    return tmp_path, base


def test_bootstrap_exception_is_bound_to_exact_base(tmp_path, monkeypatch):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.email", "tests@example.invalid")
    git(tmp_path, "config", "user.name", "Path Scope Tests")
    write(tmp_path, "contrib/bootstrap/chainstrap_bootstrap.py", """
DEFAULT_GATEWAYS = (\"https://ipfs.io/ipfs/\",)
ALLOWED_GATEWAY_HOSTS = frozenset((\"ipfs.io\",))
""".lstrip())
    base = commit(tmp_path, "pre-gate")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(scope, "BOOTSTRAP_BASE_SHA", base)
    write(tmp_path, scope.WORKFLOW_PATH, "name: path-scope\n")
    write(tmp_path, scope.SCRIPT_PATH, "# verifier\n")
    write(tmp_path, scope.TEST_PATH, "# tests\n")
    head = commit(tmp_path, "introduce gate")
    scope.verify(base, head)


def test_bootstrap_exception_does_not_rearm_after_deletion(repository):
    repo, base = repository
    (repo / scope.WORKFLOW_PATH).unlink()
    removed = commit(repo, "remove gate")
    with pytest.raises(scope.ScopeViolation, match="protected path"):
        scope.verify(base, removed)


def test_rename_of_path_scope_is_refused(repository):
    repo, base = repository
    git(repo, "mv", scope.WORKFLOW_PATH, ".github/workflows/path-scope-renamed.yml")
    renamed = commit(repo, "rename gate")
    with pytest.raises(scope.ScopeViolation, match="protected path"):
        scope.verify(base, renamed)


def test_verifier_itself_is_protected(repository):
    repo, base = repository
    write(repo, scope.SCRIPT_PATH, "# malicious replacement\n")
    head = commit(repo, "change verifier")
    with pytest.raises(scope.ScopeViolation, match=scope.SCRIPT_PATH):
        scope.verify(base, head)


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


def test_compose_change_is_ordinary_reviewable_code(repository):
    repo, base = repository
    write(repo, "compose.yaml", "services:\n  electrumx: {}\n")
    head = commit(repo, "change release image")
    scope.verify(base, head)


def test_unrelated_regular_file_change_is_allowed(repository):
    repo, base = repository
    write(repo, "docs/example.md", "safe\n")
    head = commit(repo, "docs")
    scope.verify(base, head)


def test_workflow_executes_verifier_from_base_revision():
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert 'git show "$BASE_SHA:$VERIFIER_PATH" > "$TRUSTED_VERIFIER"' in workflow
    assert 'python3 "$TRUSTED_VERIFIER"' in workflow
    assert "python3 core-safety/scripts/verify_path_scope.py" not in workflow
    assert "persist-credentials: false" in workflow
