"""P0 #1132: settings choosers cannot mutate stale or closed UI editors."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.settings_chooser_controller import (
    choose_identity_directory, choose_adept_profile,
)


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "settings_chooser_controller.py"


class DirectoryChooser:
    def __init__(self):
        self.handlers = {}
        self.folder = None
        self.shown = 0
        self.destroyed = 0
        self.file = None

    def set_current_folder(self, folder):
        self.folder = folder

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def show(self):
        self.shown += 1

    def destroy(self):
        self.destroyed += 1

    def get_file(self):
        return self.file

    def respond(self, code):
        self.handlers["response"](self, code)


class Dialog:
    def __init__(self, window, title, body):
        self.window = window
        self.title = title
        self.body = body
        self.responses = []
        self.handlers = {}
        self.presented = False
        self.close_response = None

    def add_response(self, code, title):
        self.responses.append((code, title))

    def set_close_response(self, code):
        self.close_response = code

    def connect(self, signal, callback):
        self.handlers[signal] = callback

    def present(self):
        self.presented = True

    def respond(self, code):
        self.handlers["response"](self, code)


def _directory_host():
    choosers, errors = [], []
    def factory(*args):
        chooser = DirectoryChooser()
        choosers.append((args, chooser))
        return chooser

    gtk = NS(
        FileChooserNative=NS(new=factory),
        FileChooserAction=NS(SELECT_FOLDER=7),
        ResponseType=NS(ACCEPT=1),
    )
    gio = NS(File=NS(new_for_path=lambda path: ("folder", path)))
    window = NS(
        _closed=False,
        _identity_context_generation=4,
        _account_context_generation=9,
        show_error=lambda msg, title: errors.append((msg, title)),
    )
    entry = Mock()
    return window, entry, gtk, gio, choosers, errors


def _adept_host():
    errors, dialogs, submitted = [], [], []

    def new_dialog(*args):
        dialog = Dialog(*args)
        dialogs.append(dialog)
        return dialog

    runner = NS(submit=lambda *args, **kwargs: submitted.append((args, kwargs)))
    service = NS(adept_profile_choices=Mock())
    window = NS(
        _closed=False, _identity_context_generation=4,
        _account_context_generation=9, runner=runner, service=service,
        show_error=lambda msg, title: errors.append((msg, title)),
    )
    adw = NS(MessageDialog=NS(new=new_dialog))
    return window, Mock(), object(), adw, errors, dialogs, submitted


def test_owner_does_not_import_gi_and_mainwindow_signatures_remain_unchanged():
    owner = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(n) for n in ast.walk(owner)
               if isinstance(n, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in item or "main_window" in item
                   or "task_runner" in item for item in imports)
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    for name, args, target, deps in (
        ("_choose_identity_directory", ["self", "entry", "root"],
         "choose_identity_directory", ("Gtk=Gtk", "Gio=Gio")),
        ("_choose_adept_profile", ["self", "entry", "control"],
         "choose_adept_profile", ("Adw=Adw",)),
    ):
        method = next(n for n in cls.body
                      if isinstance(n, ast.FunctionDef) and n.name == name)
        assert [a.arg for a in method.args.args] == args
        source = ast.unparse(method)
        assert f"settings_chooser_controller.{target}" in source
        assert all(dep in source for dep in deps)


def test_directory_chooser_retains_native_labels_parent_folder_and_close_contract(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    assert len(choosers) == 1
    args, dialog = choosers[0]
    assert args == ("Choisir un dossier d’identité", window, 7, "Choisir", "Annuler")
    assert dialog.folder == ("folder", str(tmp_path))
    assert dialog.shown == 1
    assert window._identity_directory_chooser is dialog
    assert errors == []


def test_cancel_preserves_field_and_disposes_native_chooser(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.respond(0)
    entry.set_text.assert_not_called()
    assert chooser.destroyed == 1
    assert window._identity_directory_chooser is None
    assert errors == []


def test_valid_direct_child_directory_updates_only_editor_not_disk(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    child = tmp_path / "livres"
    child.mkdir()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = NS(get_path=lambda: str(child))
    chooser.respond(1)
    entry.set_text.assert_called_once_with("livres")
    assert chooser.destroyed == 1
    assert child.is_dir()
    assert window._identity_directory_chooser is None
    assert errors == []


@pytest.mark.parametrize("name", ["../outside", "invalid space", "."])
def test_directory_choice_rejects_unsafe_names_and_leaves_entry_intact(tmp_path, name):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = NS(get_path=lambda: str(tmp_path / name))
    chooser.respond(1)
    entry.set_text.assert_not_called()
    assert len(errors) == 1
    assert errors[0][1] == "Choix du répertoire impossible"
    assert chooser.destroyed == 1


def test_missing_file_reports_validation_error_and_always_destroys(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = None
    chooser.respond(1)
    entry.set_text.assert_not_called()
    assert errors == [("Choisissez un dossier local.", "Choix du répertoire impossible")]
    assert chooser.destroyed == 1


def test_overlapping_directory_choosers_do_not_overwrite_newer_selection(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    (tmp_path / "ancien").mkdir()
    (tmp_path / "nouveau").mkdir()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    old = choosers[-1][1]
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    recent = choosers[-1][1]
    old.file = NS(get_path=lambda: str(tmp_path / "ancien"))
    old.respond(1)
    entry.set_text.assert_not_called()
    assert old.destroyed == 1
    assert window._identity_directory_chooser is recent
    recent.file = NS(get_path=lambda: str(tmp_path / "nouveau"))
    recent.respond(1)
    entry.set_text.assert_called_once_with("nouveau")
    assert window._identity_directory_chooser is None


@pytest.mark.parametrize("scope", ["_closed", "_identity_context_generation", "_account_context_generation"])
def test_late_directory_response_only_cleans_chooser(tmp_path, scope):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    (tmp_path / "private").mkdir()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = NS(get_path=lambda: str(tmp_path / "private"))
    if scope == "_closed":
        window._closed = True
    else:
        setattr(window, scope, getattr(window, scope) + 1)
    chooser.respond(1)
    entry.set_text.assert_not_called()
    assert window._identity_directory_chooser is None
    assert chooser.destroyed == 1
    assert errors == []


def test_closed_directory_chooser_does_not_create_widgets():
    choose_identity_directory(NS(_closed=True), Mock(), Path("/tmp"), Gtk=None, Gio=None)


def test_adept_profile_request_is_explicit_and_does_not_fetch_synchronously():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    assert dialogs == []
    assert errors == []
    assert len(submitted) == 1
    args, kwargs = submitted[0]
    assert args[0] == "Profils ADE…"
    assert args[1] is window.service.adept_profile_choices
    assert kwargs["control"] is control
    window.service.adept_profile_choices.assert_not_called()


def test_adept_dialog_requires_explicit_exact_profile_response():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    done = submitted[0][0][2]
    done([
        {"name": "Alex", "reference": "uuid-a"},
        {"name": "Bob", "reference": "uuid-b"},
    ])
    assert len(dialogs) == 1
    dialog = dialogs[0]
    assert dialog.title == "Choisir le profil ADE"
    assert dialog.responses == [
        ("profile-0", "Alex · uuid-a"),
        ("profile-1", "Bob · uuid-b"),
        ("cancel", "Annuler"),
    ]
    assert dialog.close_response == "cancel"
    assert dialog.presented
    dialog.respond("cancel")
    dialog.respond("unknown")
    entry.set_text.assert_not_called()
    dialog.respond("profile-1")
    entry.set_text.assert_called_once_with("uuid-b")
    assert errors == []


def test_empty_adept_inventory_keeps_error_and_never_sets_editor_value():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    submitted[0][0][2]([])
    assert dialogs == []
    entry.set_text.assert_not_called()
    assert errors == [(
        "Autorisez un profil Adobe capable de récupérer les ACSM dans Kobo-Adept, puis réessayez.",
        "Aucun profil ADE prêt",
    )]


def test_adept_worker_error_retains_old_dialog_title():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    submitted[0][0][3](RuntimeError("inventaire indisponible"))
    assert errors == [("Lecture des profils ADE impossible", "Profils ADE")]


@pytest.mark.parametrize("scope", ["_closed", "_identity_context_generation", "_account_context_generation"])
def test_late_adept_inventory_does_not_construct_dialog(scope):
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    if scope == "_closed":
        window._closed = True
    else:
        setattr(window, scope, getattr(window, scope) + 1)
    submitted[0][0][2]([{"name": "Stale", "reference": "private-uuid"}])
    submitted[0][0][3](RuntimeError("private error"))
    assert dialogs == []
    assert errors == []


@pytest.mark.parametrize("scope", ["_closed", "_identity_context_generation", "_account_context_generation"])
def test_late_adept_selection_is_inert_even_if_dialog_was_open(scope):
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    submitted[0][0][2]([{"name": "Alex", "reference": "uuid-a"}])
    if scope == "_closed":
        window._closed = True
    else:
        setattr(window, scope, getattr(window, scope) + 1)
    dialogs[0].respond("profile-0")
    entry.set_text.assert_not_called()


def test_closed_adept_chooser_does_not_submit_tasks_or_touch_services():
    choose_adept_profile(NS(_closed=True), Mock(), object(), Adw=None)


PRIVATE = "https://private.invalid/LOAN/borrow?token=SECRET_CHOOSER /home/secret/account.acsm"


def test_directory_chooser_does_not_echo_private_path_or_exception(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = NS(get_path=Mock(side_effect=OSError(PRIVATE)))
    chooser.respond(gtk.ResponseType.ACCEPT)
    entry.set_text.assert_not_called()
    assert chooser.destroyed == 1
    assert window._identity_directory_chooser is None
    assert errors == [("Répertoire indisponible", "Choix du répertoire impossible")]
    assert "SECRET_CHOOSER" not in repr(errors)


def test_invalid_directory_selection_does_not_echo_sensitive_name(tmp_path):
    window, entry, gtk, gio, choosers, errors = _directory_host()
    choose_identity_directory(window, entry, tmp_path, Gtk=gtk, Gio=gio)
    chooser = choosers[-1][1]
    chooser.file = NS(get_path=lambda: str(tmp_path / "SECRET_CHOOSER invalid"))
    chooser.respond(gtk.ResponseType.ACCEPT)
    entry.set_text.assert_not_called()
    assert len(errors) == 1
    assert errors[0][1] == "Choix du répertoire impossible"
    assert "sous-répertoire direct" in errors[0][0]
    assert "SECRET_CHOOSER" not in repr(errors)
    assert chooser.destroyed == 1


def test_adept_inventory_error_does_not_stringify_raw_exception():
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)

    class HostileError(RuntimeError):
        def __str__(self):
            raise AssertionError("Exception __str__ must never enter GTK")

    submitted[0][0][3](HostileError(PRIVATE))
    assert errors == [("Lecture des profils ADE impossible", "Profils ADE")]
    assert dialogs == []


def test_adept_inventory_userfacing_message_is_kept_without_technical_details():
    from cifereca.plugin_api import UserFacingError
    window, entry, control, adw, errors, dialogs, submitted = _adept_host()
    choose_adept_profile(window, entry, control, Adw=adw)
    submitted[0][0][3](
        UserFacingError("Associez un profil Adobe.", code="profile_required",
                        technical_detail=PRIVATE)
    )
    assert errors == [("Associez un profil Adobe.", "Profils ADE")]
    assert "SECRET_CHOOSER" not in repr(errors)
