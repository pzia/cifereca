"""P1 #3885: deployed revision must describe the installed bits, not live Git."""
from __future__ import annotations

import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from cifereca import __version__
from cifereca import startup_revision as revision


def _in_package(tmp_path, monkeypatch, *, manifest=None, install=None):
    package = tmp_path / "release" / ".venv" / "lib" / "python3.11" / "site-packages" / "cifereca"
    package.mkdir(parents=True)
    monkeypatch.setattr(revision, "__file__", str(package / "startup_revision.py"))
    if manifest is not None:
        (package / "_build_identity.json").write_text(json.dumps(manifest), encoding="utf-8")
    if install is not None:
        (tmp_path / "release" / ".cifereca-install.json").write_text(
            json.dumps(install), encoding="utf-8"
        )
    return package


def _manifest(sha="a" * 40):
    return {
        "schema_version": 1, "build_version": __version__,
        "build_timestamp": "2026-10-31T14:30:00+00:00",
        "distribution_mode": "wheel",
        "origin": "git_clean_checkout" if sha else "unavailable",
        "commit_sha": sha,
    }


def test_embedded_wheel_receipt_survives_without_git(monkeypatch, tmp_path):
    package = _in_package(tmp_path, monkeypatch, manifest=_manifest())
    monkeypatch.setenv("CIFERECA_GIT_COMMIT", "b" * 40)
    result = revision.build_identity()
    assert result == {
        "schema_version": 1, "status": "known", "commit_sha": "a" * 40,
        "build_version": __version__, "build_timestamp": "2026-10-31T14:30:00+00:00",
        "distribution_mode": "wheel", "origin": "wheel_manifest",
    }
    assert not (package.parent / ".git").exists()


def test_unavailable_when_only_untrusted_runtime_override(monkeypatch, tmp_path):
    _in_package(tmp_path, monkeypatch)
    monkeypatch.setenv("CIFERECA_GIT_COMMIT", "a" * 40)
    result = revision.build_identity()
    assert result["status"] == "unavailable"
    assert result["commit_sha"] is None
    assert result["origin"] == "unavailable"


@pytest.mark.parametrize("bad", [
    {"schema_version": True, "commit_sha": "a" * 40},
    {**_manifest(), "build_version": "0.0.0"},
    {**_manifest(), "commit_sha": "a" * 39},
    {**_manifest(), "origin": "unavailable"},
    {**_manifest(), "commit_sha": "/tmp/private"},
])
def test_invalid_wheel_metadata_never_emits_git_sha(monkeypatch, tmp_path, bad):
    _in_package(tmp_path, monkeypatch, manifest=bad)
    result = revision.build_identity()
    assert result["status"] == "unavailable"
    assert result["commit_sha"] is None


def test_managed_github_receipt_can_prove_commit_without_wheel_metadata(monkeypatch, tmp_path):
    _in_package(tmp_path, monkeypatch, install={
        "version": __version__,
        "provenance": {"type": "github", "commit": "b" * 40},
    })
    result = revision.build_identity()
    assert result["status"] == "known"
    assert result["commit_sha"] == "b" * 40
    assert result["origin"] == "managed_github_receipt"
    assert result["distribution_mode"] == "managed_github"


def test_different_managed_and_wheel_commits_fail_closed(monkeypatch, tmp_path):
    _in_package(tmp_path, monkeypatch, manifest=_manifest(), install={
        "version": __version__,
        "provenance": {"type": "github", "commit": "b" * 40},
    })
    result = revision.build_identity()
    assert result["status"] == "unavailable"
    assert result["commit_sha"] is None
    assert result["origin"] == "provenance_conflict"


def test_managed_ref_cannot_rescue_explicitly_unverified_wheel(monkeypatch, tmp_path):
    _in_package(tmp_path, monkeypatch, manifest=_manifest(None), install={
        "version": __version__,
        "provenance": {"type": "github", "commit": "b" * 40},
    })
    result = revision.build_identity()
    assert result["status"] == "unavailable"
    assert result["commit_sha"] is None
    assert result["origin"] == "wheel_provenance_unavailable"


def test_source_archive_without_metadata_is_unavailable(monkeypatch, tmp_path):
    _in_package(tmp_path, monkeypatch, manifest=_manifest(None), install={
        "version": __version__,
        "provenance": {"type": "archive", "commit": "c" * 40},
    })
    result = revision.build_identity()
    assert result["status"] == "unavailable"
    assert result["commit_sha"] is None
    assert result["distribution_mode"] == "managed_archive"


def test_malformed_json_and_symlink_cannot_claim_provenance(monkeypatch, tmp_path):
    package = _in_package(tmp_path, monkeypatch)
    (package / "_build_identity.json").write_text("{bad}", encoding="utf-8")
    assert revision.build_identity()["status"] == "unavailable"
    (package / "_build_identity.json").unlink()
    (package / "_build_identity.json").symlink_to(tmp_path / "unknown")
    assert revision.build_identity()["status"] == "unavailable"


def test_packaging_producer_refuses_untrusted_or_dirty_source(monkeypatch, tmp_path):
    import setuptools
    with monkeypatch.context() as ctx:
        ctx.setattr(setuptools, "setup", lambda **kwargs: None)
        producer = runpy.run_path(str(Path(__file__).resolve().parents[1] / "setup.py"))
    producer["source_commit"].__globals__["_ROOT"] = tmp_path
    assert producer["source_commit"]() is None
    (tmp_path / ".git").mkdir()
    calls = {
        ("rev-parse", "--show-toplevel"): str(tmp_path),
        ("status", "--porcelain", "--untracked-files=normal"): " M src/cifereca/app.py",
        ("rev-parse", "--verify", "HEAD"): "d" * 40,
    }
    producer["source_commit"].__globals__["_git_output"] = lambda *args: calls.get(args)
    assert producer["source_commit"]() is None
    calls[("status", "--porcelain", "--untracked-files=normal")] = ""
    assert producer["source_commit"]() == "d" * 40


def test_startup_event_is_json_and_fails_closed_before_gtk(monkeypatch, capsys):
    import sys
    from cifereca import app

    infos = []
    monkeypatch.setattr(app, "configure_logging", lambda **kwargs: SimpleNamespace(
        info=lambda *args: infos.append(args)
    ))
    monkeypatch.setattr(app, "build_identity", lambda: {
        **revision.build_identity(), "status": "unavailable", "commit_sha": None,
    })
    monkeypatch.setitem(sys.modules, "gi", None)
    monkeypatch.setattr(sys, "argv", ["cifereca"])
    assert app.main() == 3
    events = [json.loads(row[1]) for row in infos if row[0] == "DIAG_JSON %s"]
    assert len(events) == 1
    assert events[0]["operation"] == "build_identity"
    assert events[0]["detail"]["status"] == "unavailable"
    assert events[0]["detail"]["commit_sha"] is None
