"""Producer-declared action state survives both shared detail adapters."""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.detail_enrichment import _valid_result
from cifereca.gui.detail_action_state import apply_detail_action_state
from cifereca.plugin_api import BookRef, CachedBookDetail, UiBookDetailAction, UiBookDetailPresentation
from cifereca.plugin_api.book_detail_cache import _sanitize
from tests.test_core_p0_6366_plugin_detail_action_revocation import (
    FakeButton, adapter, cached_extension,
)


@pytest.mark.parametrize("state", ["available", "unavailable", "busy", "unknown", None])
@pytest.mark.parametrize("uri", ["", "https://catalogue.example/book/42"])
@pytest.mark.parametrize("cached", [False, True])
def test_declared_state_blocks_callback_and_uri_even_on_synthetic_activation(state, uri, cached):
    calls = []
    action = UiBookDetailAction(
        "open", "Consulter", uri=uri, callback=lambda: calls.append("current"),
        state=state, unavailable_reason="Compte à reconnecter",
    )
    if cached:
        update, panel = cached_extension()
        update(panel, [NS(plugin_id="source", detail=NS(
            source=None, stale=False, updated_at=None, sections=(), actions=(action,),
        ))])
        button = panel._action_buttons[0]
    else:
        view, buttons = adapter()
        view.show_detail(UiBookDetailPresentation(title="Livre", actions=(action,)))
        button = buttons[0]
    assert button.sensitive is (state == "available")
    if state != "available":
        assert "Compte à reconnecter" in button.tooltip
        assert ("En cours" if state == "busy" else "Indisponible") in button.tooltip
    if uri:
        button.activate_link()
        assert button.opened == ([uri] if state == "available" else [])
    else:
        button.click()
        assert calls == (["current"] if state == "available" else [])


def test_state_update_requires_new_presentation_and_never_revives_retired_action():
    view, buttons = adapter()
    calls = []
    action = UiBookDetailAction("open", "Consulter", callback=lambda: calls.append("old"))
    view.show_detail(UiBookDetailPresentation(title="Livre", actions=(action,)))
    old = buttons[0]
    view.show_detail(UiBookDetailPresentation(title="Livre", actions=(replace(action, state="busy"),)))
    busy = buttons[0]
    old.click()
    busy.click()
    assert calls == []
    view.show_detail(UiBookDetailPresentation(title="Livre", actions=(replace(
        action, callback=lambda: calls.append("new"),
    ),)))
    old.click()
    busy.click()
    buttons[0].click()
    assert calls == ["new"]


@pytest.mark.parametrize("sanitize", [_sanitize, _valid_result])
@pytest.mark.parametrize("state", ["available", "unavailable", "busy", "unknown"])
def test_both_cache_collectors_preserve_state_and_bound_reason(sanitize, state):
    action = UiBookDetailAction("open", "Consulter", callback=lambda: None,
                                state=state, unavailable_reason="x" * 400)
    book = BookRef("book", "Livre")
    cached = CachedBookDetail("book", "profile", actions=(action,))
    result = sanitize(cached, book, "profile", "source")
    assert result.actions[0].state == state
    assert result.actions[0].unavailable_reason == "x" * 280
    assert result.actions[0].callback is action.callback


def test_existing_positional_action_abi_defaults_to_available():
    action = UiBookDetailAction("open", "Consulter", "book-symbolic", "", lambda: None)
    button = FakeButton(action.title)
    apply_detail_action_state(button, action)
    assert button.sensitive
    assert button.tooltip == "Consulter"
    assert not button._cifereca_action_state_blocked
