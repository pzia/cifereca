"""#4988: reject a revoked lender before constructing the GTK action button."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.cross_profile_normal_lender_dialog import append_normal_lender_action
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


@pytest.mark.parametrize("change", ["disconnect", "epoch", "account"])
def test_stale_lender_does_not_create_action_widget(harness, change):
    service, account, session = harness
    item = _candidate().notice
    rows = service.possible_borrow_lenders_for_current_identity(exact_notice=item)
    assert len(rows) == 1
    if change == "disconnect":
        session.connected = False
    elif change == "epoch":
        session._epoch += 1
    else:
        account.unique_id += "-replaced"
    panel = NS(window=NS(_route_display_token=object()), _actions=Mock())
    assert append_normal_lender_action(
        panel, item=item, rows=rows, visible=(item, object()), service=service,
        identity=service.current_identity,
        identity_id=service.current_identity.identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
        route=panel.window._route_display_token,
    ) is False
    panel._actions.assert_not_called()
    assert session.calls == 0
