"""#6397: real SQLite Core choice, fake GTK/runner delivery boundary."""
from contextlib import closing
from types import SimpleNamespace as NS

from cifereca.storage import Storage
from cifereca.gui.notice_c_web_action_controller import request_public_web_actions
from tests.test_core_p0_6397_c_web_reference_persistence import source, link, publish

URL = "https://books.example.org/title/one"
OTHER = "https://books.example.org/title/two"


class Widget:
    def __init__(self, label=None, **kw):
        self.label = label
        self.children = []
        self.callback = None
    def connect(self, signal, callback):
        self.callback = callback
    def set_child(self, child):
        self.child = child
    def set_popover(self, popover):
        self.popover = popover
    def append(self, child):
        self.children.append(child)
    def set_max_content_height(self, height):
        pass
    def set_propagate_natural_height(self, enabled):
        pass


GTK = NS(Button=Widget, MenuButton=Widget, Popover=Widget, Box=Widget,
         ScrolledWindow=Widget, Orientation=NS(VERTICAL=1))


def request(db, c, *, context=None):
    pending, actions, launches, toasts = [], [], [], []
    generation = [0]
    runner = NS(submit=lambda label, work, done, fail, **kw:
                pending.append((work, done, fail)))
    host = NS(service=NS(storage=db), runner=runner, _closed=False, toast=toasts.append)
    panel = NS(add_actions=actions.extend)
    if context is not None:
        host._route_display_token = object()
        context.host = host
        panel.get_mapped = lambda: context.mapped
    gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))
    assert request_public_web_actions(
        host, panel, c.document, expected_epoch=c.epoch,
        current=lambda: generation[0] == 0, Gtk=GTK, Gio=gio,
    )
    return pending, actions, launches, generation, toasts


def deliver(pending):
    work, done, _ = pending.pop(0)
    done(work())


def test_opaque_single_action_checks_selection_before_and_after_worker(tmp_path):
    with closing(Storage(tmp_path / "one.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        c = publish(db, a.observation.key.edition_id, a)
        pending, actions, launches, generation, _ = request(db, c)
        assert not actions and len(pending) == 1
        deliver(pending)
        button, = actions
        assert button.label == "Ouvrir la fiche web"
        button.callback(button)
        assert not launches
        deliver(pending)
        assert launches == [URL]
        button.callback(button)
        generation[0] += 1
        deliver(pending)
        assert launches == [URL]
        button.callback(button)
        assert pending == []


def test_multi_choice_changed_source_and_revoked_action_fail_closed(tmp_path):
    with closing(Storage(tmp_path / "multiple.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL), link("seller-a", OTHER)])
        c = publish(db, a.observation.key.edition_id, a)
        pending, actions, launches, generation, toasts = request(db, c)
        deliver(pending)
        menu, = actions
        assert menu.label == "Ouvrir la fiche sur…"
        first, second = menu.popover.child.child.children
        first.callback(first)
        deliver(pending)
        assert launches == [URL]
        newer = source(db, "seller-a", "a", [link("seller-a", OTHER)], now=101)
        second.callback(second)
        deliver(pending)
        assert launches == [URL] and toasts
        menu._cifereca_action_context_revoked = True
        second.callback(second)
        assert not pending
        assert db.entitlements() == [] and db.acquisitions() == []


def test_late_menu_delivery_and_empty_links_do_not_create_action(tmp_path):
    with closing(Storage(tmp_path / "late.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        c = publish(db, a.observation.key.edition_id, a)
        pending, actions, _, generation, _ = request(db, c)
        generation[0] += 1
        deliver(pending)
        assert not actions
        b = source(db, "seller-b", "b", None)
        c = publish(db, b.observation.key.edition_id, b)
        pending, actions, _, _, _ = request(db, c)
        deliver(pending)
        assert not actions


def test_route_aba_revokes_in_flight_menu_and_browser_launch(tmp_path):
    with closing(Storage(tmp_path / "route.sqlite")) as db:
        a = source(db, "seller-a", "a", [link("seller-a", URL)])
        c = publish(db, a.observation.key.edition_id, a)
        context = NS(mapped=True)
        pending, actions, launches, _, toasts = request(db, c, context=context)
        context.host._route_display_token = object()
        context.host._route_display_token = object()
        deliver(pending)
        assert not actions

        pending, actions, launches, _, toasts = request(db, c, context=context)
        deliver(pending)
        button, = actions
        context.mapped = False
        button.callback(button)
        assert not pending
        context.mapped = True
        button.callback(button)
        assert len(pending) == 1
        context.host._route_display_token = object()
        context.host._route_display_token = object()
        deliver(pending)
        assert not launches and not toasts
        # A route change permanently revokes the old menu. An old button
        # cannot be reused merely because the same detail appears again.
        button.callback(button)
        assert not pending and not launches
        # The freshly mounted detail may request a new, independently
        # scoped menu. Only that new button can start a fresh C validation.
        pending, actions, launches, _, toasts = request(db, c, context=context)
        deliver(pending)
        button, = actions
        button.callback(button)
        assert len(pending) == 1
        deliver(pending)
        assert launches == [URL] and not toasts
        button.callback(button)
        context.mapped = False
        deliver(pending)
        assert launches == [URL]
        assert db.entitlements() == [] and db.acquisitions() == []


def test_search_detail_port_exposes_route_generation_read_only():
    import pytest
    from cifereca.gui.search_host_port import SearchDetailPort
    host = NS(_route_display_token=object())
    port = SearchDetailPort(host)
    original = port._route_display_token
    assert original is host._route_display_token
    with pytest.raises(AttributeError):
        port._route_display_token = object()
    host._route_display_token = object()
    assert port._route_display_token is not original


def test_multi_source_web_menu_uses_installed_names_without_merging_choices(tmp_path):
    with closing(Storage(tmp_path / "library-names.sqlite")) as db:
        a = source(db, "seller-a", "same", [link("seller-a", URL)])
        b = source(db, "seller-b", "same", [link("seller-b", OTHER)])
        target = a.observation.key.edition_id
        publish(db, target, a)
        c = publish(db, target, b)
        context = NS(mapped=True)
        pending, actions, launches, _, _ = request(db, c, context=context)
        calls = []
        def name(provider):
            calls.append(provider)
            return "Bibliothèque commune"
        context.host.service.provider_display_name = name
        deliver(pending)
        buttons = actions[0].popover.child.child.children
        assert len(buttons) == 2
        assert len({button.label for button in buttons}) == 2
        assert all(button.label.startswith("Bibliothèque commune") for button in buttons)
        assert sorted(calls) == ["seller-a", "seller-b"]
        for button in buttons:
            button.callback(button)
            deliver(pending)
        assert set(launches) == {URL, OTHER}
        pending, actions, launches, _, _ = request(db, c, context=context)
        context.host.service.provider_display_name = lambda _: "https://private.example/?token=SECRET"
        deliver(pending)
        buttons = actions[0].popover.child.child.children
        assert all("SECRET" not in button.label for button in buttons)
        assert db.entitlements() == [] and db.acquisitions() == []
