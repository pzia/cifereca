"""#3685: cached extension actions cannot outlive their detail context."""
from types import SimpleNamespace as NS

import pytest

from tests.test_core_p0_6366_plugin_detail_action_revocation import (
    cached_extension, contribution,
)


@pytest.mark.parametrize("change", [
    "account", "source", "identity", "route", "ui_identity", "ui_account",
    "detail", "service", "closed", "unmapped",
])
def test_cached_action_context_revoked_before_refresh(change):
    update, panel = cached_extension()
    window = panel.window
    window.service = NS(current_identity=NS(identity_id="alice"),
                        _account_context_epoch=1, _source_context_epoch=1)
    window._route_display_token = object()
    window._identity_context_generation = 1
    window._account_context_generation = 1
    panel._rendered_search_detail = (object(), object())
    events = []
    entry = contribution(lambda: events.append("callback"))
    url = "https://example.org/catalogue/exact"
    entry.detail.actions += (NS(id="web", title="Fiche", icon_name="",
                                uri=url, callback=None),)
    update(panel, [entry])
    callback, link = panel._action_buttons
    callback.click()
    link.activate_link()
    assert events == ["callback"] and link.opened == [url]
    if change == "account":
        window.service._account_context_epoch += 2
    elif change == "source":
        window.service._source_context_epoch += 2
    elif change == "identity":
        window.service.current_identity = NS(identity_id="alice")
    elif change == "route":
        window._route_display_token = object()
    elif change == "ui_identity":
        window._identity_context_generation += 2
    elif change == "ui_account":
        window._account_context_generation += 2
    elif change == "detail":
        panel._rendered_search_detail = tuple(list(panel._rendered_search_detail))
    elif change == "service":
        window.service = NS(**vars(window.service))
    elif change == "closed":
        window._closed = True
    else:
        callback.get_mapped = link.get_mapped = lambda: False
    callback.click()
    link.activate_link()
    assert events == ["callback"] and link.opened == [url]
