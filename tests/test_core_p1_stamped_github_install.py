"""P1: the exact GitHub SHA survives stripping the checkout from an installed wheel."""
from pathlib import Path

import pytest

from cifereca import startup_revision as revision


def test_stamped_checkout_revision_survives_without_git(tmp_path, monkeypatch):
    package = tmp_path / "src" / "cifereca"
    package.mkdir(parents=True)
    marker = package / "_build_revision.txt"
    revision.stamp_source_git_commit(tmp_path, "A" * 40)
    assert marker.read_text(encoding="ascii") == "a" * 40 + "\n"
    monkeypatch.setattr(revision, "__file__", str(package / "startup_revision.py"))
    monkeypatch.delenv("CIFERECA_GIT_COMMIT", raising=False)
    assert revision.startup_git_commit() == "a" * 40


@pytest.mark.parametrize("bad", ["short", "Z" * 40, "a" * 39, "a" * 41])
def test_reject_malformed_sha_before_stamping(tmp_path, bad):
    package = tmp_path / "src" / "cifereca"
    package.mkdir(parents=True)
    with pytest.raises(ValueError, match="invalid_source_git_commit"):
        revision.stamp_source_git_commit(tmp_path, bad)
    assert not (package / "_build_revision.txt").exists()


def test_untrusted_or_damaged_marker_fails_closed(tmp_path, monkeypatch):
    package = tmp_path / "src" / "cifereca"
    package.mkdir(parents=True)
    marker = package / "_build_revision.txt"
    marker.write_bytes(b"a" * 40 + b"\n" + b"unexpected")
    monkeypatch.setattr(revision, "__file__", str(package / "startup_revision.py"))
    monkeypatch.delenv("CIFERECA_GIT_COMMIT", raising=False)
    assert revision.startup_git_commit() is None


def test_symlinked_marker_is_never_overwritten(tmp_path):
    package = tmp_path / "src" / "cifereca"
    package.mkdir(parents=True)
    victim = tmp_path / "outside"
    victim.write_text("do not overwrite")
    (package / "_build_revision.txt").symlink_to(victim)
    with pytest.raises(ValueError, match="invalid_source_revision_target"):
        revision.stamp_source_git_commit(tmp_path, "b" * 40)
    assert victim.read_text() == "do not overwrite"


def test_release_workflow_builds_and_checks_embedded_sha():
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "release.yml").read_text()
    pyproject = (root / "pyproject.toml").read_text()
    assert '"_build_revision.txt"' in pyproject
    assert workflow.index("Stamp verified Core source Git revision") < workflow.index("Build Core wheel")
    assert workflow.index("Build Core wheel") < workflow.index("Verify Core wheel embeds the exact release SHA")
    assert workflow.index("Verify Core wheel embeds the exact release SHA") < workflow.index("Smoke installed Core wheel")
    assert 'wheel.read("cifereca/_build_revision.txt")' in workflow
