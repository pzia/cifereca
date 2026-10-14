"""P0 #1132: headless feedback dialog rendering, severity and lifetime."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.feedback_dialog_controller import show_error, show_attention


ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"
MAIN = ROOT / "main_window.py"
OWNER = ROOT / "feedback_dialog_controller.py"


class Dialog:
    def __init__(self, window, title, body, events):
        self.window = window
        self.title = title
        self.body = body
        self.events = events
        self.responses = []
        self.default = self.close_response = None
        self.markup = None

    def add_response(self, key, caption):
        self.responses.append((key, caption))
        self.events.append(("response", key, caption))

    def set_default_response(self, key):
        self.default = key
        self.events.append(("default", key))

    def set_close_response(self, key):
        self.close_response = key
        self.events.append(("close-response", key))

    def set_body_use_markup(self, enabled):
        self.markup = enabled
        self.events.append(("markup", enabled))

    def present(self):
        self.events.append(("present", self.title, self.body))


def _fixture():
    events, dialogs = [], []

    def new_dialog(window, title, body):
        events.append(("create", title, body))
        dialog = Dialog(window, title, body, events)
        dialogs.append(dialog)
        return dialog

    adw = NS(MessageDialog=NS(new=new_dialog))
    diagnostics = NS(add=lambda *args, **kwargs: events.append(("diagnostic", args, kwargs)))
    window = NS(
        _closed=False,
        diagnostics=diagnostics,
        _functional_error_message=lambda message: f"public: {message}",
        toast=lambda message, timeout: events.append(("toast", message, timeout)),
    )
    return window, adw, events, dialogs


def test_owner_does_not_import_gtk_or_mainwindow():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    imports = [ast.unparse(node) for node in ast.walk(tree)
               if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert not any("import gi" in entry or "main_window" in entry for entry in imports)


def test_mainwindow_facades_retain_arguments_and_early_close_guards():
    tree = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(node for node in tree.body
               if isinstance(node, ast.ClassDef) and node.name == "MainWindow")
    methods = {node.name: node for node in cls.body
               if isinstance(node, ast.FunctionDef)}
    error = methods["show_error"]
    attention = methods["show_attention"]
    assert [arg.arg for arg in error.args.args] == ["self", "message", "title"]
    assert [arg.arg for arg in attention.args.args] == ["self", "message"]
    assert [arg.arg for arg in attention.args.kwonlyargs] == ["code"]
    assert [value.value for value in attention.args.kw_defaults] == ["attention_required"]
    for function, target in ((error, "show_error"), (attention, "show_attention")):
        text = ast.unparse(function)
        assert "getattr(self, '_closed', False)" in text
        assert f"feedback_dialog_controller.{target}" in text
        assert "Adw=Adw" in text


def test_error_uses_public_message_before_six_second_toast_and_dialog():
    window, adw, events, dialogs = _fixture()
    show_error(window, "not authorized/default", Adw=adw)
    assert events[0] == ("toast", "public: not authorized/default", 6)
    assert events[1] == (
        "create", "Une erreur est survenue", "public: not authorized/default",
    )
    assert events[-1] == (
        "present", "Une erreur est survenue", "public: not authorized/default",
    )
    dialog = dialogs[0]
    assert dialog.window is window
    assert dialog.responses == [("ok", "Fermer")]
    assert dialog.default == "ok"
    assert dialog.close_response == "ok"
    assert dialog.markup is None
    assert not any(event[0] == "diagnostic" for event in events)


def test_error_preserves_custom_title_and_uses_exactly_one_dialog():
    window, adw, events, dialogs = _fixture()
    show_error(window, "op failed", "Synchronisation impossible", Adw=adw)
    assert len(dialogs) == 1
    assert dialogs[0].title == "Synchronisation impossible"
    assert events.count(("toast", "public: op failed", 6)) == 1


def test_attention_disables_markup_before_presentation_and_records_warning():
    window, adw, events, dialogs = _fixture()
    message = "<b>Attention</b> & untrusted"
    show_attention(window, message, code="repair_needed", Adw=adw)
    dialog = dialogs[0]
    assert dialog.title == "Action requise"
    assert dialog.body == message
    assert dialog.responses == [("close", "Fermer")]
    assert dialog.default == "close"
    assert dialog.close_response == "close"
    assert dialog.markup is False
    assert events.index(("markup", False)) < events.index(
        ("present", "Action requise", message)
    )
    assert events[-1] == (
        "diagnostic", ("UX", "attention required", "warning"),
        {"detail": {"code": "repair_needed", "message": message},
         "level": "detailed"},
    )
    assert not any(event[0] == "toast" for event in events)


def test_attention_uses_existing_default_public_code():
    window, adw, events, dialogs = _fixture()
    show_attention(window, "Réparer la configuration", Adw=adw)
    diagnostic = [e for e in events if e[0] == "diagnostic"]
    assert len(diagnostic) == 1
    assert diagnostic[0][2]["detail"] == {
        "code": "attention_required", "message": "Réparer la configuration",
    }


@pytest.mark.parametrize("method", ["error", "attention"])
def test_closed_window_skips_every_dialog_toast_and_diagnostic(method):
    window, adw, events, dialogs = _fixture()
    window._closed = True
    window._functional_error_message = Mock(
        side_effect=AssertionError("closed window must not format private errors")
    )
    if method == "error":
        show_error(window, "secret", Adw=adw)
    else:
        show_attention(window, "secret", Adw=adw)
    assert events == []
    assert dialogs == []


def test_attention_preserves_diagnostic_only_after_successful_presentation():
    window, adw, events, _dialogs = _fixture()
    class FailingDialog(Dialog):
        def present(self):
            raise RuntimeError("widget destroyed")
    adw.MessageDialog.new = lambda w, title, body: FailingDialog(w, title, body, events)
    with pytest.raises(RuntimeError, match="widget destroyed"):
        show_attention(window, "late", Adw=adw)
    assert not any(e[0] == "diagnostic" for e in events)


def test_error_does_not_create_dialog_when_public_message_formatting_fails():
    window, adw, events, dialogs = _fixture()
    window._functional_error_message = Mock(side_effect=ValueError("invalid message"))
    with pytest.raises(ValueError, match="invalid message"):
        show_error(window, "private error", Adw=adw)
    assert events == []
    assert dialogs == []
