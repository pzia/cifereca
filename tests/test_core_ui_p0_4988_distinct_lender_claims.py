"""#4988: independently allocated receipts for one exact lender are one option."""
from dataclasses import replace
from unittest.mock import Mock

from cifereca.core.borrow_for_identity_discovery import possible_lender_claims_distinct
from cifereca.gui.cross_profile_normal_lender_dialog import present_normal_lender_choice
from tests.test_core_ui_p0_4988_normal_lender_dialog import setup


def test_two_real_distinct_lenders_still_offer_two_consent_choices():
    panel, args, dialogs = setup()
    assert possible_lender_claims_distinct(
        args["rows"], args["item"], requesting_identity_id=args["identity_id"])
    assert present_normal_lender_choice(panel, **args) is True
    assert len(dialogs) == 1
    dialogs[0].respond("cancel")
    panel.window.runner.submit.assert_not_called()


def test_two_allocated_receipts_for_same_lender_cannot_offer_fake_multi_choice():
    panel, args, dialogs = setup(multiple=False)
    original, = args["rows"]
    duplicate = replace(original)
    assert original is not duplicate
    args["rows"] = (original, duplicate)
    assert not possible_lender_claims_distinct(
        args["rows"], args["item"], requesting_identity_id=args["identity_id"])
    assert present_normal_lender_choice(panel, **args) is False
    assert dialogs == []
    panel.window.runner.submit.assert_not_called()
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()
    assert getattr(panel.window, "_cross_profile_borrow_pending", None) is None
