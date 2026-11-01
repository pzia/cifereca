"""P1 #4048: retired PNB return, immutable historical SQLite receipts.

All requests are fakes: NO network, Adobe mutation, file deletion, GTK, or
additional CI workflow. Durable SQLite claims prevent duplicate POSTs across
threads and restarts, including cases where the first POST timed out.
"""
from __future__ import annotations

from tests.loan_return_inventory_fixtures import attest_paris_dilicom_complete

from dataclasses import replace
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


DENIED = {"status": "blocked", "code": "loan_return_removed", "retry": "none"}


def test_active_loan_return_denied_without_network_checkpoint_or_rights(tmp_path):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        before = svc.loan_return_state(loan)
        for _ in range(3):
            assert svc.return_digital(loan) == DENIED
        assert svc.loan_return_state(loan) == before
        assert session.posts == [] and session.checks == []
        assert svc.storage.entitlements(provider_id="paris") == []
    finally:
        svc.close()


def test_complete_inventory_does_not_authorize_return_across_restart(tmp_path):
    loan = _loan()
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(loan) == DENIED
        assert svc.loan_return_state(loan) is None
        assert session.posts == [] and session.checks == []
    finally:
        svc.close()
    reopened = _library(tmp_path, session=session)
    try:
        assert reopened.return_digital(loan) == DENIED
        assert reopened.loan_return_state(loan) is None
        assert session.posts == [] and session.checks == []
    finally:
        reopened.close()


def test_partial_dilicom_failure_cannot_start_a_return(tmp_path):
    session = Session([])
    session.last_loan_errors = [{"upstream": "DILICOM", "type": "TimeoutError"}]
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(_loan()) == DENIED
        assert session.posts == [] and session.checks == []
        session.last_loan_errors = []
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        assert svc.return_digital(_loan()) == DENIED
        assert session.posts == []
    finally:
        svc.close()


def test_old_timeout_or_malformed_ack_cannot_trigger_a_new_return(tmp_path):
    session = Session([])
    session.failure = TimeoutError("unknown network delivery")
    session.response = {"success": object()}
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(_loan()) == DENIED
        assert svc.return_digital(_loan()) == DENIED
        assert session.posts == [] and session.checks == []
    finally:
        svc.close()


@pytest.mark.parametrize("retry", [False, True, 1, "true", {"retry": True}])
def test_explicit_retry_is_ineffective_after_return_retirement(tmp_path, retry):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(loan, explicit_retry=retry) == DENIED
        assert session.posts == [] and session.checks == []
        assert svc.loan_return_state(loan) is None
    finally:
        svc.close()


def test_foreign_borrower_or_missing_session_cannot_start_a_return(tmp_path):
    session = Session([])
    svc = _library(tmp_path, session=session)
    try:
        for variant in (
            replace(_loan(), source_account_id=None),
            replace(_loan(), source_account_id="unknown"),
            _loan(credential="absent-connection"),
        ):
            assert svc.return_digital(variant) == DENIED
        assert session.posts == [] and session.checks == []
    finally:
        svc.close()


def test_historical_sqlite_claim_is_still_readable_and_at_most_once(tmp_path):
    db = Storage(tmp_path / "parallel.sqlite3")
    from cifereca.core.loan_return_guard import return_key
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


def test_retired_return_preserves_existing_local_file_and_sqlite(tmp_path):
    library = tmp_path / "existing.epub"
    library.write_text("unmodified")
    svc = _library(tmp_path, session=Session([]))
    try:
        assert svc.return_digital(_loan()) == DENIED
        assert library.read_text() == "unmodified"
        assert svc.loan_return_state(_loan()) is None
    finally:
        svc.close()


def test_stale_inflight_checkpoint_survives_restart_without_return_or_post(tmp_path):
    from cifereca.core.loan_return_guard import return_key

    loan = _loan()
    session = Session([])
    svc = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        claimed, _ = svc.storage.loan_return_claim(key, "inflight-op")
        assert claimed
        record = svc.storage.loan_return_state(key)
        assert record["status"] == "request_sent"
    finally:
        svc.close()

    reopened = _library(tmp_path, session=session)
    try:
        before = reopened.storage.loan_return_state(key)
        assert reopened.return_digital(loan) == DENIED
        assert reopened.storage.loan_return_state(key) == before
        assert session.posts == [] and session.checks == []
    finally:
        reopened.close()


def test_recent_inflight_does_not_race_with_first_post(tmp_path):
    from cifereca.core.loan_return_guard import return_key
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        key = return_key("paris", "paris-a", "linked-b", loan.id)
        assert svc.storage.loan_return_claim(key, "inflight")[0]
        result = svc.verify_return_digital(loan)
        assert result["status"] == "request_sent"
        assert session.checks == [] and session.posts == []
    finally:
        svc.close()
