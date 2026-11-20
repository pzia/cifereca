"""An available-looking detail action still needs an executable target."""
from types import SimpleNamespace

import pytest

from cifereca.gui.detail_action_state import apply_detail_action_state
from cifereca.plugin_api import UiBookDetailAction, UiBookDetailPresentation
from tests.test_core_p0_6366_plugin_detail_action_revocation import FakeButton, adapter


@pytest.mark.parametrize("callback", [None, "non-callable"])
def test_public_button_without_target_is_disabled(callback):
    view, buttons = adapter()
    action = UiBookDetailAction("missing", "Consulter", callback=callback)
    view.show_detail(UiBookDetailPresentation(title="Livre", actions=(action,)))
    button = buttons[0]
    assert not button.sensitive
    assert button._cifereca_action_state_blocked
    assert button.tooltip == "Consulter · Indisponible · Aucune action exécutable"


def test_malformed_state_is_not_compared_or_converted():
    class Invalid:
        def __eq__(self, other):
            raise AssertionError("plugin state must not be compared")
        def __str__(self):
            raise AssertionError("plugin reason must not be converted")

    button = FakeButton("Consulter")
    action = SimpleNamespace(title="Consulter", state=Invalid(),
                             unavailable_reason=Invalid(), callback=lambda: None)
    apply_detail_action_state(button, action)
    assert not button.sensitive
    assert button.tooltip == "Consulter · Indisponible"


@pytest.mark.parametrize("state,expected", [
    ("busy", "En cours"), ("unavailable", "Indisponible"),
])
def test_no_target_does_not_erase_declared_unavailability(state, expected):
    button = FakeButton("Consulter")
    action = UiBookDetailAction("pending", "Consulter", state=state,
                                unavailable_reason="Compte non vérifié")
    apply_detail_action_state(button, action)
    assert not button.sensitive
    assert button.tooltip == f"Consulter · {expected} · Compte non vérifié"


def test_valid_callback_and_valid_link_keep_legacy_state():
    for target in ({"callback": lambda: None}, {"uri": "https://example.org/books/1"}):
        button = FakeButton("Consulter")
        apply_detail_action_state(button, UiBookDetailAction("open", "Consulter", **target))
        assert button.sensitive
        assert not button._cifereca_action_state_blocked
        assert button.tooltip == "Consulter"
