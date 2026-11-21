"""Malformed action URI is neither a browser target nor a callback fallback."""
import pytest

from cifereca.plugin_api import UiBookDetailAction, UiBookDetailPresentation
from tests.test_core_p0_6366_plugin_detail_action_revocation import (
    adapter, cached_extension, contribution,
)


class ForeignURI:
    def __str__(self):
        raise AssertionError("URI must not be converted")

    def __bool__(self):
        raise AssertionError("URI must not be tested for truthiness")


@pytest.mark.parametrize("uri", [None, False, 0, ForeignURI()])
@pytest.mark.parametrize("cached", [False, True])
def test_malformed_action_uri_is_rejected_without_callback_fallback(uri, cached):
    events = []
    action = UiBookDetailAction("invalid", "Consulter", uri=uri,
                                callback=lambda: events.append("invalid"))
    valid = UiBookDetailAction("valid", "Ouvrir", callback=lambda: events.append("valid"))
    if cached:
        update, panel = cached_extension()
        entry = contribution(lambda: None)
        entry.detail.actions = (action, valid)
        update(panel, [entry])
        buttons = panel._action_buttons
    else:
        view, buttons = adapter()
        view.show_detail(UiBookDetailPresentation(title="Livre", actions=(action, valid)))
    button, = buttons
    assert button.title == "Ouvrir"
    button.click()
    assert events == ["valid"]
