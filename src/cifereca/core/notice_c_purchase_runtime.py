"""Production #1072 composition on the existing NoticeWorkRuntime/Sync.

Reciprocal optional provider ports, already connected exact private account
and explicitly enabled anonymous public singleton only. No new sessions,
provider imports, network discovery, timer or contact at installation.
"""
from __future__ import annotations

from contextlib import contextmanager
from inspect import getattr_static
import time
import sqlite3
import re

from . import notice_c_job_journal as journal
from .notice_c_purchase_execution import PurchaseIntentExecution
from .notice_c_purchase_composition import make_purchase_enrichment_sink
from .notice_c_purchase_intent import _POLICY, _allowed_txn
from .notice_c_purchase_settings import _read_policy_txn
from .source_sync_consent import inventory_epoch_key, policy_in_transaction
from .notice_c_public_search_replay import _anonymous_session
from .source_context import source_context_epoch
from ..plugin_api.provider_rights_observation import ProviderRightsScope


def _permission_fence_txn(db, scope):
    """Capture both inventory ABA representations, not acquisition consent.

    The source-wide policy revision can rotate instead of inventory_epoch
    on legacy/configured toggles. Its mode is deliberately NOT a grant.
    """
    if not _allowed_txn(db, scope):
        return None
    preference = _read_policy_txn(db)
    generic = policy_in_transaction(db, scope)
    row = db.execute("SELECT value FROM metadata WHERE key=?",
                     (inventory_epoch_key(scope),)).fetchone()
    epoch = row[0] if row else None
    if (not preference.enabled or generic.mode == "invalid"
            or (row is not None and (type(epoch) is not str
                or re.fullmatch("[0-9a-f]{32}", epoch) is None))):
        return None
    return preference, generic.revision, epoch


def _capture_sink(service, runtime, executor, account, private):
    lock = getattr(service, "_source_context_lock", None)
    if lock is None:
        return None
    public_id = getattr_static(private, "purchase_enrichment_public_provider_id", None)
    if (type(public_id) is not str or not public_id
            or not callable(getattr_static(private, "capture_purchase_enrichment_guard", None))
            or not callable(getattr_static(private, "purchase_enrichment_observation", None))):
        return None
    owner, private_id = account.identity_id, account.provider_id
    if public_id == private_id:
        return None
    key = service._session_key(public_id, "__public__")
    public = service._sessions.get(key)
    if (public is None or not _anonymous_session(public, public_id)
            or getattr_static(public, "purchase_enrichment_source_provider_id", None) != private_id
            or not callable(getattr_static(public, "purchase_enrichment_public_response", None))):
        return None
    if service._provider_enabled_for_identity(public_id, owner) is not True:
        return None
    scope = ProviderRightsScope(owner, private_id, account.credential_id, account.unique_id)
    epoch = source_context_epoch(service)
    accounts, sessions, storage = service.accounts, service._sessions, service.storage
    private_key = service._session_key(private_id, scope.credential_id)
    policy = [None]
    capture = private.capture_purchase_enrichment_guard(account)
    if not callable(capture):
        return None

    def permission_current_txn(db):
        return policy[0] is not None and _permission_fence_txn(db, scope) == policy[0]

    def permission_current():
        if policy[0] is None:
            return True
        try:
            with storage._lock, storage._connect() as db:
                db.execute("BEGIN")
                return permission_current_txn(db)
        except (ValueError, sqlite3.Error):
            return False

    def current():
        coords = (scope.identity_id, scope.provider_id, scope.credential_id, scope.account_id)
        return (not executor._closed and source_context_epoch(service) == epoch
                and permission_current()
                and service.accounts is accounts and service._sessions is sessions
                and service.storage is storage and sessions.get(private_key) is private
                and sessions.get(key) is public and _anonymous_session(public, public_id)
                and (account.identity_id, account.provider_id, account.credential_id,
                     account.unique_id) == coords
                and sum((row.identity_id, row.provider_id, row.credential_id, row.unique_id)
                        == coords for row in accounts) == 1
                and any(row is account for row in accounts)
                and sum(getattr(row, "provider_id", None) == public_id
                        and getattr(row, "credential_id", None) == "__public__"
                        for row in sessions.values()) == 1
                and service._provider_enabled_for_identity(public_id, owner) is True)

    @contextmanager
    def hold_context():
        if not lock.acquire(blocking=False):
            yield False
            return
        try:
            with capture() as held:
                yield held is True and current()
        finally:
            lock.release()

    with hold_context() as held:
        if held is not True:
            return None
        # Default ON only at this qualified production boundary. Respect
        # existing OFF and never force-enable the optional public catalogue.
        with storage._lock, storage._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("INSERT OR IGNORE INTO metadata(key,value) VALUES(?,?)",
                       (_POLICY, "true"))
            try:
                policy[0] = _permission_fence_txn(db, scope)
            except ValueError:
                return None
            if policy[0] is None:
                return None
    sink = make_purchase_enrichment_sink(
        executor, private_provider_id=private_id, public_session=public,
        read_response=public.purchase_enrichment_public_response,
        hold_context=hold_context, wake=runtime.request_tick, ttl_seconds=300,
        input_current_txn=permission_current_txn,
    )

    def record_omissions(snapshot, omissions, candidates, *, context_current):
        # Pin the same private source/session and permission generation as
        # candidate admission. This does not grant transport or change A.
        from .notice_c_purchase_omissions import record_purchase_omissions
        if snapshot.scope != scope:
            raise ValueError("purchase_omission_context_changed")
        with hold_context() as held:
            if held is not True:
                raise ValueError("purchase_omission_context_changed")
            changed = record_purchase_omissions(
                storage, snapshot, omissions, candidates,
                context_current=context_current,
                permission_current_txn=permission_current_txn,
            )
        if changed:
            # Same existing in-memory documentary revision as job transitions.
            # Omissions can change presentation even without any queued job.
            runtime._notify_purchase()

    sink.record_omissions = record_omissions
    return sink


def install_purchase_runtime(service, runtime):
    """Install exactly once, before the existing heartbeat, no dispatch."""
    if getattr(service, "_notice_purchase_sink_factory", None) is not None:
        raise ValueError("notice_purchase_runtime_already_installed")
    executor = PurchaseIntentExecution(
        service.storage, clock=lambda: int(time.time()), attestation_capacity=journal._MAX_JOBS,
    )
    if not runtime.install_purchase_execution(executor):
        executor.close()
        raise ValueError("notice_purchase_runtime_unavailable")

    def factory(account, session):
        return _capture_sink(service, runtime, executor, account, session)

    service._notice_purchase_sink_factory = factory
    return executor
