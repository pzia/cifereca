"""#4988: disconnect without epoch rotation revokes displayed lender and chooser.

Uses real in-memory provider session and Core SQLite; no supplier I/O or POST.
"""
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from cifereca.gui.cross_profile_possible_lender_detail import append_possible_lender_summary
from cifereca.gui.cross_profile_normal_lender_dialog import present_normal_lender_choice
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def panel_for(harness):
    service, account, session = harness
    item = _candidate().notice
    calls = []
    def submit(_name, work, done, fail, **kwargs):
        calls.append((work, done, fail))
    window = NS(service=service, runner=NS(submit=submit),
                _route_display_token=object(), _cross_profile_borrow_pending=None)
    group = Mock()
    panel = NS(window=window, _rendered_search_detail=(item, object()),
               _group=group, get_mapped=lambda: True)
    return panel, item, service, session, calls, group


@pytest.mark.parametrize("moment", ["before_delivery", "during_group"])
def test_stale_session_never_publishes_normal_lender_action(harness, monkeypatch, moment):
    from cifereca.gui import cross_profile_normal_lender_dialog as dialog
    panel, item, _service, session, calls, group = panel_for(harness)
    attach = Mock()
    monkeypatch.setattr(dialog, "append_normal_lender_action", attach)
    assert append_possible_lender_summary(panel, item)
    work, delivered, _failed = calls.pop()
    rows = work()
    assert len(rows) == 1 and rows[0]._session is session
    if moment == "before_delivery":
        session.connected = False
    else:
        group.side_effect = lambda *_: setattr(session, "connected", False)
    delivered(rows)
    if moment == "before_delivery":
        group.assert_not_called()
    else:
        group.assert_called_once()
    attach.assert_not_called()
    assert session.calls == 0


def test_explicit_chooser_denies_disconnect_without_dialog_or_worker(harness):
    panel, item, service, session, calls, group = panel_for(harness)
    rows = service.possible_borrow_lenders_for_current_identity(exact_notice=item)
    assert len(rows) == 1
    session.connected = False
    opened = present_normal_lender_choice(
        panel, item=item, rows=rows, current=lambda: True, service=service,
        identity=service.current_identity,
        identity_id=service.current_identity.identity_id,
        account_epoch=service._account_context_epoch,
        source_epoch=service._source_context_epoch,
        Adw=NS(MessageDialog=NS(new=Mock(side_effect=AssertionError("no dialog")))),
        TaskPriority=NS(USER_ACTION="user"),
    )
    assert opened is False
    assert panel.window._cross_profile_borrow_pending is None
    assert not calls and session.calls == 0
