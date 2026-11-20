"""Production runtime composition through the actual source Sync entrypoint.

Real journal, SQLite and shared broker. Provider responses are synthetic;
no acquisition or remote HTTP. A manual tick stands in for desktop wakeup.
"""
from contextlib import contextmanager, nullcontext
from threading import RLock
from types import SimpleNamespace
import time

import pytest

from cifereca.core.notice_c_purchase_runtime import install_purchase_runtime
from cifereca.core.notice_c_work_runtime import NoticeWorkRuntime
from cifereca.core.notice_c_purchase_intent import _POLICY
from cifereca.core.source_sync_consent import inventory_epoch_key, source_enablement_key, scope_for_account
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.source_context import revoke_source_context
from cifereca.core.sync_coordination import synchronize_account_source
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.task_admission import SharedTaskAdmission
from cifereca.task_runtime import TaskBrokerImpl
from tests.test_core_p0_3171_canonical_sync_stage import rig, refs
from tests.test_core_p0_1072_purchase_enrichment_same_read_admission import install
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


@pytest.mark.parametrize("mode", ("inventory_aba", "late_inventory_aba"))
def test_inventory_aba_revokes_same_read_admission(rig, monkeypatch, mode):
    service, db, root = rig
    service._source_context_lock = RLock()
    reads = install(service)
    calls = []
    public_id = "seller-a-catalog"
    service._provider_enabled_for_identity = lambda provider, owner: (
        provider == public_id and owner == "A" and mode != "public_off"
    )
    service.session.purchase_enrichment_public_provider_id = public_id
    @contextmanager
    def source_guard():
        yield True
    service.session.capture_purchase_enrichment_guard = lambda account: source_guard
    public = SimpleNamespace(
        provider_id=public_id, credential_id="__public__", credentials={},
        _accounts=[], connected=True, purchase_enrichment_source_provider_id="seller-a",
    )

    def toggle():
        scope = scope_for_account(service.account)
        db.metadata_set_with_revision(source_enablement_key(scope), "0", inventory_epoch_key(scope))
        db.metadata_set_with_revision(source_enablement_key(scope), "1", inventory_epoch_key(scope))

    def response(url, title, *, check_authority, control):
        assert title == "Titre synthétique" and check_authority() is True
        if mode == "late_inventory_aba":
            toggle()
        assert len(refs(db)[0]) == 1
        if mode == "late_revoke":
            revoke_source_context(service)
        with check_authority.hold_transport_start():
            calls.append("get_admitted")
        calls.append("public")
        row = public_row(
            source_provider=public_id, title=title, friendly_url=url, thumbnail_url=None,
            metadata={"public_url_evidence": public_id + ".public_search_product_card"},
        )
        return ProviderSearchResponse(
            results=[row], public_notices=(PublicSearchNotice(result=row),),
        ), int(time.time()), nullcontext

    public.purchase_enrichment_public_response = response
    service._sessions[service._session_key(public_id, "__public__")] = public
    tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                           SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
    runtime = NoticeWorkRuntime(db, tasks)
    monkeypatch.setattr(runtime, "request_tick", lambda: calls.append("wake"))
    try:
        if mode == "policy_off":
            db.metadata_set(_POLICY, "false")
        executor = install_purchase_runtime(service, runtime)
        assert calls == [] and reads == []
        result = synchronize_account_source(service, service.account)["canonical_rights"]
        assert len(refs(db)[0]) == 1 and refs(db)[1] == 0
        if mode in ("policy_off", "public_off"):
            assert service.read_calls == 1 and reads == []
            assert "purchase_enrichment" not in result and calls == []
            assert executor.ready_refs() == ()
            assert db.metadata_get(_POLICY) == ("false" if mode == "policy_off" else None)
        else:
            assert service.read_calls == 0 and reads == ["same-read"]
            assert result["purchase_enrichment"] == {
                "requested": 1, "admitted": 1, "deferred": 0, "unrequested": 0,
            }
            assert calls == ["wake"]
            if mode == "revoke":
                revoke_source_context(service)
            if mode == "inventory_aba":
                toggle()
            tick = runtime._pump.tick()
            handle, = tick.handles
            assert handle.result(timeout=10) == (
                "current" if mode == "qualified" else "not_ready" if mode in ("revoke", "inventory_aba") else "error"
            )
            hits = search_public_notice_c(db, "synthétique", now=int(time.time())).hits
            if mode == "qualified":
                assert len(hits) == 1 and calls == ["wake", "get_admitted", "public"]
                # Subsequent identical inventory does not require another GET.
                synchronize_account_source(service, service.account)
                assert reads == ["same-read", "same-read"]
                assert calls == ["wake", "get_admitted", "public"]
            else:
                assert not hits and calls == ["wake"]
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        assert not root.exists()
    finally:
        runtime.close()
        tasks.close()


