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


class _HostileReason:
    def __str__(self):
        raise AssertionError("must not coerce plugin reason")

    def __bool__(self):
        raise AssertionError("must not evaluate plugin reason")


class _ReasonSubclass(str):
    def __str__(self):
        raise AssertionError("must not coerce native-string impostor")


@pytest.mark.parametrize("reason, expected", [
    ("  Compte\n\t à reconnecter  ", "Compte à reconnecter"),
    ("x" * 400, "x" * 280),
    ("source\x00invalide", ""),
    ("source\u202ereversed", ""),
    (_HostileReason(), ""),
    (_ReasonSubclass("faux"), ""),
    (42, ""),
])
def test_reason_parity_direct_and_both_cache_collectors(reason, expected):
    action = UiBookDetailAction("refresh", "Actualiser", state="busy",
                                unavailable_reason=reason)
    book = BookRef("book", "Livre")
    payload = CachedBookDetail("book", "profile", actions=(action,))
    variants = [action]
    for collect in (_sanitize, _valid_result):
        result = collect(payload, book, "profile", "source")
        assert result is not None and len(result.actions) == 1
        assert result.actions[0].unavailable_reason == expected
        variants.append(result.actions[0])
    for variant in variants:
        button = FakeButton("Actualiser")
        apply_detail_action_state(button, variant)
        assert not button.sensitive
        assert button._cifereca_action_state_blocked
        assert button.tooltip == ("Actualiser · En cours" +
                                  (f" · {expected}" if expected else ""))


@pytest.mark.parametrize("collect", [_sanitize, _valid_result])
def test_malformed_uri_rejected_before_truth_test(collect):
    book = BookRef("book", "Livre")
    action = UiBookDetailAction("bad", "Action", uri=_HostileReason())
    good = UiBookDetailAction("good", "Actualiser", callback=lambda: None)
    result = collect(CachedBookDetail("book", "profile", actions=(action, good)),
                     book, "profile", "source")
    assert result is not None and len(result.actions) == 1
    assert result.actions[0].title == "Actualiser"
