"""#4988: an in-place rewritten lender name is not the original consent receipt."""
from cifereca.core.borrow_for_identity_discovery import possible_lender_matches
from tests.test_core_p1_4662_live_lender_preview import _candidate, harness


def test_mutated_frozen_lender_label_revokes_exact_receipt(harness):
    service, _account, session = harness
    notice = _candidate().notice
    rows = service.possible_borrow_lenders_for_current_identity(exact_notice=notice)
    assert len(rows) == 1
    candidate = rows[0]
    requester = service.current_identity.identity_id
    assert possible_lender_matches(candidate, notice, requesting_identity_id=requester)
    object.__setattr__(candidate, "label", "Autre bibliothèque prêteuse")
    assert not possible_lender_matches(candidate, notice, requesting_identity_id=requester)
    assert session.calls == 0
