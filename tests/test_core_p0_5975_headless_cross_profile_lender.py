"""P0 #5975: headless lender read + transfer workers use an explicit revocable lease."""
from __future__ import annotations

import ast
import inspect
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core import borrow_for_identity_progress as progress
from cifereca.core import borrow_for_identity_placement_resolution as placement
from cifereca.core import borrow_for_identity_transfer_preflight as preflight
from cifereca.core import borrow_for_identity_transfer_executor as executor
from cifereca.core import borrow_for_identity_send_composition as composition
from cifereca.core import borrow_for_identity_existing_loan as recovery
from cifereca.services import LibraryService, _cross_profile_read_scope_current
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


class Headless:
    _account_context_epoch = 7
    _source_context_epoch = 11

    @property
    def current_identity(self):
        raise AssertionError("Core inspected mutable GTK selection")


def test_core_workers_have_no_current_identity_navigation():
    for worker in (
        progress.observed_cross_profile_borrow_progress,
        placement.resolve_cross_profile_lender_placement,
        preflight.preflight_cross_profile_loan_transfer,
        executor.send_cross_profile_borrowed_placement,
        composition.send_confirmed_lender_local_copy,
        recovery.existing_lender_loan_candidates,
    ):
        tree = ast.parse(inspect.getsource(worker))
        assert not any(isinstance(node, ast.Attribute)
                       and node.attr == "current_identity"
                       for node in ast.walk(tree))


def test_unscoped_lender_queries_fail_before_private_storage():
    w = witness()
    class Unscoped(Headless):
        @property
        def storage(self):
            raise AssertionError("scope absent: no private SQLite reads")
    host = Unscoped()
    assert progress.observed_cross_profile_borrow_progress(
        host, requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice,
    ).reason_code == "lender_scope_unverified"
    assert placement.resolve_cross_profile_lender_placement(
        host, requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice,
    ).status == "unavailable"
    assert preflight.preflight_cross_profile_loan_transfer(
        host, requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice, lender_placement_id="P",
    ).executable is False
    assert recovery.existing_lender_loan_candidates(
        host, exact_notice=w.notice,
    ) == ()


def test_explicit_headless_scope_reads_exact_lender_without_navigation():
    w = witness()
    account = w.account
    storage = NS(
        identity_for_connection=Mock(return_value=account.identity_id),
        provider_connections=Mock(return_value=[{
            "identity_id": account.identity_id,
            "provider_id": account.provider_id,
            "credential_id": account.credential_id,
            "enabled": True,
        }]),
        metadata_get=Mock(return_value=None),
        loan_borrow_state=Mock(return_value=None),
    )
    class Host(Headless):
        accounts = [account]
        def identity_by_id(self, identity):
            return NS(identity_id=identity) if identity in (
                "bob", account.identity_id,
            ) else None
    host = Host()
    host.storage = storage
    outcome = progress.observed_cross_profile_borrow_progress(
        host, requesting_identity_id="bob", borrowing_account=account,
        exact_notice=w.notice, scope_current=lambda: True,
    )
    assert outcome.loan == "not_attempted", outcome
    assert outcome.executable is outcome.retry_borrow is False
    storage.metadata_get.assert_called_once()
    storage.loan_borrow_state.assert_called_once()


def test_missing_or_revoked_scope_never_reaches_local_copy():
    w = witness()
    host = Headless()
    host.materialize_to_identity = Mock(side_effect=AssertionError("no copy"))
    for scope in (None, lambda: False):
        result = executor.send_cross_profile_borrowed_placement(
            host, requesting_identity_id="bob", borrowing_account=w.account,
            exact_notice=w.notice, lender_placement_id="P",
            confirmed=True, scope_current=scope,
        )
        assert result["status"] == "blocked"
    host.materialize_to_identity.assert_not_called()


def test_post_materialization_revoke_suppresses_success(monkeypatch):
    w = witness()
    host = Headless()
    lease = {"current": True}
    monkeypatch.setattr(
        executor, "preflight_cross_profile_loan_transfer",
        lambda *_a, **_kw: preflight.CrossProfileTransferPreflight(
            "confirmed", "present", "ready_for_send", "ready",
            executable=True,
        ),
    )
    def copy(*_args):
        lease["current"] = False
        return {"status": "complete"}
    host.materialize_to_identity = Mock(side_effect=copy)
    result = executor.send_cross_profile_borrowed_placement(
        host, requesting_identity_id="bob", borrowing_account=w.account,
        exact_notice=w.notice, lender_placement_id="P", confirmed=True,
        scope_current=lambda: lease["current"],
    )
    assert result["status"] == "blocked"
    assert result["reason_code"] == "transfer_context_revoked"
    assert result["retry_borrow"] is False
    host.materialize_to_identity.assert_called_once_with("P", "bob")


def test_facade_scope_rejects_aba_and_same_id_replacement():
    identity = NS(identity_id="bob")
    service = NS(current_identity=identity,
                 _account_context_epoch=13, _source_context_epoch=19)
    lease = _cross_profile_read_scope_current(service, "bob")
    assert lease() is True
    service._account_context_epoch += 2
    assert lease() is False
    service._account_context_epoch -= 2
    service.current_identity = NS(identity_id="bob")
    assert lease() is False


def test_facade_supplies_lease_without_changing_public_signature(monkeypatch):
    w = witness()
    service = NS(current_identity=NS(identity_id="bob"),
                 _account_context_epoch=2, _source_context_epoch=4)
    captured = []
    def observe(_service, **kwargs):
        captured.append(kwargs)
        assert kwargs["scope_current"]() is True
        service._account_context_epoch += 2
        assert kwargs["scope_current"]() is False
        return progress.CrossProfileBorrowProgress(
            "unknown", "unavailable", "blocked", "lender_scope_unverified",
        )
    monkeypatch.setattr(progress, "observed_cross_profile_borrow_progress", observe)
    outcome = LibraryService.cross_profile_borrow_progress(
        service, requesting_identity_id="bob",
        borrowing_account=w.account, exact_notice=w.notice,
    )
    assert outcome.reason_code == "lender_scope_unverified"
    assert len(captured) == 1
