"""Kobo P0: interactive sync CLI stdout AND stderr must be shareable.

Provider progress fields are not trusted: title, item_id, path, error code,
software version and signed URLs can all contain private values.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from cifereca import cli


@pytest.mark.parametrize("command", ["kobo-sync", "kobo-backup"])
@pytest.mark.parametrize("quiet", [False, True])
def test_kobo_sync_cli_never_exports_provider_or_book_metadata(
    monkeypatch, capsys, command, quiet,
):
    secrets = [
        "SECRET_BOOK_TITLE", "PRIVATE_PRODUCT_ID", "/home/PRIVATE_BOOK.epub",
        "https://vault.example/?sig=PRIVATE_SIGNED_URL",
        "PRIVATE_ERROR_CODE", "PRIVATE_SOFTWARE_VERSION",
        "PRIVATE_CREDENTIAL_ID", "PRIVATE_IDENTITY_ID",
    ]
    events = [
        {"event": "adept_probe", "message": secrets[3]},
        {"event": "adept_ready", "software_version": secrets[5], "contract_version": secrets[3]},
        {"event": "manifest_fetch", "credential_id": secrets[6]},
        {"event": "manifest", "total": 2, "title": secrets[0]},
        {"event": "recovered", "count": 1, "item_id": secrets[1]},
        {"event": "recovered_unparsed", "count": 1, "path": secrets[2]},
        {"event": "reconcile_required", "title": secrets[0], "item_id": secrets[1]},
        {"event": "reconcile_conflict", "count": 3, "path": secrets[2]},
        {"event": "scan", "current": 1, "total": 2, "downloaded": 0, "errors": 1,
         "title": secrets[0]},
        {"event": "downloaded", "title": secrets[0], "item_id": secrets[1],
         "path": secrets[2]},
        {"event": "attention_required", "title": secrets[0], "code": secrets[4]},
        {"event": "item_error", "errors": 1, "title": secrets[0], "code": secrets[4]},
        {"event": "scan", "current": 2, "total": 2, "downloaded": 1, "errors": 1,
         "title": secrets[0]},
        {"event": "done", "downloaded": 1, "unchanged": 0, "attention_required": 1,
         "errors": 1, "reconcile_pending": 1, "message": secrets[3]},
    ]
    summary = {
        "status": "partial", "sessions": 1, "manifests_fetched": 1,
        "manifest_items": 2, "downloaded": 1, "attempted": 2,
        "errors": 1, "attention_required": 1, "reconcile_pending": 1,
        "credential_id": secrets[6], "identity_id": secrets[7],
        "error_examples": [{"title": secrets[0], "item_id": secrets[1],
                            "message": secrets[3]}],
        "error_summary": [{"phase": "vault", "code": secrets[4],
                           "message": secrets[3], "count": 1}],
        "sources": [{"status": "partial", "credential_id": secrets[6],
                     "identity_id": secrets[7], "title": secrets[0],
                     "downloaded": 1, "errors": 1}],
        "target_dirs": {"private": secrets[2]},
    }
    actions = []
    def sync(*, progress):
        actions.append("sync")
        for event in events:
            progress(event)
        return summary

    service = SimpleNamespace(
        connect_configured=lambda: actions.append("connect"),
        sync_kobo_backup=sync,
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: SimpleNamespace(
        library=service, close=lambda: actions.append("close"),
    ))
    args = [command] + (["--quiet"] if quiet else [])
    assert cli.main(args) == 0
    captured = capsys.readouterr()
    result = json.loads(captured.out)
    assert result["redacted"] is True
    assert result["status"] == "partial"
    assert result["downloaded"] == 1
    assert result["attempted"] == 2
    assert result["errors"] == 1
    assert result["sources"] == [
        {"source_index": 1, "status": "partial", "downloaded": 1, "errors": 1}
    ]
    assert result["error_summary"] == [
        {"phase": "vault", "code": "other", "count": 1}
    ]
    for secret in secrets:
        assert secret not in captured.out
        assert secret not in captured.err
    if quiet:
        assert captured.err == ""
    else:
        assert "Téléchargement terminé" in captured.err
        assert "Réconciliation requise" in captured.err
    assert actions == ["connect", "sync", "close"]


def test_kobo_sync_progress_rejects_malformed_counters(monkeypatch, capsys):
    def sync(*, progress):
        for event in (
            {"event": "scan", "current": "PRIVATE_COUNTER", "total": 1,
             "downloaded": "PRIVATE_URL", "errors": True},
            {"event": "manifest", "total": "PRIVATE_TOKEN"},
            {"event": "reconcile_conflict", "count": -5},
            {"event": "done", "downloaded": 1_000_001,
             "unchanged": "PRIVATE_BOOK", "errors": 1.5},
        ):
            progress(event)
        return {"status": "complete", "sessions": 1, "manifests_fetched": 1}
    service = SimpleNamespace(connect_configured=lambda: None, sync_kobo_backup=sync)
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: SimpleNamespace(
        library=service, close=lambda: None,
    ))
    assert cli.main(["kobo-sync"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out)["status"] == "complete"
    for secret in ("PRIVATE_COUNTER", "PRIVATE_URL", "PRIVATE_TOKEN", "PRIVATE_BOOK"):
        assert secret not in output.out + output.err
    assert "téléchargés=0" in output.err
