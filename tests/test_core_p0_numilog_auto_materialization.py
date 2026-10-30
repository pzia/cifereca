"""#1905: Numilog automatic downloads require an explicit separate opt-in."""
from __future__ import annotations

from types import SimpleNamespace as NS
from time import time
import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.numilog_materialization_consent import mode, policy_key
from cifereca.core.numilog_materialization_startup import (
    eligible_source_ids, execute,
)
from cifereca.models import SearchResult
from cifereca.plugin_api import Cancelled, SyncResult
from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
from tests.test_core_p0_1905_numilog_offline_preview import _owned
from tests.test_sync_state_contract import _service


def _setup(tmp_path, *, count=0):
    service = _service(tmp_path)
    account, session, calls = _connect(service, number=0)
    for i in range(count):
        _owned(service, account, lid=str(100+i))
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    return service, account, session, calls, broker, sid


def _authorize(broker, sid):
    proposed = broker.prepare_numilog_materialization_consent(sid)
    assert proposed.ok and proposed.facts["status"] == "confirmation_required"
    assert proposed.facts["acquisition_authorized"] is False
    token = proposed.facts["confirmation_token"]
    accepted = broker.confirm_numilog_materialization_consent(
        sid, token, mode="on_start",
    )
    assert accepted.ok and accepted.facts["status"] == "on_start"
    assert accepted.facts["acquisition_authorized"] is False
    return token


def test_default_is_manual_and_never_calls_private_provider(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path, count=2)
    try:
        session.library = lambda: pytest.fail("automatic fulfillment without consent")
        assert mode(broker, sid) == "manual"
        assert eligible_source_ids(broker, account.identity_id) == ()
        receipt = execute(broker, account.identity_id, "startup")
        assert receipt.ok and receipt.changed == 0
        assert receipt.facts["status"] == "not_due"
        assert service.storage.metadata_get(policy_key(sid)) is None
        assert calls == []
    finally:
        service.close()


