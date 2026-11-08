"""P1 #4987: real BnF provider under a synthetic HTTP transport.

Uses the provider's existing MockTransport capture fixture: never contacts
pret.bnf.fr and never moves a lender's loan to the requesting identity.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace as NS

from cifereca.core.borrow_action_scope import borrow_claim_key
from tests.test_core_p1_4662_live_lender_preview import _candidate
from tests.test_sync_state_contract import _service

ROOT = Path(__file__).resolve().parents[1]
BNF_TESTS = ROOT / "src/plugins/provider-bnf-pret/tests"
if str(BNF_TESTS) not in sys.path:
    sys.path.insert(0, str(BNF_TESTS))
from test_p0_2055_borrow_explicit_execution import BnfExplicitExecutionTests


def setup(tmp_path):
    producer = BnfExplicitExecutionTests()
    session, account, item, transport = producer.make()
    service = _service(tmp_path)
    account.identity_id = "alex"
    service.accounts = [account]
    service._sessions[service._session_key(
        account.provider_id, account.credential_id,
    )] = session
    service.storage.provider_connection_put({
        "identity_id": "alex", "provider_id": account.provider_id,
        "credential_id": account.credential_id, "label": "BnF fake transport",
        "enabled": True, "metadata": {},
    })
    service._provider_plugin_ids[account.provider_id] = "provider.bnf-pret"
    service._plugin_manager = NS(
        status=lambda _id: NS(
            enabled=True, state="ready",
            descriptor=NS(
                id="provider.bnf-pret", type="provider.library",
                capabilities=("sync.loans", "acquisition.borrow"),
            ),
        ),
        enabled_for_identity=lambda *_args: True,
    )
    initial = _candidate()
    row = replace(
        initial, notice=item,
        evidence=replace(
            initial.evidence, identity_id="alex", provider_id="bnf-pret",
            credential_id=account.credential_id, account_id=account.unique_id,
            branch=item.docbase,
        ),
        edition_evidence=replace(
            initial.edition_evidence, identity_id="alex",
            provider_id="bnf-pret", credential_id=account.credential_id,
            borrower_id=account.unique_id, docbase=item.docbase,
            record_id=item.record_id, source_collection=item.source_collection,
        ),
    )
    assert type(session._epoch) is str
    preview = service.preview_borrow_for_current_identity(
        expected_edition_id="edition-1", observations=(row,), now=1100,
    )
    assert preview.status == "external"
    return service, session, account, item, transport, row, preview


def test_actual_bnf_adapter_claimed_post_is_one_and_remains_on_lender(tmp_path):
    service, session, account, item, transport, row, preview = setup(tmp_path)
    requester = service.current_identity
    try:
        def execute():
            return service.execute_cross_profile_borrow(
                captured_preview=preview,
                selected_option_id=preview.options[0].option_id,
                expected_edition_id="edition-1", observations=(row,),
                confirmed=True, now=1100,
            )
        result = execute()
        assert result["status"] == "confirmed", result
        assert result["provider_verified"] is True
        assert result["transfer"] == "blocked"
        assert result["delivery"] == "unavailable"
        assert transport["post_count"] == 1
        assert transport["activity_reads"] == 2
        assert service.current_identity is requester
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item),
        )["status"] == "confirmed"
        assert execute()["status"] == "blocked"
        assert transport["post_count"] == 1
        assert service.storage.entitlements(provider_id="bnf-pret") == []
        assert not service.storage.acquisitions()
    finally:
        session.close()
        service.close()


def test_actual_bnf_adapter_rebind_mid_post_preserves_uncertain_claim(tmp_path):
    service, session, account, item, transport, row, preview = setup(tmp_path)
    try:
        session.client._http  # keep real provider session alive
        original = session.client
        # BnF's MockTransport callback is closed over its test fixture;
        # a Core source generation can also be revoked during the HTTP
        # transport via its preflight callback, simulated by wrapping the
        # provider's verified method after the one POST.
        method = session.borrow_explicit_with_claim

        def rebind_after_post(*args, **kwargs):
            loan = method(*args, **kwargs)
            service._source_context_epoch += 1
            return loan

        session.borrow_explicit_with_claim = rebind_after_post
        result = service.execute_cross_profile_borrow(
            captured_preview=preview,
            selected_option_id=preview.options[0].option_id,
            expected_edition_id="edition-1", observations=(row,),
            confirmed=True, now=1100,
        )
        assert result["status"] == "verification_unavailable"
        assert result["provider_verified"] is False
        assert result["retry_borrow"] is False
        assert transport["post_count"] == 1
        assert service.storage.loan_borrow_state(
            borrow_claim_key(account, item),
        )["status"] == "verification_unavailable"
    finally:
        session.close()
        service.close()
