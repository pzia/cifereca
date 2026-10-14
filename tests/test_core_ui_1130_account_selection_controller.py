"""P0 #1130: account/identity selection guards, privacy and async boundaries."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.shell_account_selection_controller import (
    identity_changed, account_changed, invalidate_secondary_account_pages,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_account_selection_controller.py"


def _account(uid, *, identity="alice", provider="paris", credential="vault",
             display=None):
    return NS(
        unique_id=uid, identity_id=identity, provider_id=provider,
        credential_id=credential, display_name=display,
        provider_label="Paris",
    )


def _window(*, with_pages=True):
    events = []
    a = _account("a", display="Compte A")
    b = _account("b", display="Compte B", credential="vault-b")
    service = NS(
        current_account=a,
        accounts=[a, b],
        identities=[NS(identity_id="alice"), NS(identity_id="bob")],
        last_history=["A privée"],
        last_bookings=["Réservation A"],
    )

    def select_account(unique_id, provider_id, credential_id):
        events.append(("select", unique_id, provider_id, credential_id))
        service.current_account = next(
            account for account in service.accounts
            if (account.unique_id, account.provider_id, account.credential_id)
            == (unique_id, provider_id, credential_id)
        )

    service.select_account = select_account
    window = NS(
        _closed=False,
        _identity_changing=False,
        _account_changing=False,
        _visible_accounts=[a, b],
        _account_context_generation=4,
        _identity_context_generation=9,
        service=service,
        history_objects={"A": "private"},
        booking_objects={"A": "private"},
        _history_all_rows=["A"],
        _history_rows=["A"],
        _history_loaded_count=1,
        _history_append_scheduled=True,
        toast=lambda msg: events.append(("toast", msg)),
        _activate_identity=lambda identity: events.append(("identity", identity)),
    )
    if with_pages:
        window.history_page = NS(
            show_rows=lambda rows: events.append(("history-clear", rows)),
            detail=NS(empty=lambda title, msg: events.append(("history-empty", title, msg))),
        )
        window.booking_page = NS(
            show_rows=lambda rows: events.append(("booking-clear", rows)),
            detail=NS(empty=lambda title, msg: events.append(("booking-empty", title, msg))),
        )
    return window, events, a, b


def _dropdown(idx):
    return NS(get_selected=lambda: idx)


def test_owner_has_no_gtk_or_mainwindow_runtime_imports():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in row or "main_window" in row for row in imports)


def test_mainwindow_selection_callback_signatures_remain_compatible():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    main = next(node for node in tree.body
                if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for name, target in (
        ("_identity_changed", "identity_changed"),
        ("_account_changed", "account_changed"),
    ):
        method = next(node for node in main.body
                      if isinstance(node, ast.FunctionDef) and node.name == name)
        assert [arg.arg for arg in method.args.args] == ["self", "dropdown", "_pspec"]
        assert f"shell_account_selection_controller.{target}" in ast.unparse(method)


def test_identity_selects_only_the_requested_known_identity():
    window, events, _a, _b = _window()
    identity_changed(window, _dropdown(1), None)
    assert events == [("identity", "bob")]


@pytest.mark.parametrize("idx", [-1, 2, 200])
def test_identity_ignores_invalid_or_unavailable_positions(idx):
    window, events, _a, _b = _window()
    identity_changed(window, _dropdown(idx), None)
    assert events == []


def test_identity_ignores_reentrant_selection_notification():
    window, events, _a, _b = _window()
    window._identity_changing = True
    identity_changed(window, _dropdown(1), None)
    assert events == []


def test_identity_ignores_late_notification_after_window_close():
    window, events, _a, _b = _window()
    window._closed = True
    identity_changed(window, _dropdown(1), None)
    assert events == []


def test_account_commit_precedes_private_view_clear_and_feedback():
    window, events, _a, b = _window()
    account_changed(window, _dropdown(1), None)
    assert window.service.current_account is b
    assert [evt[0] for evt in events] == [
        "select", "history-clear", "history-empty",
        "booking-clear", "booking-empty", "toast",
    ]
    assert events[-1] == ("toast", "Compte : Compte B")
    assert window._account_context_generation == 5
    assert window._identity_context_generation == 9
    assert window.service.last_history == []
    assert window.service.last_bookings == []
    assert window.history_objects == {}
    assert window.booking_objects == {}
    assert window._history_all_rows == []
    assert window._history_rows == []
    assert window._history_loaded_count == 0
    assert window._history_append_scheduled is False


def test_secondary_reset_does_not_require_unopened_page_widgets():
    window, events, _a, _b = _window(with_pages=False)
    account_changed(window, _dropdown(1), None)
    assert [name for name, *_ in events] == ["select", "toast"]
    assert window._account_context_generation == 5
    assert window.service.last_bookings == []


def test_uncommitted_failed_account_switch_keeps_previous_private_rows():
    window, events, a, _b = _window()
    def reject(*args):
        raise ValueError("account unavailable")
    window.service.select_account = reject
    with pytest.raises(ValueError, match="account unavailable"):
        account_changed(window, _dropdown(1), None)
    assert window.service.current_account is a
    assert window._account_context_generation == 4
    assert window.service.last_history == ["A privée"]
    assert window.service.last_bookings == ["Réservation A"]
    assert window.history_objects == {"A": "private"}
    assert events == []


def test_current_account_compound_identity_does_not_reinvalidate_views():
    window, events, a, _b = _window()
    account_changed(window, _dropdown(0), None)
    assert window.service.current_account is a
    assert window._account_context_generation == 4
    assert window.service.last_history == ["A privée"]
    assert events == []


def test_equivalent_account_object_is_considered_current_by_compound_key():
    window, events, a, _b = _window()
    window._visible_accounts[0] = _account(
        a.unique_id, credential=a.credential_id,
    )
    account_changed(window, _dropdown(0), None)
    assert events == []
    assert window._account_context_generation == 4


def test_distinct_credential_with_same_account_id_is_not_conflated():
    window, events, a, _b = _window()
    changed = _account(a.unique_id, credential="vault-2")
    window._visible_accounts = [changed]
    window.service.accounts.append(changed)
    account_changed(window, _dropdown(0), None)
    assert window.service.current_account is changed
    assert window._account_context_generation == 5
    assert events[0] == ("select", "a", "paris", "vault-2")


@pytest.mark.parametrize("idx", [-1, 2, 1000])
def test_account_ignores_invalid_row_without_touching_private_cache(idx):
    window, events, _a, _b = _window()
    account_changed(window, _dropdown(idx), None)
    assert window._account_context_generation == 4
    assert window.service.last_history == ["A privée"]
    assert events == []


def test_account_ignores_selection_emitted_during_model_rebuild():
    window, events, _a, _b = _window()
    window._account_changing = True
    account_changed(window, _dropdown(1), None)
    assert window._account_context_generation == 4
    assert events == []


def test_late_account_selection_after_close_is_side_effect_free():
    window = NS(_closed=True)
    # No service or dropdown contents are needed once the host closes.
    account_changed(window, NS(get_selected=Mock(side_effect=AssertionError)), None)


def test_account_a_b_a_increments_generation_even_when_returning_to_a():
    window, events, a, b = _window()
    account_changed(window, _dropdown(1), None)
    assert window.service.current_account is b
    window.service.last_history = ["B privée"]
    window.history_objects = {"B": "private"}
    account_changed(window, _dropdown(0), None)
    assert window.service.current_account is a
    assert window._account_context_generation == 6
    assert window._identity_context_generation == 9
    assert window.service.last_history == []
    assert window.history_objects == {}
    assert [event[0] for event in events].count("select") == 2


def test_account_empty_visible_list_does_not_select_fallback_account():
    window, events, _a, _b = _window()
    window._visible_accounts = []
    account_changed(window, _dropdown(0), None)
    assert window._account_context_generation == 4
    assert events == []


def test_secondary_invalidated_only_once_for_a_single_account_change():
    window, events, _a, _b = _window()
    account_changed(window, _dropdown(1), None)
    account_changed(window, _dropdown(1), None)
    assert window._account_context_generation == 5
    assert [event[0] for event in events].count("history-clear") == 1
    assert [event[0] for event in events].count("booking-clear") == 1
    assert [event[0] for event in events].count("toast") == 1
