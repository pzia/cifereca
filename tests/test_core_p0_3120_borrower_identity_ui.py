"""P0 #3120: borrower/account selection and queued borrow never cross identities.

Execute the real MainWindow methods without GTK to exercise both the
selection and the delayed task boundary. Synthetic sessions do no HTTP.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Account
from cifereca.plugin_api import Cancelled


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _methods():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = [node for node in owner.body
               if isinstance(node, ast.FunctionDef)
               and node.name in {"_eligible_borrow_accounts", "borrow_search_item"}]
    assert len(methods) == 2
    namespace = {
        "__name__": "cifereca.gui.main_window",
        "__package__": "cifereca.gui",
        "TaskPriority": NS(USER_ACTION="user"),
    }
    module = ast.Module(body=[
        ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")],
                       level=0),
        *methods,
    ], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(SOURCE), "exec"), namespace)
    return namespace


def _account(identity="alice", credential="A", provider="paris", uid=""):
    return Account(identity_id=identity, credential_id=credential,
                   provider_id=provider, unique_id=uid, is_primary=True)


def _ui(*, accounts=None, identity="alice"):
    rows = [_account()] if accounts is None else accounts
    session = NS(
        provider_id="paris", credential_id="A", connected=True,
        capabilities=NS(borrow=True), descriptor=NS(display_name="Paris"),
        accounts=rows,
        borrow=Mock(return_value=NS(title="Titre", identity_id=None,
                                    source_account_id=None)),
    )
    state = NS(session=session)
    def session_for_source(provider, credential):
        if provider != "paris" or credential != "A":
            raise RuntimeError("Wrong source")
        return state.session
    service = NS(
        current_identity=NS(identity_id=identity),
        _session_for_source=session_for_source,
        refresh_identity_loans=Mock(return_value=[]),
    )
    queued = []
    events = []
    runner = NS(submit=lambda *args, **kwargs: queued.append((args, kwargs)))
    ui = NS(
        service=service, _identity_context_generation=1,
        _closed=False, runner=runner,
        show_error=lambda message, title: events.append(("error", message, title)),
        _activity_result=lambda message: events.append(("activity", message)),
        refresh_library=lambda value: events.append(("refresh_library", value)),
        refresh_recent=lambda: events.append(("refresh_recent",)),
    )
    methods = _methods()
    ui._eligible_borrow_accounts = MethodType(methods["_eligible_borrow_accounts"], ui)
    ui.borrow_search_item = MethodType(methods["borrow_search_item"], ui)
    item = NS(source_provider="paris", source_credential="A")
    return ui, item, state, queued, events


def _queued_work(queued):
    assert len(queued) == 1
    # runner.submit(message, work, on_success, ..., kind="borrow")
    args, kwargs = queued[0]
    assert kwargs["kind"] == "borrow"
    return args[1], args[2]


def test_no_cross_identity_primary_fallback_or_borrow_post():
    ui, item, state, queued, events = _ui(accounts=[_account("bob")])
    session, accounts = ui._eligible_borrow_accounts(item)
    assert session is state.session and accounts == []
    ui.borrow_search_item(item)
    assert queued == []
    state.session.borrow.assert_not_called()
    assert events and events[0][0] == "error"


def test_selection_filters_exact_identity_provider_credential_and_borrower():
    alice = _account("alice", uid="primary-A")
    others = [
        _account("bob", uid="primary-B"),
        _account("alice", credential="B", uid="wrong-credential"),
        _account("alice", provider="other", uid="wrong-provider"),
    ]
    ui, item, _state, _queued, _events = _ui(accounts=others + [alice])
    assert ui._eligible_borrow_accounts(item)[1] == [alice]


@pytest.mark.parametrize("mode", ["no_identity", "disconnected", "rebound_credential",
                                   "duplicate_borrower"])
def test_unusable_borrower_scope_never_offers_an_account(mode):
    alice = _account()
    ui, item, state, _queued, _events = _ui(accounts=[alice])
    if mode == "no_identity":
        ui.service.current_identity = None
    elif mode == "disconnected":
        state.session.connected = False
    elif mode == "rebound_credential":
        state.session.credential_id = "B"
    else:
        state.session.accounts.append(_account())
    assert ui._eligible_borrow_accounts(item)[1] == []


def test_stale_dialog_account_rejected_before_queue_even_when_fields_match():
    ui, item, state, queued, events = _ui()
    # A new account registration has identical coordinates, but the dialog
    # still contains the prior Account object from before a reconnect.
    old = _account()
    ui.borrow_search_item(item, account=old)
    assert queued == [] and events[0][0] == "error"
    state.session.borrow.assert_not_called()


@pytest.mark.parametrize("change", [
    "identity", "aba_generation", "replaced_session", "disconnected",
    "mutated_borrower", "window_closed",
])
def test_queued_borrow_revalidates_scope_before_provider_post(change):
    alice = _account()
    ui, item, state, queued, _events = _ui(accounts=[alice])
    ui.borrow_search_item(item)
    work, _ok = _queued_work(queued)
    original_session = state.session
    if change == "identity":
        ui.service.current_identity = NS(identity_id="bob")
    elif change == "aba_generation":
        ui._identity_context_generation += 2  # A -> B -> A
    elif change == "replaced_session":
        state.session = NS(**vars(state.session))
    elif change == "disconnected":
        state.session.connected = False
    elif change == "mutated_borrower":
        alice.unique_id = "other-account"
    else:
        ui._closed = True
    with pytest.raises(Cancelled, match="contexte"):
        work()
    assert len(queued) == 1
    # The captured original session never sends a borrow, even if replaced.
    original_session.borrow.assert_not_called()


def test_current_account_executes_once_and_stale_success_never_updates_new_ui():
    alice = _account()
    ui, item, state, queued, events = _ui(accounts=[alice])
    ui.borrow_search_item(item)
    work, ok = _queued_work(queued)
    result = work()
    state.session.borrow.assert_called_once_with(item, alice)
    ui.service.refresh_identity_loans.assert_called_once_with(
        force=True, identity_id="alice",
    )
    assert result[0].identity_id == "alice"
    ok(result)
    assert any(event[0] == "activity" for event in events)
    prior = list(events)
    ui._identity_context_generation += 1
    ok(result)
    assert events == prior
