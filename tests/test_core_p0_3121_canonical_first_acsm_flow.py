"""#3121: canonical Sync must fulfill the FIRST claimed Paris ACSM, not GET again.

Synthetic current-inventory borrower, ACSM, Adobe runtime and SQLite only.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca import acsm_cache
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.models import Account, Loan
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_3171_atomic_publication_v14 import (
    BYTES, SHA, new_source, register,
)


@pytest.mark.parametrize("provider_offer_available", [False, True])
@pytest.mark.parametrize("invalid_receipt", [False, True])
def test_canonical_paris_consumes_first_confirmed_acsm_without_second_get(
    rig, tmp_path, invalid_receipt, provider_offer_available, monkeypatch,
):
    # The selected executor verifies the SAME existing acquisition lock as
    # sync_coordination; the shared rig patches its own module only.
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
        lambda: tmp_path / "state" / "acquisition.lock",
    )
    service, db, root = rig
    account = Account(
        identity_id="A", provider_id="paris",
        credential_id="paris-card", unique_id="",
    )
    register(db, "A", "paris", "paris-card", account="")
    service.account = account
    service.accounts = [account]
    source, snapshot = new_source(
        "A", provider="paris", credential="paris-card",
        account="", branch="dilicom", edition="pnb-edition",
        right="pnb-loan", kind="loan",
    )
    service.source = source
    service.snapshot = replace(
        snapshot, rights=(replace(
            snapshot.rights[0],
            delivery="available" if provider_offer_available else "unknown",
            delivery_offer_id=(
                "signed-existing-loan-offer" if provider_offer_available else ""
            ),
        ),),
    )
    session = service.session
    session.provider_id = "paris"
    session.credential_id = "paris-card"
    session.accounts = [account]
    service._sessions = {"paris:paris-card": session}
    service._canonical_persisted_consent = (
        lambda _account: frozenset({source})
    )

    loan = Loan(
        id="pnb-loan", record_id="pnb-edition", title="Synthetic",
        provider="DILICOM", medium="digital",
        source_provider="paris", source_credential="paris-card",
        source_account_id="primary", identity_id="A",
        due_at="2099-01-01T00:00:00Z", ebook_url=None,
    )
    session.loans = lambda _: [loan]
    # When the later signed EbookUrl has disappeared, the selected provider
    # offers only an observed right, not a new content URL. The Core claim
    # receipt is the ONLY admissible initial fulfillment token.
    session.rights_selected_delivery_offer_snapshot = (
        lambda _account, **_kw: service.snapshot
    )
    private = tmp_path / "staging" / "first-offer"
    private.mkdir(parents=True)
    initial = private / "original.acsm"
    initial.write_bytes(
        b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
        b'<format>application/pdf</format></fulfillmentToken>'
    )
    key, operation = "loan.borrow.v1:" + "d" * 64, "synthetic-confirmed"
    assert db.loan_borrow_claim_once(key, operation)[0]
    assert db.loan_borrow_finish(
        key, operation, status="confirmed",
        accepted=True, verified_loan_id=loan.id,
    )
    assert acsm_cache.remember_initial(
        initial, private, loan, "A", "primary",
        borrow_claim=(key, operation),
    )
    if invalid_receipt:
        initial.write_bytes(b"tampered-invalid-first-token")
    service._acquisition_work_dir = lambda *_: private
    service.resolve_adept_profile_for_identity = (
        lambda _identity: ("borrower-profile", "configured")
    )
    service._originating_adept_profile_id = lambda *_: "profile-id"

    # A second content request, including an old mutating ASSARedirect, would
    # fail immediately. The read-only selected offer is already attested.
    session.rights_selected_delivery_candidate = lambda *_a, **_kw: pytest.fail(
        "second provider content GET after first signed Paris ACSM"
    )
    executed = []
    def fulfill(request):
        executed.append(request)
        assert Path(request["source"]["path"]).name == "first-offer.acsm"
        assert Path(request["source"]["path"]).read_bytes() == (
            b'<fulfillmentToken xmlns="http://ns.adobe.com/adept">'
            b'<format>application/pdf</format></fulfillmentToken>'
        )
        target = Path(request["destination"])
        target.write_bytes(BYTES)
        return {
            "ok": True, "profile": "borrower-profile",
            "artifact": {"path": str(target)},
        }
    service.execute_acquisition_request = fulfill

    outcome = synchronize_account_source(service, account)
    if invalid_receipt:
        assert outcome["canonical_execution"]["failed" if provider_offer_available
                                              else "pending"] == 1
        assert executed == []
        assert db.placements() == []
        assert refs(db)[1] == 0
        return
    assert outcome["canonical_execution"]["downloaded"] == 1
    assert len(executed) == 1
    placement = db.placements()[0]
    assert placement["identity_id"] == "A"
    assert placement["artifact_id"] == "sha256:" + SHA
    assert Path(placement["path"]).read_bytes() == BYTES
    assert initial.is_file()  # expiry/recovery cleanup is a separate contract
    later = synchronize_account_source(service, account)
    assert later["canonical_execution"]["present_attested"] == 1
    assert len(executed) == 1  # no repeat ADEPT, no second provider GET
