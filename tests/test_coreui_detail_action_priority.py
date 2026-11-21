"""Producer layout hints survive collectors and use the native action bar."""
from types import SimpleNamespace as NS

import pytest

from cifereca.detail_enrichment import _valid_result
from cifereca.gui.detail_action_state import apply_detail_action_state
from cifereca.plugin_api import BookRef, CachedBookDetail, UiBookDetailAction, UiBookDetailPresentation
from cifereca.plugin_api.book_detail_cache import _sanitize
from tests.test_core_p0_6366_plugin_detail_action_revocation import FakeButton, adapter, cached_extension
from tests.test_core_ui_375_detail_action_order import _methods, Button, Bar


@pytest.mark.parametrize("cached", [False, True])
@pytest.mark.parametrize("priority", [None, 5, 70, True, "5"])
def test_both_detail_adapters_keep_declared_layout_and_existing_defaults(cached, priority):
    calls = []
    action = UiBookDetailAction("remove", "Retirer", callback=lambda: calls.append(1),
                                state="unavailable", priority=priority, destructive=True)
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
    if type(priority) is int:
        assert button._cifereca_action_priority == priority
    elif cached:
        assert button._cifereca_action_priority == 35
    else:
        assert not hasattr(button, "_cifereca_action_priority")
    assert button.classes == {"destructive-action"}
    assert not button.sensitive
    button.click()
    assert calls == []


@pytest.mark.parametrize("sanitize", [_sanitize, _valid_result])
def test_both_collectors_preserve_priority_and_destructive_declaration(sanitize):
    callback = lambda: None
    action = UiBookDetailAction("remove", "Retirer", callback=callback, priority=5, destructive=True)
    book = BookRef("book", "Livre")
    result = sanitize(CachedBookDetail("book", "profile", actions=(action,)), book, "profile", "source")
    assert result.actions[0].priority == 5
    assert result.actions[0].destructive is True
    assert result.actions[0].callback is callback


def test_native_bar_uses_stable_declared_order_and_never_suggests_destructive_actions():
    priority, actions = _methods("_detail_action_priority", "_actions")
    bar = Bar()
    host = NS(action_scroll=bar, _action_ids=set(), _action_buttons=[], _rendered_action_count=0,
              _detail_action_priority=lambda button: priority(button))
    opening = Button("Ouvrir", "open")
    destructive = Button("Retirer", "remove")
    secondary = Button("Autre", "other")
    for button, rank in ((opening, 5), (destructive, 1), (secondary, 5)):
        button._cifereca_action_priority = rank
    destructive._cifereca_action_destructive = True
    destructive.add_css_class("destructive-action")
    actions(host, [opening, secondary, destructive])
    assert bar.buttons == [destructive, opening, secondary]
    assert destructive.classes == {"destructive-action"}
    assert opening.classes == {"suggested-action"}
    assert secondary.classes == set()


def test_native_destructive_css_is_respected_without_a_plugin_marker():
    priority, actions = _methods("_detail_action_priority", "_actions")
    bar = Bar()
    host = NS(action_scroll=bar, _action_ids=set(), _action_buttons=[], _rendered_action_count=0,
              _detail_action_priority=lambda button: priority(button))
    button = Button("Ouvrir", "open")
    button.classes.add("destructive-action")
    button.has_css_class = lambda value: value in button.classes
    actions(host, [button])
    assert button.classes == {"destructive-action"}


def test_default_abi_keeps_native_layout_and_reapplying_state_clears_destructive_style():
    button = FakeButton("Ouvrir")
    apply_detail_action_state(button, UiBookDetailAction("open", "Ouvrir", callback=lambda: None, destructive=True))
    assert button.classes == {"destructive-action"}
    apply_detail_action_state(button, UiBookDetailAction("open", "Ouvrir", callback=lambda: None))
    assert button.classes == set()
    assert not hasattr(button, "_cifereca_action_priority")
    assert button.sensitive


def test_native_review_action_removal_releases_id_and_preserves_peer():
    priority, actions, retire = _methods("_detail_action_priority", "_actions", "remove_review_action")
    bar = Bar()
    host = NS(action_scroll=bar, _action_ids=set(), _action_buttons=[], _rendered_action_count=0,
              _detail_action_priority=lambda button: priority(button))
    host._actions = lambda buttons: actions(host, buttons)
    peer = Button("Ouvrir", "open")
    old = Button("Catalogue 1", "book-review:fixture:public-0")
    replacement = Button("Catalogue 2", "book-review:fixture:public-0")
    actions(host, [peer, old])
    retire(host, old)
    assert bar.buttons == [peer, old]
    old._cifereca_review_action_retired = True
    old._cifereca_action_context_revoked = True
    retire(host, old)
    assert bar.buttons == [peer] and host._action_buttons == [peer]
    assert old._cifereca_action_id not in host._action_ids
    actions(host, [replacement])
    assert bar.buttons == [peer, replacement]
    assert old._cifereca_action_context_revoked is True
