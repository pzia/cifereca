"""P0 #5048: exact borrower admission and no legacy session.borrow fallback.

Execute real MainWindow methods without GTK. A provider capability or a
historical session.borrow is NOT sufficient authorization to issue a loan.
Only an installed, typed manual-borrow contract may reach the shared GUI
controller; all real mutations remain under Core's durable one-shot claim.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import MethodType, SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.models import Account


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"


def _methods():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = [node for node in owner.body
               if isinstance(node, ast.FunctionDef)
               and node.name in {"can_borrow_item", "_eligible_borrow_accounts",
                                 "borrow_search_item"}]
    assert len(methods) == 3
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


def _account(identity="alice", credential="A", provider="synthetic", uid=""):
    return Account(identity_id=identity, credential_id=credential,
                   provider_id=provider, unique_id=uid, is_primary=True)


def _ui(*, accounts=None, identity="alice"):
    rows = [_account()] if accounts is None else accounts
    session = NS(
        provider_id="synthetic", credential_id="A", connected=True,
        capabilities=NS(borrow=True), descriptor=NS(display_name="Synthetic"),
        accounts=rows, borrow=Mock(side_effect=AssertionError("legacy POST")),
    )
    state = NS(session=session)

    def session_for_source(provider, credential):
        if provider != "synthetic" or credential != "A":
            raise RuntimeError("wrong source")
        return state.session

    service = NS(
        current_identity=NS(identity_id=identity),
        _session_for_source=session_for_source,
        can_borrow_manual=Mock(return_value=False),
        manual_borrow_policy=Mock(return_value=None),
        borrow_manual=Mock(side_effect=AssertionError("unscoped POST")),
        provider_display_name=lambda _provider: "Synthetic",
    )
    queued = []
    runner = NS(submit=lambda *args, **kwargs: queued.append((args, kwargs)))
    ui = NS(service=service, runner=runner, _closed=False,
            _identity_context_generation=1)
    methods = _methods()
    ui._eligible_borrow_accounts = MethodType(methods["_eligible_borrow_accounts"], ui)
    ui.can_borrow_item = MethodType(methods["can_borrow_item"], ui)
    ui.borrow_search_item = MethodType(methods["borrow_search_item"], ui)
    item = NS(source_provider="synthetic", source_credential="A")
    return ui, item, state, queued


@pytest.mark.parametrize("scenario", [
    "exact_primary", "linked_primary", "foreign_only", "wrong_credential",
    "wrong_provider", "no_identity", "disconnected", "duplicate_primary",
])
def test_legacy_capability_never_grants_manual_borrow(scenario):
    primary = _account(uid="borrower-1")
    accounts = [primary]
    if scenario == "linked_primary":
        accounts.append(_account(uid="borrower-2"))
    elif scenario == "foreign_only":
        accounts = [_account(identity="bob")]
    elif scenario == "wrong_credential":
        accounts = [_account(credential="other")]
    elif scenario == "wrong_provider":
        accounts = [_account(provider="other")]
    elif scenario == "duplicate_primary":
        accounts.append(_account(uid="borrower-1"))
    ui, item, state, queued = _ui(accounts=accounts)
    if scenario == "no_identity":
        ui.service.current_identity = None
    elif scenario == "disconnected":
        state.session.connected = False
    assert ui.can_borrow_item(item) is False
    ui.borrow_search_item(item)
    assert queued == []
    state.session.borrow.assert_not_called()
    ui.service.borrow_manual.assert_not_called()


def test_exact_account_enumeration_remains_identity_and_source_bound():
    primary = _account(uid="borrower-1")
    ui, item, _state, _queued = _ui(accounts=[
        _account(identity="bob"), _account(credential="other"),
        _account(provider="foreign"), primary,
    ])
    assert ui._eligible_borrow_accounts(item)[1] == [primary]


def test_duplicate_primary_registration_remains_ambiguous():
    ui, item, state, _queued = _ui(accounts=[
        _account(uid="duplicate"), _account(uid="duplicate"),
    ])
    assert ui._eligible_borrow_accounts(item)[1] == []


def test_unknown_or_invalid_manual_policy_cannot_use_legacy_mutator():
    ui, item, state, queued = _ui()
    assert ui.can_borrow_item(item) is False
    ui.borrow_search_item(item)
    ui.service.manual_borrow_policy.assert_called_once_with(item)
    assert queued == []
    state.session.borrow.assert_not_called()


def test_policy_lookup_error_fails_closed_without_legacy_post():
    ui, item, state, queued = _ui()
    ui.service.manual_borrow_policy.side_effect = RuntimeError("invalid ABI")
    ui.borrow_search_item(item)
    assert queued == []
    state.session.borrow.assert_not_called()


def test_generic_policy_enters_only_shared_controller(monkeypatch):
    from cifereca.gui import manual_borrow_controller

    ui, item, state, queued = _ui()
    ui.service.manual_borrow_policy.return_value = object()
    ui.service.can_borrow_manual.return_value = True
    present = Mock()
    monkeypatch.setattr(manual_borrow_controller, "_present_manual_borrow", present)
    assert ui.can_borrow_item(item) is True
    ui.borrow_search_item(item)
    present.assert_called_once()
    assert present.call_args.args[1] is item
    assert "available" not in present.call_args.kwargs
    assert "dispatch" not in present.call_args.kwargs
    assert queued == []
    state.session.borrow.assert_not_called()
    ui.service.borrow_manual.assert_not_called()


def test_readonly_followup_never_enters_manual_or_legacy_path(monkeypatch):
    from cifereca.gui import manual_borrow_controller

    ui, item, state, queued = _ui()
    verify = Mock()
    mutate = Mock()
    monkeypatch.setattr(
        manual_borrow_controller, "present_manual_borrow_verification", verify,
    )
    monkeypatch.setattr(manual_borrow_controller, "_present_manual_borrow", mutate)
    control = NS(get_name=lambda: "cifereca-borrow-followup")
    ui.borrow_search_item(item, control)
    verify.assert_called_once()
    mutate.assert_not_called()
    ui.service.manual_borrow_policy.assert_not_called()
    assert queued == []
    state.session.borrow.assert_not_called()


def test_mainwindow_contains_no_legacy_mutating_borrow_route():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    owner = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: node for node in owner.body
               if isinstance(node, ast.FunctionDef)}
    for name in ("can_borrow_item", "borrow_search_item"):
        source = ast.unparse(methods[name])
        assert "'bnf-pret'" not in source and "'paris'" not in source
        assert "session.borrow(" not in source
    assert "self.service.manual_borrow_policy(item)" in ast.unparse(
        methods["borrow_search_item"],
    )
