"""Real BnF adapter -> Core candidate consumer; synthetic HTTP/ADEPT only.

SQLite, locks, ACSM detection and final-byte inspection are real. The runtime
callback writes a synthetic PDF: this is not Adobe fulfillment or a live loan.
"""
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest

from cifereca.core.canonical_selected_delivery import complete_selected_delivery
from cifereca.core.source_convergence import SourceKey
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from test_p0_3633_selected_acsm_staging import ready


PDF = b"%PDF-1.7\nSynthetic BnF adapter reception\n"
ACSM = (
    b'<fulfillmentToken xmlns="http://ns.adobe.com/adept" '
    b'fulfillmentType="loan" auth="user"><resourceItemInfo>'
    b'<format>application/pdf</format></resourceItemInfo></fulfillmentToken>'
)


@pytest.fixture
def adapter(tmp_path, monkeypatch):
    session, account, state, calls, token_gets, offer = ready(payload=ACSM)
    db = Storage(tmp_path / "state.sqlite")
    identity, provider, credential, borrower = (
        account.identity_id, account.provider_id,
        account.credential_id, account.unique_id,
    )
    db.identity_put(dict(identity_id=identity, directory_name="borrower", label="Synthetic"))
    connection = db.provider_connection_put(dict(
        identity_id=identity, provider_id=provider, credential_id=credential,
        label="Synthetic BnF",
    ))
    db.provider_account_put(dict(
        identity_id=identity, provider_id=provider, credential_id=credential,
        connection_id=connection, account_id=borrower, label="Synthetic borrower",
    ))
    source = SourceKey(
        session.rights_observation_snapshot(account).scope,
        offer.edition_id, offer.right_id, "loan", "bnf_html",
    )
    service = SimpleNamespace(
        storage=db, accounts=[account], _account_context_epoch=1,
        _sessions={(provider, credential): session},
        _session_key=lambda p, c: (p, c),
        sync_source_enabled=lambda a: a is account,
        resolve_adept_profile_for_identity=lambda i: ("borrower-adobe", "configured"),
        _originating_adept_profile_id=lambda i, p: "synthetic-adobe-profile-id",
    )
    runtime_calls = []

    def runtime(request):
        runtime_calls.append(request)
        Path(request["destination"]).write_bytes(PDF)
        return dict(ok=True, profile="borrower-adobe",
                    artifact=dict(path=request["destination"]))

    service.execute_acquisition_request = runtime
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(
        "cifereca.core.canonical_selected_delivery.acquisition_lock_file",
        lambda: lock_path,
    )
    root = tmp_path / "EBooks"

    def complete():
        with InterProcessLock(lock_path) as lock:
            return complete_selected_delivery(
                service, account, session, source, offer,
                library_root=root, shared_lock=lock,
            )

    try:
        yield SimpleNamespace(
            complete=complete, service=service, source=source, account=account,
            root=root, db=db, calls=calls, token_gets=token_gets,
            runtime_calls=runtime_calls,
        )
    finally:
        session.close()
        db.close()


def test_real_bnf_offer_and_token_are_accepted_by_core_consumer(adapter):
    a = adapter
    completed = a.complete()
    assert len(a.token_gets) == len(a.runtime_calls) == 1
    request, = a.runtime_calls
    assert request["source"]["type"] == "acsm"
    assert request["source"]["provider"] == "bnf-pret"
    assert request["profile"] == "borrower-adobe"
    assert request["metadata"]["identity_id"] == a.account.identity_id
    assert Path(request["source"]["path"]).read_bytes() == ACSM
    assert completed.source == a.source
    assert completed.path.read_bytes() == PDF
    assert completed.final_sha256 == sha256(PDF).hexdigest()
    assert completed.path.is_relative_to(a.root / "borrower" / ".canonical-delivery")
    assert completed.receipt_ref.startswith("core-delivery:")
    assert completed.adept_receipt_ref.startswith("core-adept:")
    # The consumer returns a witness; only the separate executor can publish.
    assert a.db.placements() == []


def test_bnf_candidate_cannot_finish_under_a_different_adobe_profile(adapter):
    a = adapter
    original = a.service.execute_acquisition_request

    def wrong_profile(request):
        result = original(request)
        result["profile"] = "another-borrower"
        return result

    a.service.execute_acquisition_request = wrong_profile
    with pytest.raises(ValueError, match="Profil ADEPT du résultat incohérent"):
        a.complete()
    assert len(a.token_gets) == len(a.runtime_calls) == 1
    assert a.db.placements() == []


def test_bnf_source_rebound_before_core_admission_makes_no_token_get(adapter):
    a = adapter
    a.account.identity_id = "another-identity"
    before = list(a.calls)
    with pytest.raises(ValueError, match="scope_mismatch"):
        a.complete()
    assert a.calls == before
    assert a.token_gets == a.runtime_calls == []
    assert a.db.placements() == []
