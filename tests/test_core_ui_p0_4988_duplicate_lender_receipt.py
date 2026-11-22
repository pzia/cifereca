"""#4988: one exact lender receipt must not appear as multiple lender choices."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.gui.cross_profile_possible_lender_detail import append_possible_lender_summary
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def test_duplicate_exact_lender_receipt_never_creates_fake_multi_choice(harness):
    service, _account, session = harness
    notice = _candidate().notice
    original = service.possible_borrow_lenders_for_current_identity(exact_notice=notice)
    assert len(original) == 1
    service.possible_borrow_lenders_for_current_identity = Mock(return_value=(original[0], original[0]))
    tasks = []
    group = Mock()
    panel = NS(
        window=NS(service=service, runner=NS(submit=lambda _name, work, done, fail, **kwargs:
                                                 tasks.append((work, done))),
                  _route_display_token=object()),
        _rendered_search_detail=(notice, object()), _group=group,
        get_mapped=lambda: True,
    )
    assert append_possible_lender_summary(panel, notice) is True
    work, done = tasks.pop()
    done(work())
    group.assert_not_called()
    assert session.calls == 0
