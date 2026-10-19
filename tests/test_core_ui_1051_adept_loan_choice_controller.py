"""P0 #1051: ADEPT manual binding is explicit, account-scoped and headless."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.adept_loan_choice_controller import present_adept_loan_choices
from cifereca.task_queue import TaskPriority


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "adept_loan_choice_controller.py"


class Widget:
    def __init__(self, **props):
        self.props = props
        self.children = []
        self.handlers = {}
        self.wrap = False

    def append(self, child):
        self.children.append(child)

    def set_wrap(self, state):
        self.wrap = state

    def set_label(self, text):
        self.props["label"] = text

    def connect(self, signal, callback):
        self.handlers[signal] = callback


class Dropdown(Widget):
    @classmethod
    def new_from_strings(cls, labels):
        return cls(labels=list(labels), selected=Gtk.INVALID_LIST_POSITION)

    def set_enable_search(self, enabled):
        self.props["search"] = enabled

    def set_selected(self, index):
        self.props["selected"] = index

    def get_selected(self):
        return self.props["selected"]

    def set_tooltip_text(self, text):
        self.props["tooltip"] = text

    def select(self, index):
        self.set_selected(index)
        self.handlers["notify::selected"](self, None)


class Dialog(Widget):
    def __init__(self, owner, title, body):
        super().__init__()
        self.owner = owner
        self.title = title
        self.body = body
        self.responses = []
        self.enabled = {}
        self.close_response = None
        self.presentations = 0

    def set_extra_child(self, widget):
        self.child = widget

    def add_response(self, code, text):
        self.responses.append((code, text))

    def set_response_enabled(self, code, enabled):
        self.enabled[code] = enabled

    def set_close_response(self, code):
        self.close_response = code

    def present(self):
        self.presentations += 1

    def respond(self, choice):
        self.handlers["response"](self, choice)


Gtk = NS(
    Box=Widget,
    Label=Widget,
    DropDown=Dropdown,
    INVALID_LIST_POSITION=2**32 - 1,
    Orientation=NS(VERTICAL=1),
)


def _option(loan_id, *, profile="profile-1", title="Faux titre", **changes):
    option = {
        "loan_id": loan_id,
        "profile_id": profile,
        "profile_name": profile,
        "title": title,
        "state": "Prêt",
        "validity": None,
        "local_book": None,
    }
    option.update(changes)
    return option


def _window():
    events = []
    dialog_instances = []
    service = NS(
        attach_adept_loan=lambda acq, profile, loan: (
            events.append(("attach", acq, profile, loan))
            or {"status": "resolved"}
        ),
    )
    window = NS(
        _closed=False,
        _identity_context_generation=5,
        _account_context_generation=3,
        service=service,
        runner=NS(submissions=[]),
        loan_page=NS(detail=NS(
            show_loan=lambda loan: events.append(("show-loan", loan)),
        )),
        toast=lambda message, timeout: events.append(("toast", message, timeout)),
        show_error=lambda message, title: events.append(("error", message, title)),
        refresh_library=lambda force: events.append(("refresh", force)),
        _activity_result=lambda message: events.append(("activity", message)),
    )

    def submit(title, work, done, error, **kwargs):
        window.runner.submissions.append(NS(
            title=title, work=work, done=done, error=error, kwargs=kwargs,
        ))
        events.append(("submit", title))

    window.runner.submit = submit

    def new_dialog(owner, title, body):
        dialog = Dialog(owner, title, body)
        dialog_instances.append(dialog)
        events.append(("dialog", title))
        return dialog

    adw = NS(MessageDialog=NS(new=new_dialog))
    return window, adw, events, dialog_instances


def _open(window, adw, options=None, *, complete=True, loan=None):
    if options is None:
        options = [_option("loan-1")]
    present_adept_loan_choices(
        window, "acq-1", loan or NS(title="Livre local"),
        {"options": options, "complete": complete},
        Gtk=Gtk, Adw=adw,
    )


def _dropdown(dialog):
    return next(child for child in dialog.child.children if isinstance(child, Dropdown))


def test_module_is_gtk_free_and_mainwindow_facade_signature_is_stable():
    parsed = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(parsed)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in n or "main_window" in n or "task_runner" in n
                   for n in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    owner = next(n for n in main.body
                 if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    method = next(n for n in owner.body
                  if isinstance(n, ast.FunctionDef)
                  and n.name == "_present_adept_loan_choices")
    assert [arg.arg for arg in method.args.args] == [
        "self", "acquisition_id", "loan", "result",
    ]
    assert "adept_loan_choice_controller.present_adept_loan_choices" in ast.unparse(method)


@pytest.mark.parametrize("complete,message", [
    (True, "Aucun prêt ADEPT dans les profils liés"),
    (False, "Inventaire incomplet : aucun prêt disponible"),
])
def test_empty_inventory_toasts_without_creating_dialog_or_job(complete, message):
    window, adw, events, dialogs = _window()
    _open(window, adw, [], complete=complete)
    assert events == [("toast", message, 6)]
    assert dialogs == []
    assert window.runner.submissions == []


def test_dialog_requires_explicit_selection_even_if_title_matches():
    window, adw, events, dialogs = _window()
    _open(window, adw, [_option("loan-1", title="Livre local")])
    dialog = dialogs[0]
    assert dialog.owner is window
    assert dialog.title == "Rattacher un prêt ADEPT"
    assert "il ne constitue pas une preuve automatique" in dialog.body
    assert dialog.responses == [
        ("cancel", "Annuler"),
        ("attach", "Confirmer le rattachement"),
    ]
    assert dialog.close_response == "cancel"
    assert dialog.enabled["attach"] is False
    assert _dropdown(dialog).get_selected() == Gtk.INVALID_LIST_POSITION
    assert window.runner.submissions == []
    assert not any(event[0] == "attach" for event in events)


def test_partial_inventory_warns_explicitly_and_never_claims_completeness():
    window, adw, events, dialogs = _window()
    _open(window, adw, complete=False)
    texts = [child.props.get("label") for child in dialogs[0].child.children]
    assert texts == [
        "Livre local",
        "Inventaire partiel : seuls les profils lus avec succès sont proposés.",
        None,
        "Sélectionnez un prêt pour voir sa validité et son chemin connu.",
    ]


def test_option_labels_include_exact_loan_id_profile_and_state():
    window, adw, events, dialogs = _window()
    options = [
        _option("loan-A", profile="alexandre", title="Titre A", state="Restitué"),
        _option("loan-B", profile="autre", title="Titre B", state="Prêt"),
    ]
    _open(window, adw, options)
    dropdown = _dropdown(dialogs[0])
    assert dropdown.props["labels"] == [
        "Titre A · alexandre · Restitué · loan-A",
        "Titre B · autre · Prêt · loan-B",
    ]
    assert dropdown.props["search"] is True
    assert dropdown.props["tooltip"] == "Titre · profil · état · identifiant ADEPT"


def test_selection_enables_confirmation_and_shows_validity_path_without_binding():
    window, adw, events, dialogs = _window()
    _open(window, adw, [_option("exact", validity="2026-12-31", local_book="/tmp/local.epub")])
    dialog = dialogs[0]
    dropdown = _dropdown(dialog)
    dropdown.select(0)
    assert dialog.enabled["attach"] is True
    detail = dialog.child.children[-1]
    assert detail.props["label"] == "Validité : 2026-12-31\nChemin ADEPT : /tmp/local.epub"
    assert window.runner.submissions == []


@pytest.mark.parametrize("selected", [-1, Gtk.INVALID_LIST_POSITION, 2])
def test_invalid_dropdown_indices_never_bind_even_if_attach_signal_arrives(selected):
    window, adw, events, dialogs = _window()
    _open(window, adw)
    dialog = dialogs[0]
    dropdown = _dropdown(dialog)
    dropdown.select(selected)
    assert dialog.enabled["attach"] is False
    dialog.respond("attach")
    assert window.runner.submissions == []


def test_cancel_never_submits_after_valid_selection():
    window, adw, events, dialogs = _window()
    _open(window, adw)
    dropdown = _dropdown(dialogs[0])
    dropdown.select(0)
    dialogs[0].respond("cancel")
    assert window.runner.submissions == []


def test_confirm_submits_exact_profile_and_loan_id_once_only():
    window, adw, events, dialogs = _window()
    _open(window, adw, [
        _option("A", profile="p1"),
        _option("B", profile="p2", title="Faux titre plausible"),
    ])
    dialog = dialogs[0]
    _dropdown(dialog).select(1)
    dialog.respond("attach")
    dialog.respond("attach")
    assert len(window.runner.submissions) == 1
    submitted = window.runner.submissions[0]
    assert submitted.kwargs == {
        "priority": TaskPriority.USER_ACTION,
        "kind": "adept-binding",
    }
    assert submitted.title == "Rattachement du prêt ADEPT…"
    assert submitted.work() == {"status": "resolved"}
    assert [event for event in events if event[0] == "attach"] == [
        ("attach", "acq-1", "p2", "B"),
    ]


def test_success_callback_refreshes_existing_loan_page_and_activity():
    window, adw, events, dialogs = _window()
    loan = NS(title="Livre précis")
    _open(window, adw, loan=loan)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    window.runner.submissions[0].done({"status": "resolved"})
    assert events[-3:] == [
        ("show-loan", loan),
        ("refresh", False),
        ("activity", "Prêt ADEPT rattaché · fichier conservé"),
    ]


def test_failure_callback_preserves_existing_error_dialog_contract():
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    window.runner.submissions[0].error(ValueError("édition obsolète"))
    assert events[-1] == ("error", "Rattachement ADEPT indisponible", "Rattachement impossible")


def test_closed_window_does_not_read_provider_inventory_or_create_dialog():
    window = NS(_closed=True)
    present_adept_loan_choices(
        window, "acq", NS(title="Private"),
        NS(), Gtk=Gtk, Adw=None,
    )


def test_dialog_confirmation_is_invalidated_after_identity_or_account_switch():
    for attribute in ("_identity_context_generation", "_account_context_generation"):
        window, adw, events, dialogs = _window()
        _open(window, adw)
        dialog = dialogs[0]
        _dropdown(dialog).select(0)
        setattr(window, attribute, getattr(window, attribute) + 1)
        dialog.respond("attach")
        assert window.runner.submissions == []


def test_late_success_and_error_are_ignored_after_context_switch():
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    submitted = window.runner.submissions[0]
    events.clear()
    window._identity_context_generation += 1
    submitted.done(None)
    submitted.error(ValueError("private"))
    assert events == []


def test_late_selected_and_response_after_close_ignore_destroyed_widgets():
    window, adw, events, dialogs = _window()
    _open(window, adw)
    dialog = dialogs[0]
    window._closed = True
    _dropdown(dialog).select(0)
    dialog.respond("attach")
    assert window.runner.submissions == []


def test_fallback_validity_and_path_are_neutral_when_absent():
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    assert dialogs[0].child.children[-1].props["label"] == (
        "Validité : Inconnue\nChemin ADEPT : Non fourni"
    )


@pytest.mark.parametrize("error", [
    RuntimeError("https://private.invalid/LOAN?token=SECRET_ADEPT"),
    ValueError("/home/private/borrower/loan.acsm"),
])
def test_binding_error_hides_untrusted_exception_text(error):
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    window.runner.submissions[0].error(error)
    assert events[-1] == ("error", "Rattachement ADEPT indisponible", "Rattachement impossible")
    assert "SECRET_ADEPT" not in repr(events)
    assert "/home/private" not in repr(events)


def test_binding_exception_is_never_stringified():
    class Hostile(RuntimeError):
        def __str__(self):
            raise AssertionError("A plugin exception must not be stringified")
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    window.runner.submissions[0].error(Hostile())
    assert events[-1] == ("error", "Rattachement ADEPT indisponible", "Rattachement impossible")


def test_binding_public_userfacing_message_preserves_only_safe_text():
    from cifereca.plugin_api import UserFacingError
    window, adw, events, dialogs = _window()
    _open(window, adw)
    _dropdown(dialogs[0]).select(0)
    dialogs[0].respond("attach")
    window.runner.submissions[0].error(
        UserFacingError("Choisissez un profil actif", code="profile_needed",
                        technical_detail="Bearer SECRET_ADEPT"),
    )
    assert events[-1] == ("error", "Choisissez un profil actif", "Rattachement impossible")
    assert "SECRET_ADEPT" not in repr(events)
