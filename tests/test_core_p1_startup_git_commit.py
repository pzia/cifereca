"""Startup revision evidence is bounded and never logs filesystem paths."""
from subprocess import CompletedProcess, TimeoutExpired

from cifereca import startup_revision as revision


def test_valid_deployment_revision_takes_precedence(monkeypatch):
    monkeypatch.setenv("CIFERECA_GIT_COMMIT", "A" * 40)
    assert revision.startup_git_commit() == "a" * 40


def test_invalid_env_revision_falls_back_without_leaking(monkeypatch, tmp_path):
    monkeypatch.setenv("CIFERECA_GIT_COMMIT", "private-garbage")
    monkeypatch.setattr(revision, "__file__", str(tmp_path / "src" / "cifereca" / "startup_revision.py"))
    assert revision.startup_git_commit() is None


def test_git_revision_checked_only_in_checkout(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    source = root / "src" / "cifereca" / "startup_revision.py"
    monkeypatch.setattr(revision, "__file__", str(source))
    monkeypatch.delenv("CIFERECA_GIT_COMMIT", raising=False)
    monkeypatch.setattr(revision.subprocess, "run", lambda *args, **kwargs: CompletedProcess(args, 0, "b" * 40 + "\n", ""))
    assert revision.startup_git_commit() == "b" * 40


def test_git_errors_fail_closed(monkeypatch, tmp_path):
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    monkeypatch.setattr(revision, "__file__", str(root / "src" / "cifereca" / "startup_revision.py"))
    monkeypatch.delenv("CIFERECA_GIT_COMMIT", raising=False)
    def timeout(*args, **kwargs):
        raise TimeoutExpired("git", 2)
    monkeypatch.setattr(revision.subprocess, "run", timeout)
    assert revision.startup_git_commit() is None
