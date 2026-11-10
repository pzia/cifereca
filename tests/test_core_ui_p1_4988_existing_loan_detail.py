"""#4988: read-only already-confirmed lender summary in common detail panel."""
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.core.borrow_for_identity_existing_loan import (
    ExistingLenderLoanCandidate,
)
from cifereca.gui.cross_profile_existing_loan_detail import (
    append_existing_lender_loan_summary as append,
)
from tests.test_core_p1_4662_cross_profile_borrow_preview import witness


def view(*, statuses=("present", "awaiting_delivery")):
    w = witness()
    offer = Mock(return_value=tuple(
        ExistingLenderLoanCandidate(w.account, status)
        for status in statuses
    ))
    service = NS(
        current_identity=NS(identity_id="bob"),
        _account_context_epoch=3, _source_context_epoch=5,
        existing_lender_loan_candidates=offer,
        execute_cross_profile_borrow=Mock(side_effect=AssertionError("no POST")),
        materialize_to_identity=Mock(side_effect=AssertionError("no copy")),
    )
    group = Mock()
    panel = NS(
        window=NS(service=service),
        _rendered_search_detail=(w.notice, object()),
        _group=group,
    )
    return w, service, panel, group, offer


def test_shared_detail_shows_confirmed_loan_and_local_delivery_not_permission():
    w, service, panel, group, offer = view()
    assert append(panel, w.notice) is True
    offer.assert_called_once_with(exact_notice=w.notice)
    assert group.call_count == 1
    name, rows = group.call_args.args
    assert name == "Prêts sur d’autres profils"
    assert [label for label, _text in rows] == [
        "Profil prêteur 1", "Profil prêteur 2",
    ]
    assert "copie locale présente" in rows[0][1]
    assert "envoi distinct requis" in rows[0][1]
    assert "livraison locale en attente" in rows[1][1]
    assert w.account.credential_id not in repr(rows)
    service.execute_cross_profile_borrow.assert_not_called()
    service.materialize_to_identity.assert_not_called()


def test_empty_read_only_projection_adds_no_section():
    w, service, panel, group, offer = view(statuses=())
    assert append(panel, w.notice) is False
    offer.assert_called_once()
    group.assert_not_called()


def test_old_detail_cannot_receive_confirmed_lender_state():
    w, service, panel, group, offer = view()
    assert append(panel, replace(w.notice)) is False
    group.assert_not_called()
    offer.assert_not_called()


def test_profile_rebind_during_core_read_suppresses_old_panel():
    w, service, panel, group, offer = view()
    rows = offer.return_value

    def rebind(**kw):
        service.current_identity = NS(identity_id="bob")
        return rows

    offer.side_effect = rebind
    assert append(panel, w.notice) is False
    group.assert_not_called()


def test_source_epoch_change_during_core_read_suppresses_stale_section():
    w, service, panel, group, offer = view()
    rows = offer.return_value

    def rebind(**kw):
        service._source_context_epoch += 1
        return rows

    offer.side_effect = rebind
    assert append(panel, w.notice) is False
    group.assert_not_called()


def test_replaced_panel_detail_suppresses_foreign_loan_projection():
    w, service, panel, group, offer = view()
    rows = offer.return_value

    def replaced(**kw):
        panel._rendered_search_detail = (replace(w.notice), object())
        return rows

    offer.side_effect = replaced
    assert append(panel, w.notice) is False
    group.assert_not_called()


def test_unverified_or_forged_candidate_disables_entire_section():
    w, service, panel, group, offer = view()
    offer.return_value = (
        ExistingLenderLoanCandidate(w.account, "present"),
        ExistingLenderLoanCandidate(w.account, "present", executable=True),
    )
    assert append(panel, w.notice) is False
    group.assert_not_called()


def test_wrong_shape_from_core_never_renders_private_data():
    w, service, panel, group, offer = view()
    offer.return_value = [{"status": "present", "barcode": "PRIVATE"}]
    assert append(panel, w.notice) is False
    group.assert_not_called()