def test_double_confirmation_is_one_use_and_auto_consent_persisted_only_after_confirmation(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        proposed = broker.prepare_numilog_materialization_consent(sid)
        assert proposed.ok
        assert mode(broker, sid) == "manual"
        assert service.storage.metadata_get(policy_key(sid)) is None
        token = proposed.facts["confirmation_token"]
        assert not broker.confirm_numilog_materialization_consent(
            sid, token, mode="periodic",
        ).ok
        assert mode(broker, sid) == "manual"
        assert broker.confirm_numilog_materialization_consent(
            sid, token, mode="on_start",
        ).ok
        assert mode(broker, sid) == "on_start"
        assert not broker.confirm_numilog_materialization_consent(
            sid, token, mode="on_start",
        ).ok
        assert mode(broker, sid) == "on_start"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("invalid", [
    "1", "yes", "true", "on_start", "periodic", "on_start:v2",
    "{\"mode\":\"on_start\"}", "kobo", "",
])
def test_legacy_and_malformed_metadata_cannot_grant_auto_backup(tmp_path, invalid):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        service.storage.metadata_set(policy_key(sid), invalid)
        assert mode(broker, sid) == "manual"
        assert eligible_source_ids(broker, account.identity_id) == ()
        assert calls == []
    finally:
        service.close()


def test_revoke_during_preview_and_disabling_source_clears_policy(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        pending = broker.prepare_numilog_materialization_consent(sid)
        assert pending.ok
        assert broker.revoke_numilog_materialization_consent(sid) == "manual"
        assert not broker.confirm_numilog_materialization_consent(
            sid, pending.facts["confirmation_token"], mode="on_start",
        ).ok
        _authorize(broker, sid)
        assert mode(broker, sid) == "on_start"
        broker.set_enabled(sid, False)
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        broker.set_enabled(sid, True)
        assert mode(broker, sid) == "manual"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("revocation", ["rebound", "disconnected", "changed_session"])
def test_no_opt_in_when_account_or_session_changes_after_preview(tmp_path, revocation):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        proposed = broker.prepare_numilog_materialization_consent(sid)
        assert proposed.ok
        if revocation == "rebound":
            service.storage.provider_connection_put({
                "identity_id": "alex", "provider_id": "numilog",
                "credential_id": account.credential_id, "enabled": True,
            })
        elif revocation == "disconnected":
            session.connected = False
        else:
            service._sessions[service._session_key("numilog", account.credential_id)] = NS(
                **vars(session),
            )
        rejected = broker.confirm_numilog_materialization_consent(
            sid, proposed.facts["confirmation_token"], mode="on_start",
        )
        assert rejected.ok is False
        assert mode(broker, sid) == "manual"
        assert calls == []
    finally:
        service.close()


def test_startup_claim_is_atomic_throttled_and_not_retried_on_failure(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        now = int(time())
        assert service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now,
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now,
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 3599,
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        assert service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 3600,
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        broker.revoke_numilog_materialization_consent(sid)
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 7200,
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("corrupt", ["nan", "-1", "99999999999999999", "{}"])
def test_corrupt_attempt_clock_blocks_automatic_retry(tmp_path, corrupt):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        service.storage.metadata_set(
            "sync.source.numilog.materialization.last_start.v1." + sid, corrupt,
        )
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=int(time()),
            expected_revision=service.storage.numilog_materialization_consent_revision(sid),
        )
        assert calls == []
    finally:
        service.close()


def test_startup_empty_authenticated_library_claims_only_once(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: []
        first = execute(broker, account.identity_id, "startup")
        second = execute(broker, account.identity_id, "startup")
        assert first.ok and first.facts["status"] == "complete"
        assert second.ok and second.facts["status"] == "not_due"
        assert first.facts["sources_claimed"] == 1
        assert second.facts["sources_claimed"] == 0
        assert first.facts["downloaded"] == second.facts["downloaded"] == 0
        assert calls == []
    finally:
        service.close()


def test_startup_keeps_max_eight_fresh_downloads_and_skips_reconciliations(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        books = [_row(account.credential_id, i) for i in range(14)]
        session.library = lambda: list(books)
        seen = []
        def preflight(_sid, item):
            status = "needs_reconciliation" if item == books[0].record_id else "candidate_unverified"
            return SyncResult(ok=status == "candidate_unverified",
                              facts={"status": status})
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda self, _sid, item: preflight(_sid, item),
        )
        def acquire(row, *, _admission=None, _numilog_recovery=False):
            assert _admission() is row
            seen.append(row.record_id)
            return NS(metadata={"numilog_checkpoint_id":"FICTIONAL"})
        monkeypatch.setattr(service, "acquire_direct_item", acquire)
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_a, **_kw: None,
        )
        result = execute(broker, account.identity_id, "startup")
        # A single unresolved local candidate is not a veto on a freshly
        # authenticated, consented purchase. The remaining six are deferred
        # by the eight-download quota, so the batch cannot be complete.
        assert not result.ok and result.changed == 8
        assert result.facts["status"] == "partial"
        assert result.facts["attempted"] == 8
        assert result.facts["skipped"] == 0
        assert result.facts["blocked"] == 1
        assert seen == [r.record_id for r in books[:8]]
        assert calls == []
    finally:
        service.close()


def test_revocation_inside_locked_admission_stops_without_indexing(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        book = _row(account.credential_id, 0)
        session.library = lambda: [book]
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda *_args: SyncResult(
                ok=True, facts={"status": "candidate_unverified"},
            ),
        )
        indexed = []
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_a, **_kw: indexed.append(True),
        )
        def interrupted(row, *, _admission=None):
            assert _admission() is row
            broker.revoke_numilog_materialization_consent(sid)
            _admission()
            pytest.fail("revoked acquisition must not commit")
        monkeypatch.setattr(service, "acquire_direct_item", interrupted)
        result = execute(broker, account.identity_id, "startup")
        assert not result.ok and result.facts["status"] == "failed"
        assert result.changed == 0 and indexed == []
        assert mode(broker, sid) == "manual"
        assert calls == []
    finally:
        service.close()


def test_numilog_startup_queue_is_independent_of_inventory_schedule(
    tmp_path,
):
    from cifereca.gui.source_inventory_startup import dispatch_after_opening

    service, account, session, calls, broker, sid = _setup(tmp_path)
    tasks = []
    window = NS(
        service=service,
        plugin_manager=NS(broker=lambda _name: broker),
        runner=NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs))),
        _closed=False,
    )
    try:
        assert dispatch_after_opening(window) is False
        assert tasks == []
        _authorize(broker, sid)
        session.library = lambda: []
        assert dispatch_after_opening(window) is True
        assert len(tasks) == 1
        assert tasks[0][1]["kind"] == "source-materialize"
        assert tasks[0][1]["origin"] == "automatic"
        assert tasks[0][1]["coalesce_key"] == "source:numilog:materialize:startup"
        # The inventory policy was never enabled.
        assert broker.inventory_refresh_schedule(sid)["mode"] == "manual"
        work = tasks[0][0][1]
        assert callable(work)
        work()
        assert service.storage.metadata_get(
            "sync.source.numilog.materialization.last_start.v1." + sid,
        ) is not None
        assert dispatch_after_opening(window) is False
        assert len(tasks) == 1
        assert calls == []
    finally:
        service.close()


