"""Owner option A on the actual BnF selected-offer ABI and isolated SQLite.

Only synthetic activity HTML and a stubbed local ADEPT capability are used.
No token GET, new borrow, fulfillment or filesystem publication is invoked.
"""
from dataclasses import replace
import json
from types import SimpleNamespace as NS

import pytest

from cifereca.core import adept_historical_maintenance as review
from cifereca.core import adept_historical_rearm as rearm
from cifereca.core import canonical_adept_attempt as marker
from cifereca.core import canonical_recovery as recovery
from cifereca.core.canonical_sync_stage import persist_attested_source_references
from cifereca.core.source_acquisition_consent import set_source_acquisition_consent
from cifereca.locking import InterProcessLock
from cifereca.storage import Storage
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core
from tests.test_core_p0_3171_atomic_publication_v14 import new_source, rows


@pytest.mark.parametrize("scenario", [
    "accepted", "companion_witness", "unconfirmed", "bad_digest",
    "wrong_profile", "no_consent", "stale_marker", "busy",
    "inactive_offer", "profile_changed", "audit_failure",
])
def test_owner_attested_exact_rearm(connected_core, tmp_path, monkeypatch, scenario):
    service, session, account, loan, state, downloaded = connected_core
    storage = service.storage
    source, snapshot = new_source(
        account.identity_id, provider=account.provider_id,
        credential=account.credential_id, account=account.unique_id,
        edition=loan.record_id, right=loan.id, branch="bnf_html", kind="loan",
    )
    persist_attested_source_references(storage, snapshot)
    if scenario != "no_consent":
        set_source_acquisition_consent(service, account, source, allowed=True)
    lock_path = tmp_path / "acquisition.lock"
    monkeypatch.setattr(recovery, "acquisition_lock_file", lambda **_kw: lock_path)
    with InterProcessLock(lock_path):
        pass
    original_lock_bytes = lock_path.read_bytes()
    raw = marker._value("historical-offer", "a" * 32)
    sibling = replace(source, right_id="other-loan")
    storage.metadata_set(marker._key(source), raw)
    storage.metadata_set(marker._key(sibling), raw)
    if scenario == "companion_witness":
        storage.metadata_set(marker._unsubmitted_key(source),
                             marker._unsubmitted_value(source, raw, "core_pre_dispatch_refusal"))

    old_manager = service._plugin_manager
    old_required = service._required_plugin_service
    calls = []
    profile = ["stable-adobe"]
    manager = NS(registry_generation=2)

    def preflight(name):
        assert name == "reader-adobe"
        calls.append(name)
        return True

    runtime = NS(preflight_acsm_profile=preflight)
    service._plugin_manager = manager
    service._required_plugin_service = lambda _key: runtime
    monkeypatch.setattr(service, "resolve_adept_profile_for_identity",
                        lambda _identity: ("reader-adobe", "configured"))
    monkeypatch.setattr(service, "adept_profiles_for_identity",
                        lambda *_args, **_kwargs: [{
                            "name": "reader-adobe", "profile_id": profile[0],
                            "user_uuid": "synthetic-user", "fetch_capable": True,
                        }])
    monkeypatch.setattr(service, "_originating_adept_profile_id",
                        lambda *_args: profile[0])
    monkeypatch.setattr(service, "execute_acquisition_request",
                        lambda *_args: pytest.fail("no fulfillment in Maintenance"))
    before = rows(storage), storage.acquisitions(), storage.entitlements()
    try:
        request = review.admit_historical_review(service)
        assert request is not None
        page = review.run_historical_hold_review(service, request, verify_local_bytes=True)
        assert page is not None
        ordinal = next(i for i, row in enumerate(page["rows"])
                       if row["provider_loan_id"] == source.right_id)
        selected = review.select_historical_hold(service, request, page,
                                                 ordinal=ordinal, confirmed=True)
        assert selected is not None and selected.final_receipt_evidence == "not_observed"
        assert selected.copy_evidence == "not_proven" and selected.current()
        audit_key = rearm._audit_key(selected)
        if scenario == "stale_marker":
            storage.metadata_set(marker._key(source), marker._value("changed", "b" * 32))
        if scenario == "inactive_offer":
            state["activity"] = '<section class="active-loans"></section>'
        if scenario == "profile_changed":
            original = type(session).rights_selected_delivery_offer_snapshot
            def changed(self, *args, **kwargs):
                result = original(self, *args, **kwargs)
                profile[0] = "other-adobe"
                return result
            monkeypatch.setattr(type(session), "rights_selected_delivery_offer_snapshot", changed)
        if scenario == "audit_failure":
            # A trigger aborts AFTER the audit insertion; transaction rollback
            # must preserve the original marker and leave no audit receipt.
            with storage._lock, storage._connect() as db:
                db.execute("CREATE TEMP TRIGGER fail_rearm BEFORE DELETE ON metadata "
                           "WHEN old.key = '" + marker._key(source) + "' "
                           "BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
            # TEMP schema changes do not modify the persisted review image.
        def execute():
            return rearm.rearm_reviewed_historical_hold(
                service, selected,
                journal_sha256="c" * 64 if scenario != "bad_digest" else "bad",
                historical_profile_id="stable-adobe" if scenario != "wrong_profile" else "other",
                confirmed=scenario != "unconfirmed")
        accepted = scenario in ("accepted", "companion_witness")
        if accepted:
            assert execute() == "exact_marker_released"
            assert not marker.pending_loan_adept_attempt(storage, source)
            assert storage.metadata_get(marker._unsubmitted_key(source)) is None
            reopened = Storage(storage.path)
            try:
                audit = json.loads(reopened.metadata_get(audit_key))
                assert audit["source"] == recovery._scope(source)
                assert audit["marker_fingerprint"] == selected.marker.fingerprint
                assert audit["adept_profile_id"] == "stable-adobe"
                assert audit["journal_sha256"] == "c" * 64
                assert audit["proof"] == "owner_attested_same_activation_and_pre_dispatch_refusal"
                assert audit["result"] == "exact_marker_released"
                assert audit["fresh_offer_digest"] != marker._decode_marker(raw)["offer_digest"]
                assert reopened.metadata_get(marker._key(source)) is None
            finally:
                reopened.close()
            with pytest.raises(ValueError):
                execute()
        else:
            if scenario == "busy":
                with InterProcessLock(lock_path, existing_nofollow=True):
                    with pytest.raises((ValueError, RuntimeError, OSError)):
                        execute()
            else:
                with pytest.raises(Exception):
                    execute()
            assert marker.pending_loan_adept_attempt(storage, source)
            assert storage.metadata_get(audit_key) is None
        assert storage.metadata_get(marker._key(sibling)) == raw
        assert (rows(storage), storage.acquisitions(), storage.entitlements()) == before
        assert lock_path.read_bytes() == original_lock_bytes
        assert not downloaded
    finally:
        service._plugin_manager = old_manager
        service._required_plugin_service = old_required
