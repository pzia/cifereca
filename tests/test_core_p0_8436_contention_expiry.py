"""Deterministic contention expiry delivery; no GTK loop or sleeps."""
from types import SimpleNamespace as NS
import pytest
from cifereca.gui.sync_action_scope import capture_sync_action_scope, schedule_contention_expiry

@pytest.mark.parametrize("change", ["none", "close", "navigate", "identity", "generation"])
def test_expiry_callback_respects_scope(change):
    calls, timers = [], []
    token = object()
    window = NS(_closed=False, _sync_page_token=token, _sync_preview_generation=1,
                service=NS(current_identity=NS(identity_id="fixture")),
                _refresh_sync_page=lambda: calls.append("refresh"))
    scope = capture_sync_action_scope(window, page_token=token, preview_generation=1)
    schedule = lambda delay, cb: timers.append((delay, cb))
    schedule_contention_expiry(window, scope, [NS(contention_observed=False)], schedule)
    assert not timers
    schedule_contention_expiry(window, scope, [NS(contention_observed=True)], schedule)
    assert len(timers) == 1
    if change == "close":
        window._closed = True
    elif change == "navigate":
        window._sync_page_token = object()
    elif change == "identity":
        window.service.current_identity.identity_id = "other"
    elif change == "generation":
        window._sync_preview_generation += 1
    delay, callback = timers.pop()
    assert delay == 30
    assert callback() is False
    assert calls == (["refresh"] if change == "none" else [])