def test_numilog_revoked_while_startup_task_queued_skips_all_provider_io(tmp_path):
    from cifereca.gui.source_inventory_startup import dispatch_after_opening

    service, account, session, calls, broker, sid = _setup(tmp_path)
    tasks = []
    window = NS(
        service=service,
        plugin_manager=NS(broker=lambda _name: broker),
        runner=NS(submit=lambda *args, **kwargs: tasks.append((args, kwargs))),
        _closed=False,
    )
    try:
        _authorize(broker, sid)
        session.library = lambda: pytest.fail("revoked scheduled work")
        assert dispatch_after_opening(window)
        assert len(tasks) == 1
        broker.revoke_numilog_materialization_consent(sid)
        tasks[0][0][1]()
        assert service.storage.metadata_get(
            "sync.source.numilog.materialization.last_start.v1." + sid,
        ) is None
        assert calls == []
    finally:
        service.close()


def test_numilog_startup_does_not_run_for_other_identity(tmp_path):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: pytest.fail("wrong owner")
        assert eligible_source_ids(broker, "alex") == ()
        assert execute(broker, "alex", "startup").ok
        assert execute(broker, account.identity_id, "periodic").ok is False
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize(("size", "expected"), [(300, 8), (2049, 0)])
def test_large_numilog_manifest_is_bounded_without_starving_hundreds_of_books(
    tmp_path, monkeypatch, size, expected,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        rows = [_row(account.credential_id, i) for i in range(size)]
        session.library = lambda: rows
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda *_a: SyncResult(ok=True, facts={"status": "candidate_unverified"}),
        )
        downloads = []
        def acquire(item, *, _admission=None):
            assert _admission() is item
            downloads.append(item.record_id)
            return NS(metadata={"numilog_checkpoint_id": "FICTIONAL"})
        monkeypatch.setattr(service, "acquire_direct_item", acquire)
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_a, **_kw: None,
        )
        result = execute(broker, account.identity_id, "startup")
        assert result.changed == expected
        assert len(downloads) == expected
        # A 300-item manifest has unprocessed purchases after the bounded
        # first eight downloads, so the source remains partial.
        assert result.ok is False
        assert result.facts["status"] == ("partial" if size <= 2048 else "failed")
        assert calls == []
    finally:
        service.close()


