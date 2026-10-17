"""P0 #1905: exact owned purchase preflight, never acquisition authority."""
from __future__ import annotations

from dataclasses import replace
import json

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from tests.test_core_p0_1905_numilog_offline_preview import _owned, _item
from tests.test_core_p0_1862_numilog_store_mirror import _connect
from tests.test_sync_state_contract import _service


def _rig(tmp_path):
    service = _service(tmp_path)
    account, session, calls = _connect(service, number=0)
    item, entitlement = _owned(service, account)
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    session.library = lambda: pytest.fail("preflight cannot refresh private manifest")
    session.download_direct = lambda *_a, **_k: pytest.fail("no fulfillment")
    return service, account, session, calls, item, entitlement, broker, sid


def _check(result, code, *, candidate=False):
    assert result.ok is candidate
    assert result.changed == 0
    assert result.facts == {
        "phase": "materialize", "status": code,
        "read_only": True, "offline": True,
        "acquisition_authorized": False, "confirmation_available": False,
        "schedule_available": False, "manifest_refreshed": False,
        "requires_manifest_validation": True,
        "candidate_unverified": candidate,
    }
    assert result.warnings == (() if candidate else (code,))


def test_one_exact_owned_purchase_is_only_an_unverified_candidate(tmp_path, monkeypatch):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        service.current_identity = service.identity_by_id("alex")
        writes = []
        monkeypatch.setattr(
            service.storage, "metadata_set",
            lambda *_a, **_k: pytest.fail("preflight cannot write preference"),
        )
        monkeypatch.setattr(
            service.storage, "acquisition_put",
            lambda *_a, **_k: pytest.fail("preflight cannot write acquisition"),
        )
        observed = []
        original = service.storage.numilog_purchase_preflight_evidence

        def scoped(**kwargs):
            observed.append(kwargs)
            return original(**kwargs)

        monkeypatch.setattr(service.storage, "numilog_purchase_preflight_evidence", scoped)
        for _ in range(2):
            _check(broker.preflight_numilog_purchase(sid, item),
                   "candidate_unverified", candidate=True)
        assert observed == [
            {"identity_id": account.identity_id,
             "credential_id": account.credential_id,
             "account_id": account.unique_id, "item_id": item}
        ] * 2
        assert broker.local_materialization_schedule(sid) == "not_supported"
        assert calls == writes == []
    finally:
        service.close()


