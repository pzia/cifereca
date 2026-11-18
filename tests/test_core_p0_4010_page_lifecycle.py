"""Deterministic shell lifecycle witnesses, with no GTK or provider I/O."""
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.gui.page_lifecycle import (
    PageLifecycles, PageScope, renew_lifecycles, notify_context, notify_visible,
    manages_route,
)
from cifereca.plugin_api import UiPageContribution
from cifereca.builtin_ui.pages import Pages


def _fixture():
    state = [PageScope(1, "A", 0, ("p", "c", "a"), 0)]
    alive = [True]
    errors = []
    events = {"first": [], "second": []}
    owner = PageLifecycles(lambda: state[0], lambda: alive[0],
                           lambda: errors.append("safe-code"))
    for route, scoped in (("first", False), ("second", True)):
        page = UiPageContribution(
            route, route, lifecycle_account_scoped=scoped,
            lifecycle=lambda _host, _widget, event, r=route: events[r].append(event),
        )
        owner.mount(page, object(), object())
    return state, alive, errors, events, owner


def test_visible_and_hidden_follow_latest_identity_once_and_revoke_aba():
    state, _, _, events, owner = _fixture()
    owner.show("first")
    old = events["first"][-1]
    assert old.is_current() and old.identity_id == "A"
    owner.show("first")  # duplicate GTK signal
    assert len(events["first"]) == 1
    for index, identity in enumerate(("B", "A", "C"), 1):
        state[0] = replace(state[0], identity=identity, identity_epoch=index)
        owner.context_changed()
        assert not old.is_current()
    assert [e.identity_id for e in events["first"]] == ["A", "B", "A", "C"]
    assert all(e.visible for e in events["first"])
    assert [e.identity_id for e in events["second"]] == ["B", "A", "C"]
    assert all(not e.visible and not e.is_current() for e in events["second"])
    owner.context_changed()
    assert len(events["second"]) == 3
    visible = events["first"][-1]
    owner.show("second")
    assert not visible.is_current()
    assert events["first"][-1].reason == "hide"
    show = events["second"][-1]
    assert show.reason == "show" and show.identity_id == "C" and show.is_current()
    owner.show("second")
    assert len(events["second"]) == 4


def test_account_change_notifies_only_opted_pages_and_aba_is_rejected():
    state, _, _, events, owner = _fixture()
    owner.show("first")
    initial = events["first"][-1]
    assert initial.account is None
    state[0] = replace(state[0], account=("p", "c", "b"), account_epoch=1)
    owner.context_changed()
    assert len(events["first"]) == 1 and initial.is_current()
    assert events["second"][-1].account == ("p", "c", "b")
    assert not events["second"][-1].visible
    owner.show("second")
    old = events["second"][-1]
    state[0] = replace(state[0], account=("p", "c", "a"), account_epoch=2)
    owner.context_changed()
    state[0] = replace(state[0], account=("p", "c", "b"), account_epoch=3)
    owner.context_changed()
    assert not old.is_current()
    assert events["second"][-1].is_current()


def test_invalidation_precedes_hooks_and_nested_updates_supersede_old_events():
    state, alive, errors, events, owner = _fixture()
    owner.show("second")
    old = events["second"][-1]
    seen = []

    def first(_host, _widget, event):
        if event.identity_id == "B":
            assert not old.is_current()
            state[0] = replace(state[0], identity="C", identity_epoch=2)
            owner.context_changed()
        else:
            seen.append(event.identity_id)

    owner.mount(UiPageContribution("first", "First", lifecycle=first), object(), object())
    state[0] = replace(state[0], identity="B", identity_epoch=1)
    owner.context_changed()
    assert seen == ["C"]
    assert [e.identity_id for e in events["second"]] == ["A", "C"]
    assert events["second"][-1].is_current()
    assert errors == []


def test_dispose_revokes_and_exceptions_do_not_block_other_pages():
    state, alive, errors, events, owner = _fixture()
    owner.mount(UiPageContribution("bad", "Bad", lifecycle=lambda *_: (
        _ for _ in ()).throw(RuntimeError("secret synthetic exception"))), object(), object())
    owner.show("first")
    old = events["first"][-1]
    state[0] = replace(state[0], identity="B", identity_epoch=1)
    owner.context_changed()
    assert errors == ["safe-code"]
    assert events["first"][-1].identity_id == "B"
    last = events["first"][-1]
    owner.dispose()
    assert events["first"][-1].reason == "dispose"
    assert not last.is_current() and not old.is_current()
    assert not owner.manages("first")
    alive[0] = False
    owner.show("first")
    assert events["first"][-1].reason == "dispose"


def test_shell_rebuild_window_close_and_builtin_sync_consumer():
    reads = []
    window = NS(
        service=NS(current_identity=NS(identity_id="A"), current_account=None),
        _identity_context_generation=0, _account_context_generation=0,
        _closed=False, diagnostics=NS(add=lambda *_a, **_k: None),
    )
    window._refresh_sync_page = lambda: reads.append(window.service.current_identity.identity_id)
    owner = renew_lifecycles(window)
    sync = next(p for p in Pages().pages() if p.route == "sync")
    host = NS(window=window)
    widget = object()
    owner.mount(sync, widget, host)
    assert manages_route(window, "sync")
    notify_visible(window, "sync")
    notify_visible(window, "sync")
    assert reads == ["A"]
    window.service.current_identity = NS(identity_id="B")
    window._identity_context_generation += 1
    notify_context(window)
    assert reads == ["A", "B"]
    notify_visible(window, "library")
    window.service.current_identity = NS(identity_id="C")
    window._identity_context_generation += 1
    notify_context(window)
    assert reads == ["A", "B"]
    notify_visible(window, "sync")
    assert reads == ["A", "B", "C"]

    captured = []
    owner.mount(UiPageContribution("custom", "Custom", lifecycle=lambda _h, _w, e: captured.append(e)), object(), host)
    notify_visible(window, "custom")
    first = captured[-1]
    assert first.is_current()
    replacement = renew_lifecycles(window)
    assert captured[-1].reason == "dispose" and not first.is_current()
    replacement.mount(UiPageContribution("custom", "Custom", lifecycle=lambda _h, _w, e: captured.append(e)), object(), host)
    notify_visible(window, "custom")
    second = captured[-1]
    assert second.is_current()
    window._closed = True
    assert not second.is_current()
    notify_context(window)
    notify_visible(window, "sync")
    assert reads == ["A", "B", "C"]
    assert not manages_route(window, "custom")


def test_legacy_positional_contribution_stays_inert():
    _, _, _, _, owner = _fixture()
    legacy = UiPageContribution("legacy", "Legacy", "icon", 0, None, "usage", (), 0)
    owner.mount(legacy, object(), object())
    owner.show("legacy")
    assert not owner.manages("legacy")
