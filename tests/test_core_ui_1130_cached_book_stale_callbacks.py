"""P0 #1130: cancelled plugin cache subscriptions never refresh a new context."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.gui.plugin_reload_controller import subscribe_cached_book_details


def _window():
    calls, registrations, queued = [], [], []
    window = NS(
        _closed=False,
        plugin_manager=NS(services=lambda kind: ("service",)),
        _cached_book_unsubscribe=lambda: calls.append("unsubscribe-initial"),
        _cached_book_cache_invalidated=lambda: calls.append("refresh"),
    )
    def subscribe(services, callback):
        registrations.append(callback)
        calls.append("register")
        return lambda: calls.append("unsubscribe-" + str(len(registrations)))
    def schedule(callback):
        queued.append(callback)
    def renew():
        subscribe_cached_book_details(
            window, idle_add=schedule,
            subscribe_cached_book_detail_changes=subscribe,
        )
    return window, calls, registrations, queued, renew


def test_live_subscription_publishes_cache_refresh_and_idle_from_active_plugin():
    window, calls, subscriptions, queued, renew = _window()
    renew()
    assert calls == ["unsubscribe-initial", "register", "refresh"]
    subscriptions[0]()
    assert len(queued) == 1
    assert queued.pop()() is None
    assert calls[-1] == "refresh"


def test_old_observer_is_invalidated_before_unsubscribe_callback():
    window, calls, subscriptions, queued, renew = _window()
    renew()
    window._cached_book_unsubscribe = lambda: (
        calls.append("unsub-old"),
        subscriptions[0](),
    )
    renew()
    assert len(subscriptions) == 2
    assert queued == []
    assert calls[-2:] == ["register", "refresh"]


def test_old_observer_cannot_schedule_after_new_registration():
    window, calls, subscriptions, queued, renew = _window()
    renew()
    old = subscriptions[0]
    renew()
    calls.clear()
    old()
    assert queued == []
    assert calls == []
    subscriptions[-1]()
    assert len(queued) == 1


def test_already_queued_old_idle_is_inert_after_subscription_replaced():
    window, calls, subscriptions, queued, renew = _window()
    renew()
    subscriptions[0]()
    assert len(queued) == 1
    prior = queued.pop()
    renew()
    calls.clear()
    assert prior() is False
    assert calls == []
    subscriptions[-1]()
    assert len(queued) == 1
    assert queued.pop()() is None
    assert calls == ["refresh"]


def test_close_before_or_after_queuing_prevents_idle_refresh():
    window, calls, subscriptions, queued, renew = _window()
    renew()
    subscriptions[0]()
    pending = queued.pop()
    window._closed = True
    calls.clear()
    assert pending() is False
    subscriptions[0]()
    assert queued == []
    assert calls == []


def test_close_during_new_registration_disposes_new_observer():
    window, calls, subscriptions, queued, renew = _window()
    def subscribe(services, callback):
        calls.append("register")
        window._closed = True
        return lambda: calls.append("unsubscribe-new")
    subscribe_cached_book_details(
        window, idle_add=queued.append,
        subscribe_cached_book_detail_changes=subscribe,
    )
    assert calls == ["unsubscribe-initial", "register", "unsubscribe-new"]
    assert queued == []


def test_reentrant_subscribe_during_unsubscribe_cannot_override_newer_observer():
    window, calls, registrations, queued, renew = _window()
    renew()
    first_unsubscribe = window._cached_book_unsubscribe
    entered = [False]
    def reentrant_unsubscribe():
        first_unsubscribe()
        if not entered[0]:
            entered[0] = True
            renew()
    window._cached_book_unsubscribe = reentrant_unsubscribe
    renew()
    assert len(registrations) == 2
    assert calls.count("register") == 2
    assert window._closed is False
    calls.clear()
    registrations[0]()
    assert queued == []
    registrations[1]()
    assert len(queued) == 1