@pytest.mark.parametrize(("mutation", "expected"), [
    ("no_right", "purchase_unproven"),
    ("wrong_provenance", "purchase_unproven"),
    ("not_downloadable", "not_downloadable"),
    ("loan", "purchase_unproven"),
    ("expired", "purchase_unproven"),
    ("inactive", "purchase_unproven"),
    ("duplicate_right", "needs_reconciliation"),
    ("complete_ledger", "needs_reconciliation"),
    ("failed_ledger", "needs_reconciliation"),
    ("foreign_ledger", "needs_reconciliation"),
])
def test_rights_and_ledger_conflicts_fail_closed(tmp_path, mutation, expected):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        if mutation == "no_right":
            with service.storage._lock, service.storage._connect() as db:
                db.execute("DELETE FROM entitlements WHERE entitlement_id=?", (entitlement,))
        if mutation in {"wrong_provenance", "not_downloadable"}:
            metadata = (
                {"source": "numilog.public_catalog"}
                if mutation == "wrong_provenance" else
                {"source": "numilog.authenticated_library", "downloadable": False}
            )
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "UPDATE entitlements SET metadata_json=? WHERE entitlement_id=?",
                    (json.dumps(metadata), entitlement),
                )
        if mutation in {"loan", "expired", "inactive"}:
            field, value = {
                "loan": ("loan_id", "fictional-loan"),
                "expired": ("expires_at", "2090-01-01T00:00:00Z"),
                "inactive": ("state", "revoked"),
            }[mutation]
            with service.storage._lock, service.storage._connect() as db:
                db.execute(
                    "UPDATE entitlements SET " + field + "=? WHERE entitlement_id=?",
                    (value, entitlement),
                )
        if mutation == "duplicate_right":
            service.storage.entitlement_put({
                "entitlement_id": "legacy-double-owned-id",
                "provider_id": "numilog",
                "credential_id": account.credential_id,
                "account_id": account.unique_id,
                "identity_id": account.identity_id,
                "item_id": item, "state": "active", "entitlement_type": "owned",
                "metadata": {"source": "numilog.authenticated_library"},
            })
        if mutation.endswith("_ledger"):
            service.storage.acquisition_put({
                "acquisition_id": "purchase-conflict",
                "provider_id": "numilog",
                "credential_id": account.credential_id,
                "account_id": "different" if mutation == "foreign_ledger"
                              else account.unique_id,
                "identity_id": "alex" if mutation == "foreign_ledger"
                               else account.identity_id,
                "item_id": item, "entitlement_id": entitlement,
                "status": "complete" if mutation != "failed_ledger"
                          else "retryable_error",
                "metadata": {"private_url": "SECRET_DO_NOT_ECHO"},
            })
        result = broker.preflight_numilog_purchase(sid, item)
        _check(result, expected)
        assert "SECRET_DO_NOT_ECHO" not in repr(result)
        assert account.credential_id not in repr(result)
        assert item not in repr(result)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize(("mutation", "expected"), [
    ("disabled", "invalid_source"),
    ("rebound", "invalid_source"),
    ("disconnected", "source_not_ready"),
    ("no_direct_download", "source_not_ready"),
    ("removed_session", "source_not_ready"),
    ("unknown_identity", "invalid_source"),
    ("public_item", "invalid_item_id"),
    ("wrong_credential_hash", "invalid_item_id"),
    ("null_item", "invalid_item_id"),
    ("other_source", "invalid_source"),
])
def test_preflight_refuses_invalid_scope_or_item(tmp_path, mutation, expected):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        if mutation == "disabled":
            broker.set_enabled(sid, False)
        elif mutation == "rebound":
            service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        elif mutation == "disconnected":
            session.connected = False
        elif mutation == "no_direct_download":
            session.capabilities.direct_download = False
        elif mutation == "removed_session":
            service._sessions.clear()
        elif mutation == "unknown_identity":
            account.identity_id = "unconfigured"
        elif mutation == "public_item":
            item = "numilog:public-not-owned"
        elif mutation == "wrong_credential_hash":
            item = _item("OTHER-VAULT", "123")
        elif mutation == "null_item":
            item = None
        elif mutation == "other_source":
            sid = "unknown-source"
        result = broker.preflight_numilog_purchase(sid, item)
        _check(result, expected)
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("mutation", ["rebind", "session_swap", "source_disabled"])
def test_changes_during_db_read_discard_entire_preflight(
    tmp_path, monkeypatch, mutation,
):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        original = service.storage.numilog_purchase_preflight_evidence

        def changed(**kwargs):
            evidence = original(**kwargs)
            if mutation == "rebind":
                service.storage.provider_connection_put({
                    "identity_id": "alex", "provider_id": "numilog",
                    "credential_id": account.credential_id, "enabled": True,
                })
            elif mutation == "session_swap":
                service._sessions[service._session_key("numilog", account.credential_id)] = (
                    replace(session) if hasattr(session, "__dataclass_fields__") else
                    type(session)(**vars(session))
                )
            else:
                service.storage.metadata_set("sync.source.enabled." + sid, "0")
            return evidence

        monkeypatch.setattr(service.storage, "numilog_purchase_preflight_evidence", changed)
        _check(broker.preflight_numilog_purchase(sid, item), "source_changed")
        assert calls == []
    finally:
        service.close()


def test_preflight_storage_queries_only_one_right_and_same_edition_ledger(tmp_path):
    service, account, session, calls, item, entitlement, broker, sid = _rig(tmp_path)
    try:
        db = service.storage._connect()
        trace = []
        db.set_trace_callback(trace.append)
        try:
            _check(broker.preflight_numilog_purchase(sid, item),
                   "candidate_unverified", candidate=True)
        finally:
            db.set_trace_callback(None)
        rights_sql = [s for s in trace if s.startswith("SELECT entitlement_id,identity_id")
                      and "FROM entitlements" in s]
        ledger_sql = [s for s in trace if s.startswith("SELECT acquisition_id,entitlement_id")
                      and "FROM acquisition_ledger" in s]
        assert len(rights_sql) == len(ledger_sql) == 1
        assert "identity_id=" in rights_sql[0] and "account_id=" in rights_sql[0]
        assert "credential_id=" in ledger_sql[0] and "item_id=" in ledger_sql[0]
        assert " LIMIT 3" in rights_sql[0] and " LIMIT 3" in ledger_sql[0]
        assert not any(s.startswith("UPDATE ") or s.startswith("INSERT ")
                       or s.startswith("DELETE ") for s in trace)
        assert calls == []
    finally:
        service.close()
