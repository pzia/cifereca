"""CLI preview is offline by default and never starts Kobo acquisition."""
from __future__ import annotations

import json
from types import SimpleNamespace

from cifereca import cli


def test_kobo_resume_preview_cli_only_connects_when_manifest_refresh_requested(monkeypatch, capsys):
    connections = []
    previews = []
    closed = []

    def preview(credential_id, *, refresh_manifest=False, verify_local_files=False):
        previews.append((credential_id, refresh_manifest, verify_local_files))
        return {
            "source_id": "opaque",
            "manifest_refreshed": refresh_manifest,
            "verification": "artifact_hash" if verify_local_files else "linked_path_only",
            "acquisition_authorized": False,
        }

    service = SimpleNamespace(
        connect_configured=lambda: connections.append(True),
        kobo_backup_preview=preview,
    )
    core = SimpleNamespace(library=service, close=lambda: closed.append(True))
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: core)

    assert cli.main(["kobo-resume-preview", "--credential", "vault-1"]) == 0
    offline = json.loads(capsys.readouterr().out)
    assert offline["manifest_refreshed"] is False
    assert offline["acquisition_authorized"] is False
    assert previews == [("vault-1", False, False)]
    assert connections == []

    assert cli.main([
        "kobo-resume-preview", "--credential", "vault-1",
        "--refresh-manifest", "--verify-files",
    ]) == 0
    refreshed = json.loads(capsys.readouterr().out)
    assert refreshed["manifest_refreshed"] is True
    assert refreshed["verification"] == "artifact_hash"
    assert previews[-1] == ("vault-1", True, True)
    assert connections == [True]
    assert len(closed) == 2
