"""#4988: duplicate lender receipts must not reach consent or action creation."""
from unittest.mock import Mock

from cifereca.gui.cross_profile_normal_lender_dialog import (
    append_normal_lender_action, present_normal_lender_choice,
)
from tests.test_core_ui_p0_4988_normal_lender_dialog import setup


def test_duplicate_receipt_rejected_before_consent_dialog():
    panel, args, dialogs = setup()
    original = args["rows"][0]
    args["rows"] = (original, original)
    assert present_normal_lender_choice(panel, **args) is False
    assert dialogs == []
    assert panel.window._cross_profile_borrow_pending is None
    args["service"].execute_normal_lender_borrow_for_current_identity.assert_not_called()


def test_duplicate_receipt_rejected_before_action_widget():
    panel, args, dialogs = setup()
    original = args["rows"][0]
    panel._actions = Mock()
    panel.window._route_display_token = object()
    assert append_normal_lender_action(
        panel, item=args["item"], rows=(original, original),
        visible=(args["item"], object()), service=args["service"],
        identity=args["identity"], identity_id=args["identity_id"],
        account_epoch=args["account_epoch"], source_epoch=args["source_epoch"],
        route=panel.window._route_display_token,
    ) is False
    panel._actions.assert_not_called()
