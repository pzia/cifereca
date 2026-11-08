"""P1 #5136: post-timeout Paris check from Search, including after restart.

Synthetic supplier; actual SQLite durable claim and Core service. No POST
after a spent attempt, no loan publication, no raw ACSM/credentials in UI.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from types import SimpleNamespace as NS
from pathlib import Path

import pytest

from cifereca.core.borrow_action_scope import borrow_claim_key
from cifereca.gui.bnf_borrow_controller import present_paris_borrow_verification
from tests.test_core_p1_5136_paris_borrow_later_verification import _pending, _snapshot
from tests.test_core_p0_2055_bnf_ui_consent import view
from tests.test_paris_manual_borrow_dispatch import setup
from cifereca.models import SearchResult


class ReadOnlyTasks:
    def __init__(self):
        self.tasks = []
    def submit(self, label, work, done, failed, **options):
        assert options["kind"] == "borrow-verification"
        assert options["origin"] == "user"
        self.tasks.append((work, done, failed))
    def complete(self):
        work, done, failed = self.tasks.pop()
        try:
            value = work()
        except BaseException as error:
            failed(error)
            raise
        done(value)
        return value


def _run(window, item, control=None):
    present_paris_borrow_verification(
        window, item, control, TaskPriority=NS(USER_ACTION=40),
    )


def test_check_after_timeout_uses_same_spent_claim_without_second_post(tmp_path):
    service, session, account, item, state, op = _pending(tmp_path)
    checks = []
    session.rights_observation_snapshot = (
        lambda row: checks.append(row) or _snapshot(account, item)
    )
    session.borrow_explicit_with_claim = lambda *_a, **_kw: pytest.fail("second POST")
    window, errors, messages, refreshes = view(service, session, account, item)
    window.runner = ReadOnlyTasks()
    try:
        assert service.paris_borrow_followup_available(item)
        assert not service.can_borrow_paris(item)
        _run(window, item)
        _run(window, item)
        assert len(window.runner.tasks) == 1 and checks == []
        before = service.storage.metadata_get(borrow_claim_key(account, item))
        verdict = window.runner.complete()
        assert verdict == {
            "status": "observed_current_loan",
            "reason_code": "borrow_verification_current_loan_observed",
            "retry_borrow": False, "mutated": False,
        }
        assert checks == [account]
        assert state["mutations"] == 1
        assert service.storage.metadata_get(borrow_claim_key(account, item)) == before
        assert errors == []
        assert refreshes == [True]
        assert messages == ["Prêt Paris désormais observé ; demande initiale non rejouée"]
        assert service.storage.entitlements(provider_id="paris") == []
    finally:
        service.close()


@pytest.mark.parametrize("reason", ["empty", "partial", "timeout"])
def test_uncertain_inventory_keeps_do_not_reborrow_message(tmp_path, reason):
    service, session, account, item, state, op = _pending(tmp_path)
    if reason == "timeout":
        def observe(_account):
            raise TimeoutError("PRIVATE_DILICOM_TOKEN")
    else:
        observe = lambda _account: _snapshot(
            account, item, present=reason != "empty",
            coverage="partial" if reason == "partial" else "complete",
        )
    session.rights_observation_snapshot = observe
    window, errors, messages, refreshes = view(service, session, account, item)
    window.runner = ReadOnlyTasks()
    before = service.storage.metadata_get(borrow_claim_key(account, item))
    try:
        _run(window, item)
        verdict = window.runner.complete()
        assert verdict["status"] == "unavailable"
        assert verdict["retry_borrow"] is False and verdict["mutated"] is False
        assert errors and "Ne réempruntez pas" in errors[0][0]
        assert "PRIVATE_DILICOM_TOKEN" not in repr(errors)
        assert messages == refreshes == []
        assert state["mutations"] == 1
        assert service.storage.metadata_get(borrow_claim_key(account, item)) == before
    finally:
        service.close()


def test_followup_after_cold_restart_is_available_without_reborrow(tmp_path):
    first, session, account, item, state, op = _pending(tmp_path)
    first.close()
    service, session, account, item, fresh = setup(tmp_path)
    checks = []
    session.rights_observation_snapshot = (
        lambda a: checks.append(a) or _snapshot(account, item)
    )
    session.borrow_explicit_with_claim = lambda *_a, **_kw: pytest.fail("reborrow")
    try:
        assert service.paris_borrow_followup_available(item) is True
        verdict = service.verify_paris_borrow_followup(item)
        assert verdict["status"] == "observed_current_loan"
        assert fresh["calls"] == [] and fresh["mutations"] == 0
        assert checks == [account]
        assert not service.can_borrow_paris(item)
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    "identity", "account_generation", "edition", "source_epoch", "credential",
])
def test_queued_followup_is_revoked_on_changed_context(tmp_path, change):
    service, session, account, item, state, op = _pending(tmp_path)
    window, errors, messages, refreshes = view(service, session, account, item)
    window.runner = ReadOnlyTasks()
    calls = []
    session.rights_observation_snapshot = lambda _a: calls.append("GET")
    try:
        _run(window, item)
        assert len(window.runner.tasks) == 1
        if change == "identity":
            window._identity_context_generation += 1
        elif change == "account_generation":
            window._account_context_generation += 1
        elif change == "edition":
            item.record_id = "different"
        elif change == "source_epoch":
            session._epoch = getattr(session, "_epoch", 0) + 1
        else:
            account.credential_id = "other"
        outcome = window.runner.complete()
        assert outcome["status"] == "blocked"
        assert calls == [] and messages == refreshes == errors == []
        assert state["mutations"] == 1
    finally:
        service.close()


def test_other_scope_or_confirmed_checkpoint_never_offers_followup(tmp_path):
    service, session, account, item, state, op = _pending(tmp_path)
    try:
        assert not service.paris_borrow_followup_available(
            replace(item, source_credential="other"),
        )
        assert not service.paris_borrow_followup_available(
            replace(item, record_id="another"),
        )
        assert not service.paris_borrow_followup_available(
            replace(item, docbase="SYRACUSE"),
        )
        assert not service.paris_borrow_followup_available(
            replace(item, source_provider="bnf-pret"),
        )
        assert not service.paris_borrow_followup_available(
            replace(item, metadata={"catalogue_access": "shared_read_only"}),
        )
        assert service.verify_paris_borrow_followup(
            replace(item, record_id="another"),
        )["status"] == "blocked"
        assert state["mutations"] == 1
    finally:
        service.close()


def test_search_detail_routes_only_uncertain_paris_to_nonmutating_action():
    """Static wiring: no borrow controller call on the follow-up button."""
    src = (Path(__file__).resolve().parents[1]
           / "src/cifereca/gui/detail_panel.py").read_text(encoding="utf-8")
    assert '"Vérifier le prêt sans réemprunter"' in src
    assert "paris_borrow_followup_available" in src
    assert "or pending_followup:" in src
    assert "pending_followup = (" in src
    assert "verification_only=True" in src
    router = (Path(__file__).resolve().parents[1]
              / "src/cifereca/gui/main_window.py").read_text(encoding="utf-8")
    assert "present_paris_borrow_verification(" in router
    assert "if verification_only:" in router
    method = (Path(__file__).resolve().parents[1]
              / "src/cifereca/gui/bnf_borrow_controller.py").read_text(encoding="utf-8")
    body = method.split("def present_paris_borrow_verification(", 1)[1]
    assert "verify_paris_borrow_followup(" in body
    assert "borrow_paris_manual(" not in body
    assert "borrow_explicit_with_claim(" not in body


def test_mainwindow_existing_paris_router_dispatches_only_readonly_followup(tmp_path):
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "MainWindow")
    fn = next(n for n in owner.body if isinstance(n, ast.FunctionDef)
              and n.name == "borrow_search_item")
    namespace = {
        "SearchResult": SearchResult, "Gtk": NS(Widget=object),
        "Adw": NS(), "TaskPriority": NS(USER_ACTION=40),
        "__name__": "cifereca.gui.main_window",
    }
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[fn], type_ignores=[]),
    ), str(source), "exec"), namespace)
    service, session, account, item, state, _op = _pending(tmp_path)
    window, errors, messages, refreshes = view(service, session, account, item)
    window.runner = ReadOnlyTasks()
    session.rights_observation_snapshot = lambda _a: _snapshot(account, item)
    session.borrow_explicit_with_claim = lambda *_a, **_kw: pytest.fail("POST")
    try:
        # Only the explicit verification_only flag reaches the new controller.
        namespace["borrow_search_item"](
            window, item, verification_only=True,
        )
        assert len(window.runner.tasks) == 1
        assert window.runner.complete()["status"] == "observed_current_loan"
        assert state["mutations"] == 1
        assert messages == ["Prêt Paris désormais observé ; demande initiale non rejouée"]
        assert errors == [] and refreshes == [True]
        # Another source can never reuse this flag to run a regular borrow.
        namespace["borrow_search_item"](
            window, replace(item, source_provider="unknown"),
            verification_only=True,
        )
        assert not window.runner.tasks
    finally:
        service.close()
