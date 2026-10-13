"""Kobo P0: CLI resume requires consent and touches only one connected source."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from cifereca import cli


@pytest.mark.parametrize("arguments", [
    ["kobo-resume", "--credential", "vault-1"],
    ["kobo-resume", "--credential", "vault-1", "--retry-scope-blocked"],
    ["kobo-resume", "--credential", "vault-1", "--confirm-reauthorized"],
    [
        "kobo-resume", "--credential", "vault-1", "--confirm-downloads",
        "--retry-scope-blocked",
    ],
    [
        "kobo-resume", "--credential", "vault-1", "--confirm-downloads",
        "--confirm-reauthorized",
    ],
])
def test_resume_refuses_missing_approval_before_opening_core(monkeypatch, arguments):
    def forbid_open(**_kwargs):
        pytest.fail("rejected command must never open Core or contact providers")

    monkeypatch.setattr(cli.CiferecaCore, "open", forbid_open)
    with pytest.raises(SystemExit) as exc:
        cli.main(arguments)
    assert exc.value.code == 2


@pytest.mark.parametrize("status,expected_exit", [
    ("complete", 0), ("partial", 2), ("attention_required", 2),
    ("failed", 2), ("cancelled", 2),
])
def test_resume_connects_only_target_and_sanitizes_output(
    monkeypatch, capsys, status, expected_exit,
):
    actions = []
    credential = "PRIVATE_KOBO_CREDENTIAL"
    def connect(target):
        actions.append(("connect_one", target))
    def resume(target, *, retry_scope_blocked=False):
        actions.append(("resume_one", target, retry_scope_blocked))
        return {
            "status": status, "scope": "account",
            "manifest_items": 3, "attempted": 1, "downloaded": 1,
            "unchanged": 1, "attention_required": 1, "errors": 0,
            "sources": [{"credential_id": credential, "title": "SECRET_BOOK"}],
            "error_messages": ["TOKEN_SECRET"], "target_dir": "/private/folder",
        }

    service = SimpleNamespace(
        connect_kobo_preview=connect,
        sync_kobo_backup_account=resume,
        connect_configured=lambda: pytest.fail("global connect forbidden"),
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: SimpleNamespace(
        library=service, close=lambda: actions.append(("close",)),
    ))
    exit_code = cli.main([
        "kobo-resume", "--credential", credential, "--confirm-downloads",
    ])
    public = capsys.readouterr().out
    parsed = json.loads(public)
    assert exit_code == expected_exit
    assert parsed["status"] == status
    assert parsed["downloaded"] == 1
    assert parsed["attempted"] == 1
    assert parsed["acquisition_started"] is True
    for sensitive in (
        credential, "SECRET_BOOK", "TOKEN_SECRET", "/private/folder",
        "credential_id", "target_dir", "error_messages", "sources",
    ):
        assert sensitive not in public
    assert actions == [
        ("connect_one", credential),
        ("resume_one", credential, False),
        ("close",),
    ]


def test_scope_retry_needs_separate_reauthorization_ack(monkeypatch, capsys):
    calls = []
    service = SimpleNamespace(
        connect_kobo_preview=lambda credential: calls.append(("connect", credential)),
        sync_kobo_backup_account=lambda credential, *, retry_scope_blocked=False: (
            calls.append(("sync", credential, retry_scope_blocked)) or
            {"status": "attention_required", "blocked_non_retryable": 2}
        ),
        connect_configured=lambda: pytest.fail("must not connect other sources"),
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: SimpleNamespace(
        library=service, close=lambda: None,
    ))
    rc = cli.main([
        "kobo-resume", "--credential", "vault-1", "--confirm-downloads",
        "--retry-scope-blocked", "--confirm-reauthorized",
    ])
    result = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert result["blocked_non_retryable"] == 2
    assert calls == [("connect", "vault-1"), ("sync", "vault-1", True)]


def test_end_to_end_resume_with_real_service_resets_durable_checkpoint(
    tmp_path, monkeypatch, capsys,
):
    from cifereca.core import provider_sessions
    from cifereca.core.kobo_backup import _kobo_checkpoint_key
    from tests.test_kobo_backup_reconcile_prune import _service

    service = _service(tmp_path, manifest=[])
    target_session = service._sessions[service._session_key("kobo", "vault-1")]
    key = _kobo_checkpoint_key("default", "vault-1")
    service.storage.metadata_set(key, json.dumps({
        "schema_version": 1, "status": "failed",
        "updated_at": int(time.time()), "failure_streak": 6,
    }))
    selected = []
    monkeypatch.setattr(
        provider_sessions, "connect_kobo_preview",
        lambda owner, credential: (
            selected.append((owner is service, credential)) or target_session
        ),
    )
    monkeypatch.setattr(
        service, "connect_configured",
        lambda: pytest.fail("a manual resume may not connect all providers"),
    )
    monkeypatch.setattr(
        service, "_execute_kobo_acquisition",
        lambda *_args, **_kwargs: pytest.fail("empty manifest cannot download"),
    )
    monkeypatch.setattr(cli.CiferecaCore, "open", lambda **_kwargs: SimpleNamespace(
        library=service, close=lambda: None,
    ))
    try:
        assert service._kobo_backup_account_due("vault-1") is False
        assert cli.main([
            "kobo-resume", "--credential", "vault-1", "--confirm-downloads",
        ]) == 0
        result = json.loads(capsys.readouterr().out)
        assert result["status"] == "complete"
        assert result["downloaded"] == 0
        assert selected == [(True, "vault-1")]
        checkpoint = json.loads(service.storage.metadata_get(key))
        assert checkpoint["status"] == "complete"
        assert checkpoint["failure_streak"] == 0
    finally:
        service.close()