@pytest.mark.parametrize("mode", ("qualified", "inventory_aba", "policy_aba", "session_revoked"))
def test_omission_handoff_uses_original_runtime_fence_after_a_commit(rig, monkeypatch, mode):
    from cifereca.core.canonical_sync_stage import observe_canonical_rights_in_sync
    from cifereca.core.notice_c_purchase_omissions import _PREFIX
    from cifereca.core.notice_c_purchase_settings import read_purchase_policy, change_purchase_policy
    from cifereca.plugin_api import (
        PurchaseEnrichmentObservation, PurchaseEnrichmentOmission, from_legacy_rights_snapshot,
    )
    service, db, root = rig
    service._source_context_lock = RLock()
    public_id = "seller-a-catalog"
    service._provider_enabled_for_identity = lambda provider, owner: provider == public_id and owner == "A"
    service.session.purchase_enrichment_public_provider_id = public_id
    live = [True]
    @contextmanager
    def source_guard():
        yield live[0]
    service.session.capture_purchase_enrichment_guard = lambda account: source_guard
    inventory = from_legacy_rights_snapshot(service.snapshot)
    resource, = inventory.resources
    omission = PurchaseEnrichmentOmission(resource.source, resource.observed_at,
                                         "direct_locator_unattested")
    service.session.purchase_enrichment_observation = lambda account: PurchaseEnrichmentObservation(
        inventory, omissions=(omission,))
    calls = []
    def no_get(*args, **kwargs):
        calls.append("forbidden")
        raise AssertionError("unexpected GET")
    public = SimpleNamespace(
        provider_id=public_id, credential_id="__public__", credentials={},
        _accounts=[], connected=True, purchase_enrichment_source_provider_id="seller-a",
        purchase_enrichment_public_response=no_get,
    )
    service._sessions[service._session_key(public_id, "__public__")] = public
    tasks = TaskBrokerImpl(object(), max_workers=3, shared_admission=
                            SharedTaskAdmission(max_inflight=4, reserve_user_slots=1))
    runtime = NoticeWorkRuntime(db, tasks)
    monkeypatch.setattr(runtime, "request_tick", lambda: calls.append("wake"))
    try:
        executor = install_purchase_runtime(service, runtime)
        def after_commit(_snapshot):
            assert len(refs(db)[0]) == 1
            if mode == "inventory_aba":
                scope = scope_for_account(service.account)
                db.metadata_set_with_revision(source_enablement_key(scope), "0", inventory_epoch_key(scope))
                db.metadata_set_with_revision(source_enablement_key(scope), "1", inventory_epoch_key(scope))
            elif mode == "policy_aba":
                before = read_purchase_policy(db)
                off = change_purchase_policy(service, expected_storage=db, expected=before, enabled=False)
                change_purchase_policy(service, expected_storage=db, expected=off, enabled=True)
            elif mode == "session_revoked":
                live[0] = False
        result = observe_canonical_rights_in_sync(service, service.account, snapshot_sink=after_commit)
        assert result["source_references_changed"] == 1
        with db._lock, db._connect() as sql:
            count, = sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                 (_PREFIX, _PREFIX + "\uffff")).fetchone()
        assert count == int(mode == "qualified")
        assert calls == [] and executor.ready_refs() == ()
        assert runtime.documentary_revision() == int(mode == "qualified")
        if mode == "qualified":
            from cifereca.core.notice_c_purchase_library_projection import _status_txn
            from cifereca.plugin_api import PurchaseEnrichmentCandidate
            # An identical same-A omission is idempotent, with no job.
            observe_canonical_rights_in_sync(service, service.account)
            assert runtime.documentary_revision() == 1
            assert calls == [] and executor.ready_refs() == ()
            with db._lock, db._connect() as sql:
                assert _status_txn(sql, resource.source, now=100) == (
                    "direct_locator_unattested", 0,
                )
            newer = PurchaseEnrichmentOmission(
                resource.source, resource.observed_at, "comparison_title_unattested",
            )
            service.session.purchase_enrichment_observation = lambda account: PurchaseEnrichmentObservation(
                inventory, omissions=(newer,))
            observe_canonical_rights_in_sync(service, service.account)
            assert runtime.documentary_revision() == 2
            with db._lock, db._connect() as sql:
                assert _status_txn(sql, resource.source, now=100) == (
                    "comparison_title_unattested", 0,
                )
            # A candidate clears the receipt before its separate handoff.
            # The existing wake is intercepted, so no worker/GET occurs.
            candidate = PurchaseEnrichmentCandidate(
                resource.source, "https://catalog.example.org/product/123",
                "Titre synthétique", resource.observed_at,
            )
            service.session.purchase_enrichment_observation = lambda account: PurchaseEnrichmentObservation(
                inventory, (candidate,))
            observe_canonical_rights_in_sync(service, service.account)
            assert runtime.documentary_revision() == 4  # Omission removal + new intent.
            with db._lock, db._connect() as sql:
                assert sql.execute("SELECT count(*) FROM metadata WHERE key>=? AND key<?",
                                   (_PREFIX, _PREFIX + "\uffff")).fetchone() == (0,)
            assert calls == ["wake"] and len(executor.ready_refs()) == 1
        assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
        assert not root.exists()
    finally:
        runtime.close()
        tasks.close()
