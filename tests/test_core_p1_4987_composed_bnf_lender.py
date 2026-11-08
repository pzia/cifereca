"""#4987: integration of opaque lender sessions and Core's durable mutation.

Synthetic BnF session, real Core/SQLite; no network or external credentials.
The tests intentionally combine the candidate branches #5140/#5134/#5141.
"""
from __future__ import annotations

from dataclasses import replace

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p0_2055_manual_dispatch import setup as bnf_setup
from tests.test_core_p1_4662_live_lender_preview import _candidate


def setup(tmp_path):
    service, session, account, item = bnf_setup(tmp_path)
    account.identity_id = "alex"
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": "bnf-pret",
        "credential_id": account.credential_id, "label": "BnF synthétique",
        "enabled": True, "metadata": {},
    })
    service._plugin_manager.enabled_for_identity = lambda *_args: True
    initial = _candidate()
    row = replace(
        initial, notice=item,
        evidence=replace(
            initial.evidence, identity_id="alex", provider_id="bnf-pret",
            credential_id=account.credential_id, account_id=account.unique_id,
            branch="BNF_PRET",
        ),
        edition_evidence=replace(
            initial.edition_evidence, identity_id="alex",
            provider_id="bnf-pret", credential_id=account.credential_id,
            borrower_id=account.unique_id, docbase="BNF_PRET",
            record_id=item.record_id, source_collection=item.source_collection,
        ),
    )
    assert type(session._epoch) is str
    preview = service.preview_borrow_for_current_identity(
        expected_edition_id="edition-1", observations=[row], now=1100,
    )
    assert preview.status == "external", preview
    assert len(preview.options) == 1
    assert preview.options[0].borrowing_identity_id == "alex"
    return service, session, account, item, row, preview


def execute(service, row, preview):
    return service.execute_cross_profile_borrow(
        captured_preview=preview,
        selected_option_id=preview.options[0].option_id,
        expected_edition_id="edition-1",
        observations=[row], confirmed=True, now=1100,
    )


def test_opaque_epoch_bnf_lender_confirms_once_without_profile_switch(tmp_path):
    service, session, account, item, row, preview = setup(tmp_path)
    try:
        requester = service.current_identity
        assert execute(service, row, preview) == {
            "status": "confirmed",
            "reason_code": "lender_loan_confirmed_transfer_unverified",
            "provider_verified": True,
            "delivery": "unavailable", "transfer": "blocked",
            "retry_borrow": False,
        }
        assert session.dispatches == [(item.record_id, account.unique_id)]
        assert service.current_identity is requester
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item),
        )["status"] == "confirmed"
        assert execute(service, row, preview)["status"] == "blocked"
        assert len(session.dispatches) == 1
        progress = service.cross_profile_borrow_progress(
            requesting_identity_id="default",
            borrowing_account=account, exact_notice=item,
        )
        assert (progress.loan, progress.delivery, progress.transfer) == (
            "confirmed", "unavailable", "blocked",
        )
    finally:
        service.close()


def test_opaque_epoch_bnf_session_rebind_revokes_held_click_before_claim(tmp_path):
    service, session, account, item, row, preview = setup(tmp_path)
    try:
        initial = session._epoch
        session._epoch = "new-session-uuid-on-reconnect"
        assert session._epoch != initial
        assert execute(service, row, preview)["status"] == "blocked"
        assert session.dispatches == []
        assert service.storage.metadata_get(
            borrow_claim_key(account, item),
        ) is None
    finally:
        service.close()


def test_bnf_lender_does_not_post_when_menu_has_changed(tmp_path):
    service, session, account, item, row, preview = setup(tmp_path)
    try:
        # A rejected sibling changes the held consent context but does not
        # remove the clicked exact lender's eligible row.
        new_row = replace(
            row, edition_evidence=replace(
                row.edition_evidence, item_offer="unavailable",
            ),
        )
        selected = preview.options[0].option_id
        result = service.execute_cross_profile_borrow(
            captured_preview=preview, selected_option_id=selected,
            expected_edition_id="edition-1",
            observations=[row, new_row], confirmed=True, now=1100,
        )
        assert result["status"] == "blocked"
        assert session.dispatches == []
        assert service.storage.metadata_get(borrow_claim_key(account, item)) is None
    finally:
        service.close()
