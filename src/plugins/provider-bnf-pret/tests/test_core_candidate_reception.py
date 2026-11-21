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
    preflight_calls = []
    fresh_runtime = SimpleNamespace(
        preflight_acsm_profile=lambda name: (
            preflight_calls.append(name) or name == "borrower-adobe"
        ),
    )
    service = SimpleNamespace(
        _plugin_manager=SimpleNamespace(registry_generation=1),
        _required_plugin_service=lambda pid: (
            fresh_runtime if pid == "runtime.kobo-adept" else None
        ),
        storage=db, accounts=[account], _account_context_epoch=1,
        _sessions={(provider, credential): session},
        _session_key=lambda p, c: (p, c),
        sync_source_enabled=lambda a: a is account,
        resolve_adept_borrower_for_loan=lambda loan, *, connection_identity_id: (
            (identity, None) if (
                connection_identity_id == identity
                and (loan.source_provider, loan.source_credential, loan.source_account_id)
                    == (provider, credential, borrower)
            ) else (None, "adept_borrower_ambiguous")
        ),
        adept_profiles_for_identity=lambda i, *, profile_name: (
            [{"name": "borrower-adobe", "profile_id": "synthetic-adobe-profile-id",
              "fetch_capable": True, "provisioning": {"acsm_fetch_ready": True}}]
            if i == identity and profile_name == "borrower-adobe" else []
        ),
        _adept_profile_row=lambda _reference, rows: rows[0] if rows else None,
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
    monkeypatch.setattr(
        "cifereca.core.canonical_recovery.acquisition_lock_file",
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
            runtime_calls=runtime_calls, fresh_runtime=fresh_runtime,
            preflight_calls=preflight_calls,
        )
    finally:
        session.close()
        db.close()


def test_real_bnf_offer_and_token_are_accepted_by_core_consumer(adapter):
    a = adapter
    completed = a.complete()
    assert len(a.token_gets) == len(a.runtime_calls) == 1
    assert a.preflight_calls == ['borrower-adobe']
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


def test_bnf_fresh_capability_refusal_leaves_attempt_unmarked(adapter):
    """Reject locally before any durable one-use marker or DRM mutator."""
    from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt

    a = adapter
    a.fresh_runtime.preflight_acsm_profile = lambda _name: False
    with pytest.raises(ValueError, match="^canonical_adept_preflight_unavailable$"):
        a.complete()
    assert len(a.token_gets) == 1  # ACSM type only known after candidate GET.
    assert a.runtime_calls == []
    assert not pending_loan_adept_attempt(a.db, a.source)
    assert a.db.placements() == []


def test_bnf_profile_changed_during_preflight_can_resume_after_correction(adapter):
    from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
    a = adapter
    original = a.service.resolve_adept_profile_for_identity
    def rebind(_name):
        a.service.resolve_adept_profile_for_identity = lambda _i: ("other-profile", "configured")
        return True
    a.fresh_runtime.preflight_acsm_profile = rebind
    with pytest.raises(ValueError, match="adept_borrower_profile_changed"):
        a.complete()
    assert not pending_loan_adept_attempt(a.db, a.source)
    assert a.runtime_calls == []
    a.service.resolve_adept_profile_for_identity = original
    a.fresh_runtime.preflight_acsm_profile = lambda name: name == "borrower-adobe"
    completed = a.complete()
    assert completed.path.read_bytes() == PDF
    assert len(a.token_gets) == 2 and len(a.runtime_calls) == 1


def test_bnf_local_refusal_after_claim_releases_only_unsubmitted_claim(adapter, monkeypatch):
    from cifereca.core import canonical_selected_delivery as delivery
    from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
    a = adapter
    original = delivery.begin_loan_adept_attempt
    original_binding = a.service._originating_adept_profile_id
    def claim(*args, **kwargs):
        operation = original(*args, **kwargs)
        a.service._originating_adept_profile_id = lambda *_: "changed-id"
        return operation
    monkeypatch.setattr(delivery, "begin_loan_adept_attempt", claim)
    with pytest.raises(ValueError, match="adept_borrower_profile_changed"):
        a.complete()
    assert a.runtime_calls == []
    assert not pending_loan_adept_attempt(a.db, a.source)
    a.service._originating_adept_profile_id = original_binding
    monkeypatch.setattr(delivery, "begin_loan_adept_attempt", original)
    assert a.complete().path.read_bytes() == PDF


def test_bnf_uncertain_runtime_result_keeps_claim(adapter):
    from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
    a = adapter
    def fail(request):
        a.runtime_calls.append(request)
        raise OSError("synthetic ambiguous completion")
    a.service.execute_acquisition_request = fail
    with pytest.raises(OSError):
        a.complete()
    assert pending_loan_adept_attempt(a.db, a.source)
    with pytest.raises(ValueError, match="canonical_adept_attempt_pending"):
        a.complete()
    assert len(a.token_gets) == len(a.runtime_calls) == 1


@pytest.mark.parametrize("proven", [False, True])
def test_only_proven_runtime_refusal_releases_live_adept_claim(adapter, proven):
    from types import MethodType

    from cifereca.core.canonical_adept_attempt import pending_loan_adept_attempt
    from cifereca.plugin_api import AdeptFulfillmentNotSubmitted, AttentionRequired
    from cifereca.services import LibraryService

    a = adapter
    succeeding = a.service.execute_acquisition_request
    lookup = a.service._required_plugin_service
    refusal_type = AdeptFulfillmentNotSubmitted if proven else AttentionRequired

    def refuse(request):
        a.runtime_calls.append(request)
        raise refusal_type("synthetic ADEPT profile refusal", code="adept_profile_required")

    drm = SimpleNamespace(execute=refuse)
    a.service._required_plugin_service = lambda pid: drm if pid == "drm.ade" else lookup(pid)
    a.service.execute_acquisition_request = MethodType(
        LibraryService.execute_acquisition_request, a.service,
    )
    with pytest.raises(refusal_type):
        a.complete()
    assert pending_loan_adept_attempt(a.db, a.source) == (not proven)
    assert len(a.token_gets) == len(a.runtime_calls) == 1

    drm.execute = succeeding
    if proven:
        assert a.complete().path.read_bytes() == PDF
        assert len(a.token_gets) == len(a.runtime_calls) == 2
    else:
        with pytest.raises(ValueError, match="canonical_adept_attempt_pending"):
            a.complete()
        assert len(a.token_gets) == len(a.runtime_calls) == 1
