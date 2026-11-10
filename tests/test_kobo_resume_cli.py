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
        connect_kobo_canonical=connect,
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
    assert parsed["resume_attempted"] is True
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
        connect_kobo_canonical=lambda credential: calls.append(("connect", credential)),
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

