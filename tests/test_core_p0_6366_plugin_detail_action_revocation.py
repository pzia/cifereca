"""P0 #6366/#3104: plugin detail actions are revoked on detail replacement.

A retained Gtk.Button may still receive a queued click after DetailPanel has
retired it; never dispatch its old callback to a different source/account.
"""
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace as NS

from cifereca.plugin_api import UiBookDetailAction, UiBookDetailPresentation


SOURCE = Path(__file__).resolve().parents[1] / "src/cifereca/gui/public_book_view.py"


class FakeButton:
    def __init__(self, title):
        self.title = title
        self._clicked = None

    def connect(self, signal, callback):
        assert signal == "clicked"
        self._clicked = callback

    def click(self):
        assert self._clicked is not None
        return self._clicked(self)


def adapter():
    tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
    cls = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.ClassDef) and stmt.name == "PublicBookMasterDetailView"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__",
                           names=[ast.alias(name="annotations")], level=0),
            cls,
        ], type_ignores=[],
    ))
    namespace = {"action_button": lambda title, icon: FakeButton(title)}
    exec(compile(module, str(SOURCE), "exec"), namespace)
    view = object.__new__(namespace["PublicBookMasterDetailView"])
    buttons = []

    def show(_detail):
        for button in buttons:
            button._cifereca_action_context_revoked = True
        buttons.clear()

    panel = NS(show_presentation=show, add_actions=lambda new: buttons.extend(new))
    view._page = NS(detail=panel)
    view._window = NS()
    return view, buttons


def detail(callback, *, action_id="download"):
    return UiBookDetailPresentation(
        title="Exact same book title",
        actions=(UiBookDetailAction(
            id=action_id, title="Télécharger", callback=callback,
        ),),
    )


def test_retired_button_never_executes_previous_plugin_callback():
    view, buttons = adapter()
    calls = []
    view.show_detail(detail(lambda: calls.append("account-A")))
    original = buttons[0]
    assert original.click() is None and calls == ["account-A"]

    view.show_detail(detail(lambda: calls.append("account-B")))
    assert original._cifereca_action_context_revoked is True
    assert original.click() is None
    assert calls == ["account-A"]

    assert buttons[0] is not original
    buttons[0].click()
    assert calls == ["account-A", "account-B"]


def test_retired_callback_cannot_bypass_revoke_with_same_action_id_and_label():
    view, buttons = adapter()
    events = []
    view.show_detail(detail(lambda: events.append("old"), action_id="open"))
    old_button = buttons[0]
    view.show_detail(detail(lambda: events.append("new"), action_id="open"))
    old_button.click()
    assert events == []
    buttons[0].click()
    assert events == ["new"]


def test_unrevoked_plugin_button_keeps_existing_callback_behavior():
    view, buttons = adapter()
    count = []
    view.show_detail(detail(lambda: count.append("once")))
    button = buttons[0]
    assert not getattr(button, "_cifereca_action_context_revoked", False)
    button.click()
    button.click()
    assert count == ["once", "once"]
