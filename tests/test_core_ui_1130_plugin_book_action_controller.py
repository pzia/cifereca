"""P0 #1130: contextual plugin actions on book details."""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.gui.plugin_book_action_controller import add_plugin_book_actions
from cifereca.plugin_api import UiActionContribution, UiPageContribution
from cifereca.gui.shell_model import contextual_actions


MAIN = Path(__file__).resolve().parents[1] / "src/cifereca/gui/main_window.py"
OWNER = MAIN.with_name("plugin_book_action_controller.py")


class Button:
    def __init__(self):
        self.callbacks = {}
        self.sensitive = None

    def connect(self, signal, callback):
        self.callbacks[signal] = callback

    def set_sensitive(self, enabled):
        self.sensitive = enabled

    def click(self):
        self.callbacks["clicked"](self)


def _setup(*, enabled=True, page=True, action_id="act"):
    calls = []
    action = UiActionContribution(
        id=action_id, title="Plugin action", icon_name="starred-symbolic",
        callback=lambda host: calls.append(("callback", host.context)),
        target="book.detail", enabled_when=(lambda ctx: enabled),
    )
    pages = {"plugin": UiPageContribution(
        route="plugin", title="Plugin", factory=lambda _: None,
        actions=(action,),
    )} if page else {}
    buttons = []
    context = NS(target="book.detail", route="library", book=NS(book_id="id"))
    window = NS(
        _closed=False, _pages=pages,
        _book_action_context=lambda _: context,
        action_button=lambda *args: buttons.append(Button()) or buttons[-1],
        _UiHost=lambda owner, ctx: NS(context=ctx),
        _invoke_ui_action=lambda a, host: a.callback(host),
    )
    detail = NS(add_actions=lambda items: calls.append(("buttons", items)))
    return window, detail, buttons, calls, action, context


def test_headless_module_and_stable_mainwindow_facade():
    tree = ast.parse(OWNER.read_text(encoding="utf-8"))
    assert not any("gi" in ast.unparse(n) for n in tree.body
                   if isinstance(n, (ast.Import, ast.ImportFrom)))
    main = ast.parse(MAIN.read_text(encoding="utf-8"))
    cls = next(n for n in main.body if isinstance(n, ast.ClassDef) and n.name == "MainWindow")
    facade = next(n for n in cls.body
                  if isinstance(n, ast.FunctionDef) and n.name == "_add_plugin_book_actions")
    assert [a.arg for a in facade.args.args] == ["self", "detail", "item"]
    assert "plugin_book_action_controller.add_plugin_book_actions" in ast.unparse(facade)
    assert "contextual_actions=contextual_actions" in ast.unparse(facade)


def test_exact_book_context_is_forwarded_to_public_action():
    window, detail, buttons, calls, action, context = _setup()
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    assert len(buttons) == 1
    assert buttons[0]._cifereca_action_id == "plugin:act"
    assert buttons[0].sensitive is True
    buttons[0].click()
    assert calls == [("buttons", buttons), ("callback", context)]


def test_disabled_action_is_not_dispatched():
    window, detail, buttons, calls, action, context = _setup(enabled=False)
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    assert len(buttons) == 1
    assert buttons[0].sensitive is False
    buttons[0].click()
    assert calls == [("buttons", buttons)]


def test_no_action_no_button_and_no_empty_group():
    window, detail, buttons, calls, action, context = _setup(page=False)
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    assert buttons == []
    assert calls == []


def test_removed_plugin_action_cannot_execute_stale_button():
    window, detail, buttons, calls, action, context = _setup()
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    window._pages = {}
    buttons[0].click()
    assert calls == [("buttons", buttons)]


def test_reloaded_plugin_with_same_id_cannot_execute_old_callback():
    window, detail, buttons, calls, action, context = _setup()
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    replacement = UiActionContribution(
        id=action.id, title=action.title, icon_name=action.icon_name,
        callback=lambda host: calls.append(("replacement", host.context)),
        target="book.detail",
    )
    window._pages = {"plugin": UiPageContribution(
        route="plugin", title="Plugin", factory=lambda _: None,
        actions=(replacement,),
    )}
    buttons[0].click()
    assert calls == [("buttons", buttons)]


def test_close_prevents_context_resolution_and_callbacks():
    window, detail, buttons, calls, action, context = _setup()
    window._closed = True
    window._book_action_context = lambda _: (_ for _ in ()).throw(
        AssertionError("context resolved after close")
    )
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    assert calls == []
    assert buttons == []


def test_late_click_after_close_is_ignored():
    window, detail, buttons, calls, action, context = _setup()
    add_plugin_book_actions(window, detail, {}, contextual_actions=contextual_actions)
    window._closed = True
    buttons[0].click()
    assert calls == [("buttons", buttons)]
