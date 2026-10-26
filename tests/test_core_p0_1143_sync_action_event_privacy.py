"""P0 #1143: SQLite action statistics never persist private loan IDs/errors.

Real local SQLite; all providers, returned loans and synchronization outcomes
are offline fixtures. No network, ACSM, Adobe mutation, or extra workflow.
"""
from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.core import sync_coordination
from cifereca.models import Account, Loan
from tests.test_sync_state_contract import _service


PRIVATE = "https://private.invalid/loan.acsm?token=NEVER_ECHO_SYNC_EVENT"


def _actions(service):
    with service.storage._lock, service.storage._connect() as db:
        rows = db.execute(
            "SELECT action_type,outcome,facts_json FROM action_events"
            " ORDER BY occurred_at, event_id"
        ).fetchall()
    return [(kind, outcome, json.loads(facts)) for kind, outcome, facts in rows]


class Hostile(RuntimeError):
    def __str__(self):
        raise AssertionError("Must not stringify provider exception")
    def __repr__(self):
        raise AssertionError("Must not repr provider exception")


Hostile.__name__ = "NEVER_ECHO_SYNC_EVENT"


def test_source_sync_action_statistics_keep_closed_reason_and_exception(
    tmp_path, monkeypatch,
):
    service = _service(tmp_path)
    account = Account(
        display_name="Synthetic", unique_id="synthetic-account",
        provider_id="local", credential_id="fake", identity_id="default",
    )
    monkeypatch.setattr(
        sync_coordination, "InterProcessLock", lambda *_a: nullcontext(),
    )
    try:
        monkeypatch.setattr(
            service, "_synchronize_account_source_impl",
            lambda *_a, **_k: {"status": "skipped", "reason": PRIVATE},
        )
        result = service.synchronize_account_source(account)
        assert result["reason"] == PRIVATE  # Original Core return is unchanged.

        def fail(*_a, **_k):
            raise Hostile(PRIVATE)

        monkeypatch.setattr(service, "_synchronize_account_source_impl", fail)
        with pytest.raises(Hostile):
            service.synchronize_account_source(account)
        events = [x for x in _actions(service) if x[0] == "synchronization"]
        assert len(events) == 2
        assert ("synchronization", "skipped", {"deep": False, "reason": ""}) in events
        assert ("synchronization", "failed", {
            "deep": False, "error": "source_sync_failed",
        }) in events
        assert "NEVER_ECHO_SYNC_EVENT" not in repr(events)
        assert PRIVATE not in repr(events)
    finally:
        service.close()


@pytest.mark.parametrize("status", [
    "complete", "attention_required", "retryable_error",
])
def test_opening_sync_statistics_are_counts_without_raw_borrower_id(
    tmp_path, monkeypatch, status,
):
    service = _service(tmp_path)
    account = Account(
        display_name="Synthetic", unique_id="reader",
        provider_id="paris", credential_id="fake-paris", identity_id="default",
    )
    loan = Loan(
        id=PRIVATE, provider="PNB", medium="digital",
        record_id="fiction-book", title="Fictional",
        source_provider="paris", source_credential=account.credential_id,
        source_account_id=account.unique_id, identity_id="default",
        ebook_url="https://example.invalid/download.acsm",
        due_at="2099-01-01T00:00:00Z",
    )
    service.accounts = [account]
    monkeypatch.setattr(service, "_loans_for_account", lambda *_a, **_kw: [loan])
    monkeypatch.setattr(service, "_acquire_active_loan_automatically",
                        lambda _loan: {"status": status, "loan_id": PRIVATE})
    monkeypatch.setattr(service, "kobo_backup_enabled", lambda: False)
    try:
        enable_synthetic_paris_opening(service)
        outcome = service.opening_sync()
        assert outcome["identities"]
        acquisition_events = [x for x in _actions(service) if x[0] == "acquisition"]
        assert len(acquisition_events) == 1
        kind, actual, facts = acquisition_events[0]
        assert kind == "acquisition"
        assert actual == ("success" if status == "complete" else "failed")
        assert facts["origin"] == "opening_sync"
        assert "loan_id" not in facts
        assert facts.get("status") == (None if status == "complete" else status)
        assert PRIVATE not in repr(acquisition_events)
    finally:
        service.close()