def test_concurrent_revocation_after_confirmation_snapshot_blocks_reactivation(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        proposed = broker.prepare_numilog_materialization_consent(sid)
        assert proposed.ok
        original = service.storage.numilog_materialization_change_consent

        def revoke_between_validation_and_commit(source_id, *, activate, expected_revision=None):
            if activate:
                # Another worker, after the SHA re-check but before the SQL
                # commit, withdraws consent in a real SQLite transaction.
                assert original(source_id, activate=False)
            return original(
                source_id, activate=activate, expected_revision=expected_revision,
            )

        monkeypatch.setattr(
            service.storage, "numilog_materialization_change_consent",
            revoke_between_validation_and_commit,
        )
        response = broker.confirm_numilog_materialization_consent(
            sid, proposed.facts["confirmation_token"], mode="on_start",
        )
        assert not response.ok and response.facts["status"] == "consent_changed"
        assert mode(broker, sid) == "manual"
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        service.close()


def test_concurrent_revocation_during_sha_preview_issues_no_usable_token(
    tmp_path, monkeypatch,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        original = SynchronizationBrokerImpl.preview_local_materialization

        def preview_with_revocation(self, source_id, **kwargs):
            receipt = original(self, source_id, **kwargs)
            self.revoke_numilog_materialization_consent(source_id)
            return receipt

        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preview_local_materialization",
            preview_with_revocation,
        )
        result = broker.prepare_numilog_materialization_consent(sid)
        assert not result.ok and result.facts["status"] == "preview_changed"
        assert "confirmation_token" not in result.facts
        assert mode(broker, sid) == "manual"
        assert calls == []
    finally:
        service.close()


def test_numilog_consent_revision_is_atomic_across_storage_instances(tmp_path):
    from cifereca.storage import Storage
    service, account, session, calls, broker, sid = _setup(tmp_path)
    other = Storage(service.storage.path)
    try:
        original = service.storage.numilog_materialization_consent_revision(sid)
        assert other.numilog_materialization_change_consent(
            sid, activate=False,
        )
        changed = service.storage.numilog_materialization_consent_revision(sid)
        assert changed != original
        assert not service.storage.numilog_materialization_change_consent(
            sid, activate=True, expected_revision=original,
        )
        assert service.storage.metadata_get(policy_key(sid)) == "manual"
        assert calls == []
    finally:
        other.close()
        service.close()


@pytest.mark.parametrize("interruption", ["during_manifest", "during_locked_admission"])
def test_revoke_and_reopt_in_cannot_resume_stale_startup_batch(
    tmp_path, monkeypatch, interruption,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        initial_revision = service.storage.numilog_materialization_consent_revision(sid)
        book = _row(account.credential_id, 0)
        seen = []
        indexed = []
        def revoke_and_reactivate():
            assert broker.revoke_numilog_materialization_consent(sid) == "manual"
            _authorize(broker, sid)
            assert mode(broker, sid) == "on_start"
            assert service.storage.numilog_materialization_consent_revision(sid) != initial_revision

        def library():
            seen.append("library")
            if interruption == "during_manifest":
                revoke_and_reactivate()
            return [book]

        session.library = library
        monkeypatch.setattr(
            SynchronizationBrokerImpl, "preflight_numilog_purchase",
            lambda *_a: SyncResult(
                ok=True, facts={"status": "candidate_unverified"},
            ),
        )
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_a, **_kw: indexed.append("indexed"),
        )
        def acquire(row, *, _admission=None):
            seen.append("acquire")
            assert _admission() is row
            revoke_and_reactivate()
            with pytest.raises(ValueError, match="numilog_auto_consent_revoked"):
                _admission()
            raise ValueError("old batch must fail")
        monkeypatch.setattr(service, "acquire_direct_item", acquire)
        result = execute(broker, account.identity_id, "startup")
        assert result.changed == 0 and indexed == []
        if interruption == "during_manifest":
            assert seen == ["library"]
            assert result.facts["status"] == "failed"
        else:
            assert seen == ["library", "acquire"]
            assert not result.ok and result.facts["status"] == "failed"
        assert mode(broker, sid) == "on_start"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("change", ["connection_rebound", "account_reassigned", "session_disconnected"])
def test_startup_claim_rechecks_owner_before_first_private_library_request(
    tmp_path, monkeypatch, change,
):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        session.library = lambda: pytest.fail("invalid owner cannot fetch purchases")
        original = service.storage.numilog_materialization_claim_startup

        def change_after_claim(**kwargs):
            ok = original(**kwargs)
            assert ok
            if change == "connection_rebound":
                service.storage.provider_connection_put({
                    "identity_id": "alex", "provider_id": "numilog",
                    "credential_id": account.credential_id, "enabled": True,
                })
            elif change == "account_reassigned":
                account.unique_id = "reassigned-after-claim"
            else:
                session.connected = False
            return ok

        monkeypatch.setattr(
            service.storage, "numilog_materialization_claim_startup",
            change_after_claim,
        )
        response = execute(broker, account.identity_id, "startup")
        assert not response.ok and response.facts["status"] == "blocked"
        assert response.changed == 0
        assert response.facts["sources_claimed"] == 1
        assert response.facts["blocked"] == 1
        assert response.facts["attempted"] == 0
        assert service.storage.numilog_materialization_run_status(sid)["status"] == "blocked"
        assert calls == []
    finally:
        service.close()


@pytest.mark.parametrize("private_owned", [False, True])
def test_first_seen_purchase_is_mirrored_only_from_authenticated_numilog_library(
    tmp_path, monkeypatch, private_owned,
):
    from dataclasses import replace

    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker, sid)
        actual = _row(account.credential_id, 0)
        item = actual if private_owned else replace(
            actual, source_collection="public_catalog",
            scenario="NUMILOG_PUBLIC", owned=False,
        )
        session.library = lambda: [item]
        assert service.storage.entitlements(provider_id="numilog") == []
        acquired = []
        def acquire(row, *, _admission=None):
            assert row.record_id == actual.record_id
            assert _admission() is row
            acquired.append(row.record_id)
            return NS(metadata={"numilog_checkpoint_id": "FICTIONAL"})
        monkeypatch.setattr(service, "acquire_direct_item", acquire)
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_args, **_kwargs: None,
        )
        result = execute(broker, account.identity_id, "startup")
        if private_owned:
            assert result.ok and result.facts["status"] == "complete"
        else:
            # A public-catalogue row in an authenticated private manifest
            # is a failed inventory, not evidence of an empty owned library.
            assert not result.ok and result.facts["status"] == "failed"
            assert result.facts["attempted"] == 0
            status = broker.numilog_materialization_run_status(sid)
            assert status["diagnostic"]["code"] == "manifest_invalid"
        assert result.changed == int(private_owned)
        assert len(acquired) == int(private_owned)
        rights = service.storage.entitlements(provider_id="numilog")
        assert len(rights) == int(private_owned)
        if private_owned:
            assert rights[0]["item_id"] == actual.record_id
            assert rights[0]["metadata"]["source"] == "numilog.authenticated_library"
        assert calls == []
    finally:
        service.close()
