"""#4988: reentrant GTK dialog construction cannot paint a stale lender choice."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.cross_profile_normal_lender_dialog import present_normal_lender_choice
from tests.test_core_ui_p0_4988_normal_lender_dialog import setup


@pytest.mark.parametrize("multiple", [False, True])
@pytest.mark.parametrize("change", ["route", "lender", "notice"])
def test_native_dialog_reentrancy_refuses_before_present(multiple, change):
    panel, args, dialogs = setup(multiple=multiple)
    window = panel.window
    original_new = args["Adw"].MessageDialog.new
    painted = []

    def new(*values):
        dialog = original_new(*values)
        dialog.present = Mock(side_effect=lambda: painted.append(True))
        connect = dialog.connect

        def connect_and_mutate(event, callback):
            connect(event, callback)
            # The route/notice/provider can change during GTK setup itself.
            if change == "route":
                window._route_display_token = object()
            elif change == "lender":
                args["rows"][-1]._account.unique_id += "-replaced"
            else:
                args["item"].record_id += "-replaced"

        dialog.connect = connect_and_mutate
        return dialog

    args["Adw"] = NS(MessageDialog=NS(new=new))
    assert present_normal_lender_choice(panel, **args) is False
    assert len(dialogs) == 1
    assert painted == []
    dialogs[0].present.assert_not_called()
    assert window._cross_profile_borrow_pending is None
    window.runner.submit.assert_not_called()
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
