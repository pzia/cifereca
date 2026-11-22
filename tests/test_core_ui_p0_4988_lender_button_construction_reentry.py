"""#4988: action-button construction must not publish an obsolete lender intent."""
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui import cross_profile_normal_lender_dialog as dialog
from tests.test_core_ui_p1_4988_possible_lender_detail import view


@pytest.mark.parametrize("change", [
    "route", "identity", "account", "detail", "hidden", "notice", "lender", "none",
])
def test_gtk_button_reentrancy_rechecks_lender_before_paint(monkeypatch, change):
    w, candidate, service, panel, _group, _read = view()
    window = panel.window
    window._identity_context_generation = 1
    window._account_context_generation = 2
    service.execute_normal_lender_borrow_for_current_identity = Mock()
    visible = panel._rendered_search_detail
    route = window._route_display_token
    mapped = [True]
    panel.get_mapped = lambda: mapped[0]
    panel._actions = Mock()
    button = NS(connect=Mock())

    def action_button(*_args):
        # The provider is never called. Change state synchronously *after*
        # the initial receipt check but *before* the widget is published.
        if change == "route":
            window._route_display_token = object()
        elif change == "identity":
            service.current_identity = NS(identity_id="bob")
        elif change == "account":
            service._account_context_epoch += 1
        elif change == "detail":
            panel._rendered_search_detail = (w.notice, object())
        elif change == "hidden":
            mapped[0] = False
        elif change == "notice":
            w.notice.record_id += "-replaced"
        elif change == "lender":
            candidate._account.unique_id += "-replaced"
        return button

    monkeypatch.setitem(sys.modules, "gi", NS())
    monkeypatch.setitem(sys.modules, "gi.repository", NS(Adw=NS()))
    monkeypatch.setitem(sys.modules, "cifereca.gui.action_widgets",
                        NS(action_button=action_button))
    monkeypatch.setitem(sys.modules, "cifereca.gui.task_runner",
                        NS(TaskPriority=NS()))
    result = dialog.append_normal_lender_action(
        panel, item=w.notice, rows=(candidate,), visible=visible, service=service,
        identity=service.current_identity, identity_id="bob",
        account_epoch=3, source_epoch=5, route=route,
    )
    assert result is (change == "none")
    if change == "none":
        panel._actions.assert_called_once_with([button])
    else:
        panel._actions.assert_not_called()
    service.execute_normal_lender_borrow_for_current_identity.assert_not_called()
