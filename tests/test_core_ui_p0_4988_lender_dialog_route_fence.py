"""A route ABA cannot renew an already-open lender consent dialog."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.cross_profile_normal_lender_dialog import present_normal_lender_choice
from tests.test_core_ui_p0_4988_normal_lender_dialog import setup


def aba(window):
    window._route_display_token = object()
    window._route_display_token = object()


@pytest.mark.parametrize("stage", ["choice", "confirmation", "queued", "delivery"])
@pytest.mark.parametrize("change", ["route", "_identity_context_generation", "_account_context_generation"])
def test_route_aba_revokes_only_old_lender_intent(stage, change):
    panel, args, dialogs = setup()
    window = panel.window
    window._route_display_token = object()
    window._identity_context_generation = 1
    window._account_context_generation = 1

    def invalidate():
        if change == "route":
            aba(window)
        else:
            setattr(window, change, getattr(window, change) + 2)

    execute = args["service"].execute_normal_lender_borrow_for_current_identity
    assert present_normal_lender_choice(panel, **args)
    if stage == "choice":
        invalidate()
        dialogs[0].respond("choice-0")
    else:
        dialogs[0].respond("choice-0")
        if stage == "confirmation":
            invalidate()
        dialogs[1].respond("borrow")
    if stage in ("choice", "confirmation"):
        window.runner.submit.assert_not_called()
        execute.assert_not_called()
    else:
        _, work, done, _ = window.runner.submit.call_args.args
        if stage == "queued":
            invalidate()
            assert work() == {"status": "blocked"}
            execute.assert_not_called()
            done({"status": "blocked"})
        else:
            result = work()
            execute.assert_called_once()
            invalidate()
            done(result)
        window._activity_result.assert_not_called()
        window.show_error.assert_not_called()
    assert window._cross_profile_borrow_pending is None
    # New explicit choice/confirmation on the current route is permitted.
    # For an already executed loan, Core (not GUI) owns any repeat refusal.
    assert present_normal_lender_choice(panel, **args)
    dialogs[-1].respond("cancel")
    assert window._cross_profile_borrow_pending is None
