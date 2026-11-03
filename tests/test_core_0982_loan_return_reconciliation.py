"""P1 #4048: archived Paris return receipts remain readable, but cannot post.

All requests use fake sessions and temporary SQLite. These tests preserve
historical claim/inventory evidence without executing a voluntary return.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace as NS
from threading import Barrier, Thread

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan
from cifereca.plugin_api import AttentionRequired
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _loan(uid="linked-b", credential="paris-a", loan_id="loan-a"):
    return Loan(
        id=loan_id, provider="DILICOM", medium="digital", record_id="book-a",
        title="Livre Paris", source_provider="paris", source_credential=credential,
        source_account_id=uid, identity_id="default", can_return=True,
    )


class Session:
    provider_id = "paris"
    connected = True
    capabilities = NS(loans=True)
    def __init__(self, inventory):
        self.inventory = list(inventory)
        self.posts = []
        self.checks = []
        self.failure = None
        self.last_loan_errors = []
        self.response = {"success": True}

    def return_digital(self, loan, account):
        self.posts.append((loan.id, account.unique_id))
        if self.failure:
            raise self.failure
        return dict(self.response)

    def loans(self, account):
        self.checks.append(account.unique_id)
        if self.failure:
            raise self.failure
        return list(self.inventory)


def _library(tmp_path, *, session=None):
    cfg = Config(tmp_path / "config.json")
    cfg.set("library.directory", str(tmp_path / "EBooks"))
    cfg.set("providers.accounts", [])
    svc = LibraryService(cfg, Storage(tmp_path / "return.sqlite3"), Diagnostics())
    svc.accounts = [
        Account(display_name="Paris login", unique_id="", is_primary=True,
                provider_id="paris", credential_id="paris-a", identity_id="default"),
        Account(display_name="Linked borrower", unique_id="linked-b", is_primary=False,
                provider_id="paris", credential_id="paris-a", identity_id="default"),
        Account(display_name="Different connection", unique_id="", is_primary=True,
                provider_id="paris", credential_id="paris-b", identity_id="other"),
    ]
    svc._sessions["paris::paris-a"] = session or Session([])
    svc._sessions["paris::paris-b"] = Session([])
    return svc



def test_retired_return_never_posts_even_when_borrower_is_still_active(tmp_path):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        for _attempt in range(3):
            with pytest.raises(AttentionRequired) as err:
                svc.return_digital(loan)
            assert err.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert svc.loan_return_state(loan) is None
        assert svc.storage.entitlements(provider_id="paris") == []
    finally:
        svc.close()


def test_historical_claim_survives_restart_without_new_post(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    loan = _loan()
    session = Session([])
    svc = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert svc.storage.loan_return_claim(key, "before-upgrade")[0] is True
        before = svc.storage.loan_return_state(key)
        with pytest.raises(AttentionRequired) as err:
            svc.return_digital(loan)
        assert err.value.code == "loan_return_removed"
        assert svc.storage.loan_return_state(key) == before
    finally:
        svc.close()
    reopened = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired):
            reopened.return_digital(loan, explicit_retry=True)
        assert reopened.storage.loan_return_state(key) == before
        assert session.posts == session.checks == []
    finally:
        reopened.close()


def test_legacy_exact_absence_may_be_verified_read_only(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    loan = _loan()
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert svc.storage.loan_return_claim(key, "before-upgrade")[0] is True
        # A fresh claim is still in-flight; simulate a receipt from an older
        # installation before exercising read-only inventory recovery.
        archived = svc.storage.loan_return_state(key)
        archived["requested_at"] = "2026-01-01T00:00:00+00:00"
        svc.storage.metadata_set(key, json.dumps(archived))
        result = svc.verify_return_digital(loan)
        assert result["status"] == "provider_return_confirmed"
        assert result["verification"] == "provider_absent"
        assert result["provider_inventory_verified"] is True
        assert session.posts == []
        assert session.checks == ["linked-b"]
        with pytest.raises(AttentionRequired):
            svc.return_digital(loan)
        assert session.posts == []
    finally:
        svc.close()


def test_partial_inventory_does_not_confirm_historical_return(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    loan = _loan()
    session = Session([])
    session.last_loan_errors = [{"upstream": "DILICOM", "type": "TimeoutError"}]
    svc = _library(tmp_path, session=session)
    try:
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert svc.storage.loan_return_claim(key, "before-upgrade")[0] is True
        archived = svc.storage.loan_return_state(key)
        archived["requested_at"] = "2026-01-01T00:00:00+00:00"
        svc.storage.metadata_set(key, json.dumps(archived))
        result = svc.verify_return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert session.posts == []
        with pytest.raises(AttentionRequired):
            svc.return_digital(loan)
        assert session.posts == []
    finally:
        svc.close()


@pytest.mark.parametrize("retry", [False, True, "true", 1, None])
def test_retry_argument_never_reopens_retired_return(tmp_path, retry):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as err:
            svc.return_digital(loan, explicit_retry=retry)
        assert err.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
        assert svc.loan_return_state(loan) is None
    finally:
        svc.close()


def test_unknown_borrower_and_unknown_session_still_cannot_return(tmp_path):
    session = Session([])
    svc = _library(tmp_path, session=session)
    try:
        for loan in (
            replace(_loan(), source_account_id=None),
            replace(_loan(), source_account_id="unknown"),
            _loan(credential="absent-connection"),
        ):
            with pytest.raises(AttentionRequired) as err:
                svc.return_digital(loan)
            assert err.value.code == "loan_return_removed"
        assert session.posts == session.checks == []
    finally:
        svc.close()


def test_atomic_sqlite_claim_only_one_winner_for_existing_historical_rows(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    db = Storage(tmp_path / "parallel.sqlite3")
    key = return_key("paris", "paris-a", "linked-b", "loan-a")
    results = []
    barrier = Barrier(2)
    def worker(index):
        barrier.wait()
        results.append(db.loan_return_claim(key, "op-" + str(index))[0])
    threads = [Thread(target=worker, args=(i,)) for i in range(2)]
    try:
        for worker_thread in threads:
            worker_thread.start()
        for worker_thread in threads:
            worker_thread.join(timeout=4)
        assert sorted(results) == [False, True]
        assert db.loan_return_state(key)["status"] == "request_sent"
    finally:
        db.close()


def test_retired_return_preserves_existing_local_file(tmp_path):
    library = tmp_path / "existing.epub"
    library.write_bytes(b"unmodified")
    session = Session([])
    svc = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as err:
            svc.return_digital(_loan())
        assert err.value.code == "loan_return_removed"
        assert library.read_bytes() == b"unmodified"
        assert session.posts == session.checks == []
    finally:
        svc.close()


def test_stale_inflight_receipt_can_only_be_verified_after_restart(tmp_path):
    from cifereca.core.loan_return_guard import return_key
    import json

    loan = _loan()
    session = Session([])
    svc = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        assert svc.storage.loan_return_claim(key, "inflight-op")[0]
        record = svc.storage.loan_return_state(key)
        record["requested_at"] = "2026-01-01T00:00:00+00:00"
        svc.storage.metadata_set(key, json.dumps(record))
    finally:
        svc.close()

    next_session = Session([])
    attest_paris_dilicom_complete(next_session, credential_id="paris-a")
    reopened = _library(tmp_path, session=next_session)
    try:
        with pytest.raises(AttentionRequired):
            reopened.return_digital(loan)
        verified = reopened.verify_return_digital(loan)
        assert verified["status"] == "provider_return_confirmed"
        assert verified["verification"] == "provider_absent"
        assert next_session.posts == []
        assert next_session.checks == ["linked-b"]
    finally:
        reopened.close()


def test_recent_inflight_remains_read_only(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert svc.storage.loan_return_claim(key, "inflight")[0]
        result = svc.verify_return_digital(loan)
        assert result["status"] == "request_sent"
        assert session.checks == session.posts == []
    finally:
        svc.close()
