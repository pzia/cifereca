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
        self.sensitive = True
        self.tooltip = ""
        self.classes = set()

    def add_css_class(self, value):
        self.classes.add(value)

    def remove_css_class(self, value):
        self.classes.discard(value)

    def has_css_class(self, value):
        return value in self.classes

    def set_sensitive(self, value):
        self.sensitive = value

    def set_tooltip_text(self, text):
        self.tooltip = text

    def connect(self, signal, callback):
        assert signal == "clicked"
        self._clicked = callback

    def click(self):
        assert self._clicked is not None
        return self._clicked(self)


class FakeLinkButton(FakeButton):
    def __init__(self, *, uri, label):
        super().__init__(label)
        self.uri = uri
        self._activate = None
        self.opened = []

    def set_tooltip_text(self, _text):
        self.tooltip = _text

    def connect(self, signal, callback):
        assert signal == "activate-link"
        self._activate = callback

    def activate_link(self):
        assert self._activate is not None
        if not self._activate(self):
            self.opened.append(self.uri)


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
    namespace = {
        "action_button": lambda title, icon: FakeButton(title),
        "Gtk": NS(LinkButton=FakeLinkButton),
        "safe_document_link_url": lambda url, **_kw: url,
    }
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


def cached_extension():
    source = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(
        stmt for stmt in tree.body
        if isinstance(stmt, ast.ClassDef) and stmt.name == "DetailPanel"
    )
    method = next(
        stmt for stmt in cls.body
        if isinstance(stmt, ast.FunctionDef)
        and stmt.name == "update_cached_book_details"
    )
    module = ast.fix_missing_locations(ast.Module(
        body=[
            ast.ImportFrom(module="__future__",
                           names=[ast.alias(name="annotations")], level=0),
            method,
        ], type_ignores=[],
    ))
    namespace = {
        "Gtk": NS(LinkButton=FakeLinkButton),
        "safe_document_link_url": lambda url, **_kw: url,
    }
    exec(compile(module, str(source), "exec"), namespace)
    update = namespace["update_cached_book_details"]
    panel = NS(
        _cached_section_widgets=[],
        _cached_action_ids=set(),
        _action_buttons=[],
        window=NS(action_button=lambda title, icon: FakeButton(title)),
    )

    def clear_actions():
        for button in panel._action_buttons:
            button._cifereca_action_context_revoked = True
        panel._action_buttons.clear()

    panel._clear_actions = clear_actions
    panel._actions = lambda buttons: panel._action_buttons.extend(buttons)
    return update, panel


def contribution(callback):
    return NS(
        plugin_id="catalogue",
        detail=NS(
            source=None, stale=False, updated_at=None, sections=(),
            actions=(NS(
                id="open-book", title="Ouvrir", icon_name="document-open-symbolic",
                uri="", callback=callback,
            ),),
        ),
    )


def test_cached_detail_actions_are_revoked_on_refresh_or_removal():
    update, panel = cached_extension()
    events = []
    update(panel, [contribution(lambda: events.append("old"))])
    old = panel._action_buttons[0]
    old.click()
    assert events == ["old"]

    update(panel, [contribution(lambda: events.append("new"))])
    assert old._cifereca_action_context_revoked is True
    old.click()
    assert events == ["old"]
    fresh = panel._action_buttons[0]
    assert fresh is not old
    fresh.click()
    assert events == ["old", "new"]

    update(panel, [])
    assert fresh._cifereca_action_context_revoked is True
    fresh.click()
    assert events == ["old", "new"]


def test_cached_detail_refresh_keeps_unrelated_current_base_action():
    update, panel = cached_extension()
    events = []
    base = FakeButton("Open local")
    base._cifereca_action_id = "open-local"
    base.connect("clicked", lambda _: events.append("base"))
    panel._action_buttons.append(base)

    update(panel, [contribution(lambda: events.append("cached"))])
    cached = next(
        b for b in panel._action_buttons if b._cifereca_action_id.startswith("book-cache:")
    )
    update(panel, [])
    assert cached._cifereca_action_context_revoked is True
    assert base._cifereca_action_context_revoked is False
    cached.click()
    base.click()
    assert events == ["base"]


def test_retired_plugin_detail_link_cannot_open_its_old_uri():
    view, buttons = adapter()
    url = "https://example.org/catalogue/old"
    action = UiBookDetailAction(
        id="open-link", title="Voir la notice", uri=url,
    )
    view.show_detail(UiBookDetailPresentation(title="Old", actions=(action,)))
    old = buttons[0]
    old.activate_link()
    assert old.opened == [url]

    view.show_detail(UiBookDetailPresentation(title="New", actions=(action,)))
    assert old._cifereca_action_context_revoked
    old.activate_link()
    assert old.opened == [url]
    current = buttons[0]
    current.activate_link()
    assert current.opened == [url]


def test_retired_cached_detail_link_cannot_reopen_old_source():
    update, panel = cached_extension()
    url = "https://example.org/old-book"
    entry = contribution(lambda: None)
    entry.detail.actions = (NS(
        id="catalogue", title="Open catalogue",
        icon_name="", uri=url, callback=None,
    ),)
    update(panel, [entry])
    old = panel._action_buttons[0]
    old.activate_link()
    assert old.opened == [url]
    update(panel, [])
    assert old._cifereca_action_context_revoked
    old.activate_link()
    assert old.opened == [url]


def test_plugin_actions_and_links_refuse_stale_context_even_before_panel_replacement():
    for invalidate in (
        lambda view, button: setattr(view._window.service, "_account_context_epoch", 3),
        lambda view, button: setattr(view._window.service, "_source_context_epoch", 3),
        lambda view, button: setattr(view._window, "_route_display_token", object()),
        lambda view, button: setattr(view._window.service, "current_identity", NS(identity_id="alice")),
        lambda view, button: setattr(view, "_public_c_page_generation", 3),
        lambda view, button: setattr(view._window, "_closed", True),
        lambda view, button: setattr(button, "get_mapped", lambda: False),
    ):
        view, buttons = adapter()
        view._window.service = NS(current_identity=NS(identity_id="alice"),
                                  _account_context_epoch=1, _source_context_epoch=1)
        view._window._route_display_token = object()
        view._public_c_page_generation = 1
        events = []
        url = "https://example.org/catalogue/exact"
        view.show_detail(UiBookDetailPresentation(title="Livre", actions=(
            UiBookDetailAction(id="local", title="Ouvrir", callback=lambda: events.append("click")),
            UiBookDetailAction(id="web", title="Fiche", uri=url),
        )))
        callback, link = buttons
        callback.click()
        link.activate_link()
        assert events == ["click"] and link.opened == [url]
        invalidate(view, callback)
        invalidate(view, link)
        callback.click()
        link.activate_link()
        assert events == ["click"] and link.opened == [url]
