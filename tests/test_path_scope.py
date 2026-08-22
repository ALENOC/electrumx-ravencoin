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
    write(tmp_path, "compose.yaml", "services: {}\n")
    write(tmp_path, "compose.storage.yaml", "volumes: {}\n")
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


def test_compose_change_is_refused_until_exact_logged_approval(repository, monkeypatch):
    repo, base = repository
    write(repo, "compose.yaml", "services:\n  electrumx: {}\n")
    head = commit(repo, "change protected compose")

    monkeypatch.setattr(scope, "find_maintainer_approval", lambda _base, _digest: None)
    with pytest.raises(scope.ScopeViolation, match="no matching logged maintainer approval"):
        scope.verify(base, head)

    digest = scope.protected_diff_digest(base, head, {"compose.yaml"})
    approval = scope.MaintainerApproval(
        login="ALENOC", url="https://github.invalid/comment/1", base=base, digest=digest)
    monkeypatch.setattr(
        scope, "find_maintainer_approval",
        lambda candidate_base, candidate_digest: approval
        if candidate_base == base and candidate_digest == digest else None)
    scope.verify(base, head)


def test_protected_diff_change_invalidates_prior_approval(repository, monkeypatch):
    repo, base = repository
    write(repo, "compose.yaml", "services:\n  electrumx: {}\n")
    first = commit(repo, "first protected compose")
    first_digest = scope.protected_diff_digest(base, first, {"compose.yaml"})
    approval = scope.MaintainerApproval(
        login="ALENOC", url="https://github.invalid/comment/1",
        base=base, digest=first_digest)
    monkeypatch.setattr(
        scope, "find_maintainer_approval",
        lambda candidate_base, candidate_digest: approval
        if candidate_base == base and candidate_digest == first_digest else None)
    scope.verify(base, first)

    write(repo, "compose.yaml", "services:\n  electrumx:\n    restart: always\n")
    second = commit(repo, "change protected diff after approval")
    assert scope.protected_diff_digest(base, second, {"compose.yaml"}) != first_digest
    with pytest.raises(scope.ScopeViolation, match="no matching logged maintainer approval"):
        scope.verify(base, second)


def test_storage_overlay_is_protected(repository, monkeypatch):
    repo, base = repository
    write(repo, "compose.storage.yaml", "volumes:\n  ravencoin-data: {}\n")
    head = commit(repo, "change storage compose")
    monkeypatch.setattr(scope, "find_maintainer_approval", lambda _base, _digest: None)
    with pytest.raises(scope.ScopeViolation, match="no matching logged maintainer approval"):
        scope.verify(base, head)


def test_verifier_policy_change_is_in_approval_digest(repository, monkeypatch):
    repo, base = repository
    write(repo, scope.SCRIPT_PATH, "# new policy\n")
    write(repo, "compose.yaml", "services:\n  electrumx: {}\n")
    head = commit(repo, "change gate and compose")
    paths = {scope.SCRIPT_PATH, "compose.yaml"}
    digest = scope.protected_diff_digest(base, head, paths)
    observed = []

    def approve(candidate_base, candidate_digest):
        observed.append((candidate_base, candidate_digest))
        return scope.MaintainerApproval(
            login="ALENOC", url="https://github.invalid/comment/2",
            base=candidate_base, digest=candidate_digest)

    monkeypatch.setattr(scope, "find_maintainer_approval", approve)
    scope.verify(base, head)
    assert observed == [(base, digest)]


def test_unrelated_regular_file_change_is_allowed(repository):
    repo, base = repository
    write(repo, "docs/example.md", "safe\n")
    head = commit(repo, "docs")
    scope.verify(base, head)
