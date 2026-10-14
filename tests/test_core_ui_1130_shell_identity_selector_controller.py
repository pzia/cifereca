"""P0 #1130: selectors keep GTK models, account identity and notify guards."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.shell_model import delegated_accounts
from cifereca.gui.shell_identity_selector_controller import (
    replace_dropdown_labels,
    populate_account_dropdown,
    populate_accounts,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "shell_identity_selector_controller.py"


class StringList:
    def __init__(self, values=()):
        self.items = list(values)
        self.splices = []

    @classmethod
    def new(cls, values):
        return cls(values)

    def get_n_items(self):
        return len(self.items)

    def get_string(self, idx):
        return self.items[idx]

    def splice(self, start, count, values):
        self.splices.append((start, count, list(values)))
        self.items[start:start + count] = values


Gtk = NS(StringList=StringList, INVALID_LIST_POSITION=2**32 - 1)


class Dropdown:
    def __init__(self, model=None):
        self.model = model
        self.set_models = []
        self.selected = None
        self.visible = None
        self.on_selected = None
        self.events = []

    def get_model(self):
        return self.model

    def set_model(self, model):
        self.model = model
        self.set_models.append(model)

    def set_visible(self, visible):
        self.visible = visible
        self.events.append(("visible", visible))

    def set_selected(self, idx):
        self.selected = idx
        self.events.append(("selected", idx))
        if self.on_selected:
            self.on_selected(idx)


def account(unique_id, identity, *, provider="private", primary=True, name=None):
    return NS(
        unique_id=unique_id, identity_id=identity, provider_id=provider,
        is_primary=primary, provider_label=provider,
        display_name=name,
    )


def host(*, accounts=None, current=None, identities=None, selected_identity=None):
    accounts = list(accounts or [])
    identities = list(identities or [])
    statuses = {
        "public": "singleton_public",
        "private": "multiple",
        "other": "multiple",
    }
    service = NS(
        accounts=accounts,
        current_account=current,
        identities=identities,
        current_identity=selected_identity,
        _provider_plugin_status=lambda provider: NS(
            descriptor=NS(instantiation=statuses.get(provider, "multiple"))
        ),
    )
    window = NS(
        _closed=False,
        _account_changing=False,
        _identity_changing=False,
        service=service,
        identity_dropdown=Dropdown(),
        account_dropdown=Dropdown(),
        _refresh_statistics=Mock(),
    )
    window._replace_dropdown_labels = lambda dropdown, labels: replace_dropdown_labels(
        dropdown, labels, Gtk=Gtk,
    )
    window._populate_account_dropdown = lambda identity_id: populate_account_dropdown(
        window, identity_id, Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    return window


def test_no_gtk_import_and_stable_main_window_facade_signatures():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry
                   for entry in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    window = next(node for node in main.body
                  if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    for facade, owner in (
        ("_replace_dropdown_labels", "replace_dropdown_labels"),
        ("_populate_account_dropdown", "populate_account_dropdown"),
        ("_populate_accounts", "populate_accounts"),
    ):
        func = next(node for node in window.body
                    if isinstance(node, ast.FunctionDef) and node.name == facade)
        text = ast.unparse(func)
        assert f"shell_identity_selector_controller.{owner}" in text


def test_existing_stringlist_same_labels_not_mutated_or_replaced():
    model = StringList(["Alice", "Bob"])
    dropdown = Dropdown(model)
    replace_dropdown_labels(dropdown, ["Alice", "Bob"], Gtk=Gtk)
    assert dropdown.model is model
    assert model.splices == []
    assert dropdown.set_models == []


def test_existing_stringlist_spliced_in_place_on_change():
    model = StringList(["Ancienne", "Autre"])
    dropdown = Dropdown(model)
    replace_dropdown_labels(dropdown, ["Nouvelle"], Gtk=Gtk)
    assert model.splices == [(0, 2, ["Nouvelle"])]
    assert model.items == ["Nouvelle"]
    assert dropdown.model is model
    assert dropdown.set_models == []


def test_missing_model_is_replaced_by_stringlist():
    dropdown = Dropdown(NS())
    replace_dropdown_labels(dropdown, ["A"], Gtk=Gtk)
    assert isinstance(dropdown.model, StringList)
    assert dropdown.model.items == ["A"]
    assert dropdown.set_models == [dropdown.model]


def test_delegated_private_accounts_preserve_order_and_exact_current_identity():
    primary = account("p", "alice", name="Principal")
    delegate = account("d", "alice", primary=False, name="Délégué")
    public = account("pub", "alice", provider="public", primary=False)
    outsider = account("other", "bob", primary=False)
    window = host(accounts=[primary, public, delegate, outsider], current=delegate)
    populate_account_dropdown(
        window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    assert window._visible_accounts == [primary, delegate]
    assert window.account_dropdown.visible is True
    assert window.account_dropdown.model.items == [
        "private · Principal", "private · Délégué",
    ]
    assert window.account_dropdown.selected == 1
    assert window._account_changing is False


def test_primary_only_and_public_catalogues_hide_delegation_selector():
    primary = account("p", "alice")
    public = account("pub", "alice", provider="public", primary=False)
    window = host(accounts=[primary, public], current=primary)
    populate_account_dropdown(
        window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    assert window._visible_accounts == []
    assert window.account_dropdown.visible is False
    assert window.account_dropdown.model.items == []
    assert window.account_dropdown.selected == Gtk.INVALID_LIST_POSITION


def test_missing_identity_never_shows_other_accounts():
    primary = account("p", "alice")
    delegate = account("d", "alice", primary=False)
    window = host(accounts=[primary, delegate], current=primary)
    populate_account_dropdown(
        window, None, Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    assert window._visible_accounts == []
    assert window.account_dropdown.selected == Gtk.INVALID_LIST_POSITION


def test_default_primary_and_delegate_labels_and_missing_current_pointer():
    primary = account("p", "alice")
    delegate = account("d", "alice", primary=False)
    equivalent = account("d", "alice", primary=False)
    window = host(accounts=[primary, delegate], current=equivalent)
    populate_account_dropdown(
        window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    assert window.account_dropdown.model.items == [
        "private · Compte principal", "private · Compte lié",
    ]
    # A stale account object is not a selected member, even if fields match.
    assert window.account_dropdown.selected == Gtk.INVALID_LIST_POSITION


def test_account_selector_blocks_reentrant_notify_during_update():
    primary = account("p", "alice")
    delegate = account("d", "alice", primary=False)
    window = host(accounts=[primary, delegate], current=delegate)
    observed = []
    window.account_dropdown.on_selected = (
        lambda selection: observed.append((selection, window._account_changing))
    )
    populate_account_dropdown(
        window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
    assert observed == [(1, True)]
    assert window._account_changing is False


def test_identity_selector_projects_current_id_and_then_account_context():
    alice = NS(identity_id="alice", label="Alice")
    bob = NS(identity_id="bob", label="Bob")
    primary = account("p", "bob")
    delegate = account("d", "bob", primary=False)
    window = host(
        identities=[alice, bob], selected_identity=bob,
        accounts=[primary, delegate], current=primary,
    )
    events = []
    window.identity_dropdown.on_selected = (
        lambda selection: events.append(("identity_notify", selection, window._identity_changing))
    )
    original = window._populate_account_dropdown
    def accounts_after_identity_guard(identity_id):
        events.append(("accounts", identity_id, window._identity_changing))
        original(identity_id)
    window._populate_account_dropdown = accounts_after_identity_guard
    populate_accounts(window, Gtk=Gtk)
    assert window.identity_dropdown.model.items == ["Alice", "Bob"]
    assert window.identity_dropdown.selected == 1
    assert events == [("identity_notify", 1, True), ("accounts", "bob", False)]
    assert window.account_dropdown.visible is True
    assert window.account_dropdown.selected == 0
    window._refresh_statistics.assert_called_once_with()


def test_empty_identity_selector_sets_invalid_position_without_rescanning():
    window = host()
    populate_accounts(window, Gtk=Gtk)
    assert window.identity_dropdown.selected == Gtk.INVALID_LIST_POSITION
    assert window.identity_dropdown.model.items == []
    assert window.account_dropdown.selected == Gtk.INVALID_LIST_POSITION
    window._refresh_statistics.assert_called_once_with()


def test_missing_selected_identity_retains_existing_first_row_fallback():
    alice = NS(identity_id="alice", label="Alice")
    unknown = NS(identity_id="deleted", label="Supprimée")
    window = host(identities=[alice], selected_identity=unknown)
    populate_accounts(window, Gtk=Gtk)
    assert window.identity_dropdown.selected == 0


def test_selector_exception_restores_account_notify_guard():
    window = host(accounts=[account("p", "alice"), account("d", "alice", primary=False)])
    window._replace_dropdown_labels = Mock(side_effect=ValueError("model destroyed"))
    with pytest.raises(ValueError, match="model destroyed"):
        populate_account_dropdown(
            window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
        )
    assert window._account_changing is False


def test_selector_exception_restores_identity_notify_guard():
    window = host(identities=[NS(identity_id="alice", label="Alice")])
    window._replace_dropdown_labels = Mock(side_effect=ValueError("model destroyed"))
    with pytest.raises(ValueError, match="model destroyed"):
        populate_accounts(window, Gtk=Gtk)
    assert window._identity_changing is False
    window._refresh_statistics.assert_not_called()


def test_closed_window_cannot_access_service_or_widgets():
    window = NS(_closed=True)
    populate_accounts(window, Gtk=Gtk)
    populate_account_dropdown(
        window, "alice", Gtk=Gtk, delegated_accounts=delegated_accounts,
    )
