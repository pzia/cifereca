"""Targetless inactive states survive both cache projections and the GTK sink."""
from types import SimpleNamespace as NS

import pytest

from cifereca.detail_enrichment import _valid_result
from cifereca.plugin_api.book_detail_cache import _sanitize
from cifereca.plugin_api import BookRef, CachedBookDetail, UiBookDetailAction
from tests.test_core_p0_6366_plugin_detail_action_revocation import cached_extension

BOOK = BookRef(book_id="book", title="Titre")


def payload(action):
    return CachedBookDetail(book_id="book", identity_id="alice",
                            actions=(action,), sections=())


@pytest.mark.parametrize("collector", [_valid_result, _sanitize])
@pytest.mark.parametrize("state", ["busy", "unavailable"])
def test_targetless_inactive_action_survives_to_passive_button(collector, state):
    action = UiBookDetailAction("refresh", "Actualiser", state=state,
                                unavailable_reason="En attente de source",
                                priority=12, destructive=True)
    result = collector(payload(action), BOOK, "alice", "provider")
    assert result is not None and result.sections == ()
    saved, = result.actions
    assert saved.state == state and saved.callback is None and saved.uri == ""
    update, panel = cached_extension()
    update(panel, [NS(plugin_id="provider", detail=result)])
    button, = panel._action_buttons
    assert button.sensitive is False and button._clicked is None
    assert button._cifereca_action_state_blocked is True
    assert button._cifereca_action_priority == 12
    assert button.has_css_class("destructive-action")
    assert "En attente de source" in button.tooltip
    assert ("En cours" if state == "busy" else "Indisponible") in button.tooltip
    update(panel, [])
    assert button._cifereca_action_context_revoked is True
    assert not panel._action_buttons


@pytest.mark.parametrize("collector", [_valid_result, _sanitize])
@pytest.mark.parametrize("action", [
    UiBookDetailAction("x", "No target"),
    UiBookDetailAction("x", "Unknown state", state="unknown"),
    UiBookDetailAction("x", "Unsafe", state="busy",
                       uri="https://example.org/notice?token=secret"),
    UiBookDetailAction("x", "Invalid callback", state="unavailable", callback=False),
])
def test_targetless_available_unknown_or_malformed_never_becomes_contribution(collector, action):
    assert collector(payload(action), BOOK, "alice", "provider") is None


def test_final_sink_rejects_targetless_noninactive_even_if_collector_bypassed():
    update, panel = cached_extension()
    for state in ("available", "unknown"):
        detail = payload(UiBookDetailAction("x", "No target", state=state))
        update(panel, [NS(plugin_id="provider", detail=detail)])
        assert panel._action_buttons == []
