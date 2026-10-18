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
        )
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now,
        )
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 3599,
        )
        assert service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 3600,
        )
        broker.revoke_numilog_materialization_consent(sid)
        assert not service.storage.numilog_materialization_claim_startup(
            source_id=sid, now=now + 7200,
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
        assert second.ok and second.facts["status"] == "complete"
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
        def acquire(row, *, _admission=None):
            assert _admission() is row
            seen.append(row.record_id)
            return NS(metadata={"numilog_checkpoint_id":"FICTIONAL"})
        monkeypatch.setattr(service, "acquire_direct_item", acquire)
        monkeypatch.setattr(
            "cifereca.core.numilog_materialization_startup.index_delivered",
            lambda *_a, **_kw: None,
        )
        result = execute(broker, account.identity_id, "startup")
        assert result.ok and result.changed == 8
        assert result.facts["attempted"] == 8
        assert result.facts["skipped"] == 1
        assert seen == [r.record_id for r in books[1:9]]
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
        assert result.ok is (size <= 2048)
        assert calls == []
    finally:
        service.close()
