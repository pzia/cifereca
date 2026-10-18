"""P0 #925: never treat an untrusted gh response as Git revision/provenance.

All CLI processes are mocked; no clone, network, installation or release.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import updater
from cifereca.update_progress import update_operation

PRIVATE = "Bearer PRIVATE_GH_TOKEN https://private.invalid/?auth=SECRET"


@pytest.mark.parametrize("output", [
    "--upload-pack=private", "a" * 39, "a" * 41, "z" * 40,
    "refs/heads/main", "a" * 40 + "\n" + PRIVATE,
    PRIVATE + "\n" + "a" * 40, "b" * 40 + " trailing",
    "a" * 20 + "\n" + "a" * 20, "\u0000" + "a" * 40,
    "\u200b" + "a" * 40,
])
def test_non_sha_gh_response_is_refused_during_resolve_before_clone_or_install(
    tmp_path, monkeypatch, output,
):
    commands = []
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: "/offline/" + cmd)
    monkeypatch.setattr(updater, "install_source",
                        lambda *_args, **_kw: pytest.fail("No install allowed"))
    def fake_run(cmd, **_kw):
        commands.append(tuple(cmd))
        if len(commands) == 1:
            return NS(returncode=0, stdout=output + "\n", stderr=PRIVATE)
        pytest.fail("A malformed commit must not reach clone/fetch/checkout")
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    root = tmp_path / "unmodified"
    events, stream = [], io.StringIO()
    with pytest.raises(updater.UpdateError, match="SHA de commit valide"):
        with update_operation("github", callback=events.append, stream=stream):
            updater.install_github("main", dry_run=True)
    assert len(commands) == 1
    assert "repos/pzia/cifereca/commits/main" in commands[0]
    assert not root.exists()
    assert [(e["stage"], e["status"]) for e in events] == [
        ("operation", "START"), ("resolve_github", "RUNNING"),
        ("resolve_github", "ERROR"), ("operation", "ERROR"),
        ("operation", "DONE"),
    ]
    assert events[-1]["outcome"] == "failed"
    assert "SECRET" not in stream.getvalue() + repr(events)
    assert "PRIVATE" not in stream.getvalue() + repr(events)


@pytest.mark.parametrize("raw", ["A" * 40, "a" * 40, ("aB" * 20)])
def test_valid_full_hash_is_normalized_and_used_for_fetch_only(
    tmp_path, monkeypatch, raw,
):
    calls = []
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: "/offline/" + cmd)
    def fake_run(command, **_kw):
        calls.append(list(command))
        if "api" in command:
            return NS(returncode=0, stdout=raw + "\n", stderr=PRIVATE)
        return NS(returncode=0, stdout="", stderr=PRIVATE)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    sha = updater._github_checkout("pzia/cifereca", "main", tmp_path / "source")
    assert sha == raw.lower()
    assert re.fullmatch(r"[a-f0-9]{40}", sha)
    assert len(calls) == 4
    assert calls[2][-1] == sha
    assert calls[3][-1] == "FETCH_HEAD"


def test_nonzero_gh_response_still_has_safe_generic_diagnostic(tmp_path, monkeypatch):
    monkeypatch.setattr(updater.shutil, "which", lambda cmd: "/offline/" + cmd)
    def fake_run(_command, **_kw):
        return NS(returncode=1, stdout="a" * 40, stderr=PRIVATE)
    monkeypatch.setattr(updater.subprocess, "run", fake_run)
    with pytest.raises(updater.UpdateError, match="introuvable ou accès refusé") as error:
        updater._github_checkout("pzia/cifereca", "main", tmp_path)
    assert PRIVATE not in str(error.value)
