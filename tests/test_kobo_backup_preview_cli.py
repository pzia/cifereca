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
        connect_configured=lambda: (_ for _ in ()).throw(
            AssertionError("global connect_configured must never be used for Kobo preview")
        ),
        connect_kobo_preview=lambda credential_id: connections.append(credential_id),
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
    assert connections == ["vault-1"]
    assert len(closed) == 2


def test_manifest_refresh_cli_runs_with_real_library_service_facade(tmp_path, monkeypatch, capsys):
    """Regression: the Core façade must expose the targeted provider connector.

    The CLI calls ``service.connect_kobo_preview``; merely implementing a
    standalone helper in provider_sessions is insufficient.
    """
    from cifereca.core import provider_sessions
    from tests.test_kobo_backup_reconcile_prune import _service

    service = _service(tmp_path, manifest=[])
    target = service._sessions[service._session_key("kobo", "vault-1")]
    selected = []

    def connect_target(owner, credential_id):
        assert owner is service
        selected.append(credential_id)
        return target

    monkeypatch.setattr(provider_sessions, "connect_kobo_preview", connect_target)
    monkeypatch.setattr(
        service, "connect_configured",
        lambda: (_ for _ in ()).throw(AssertionError("must not connect all providers")),
    )
    monkeypatch.setattr(
        cli.CiferecaCore, "open",
        lambda **_kwargs: SimpleNamespace(library=service, close=lambda: None),
    )
    try:
        assert cli.main([
            "kobo-resume-preview", "--credential", "vault-1", "--refresh-manifest",
        ]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["manifest_refreshed"] is True
        assert result["manifest_valid"] is True
        assert result["acquisition_authorized"] is False
        assert selected == ["vault-1"]
    finally:
        service.close()
