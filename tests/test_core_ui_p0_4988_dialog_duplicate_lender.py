"""#4988: independently callable GTK lender entries must not multiply one receipt."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.cross_profile_normal_lender_dialog import (
    append_normal_lender_action, present_normal_lender_choice,
)
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def _lender(harness):
    service, _account, session = harness
    item = _candidate().notice
    rows = service.possible_borrow_lenders_for_current_identity(exact_notice=item)
    assert len(rows) == 1
    return service, session, item, (rows[0], rows[0])


def test_duplicate_receipt_never_opens_a_borrow_dialog(harness):
    service, session, item, duplicates = _lender(harness)
    window = NS(_cross_profile_borrow_pending=None)
    panel = NS(window=window)
    assert present_normal_lender_choice(
        panel, item=item, rows=duplicates, current=lambda: True, service=service,
        identity=service.current_identity,
        identity_id=service.current_identity.identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
        Adw=NS(MessageDialog=NS(new=Mock(side_effect=AssertionError("dialog")))),
        TaskPriority=NS(USER_ACTION="user"),
    ) is False
    assert window._cross_profile_borrow_pending is None
    assert session.calls == 0


def test_duplicate_receipt_never_adds_an_action_button(harness):
    service, session, item, duplicates = _lender(harness)
    actions = Mock()
    window = NS(_route_display_token=object())
    panel = NS(window=window, _actions=actions)
    assert append_normal_lender_action(
        panel, item=item, rows=duplicates, visible=(item, object()), service=service,
        identity=service.current_identity,
        identity_id=service.current_identity.identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
        route=window._route_display_token,
    ) is False
    actions.assert_not_called()
    assert session.calls == 0
