"""P0 #982: distinguish accepted Paris POST from confirmed borrower inventory.

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


def test_accepted_post_but_still_present_does_not_claim_return_or_repeat(tmp_path):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(loan)
        assert result["status"] == "still_active"
        assert result["request_accepted"] is True
        assert result["provider_inventory_verified"] is False
        assert session.posts == [("loan-a", "linked-b")]
        assert session.checks == ["linked-b"]
        # Double-click AND a delayed repeat must not send a second POST.
        assert svc.return_digital(loan)["status"] == "still_active"
        assert svc.return_digital(loan)["status"] == "still_active"
        assert len(session.posts) == 1
        assert svc.loan_return_state(loan) == result
        # No local rights, library files or catalog rows are ever realized here.
        assert svc.storage.entitlements(provider_id="paris") == []
    finally:
        svc.close()


def test_provider_absence_from_exact_borrower_confirms_and_survives_restart(tmp_path):
    loan = _loan()
    session = Session([])
    attest_paris_dilicom_complete(session, credential_id="paris-a")
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(loan)
        assert result["status"] == "provider_return_confirmed"
        assert result["verification"] == "provider_absent"
        assert result["provider_inventory_verified"] is True
        assert session.posts == [("loan-a", "linked-b")]
        assert session.checks == ["linked-b"]
    finally:
        svc.close()
    restarted = _library(tmp_path, session=session)
    try:
        assert restarted.return_digital(loan)["status"] == "provider_return_confirmed"
        assert restarted.loan_return_state(loan)["verification"] == "provider_absent"
        assert len(session.posts) == 1
    finally:
        restarted.close()


def test_partial_dilicom_failure_never_verifies_absence(tmp_path):
    loan = _loan()
    session = Session([])
    session.last_loan_errors = [{"upstream": "DILICOM", "type": "TimeoutError"}]
    svc = _library(tmp_path, session=session)
    try:
        result = svc.return_digital(loan)
        assert result["status"] == "verification_unavailable"
        assert result["verification"] == "unavailable"
        assert result["request_accepted"] is True
        assert len(session.posts) == 1
        session.last_loan_errors = []
        # A side-channel reset cannot prove the end of DILICOM pagination.
        assert svc.verify_return_digital(loan)["status"] == "verification_unavailable"
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        assert svc.verify_return_digital(loan)["status"] == "provider_return_confirmed"
        assert len(session.posts) == 1
    finally:
        svc.close()


def test_uncertain_post_or_malformed_ack_is_never_resent_automatically(tmp_path):
    loan = _loan()
    session = Session([])
    session.failure = TimeoutError("unknown network delivery")
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(loan)["status"] == "verification_unavailable"
        assert svc.return_digital(loan)["status"] == "verification_unavailable"
        assert len(session.posts) == 1
        session.failure = None
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        assert svc.verify_return_digital(loan)["status"] == "provider_return_confirmed"
    finally:
        svc.close()


def test_explicit_retry_checks_first_and_only_reposts_if_still_present(tmp_path):
    loan = _loan()
    session = Session([loan])
    svc = _library(tmp_path, session=session)
    try:
        assert svc.return_digital(loan)["status"] == "still_active"
        # The caller must explicitly request a retry; it performs a fresh read
        # FIRST, then posts at most once to the same borrower.
        assert svc.return_digital(loan, explicit_retry=True)["status"] == "still_active"
        assert len(session.posts) == 2
        assert len(session.checks) == 3
        session.inventory = []
        attest_paris_dilicom_complete(session, credential_id="paris-a")
        assert svc.return_digital(loan, explicit_retry=True)["status"] == "provider_return_confirmed"
        assert len(session.posts) == 2
    finally:
        svc.close()


def test_unknown_borrower_or_session_never_posts(tmp_path):
    session = Session([])
    svc = _library(tmp_path, session=session)
    try:
        with pytest.raises(AttentionRequired) as err:
            svc.return_digital(replace(_loan(), source_account_id=None))
        assert err.value.code == "loan_return_borrower_unproven"
        with pytest.raises(Exception):
            svc.return_digital(replace(_loan(), source_account_id="unknown"))
        with pytest.raises(Exception):
            svc.return_digital(_loan(credential="absent-connection"))
        assert session.posts == []
        assert not session.posts
    finally:
        svc.close()


def test_atomic_sqlite_claim_only_one_winner(tmp_path):
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


def test_return_guard_preserves_local_file_and_is_provider_only(tmp_path):
    library = tmp_path / "existing.epub"
    library.write_text("unmodified")
    svc = _library(tmp_path, session=Session([]))
    try:
        result = svc.return_digital(_loan())
        assert result["scope"] == "provider_pnb_only"
        assert library.read_text() == "unmodified"
        assert not result.get("adept_return_confirmed")
    finally:
        svc.close()

def test_stale_inflight_post_is_verified_after_restart_without_new_post(tmp_path):
    """Simulate crash after reservation/dispatch but before HTTP response."""
    from cifereca.core.loan_return_guard import return_key
    import json

    loan = _loan()
    session = Session([])
    svc = _library(tmp_path, session=session)
    key = return_key("paris", "paris-a", "linked-b", loan.id)
    try:
        claimed, _ = svc.storage.loan_return_claim(key, "inflight-op")
        assert claimed
        # Simulate persisted checkpoint from a process that died >3 min ago.
        record = svc.storage.loan_return_state(key)
        record["requested_at"] = "2026-01-01T00:00:00+00:00"
        svc.storage.metadata_set(key, json.dumps(record))
    finally:
        svc.close()

    next_session = Session([])
    attest_paris_dilicom_complete(next_session, credential_id="paris-a")
    reopened = _library(tmp_path, session=next_session)
    try:
        # A second button click cannot POST; explicit verification can read.
        assert reopened.return_digital(loan)["status"] == "request_sent"
        verified = reopened.verify_return_digital(loan)
        assert verified["status"] == "provider_return_confirmed"
        assert verified["verification"] == "provider_absent"
        assert next_session.posts == []
        assert next_session.checks == ["linked-b"]
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
        assert session.checks == []
        assert session.posts == []
    finally:
        svc.close()
