"""Confirmed per-source acquisition: real broker/SQLite/executor, fake transport."""
import hashlib
import json
import os
import sqlite3
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core import source_materialization as materialize
from cifereca.core import kobo_backup
from cifereca.identities import Identity
from cifereca.locking import InterProcessLock
from cifereca.models import Account
from tests.test_kobo_backup_fault_isolation import _service, _write_epub, _ready_adept_state, BackupSession


@pytest.fixture
def case(tmp_path, monkeypatch):
    manifest = [{"product_id": "PRIVATE-book-1", "revision_id": "r1", "title": "PRIVATE title", "format": "EPUB"},
                {"product_id": "PRIVATE-book-2", "revision_id": "r2", "title": "PRIVATE second", "format": "EPUB"}]
    service, session = _service(tmp_path, manifest)
    service.accounts = list(session.accounts)
    sessions = [session]
    # The selected source is the middle of three identities/accounts; current
    # identity stays default, so accidentally using it is observable.
    for index in (2, 3):
        identity = Identity(f"reader-{index}", f"Reader {index}", directory_name=f"reader-{index}")
        service.identities.append(identity)
        credential = f"vault-{index}"
        service.storage.provider_connection_put({"identity_id": identity.identity_id, "provider_id": "kobo",
                                                 "credential_id": credential, "label": "Private account"})
        other = BackupSession(descriptor=session.descriptor, credential_id=credential, label="Kobo",
                              credentials={}, options={}, manifest=manifest)
        other.connect()
        other.accounts[0].identity_id = identity.identity_id
        other.accounts[0].unique_id = f"PRIVATE-account-{index}"
        service.accounts.extend(other.accounts)
        service._sessions[service._session_key("kobo", credential)] = other
        sessions.append(other)
    target = sessions[1]
    calls = {s.credential_id: 0 for s in sessions}
    for s in sessions:
        original = s.backup_manifest
        def fetch(s=s, original=original):
            calls[s.credential_id] += 1
            return original()
        s.backup_manifest = fetch
    broker = SynchronizationBrokerImpl(service)
    source_id = broker._source_id(target.accounts[0])
    monkeypatch.setattr(materialize, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    monkeypatch.setattr(service, "catalog_scan", lambda **kwargs: pytest.fail("No global scan"))
    monkeypatch.setattr(service, "kobo_adept_state", lambda **kwargs: _ready_adept_state())
    downloaded = []
    def execute(request):
        path = Path(request["destination"])
        downloaded.append(path)
        _write_epub(path)
        return {"ok": True, "operation": "download", "artifact": {
            "path": str(path), "format": "epub", "drm": "none", "size": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}}
    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    yield NS(service=service, broker=broker, source=source_id, target=target, sessions=sessions,
             calls=calls, downloaded=downloaded, root=tmp_path)
    service.close()


def _prepare(case):
    result = case.broker.prepare_local_materialization(case.source)
    assert result.ok, result
    assert result.facts["status"] == "prepared"
    return result.facts["confirmation_token"]


def _dump(case):
    with case.service.storage._lock:
        return "\n".join(case.service.storage._connect().iterdump())


def test_prepare_then_confirm_is_exactly_one_account_and_replay_cannot_download(case):
    before = _dump(case)
    token = _prepare(case)
    assert _dump(case) == before and not case.downloaded
    assert case.calls == {"vault-1": 0, "vault-2": 1, "vault-3": 0}
    result = case.broker.materialize_source(case.source, token)
    assert result.ok and result.changed == 2, result
    assert case.calls == {"vault-1": 0, "vault-2": 2, "vault-3": 0}
    assert len(case.downloaded) == 2
    assert all("reader-2/kobo" in str(path) for path in case.downloaded)
    assert all(not s.resolve_calls for s in (case.sessions[0], case.sessions[2]))
    assert {row["credential_id"] for row in case.service.storage.backup_list()} == {"vault-2"}
    assert case.broker.materialize_source(case.source, token).facts["status"] == "confirmation_unavailable"
    assert len(case.downloaded) == 2 and case.calls["vault-2"] == 2
    for secret in ("PRIVATE", "vault-2", "reader-2", str(case.root)):
        assert secret not in repr(result)
    phases = {p.phase: p for p in case.broker._ref(case.target.accounts[0]).phase_states}
    assert phases["materialize"].status == "success" and phases["materialize"].last_success
    assert phases["discover_rights"].last_success == ""
    # A fresh explicit plan is idempotent: same books do not download again.
    again = case.broker.materialize_source(case.source, _prepare(case))
    assert again.ok and again.changed == 0 and len(case.downloaded) == 2, again


@pytest.mark.parametrize("change", ["add", "revision", "remove", "owned", "duplicate"])
def test_changed_manifest_is_rejected_before_any_business_write(case, change):
    token = _prepare(case)
    before = _dump(case)
    if change == "add":
        case.target._manifest.append({"product_id": "new", "revision_id": "r3"})
    elif change == "revision":
        case.target._manifest[0]["revision_id"] = "NEW"
    elif change == "remove":
        case.target._manifest.pop()
    elif change == "owned":
        case.target._manifest[0]["owned"] = False
    else:
        case.target._manifest.append(case.target._manifest[0])
    result = case.broker.materialize_source(case.source, token)
    assert result.facts["status"] == "manifest_changed"
    assert not result.ok and not case.downloaded and _dump(case) == before


@pytest.mark.parametrize("change,reason", [("disabled", "source_disabled"), ("disconnected", "source_disconnected"),
                                         ("account", "unknown_source"), ("root", "preview_changed"),
                                         ("migration", "directory_migration_pending")])
def test_live_source_revalidation_cannot_use_current_selection_or_legacy_config(case, change, reason):
    token = _prepare(case)
    if change == "disabled":
        case.broker.set_enabled(case.source, False)
    elif change == "disconnected":
        case.target.connected = False
    elif change == "account":
        case.target.accounts[0].unique_id = "changed"
    elif change == "root":
        case.service.config.set("library.directory", str(case.root / "new-root"))
    else:
        case.service.storage.metadata_set("identity-directory-migration-v1", "pending")
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert result.facts["status"] == reason, result
    assert not result.ok and _dump(case) == before and not case.downloaded


def test_confirmation_for_another_source_and_a_new_broker_is_not_authority(case):
    token = _prepare(case)
    other = case.broker._source_id(case.sessions[0].accounts[0])
    assert case.broker.materialize_source(other, token).facts["status"] == "confirmation_unavailable"
    restarted = SynchronizationBrokerImpl(case.service)
    assert restarted.materialize_source(case.source, token).facts["status"] == "confirmation_unavailable"
    assert not case.downloaded and case.calls["vault-1"] == 0


def test_busy_lock_preserves_confirmation_until_it_can_be_revalidated(case):
    token = _prepare(case)
    with InterProcessLock(case.root / "acquisition.lock"):
        assert case.broker.materialize_source(case.source, token).facts["status"] == "acquisition_busy"
        assert case.broker.prepare_local_materialization(case.source).facts["status"] == "acquisition_busy"
    assert case.broker.materialize_source(case.source, token).ok


def test_expired_plan_and_malformed_token_do_not_call_the_provider(case, monkeypatch):
    token = _prepare(case)
    now = materialize.time.monotonic()
    monkeypatch.setattr(materialize.time, "monotonic", lambda: now + 601)
    assert case.broker.materialize_source(case.source, token).facts["status"] == "confirmation_expired"
    assert case.broker.materialize_source(case.source, "PRIVATE-token").facts["status"] == "invalid_confirmation"
    assert case.calls["vault-2"] == 1 and not case.downloaded


def test_cancel_before_execution_does_not_fetch_or_write(case):
    token = _prepare(case)
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token, NS(cancelled=lambda: True))
    assert result.facts["status"] == "cancelled" and not result.ok
    assert _dump(case) == before and case.calls["vault-2"] == 1 and not case.downloaded


def test_cancellation_between_items_keeps_file_and_has_its_own_phase_clock(case):
    token = _prepare(case)
    cancelled = []
    messages = []
    def report(**kwargs):
        messages.append(kwargs)
        if kwargs.get("current") == 1:
            cancelled.append(True)
    control = NS(cancelled=lambda: bool(cancelled), report=report)
    result = case.broker.materialize_source(case.source, token, control)
    assert result.facts["status"] == "cancelled" and result.changed == 1, result
    assert len(case.downloaded) == 1 and case.downloaded[0].is_file()
    assert "PRIVATE" not in repr(messages)
    phases = {p.phase: p for p in case.broker._ref(case.target.accounts[0]).phase_states}
    assert phases["materialize"].status == "cancelled" and phases["materialize"].last_success == ""
    assert case.service.storage.backup_get("kobo", "vault-2", "PRIVATE-book-2") is None


def test_unlinked_bypass_is_not_inherited_by_confirmed_execution(case, monkeypatch):
    case.service.config.set("kobo_backup.allow_unlinked_acquisition", True)
    monkeypatch.setattr(case.service, "kobo_unlinked_local_count", lambda identity: 1)
    before = _dump(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert result.facts["status"] == "unlinked_local_books" and not result.ok
    assert _dump(case) == before and not case.downloaded


def test_local_evidence_change_refuses_confirmation_without_delegation(case):
    token = _prepare(case)
    case.service.storage.backup_put({"provider_id": "kobo", "credential_id": "vault-2",
        "identity_id": "reader-2", "account_id": "PRIVATE-account-2", "item_id": "OTHER",
        "revision_id": "r1", "title": "PRIVATE historical", "status": "pending"})
    before = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert result.facts["status"] == "preview_changed" and not result.ok
    assert not case.downloaded and _dump(case) == before



def test_confirmation_evidence_is_one_sqlite_snapshot_across_processes(case):
    """A concurrent writer between legacy tables cannot create hybrid A/B."""

    storage = case.service.storage
    account = case.target.accounts[0]
    manifest = case.target._manifest
    # The fixture uses a reusable thread-local SQLite connection in WAL
    # mode. This is the same connection that reads six compatibility/canonical
    # tables for explicit Kobo source confirmation.
    baseline, _ = materialize._evidence(case.service, account, manifest)
    db = storage._connect()
    seen_savepoint = []
    concurrent_commit = []

    def writer_during_snapshot(statement):
        if statement.startswith("SAVEPOINT kobo_materialization_evidence"):
            seen_savepoint.append(True)
        if (seen_savepoint and not concurrent_commit
                and statement.startswith("SELECT * FROM entitlements ")):
            # backup_items has already been read, fixing SQLite's read view.
            # Update a table that _evidence reads later in its six-table walk.
            with sqlite3.connect(storage.path, timeout=2) as writer:
                cursor = writer.execute(
                    "UPDATE provider_connections SET label=? "
                    "WHERE provider_id='kobo' AND credential_id='vault-2'",
                    ("External scope mutation",),
                )
                assert cursor.rowcount == 1
            concurrent_commit.append(True)

    try:
        db.set_trace_callback(writer_during_snapshot)
        in_flight, _ = materialize._evidence(case.service, account, manifest)
    finally:
        db.set_trace_callback(None)

    assert seen_savepoint and concurrent_commit == [True]
    # A mixed six-table read would capture the newly updated scope here.
    assert in_flight == baseline
    refreshed, _ = materialize._evidence(case.service, account, manifest)
    assert refreshed != baseline
    # SAVEPOINT is always released: the shared connection stays reusable and
    # no transaction is held open after a successful proof.
    assert not db.in_transaction


def test_confirmation_snapshots_release_sqlite_savepoint_on_budget_failure(
    case, monkeypatch,
):
    storage = case.service.storage
    account = case.target.accounts[0]
    manifest = case.target._manifest
    db = storage._connect()
    # Intercept only the per-table budget check after the first SELECT.
    original_require = materialize._require
    checked = []
    def reject_after_sql_read(condition, reason):
        if reason == "evidence_budget_exhausted":
            checked.append(True)
            raise materialize._Rejected(reason)
        return original_require(condition, reason)

    with monkeypatch.context() as patch:
        patch.setattr(materialize, "_require", reject_after_sql_read)
        with pytest.raises(materialize._Rejected, match="evidence_budget_exhausted"):
            materialize._evidence(case.service, account, manifest)
    assert checked
    assert not db.in_transaction
    assert materialize._evidence(case.service, account, manifest)[0]

def test_provider_error_is_expurgated_and_does_not_change_inventory_state(case):
    token = _prepare(case)
    case.broker._set_state(case.source, {"status": "success", "last_success": "2026-10-15T10:00:00+00:00"})
    def fail():
        raise RuntimeError("Bearer PRIVATE key https://private/?token=SECRET /private/path")
    case.target.backup_manifest = fail
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.facts["status"] == "materialization_failed"
    assert "PRIVATE" not in repr(result) and "SECRET" not in repr(result)
    assert case.broker._ref(case.target.accounts[0]).last_success == "2026-10-15T10:00:00+00:00"


def test_shared_credential_is_ambiguous_even_if_only_one_account_is_selected(case):
    duplicate = Account(display_name="Other", unique_id="OTHER", provider_id="kobo",
                        credential_id="vault-2", identity_id="reader-3")
    case.service.accounts.append(duplicate)
    assert case.broker.prepare_local_materialization(case.source).facts["status"] == "ambiguous_account"
    assert not any(case.calls.values())


def test_checkpoint_failure_after_durable_download_reports_it_and_does_not_duplicate(case, monkeypatch):
    token = _prepare(case)
    original = kobo_backup._store_kobo_checkpoint
    def checkpoint(*args, **kwargs):
        if kwargs.get("processed") == 1:
            raise OSError("PRIVATE late checkpoint error")
        return original(*args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(kobo_backup, "_store_kobo_checkpoint", checkpoint)
        result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.changed == 1 and result.facts["completion_unknown"], result
    assert len(case.downloaded) == 1 and case.downloaded[0].is_file()
    assert "PRIVATE" not in repr(result)
    retry = case.broker.materialize_source(case.source, _prepare(case))
    assert retry.ok and retry.changed == 1 and len(case.downloaded) == 2, retry


def test_disabling_a_source_between_items_stops_only_remaining_acquisitions(case):
    token = _prepare(case)
    def report(**kwargs):
        if kwargs.get("current") == 1:
            case.broker.set_enabled(case.source, False)
    result = case.broker.materialize_source(case.source, token, NS(cancelled=lambda: False, report=report))
    assert not result.ok and result.changed == 1 and result.facts["status"] == "source_disabled"
    assert len(case.downloaded) == 1 and case.downloaded[0].is_file()
    assert all(not s.resolve_calls for s in (case.sessions[0], case.sessions[2]))


def test_existing_right_absent_from_preview_is_never_pruned(case):
    case.service.storage.backup_put({"provider_id": "kobo", "credential_id": "vault-2",
        "identity_id": "reader-2", "account_id": "PRIVATE-account-2", "item_id": "HISTORICAL",
        "revision_id": "old", "title": "PRIVATE historical", "status": "pending"})
    token = _prepare(case)
    assert case.broker.materialize_source(case.source, token).ok
    assert case.service.storage.backup_get("kobo", "vault-2", "HISTORICAL")["revision_id"] == "old"


@pytest.mark.parametrize("mutation,reason", [("revision", "revision_unproven"), ("duplicate", "invalid_manifest"),
                                          ("not_owned", "invalid_manifest"), ("symlink", "unsafe_directory"),
                                          ("pending", "acquisition_unresolved")])
def test_unsafe_preparations_have_no_token_or_business_writes(case, mutation, reason):
    if mutation == "revision":
        case.target._manifest[0].pop("revision_id")
    elif mutation == "duplicate":
        case.target._manifest.append(case.target._manifest[0])
    elif mutation == "not_owned":
        case.target._manifest[0]["owned"] = False
    elif mutation == "symlink":
        actual = case.root / "elsewhere"
        actual.mkdir()
        root = case.root / "EBooks"
        root.mkdir(exist_ok=True)
        (root / "reader-2").symlink_to(actual, target_is_directory=True)
    else:
        case.service.storage.backup_put({"provider_id": "kobo", "credential_id": "vault-2",
            "identity_id": "reader-2", "account_id": "PRIVATE-account-2", "item_id": "INTERRUPTED",
            "revision_id": "r1", "title": "Private", "status": "acquiring"})
    before = _dump(case)
    result = case.broker.prepare_local_materialization(case.source)
    assert not result.ok and result.facts["status"] == reason, result
    assert "confirmation_token" not in result.facts and not case.downloaded and _dump(case) == before


def test_interrupted_phase_from_an_old_process_is_not_running_or_success(case):
    case.service.storage.metadata_set("sync.source.materialization." + case.source, json.dumps({
        "status": "running", "owner": "old-process", "last_attempt": "2026-10-15T10:00:00+00:00",
        "last_success": "2026-10-14T09:00:00+00:00"}))
    phase = next(p for p in case.broker._ref(case.target.accounts[0]).phase_states if p.phase == "materialize")
    assert phase.status == "interrupted"
    assert phase.last_success == "2026-10-14T09:00:00+00:00"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX process/lock proof")
def test_real_process_crash_keeps_completed_item_and_requires_new_confirmation(case):
    token = _prepare(case)
    # Use a separate process with fresh SQLite connections. Kill it only after
    # the first durable item/checkpoint; no cleanup/final state can run.
    pid = os.fork()
    if pid == 0:
        try:
            case.service.storage.close()
            def report(**kwargs):
                if kwargs.get("current") == 1:
                    os._exit(17)
            case.broker.materialize_source(case.source, token, NS(cancelled=lambda: False, report=report))
        finally:
            os._exit(19)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 17
    first = case.service.storage.backup_get("kobo", "vault-2", "PRIVATE-book-1")
    assert first["status"] == "complete" and Path(first["local_path"]).is_file()
    restarted = SynchronizationBrokerImpl(case.service)
    phase = next(p for p in restarted._ref(case.target.accounts[0]).phase_states if p.phase == "materialize")
    assert phase.status == "interrupted" and phase.last_success == ""
    assert restarted.materialize_source(case.source, token).facts["status"] == "confirmation_unavailable"
    case.broker = restarted
    retry = restarted.materialize_source(case.source, _prepare(case))
    assert retry.ok and retry.changed == 1, retry
    assert len(case.downloaded) == 1  # only the second file was written by parent


def test_later_failure_preserves_prior_materialization_success_and_inventory_clock(case, monkeypatch):
    assert case.broker.materialize_source(case.source, _prepare(case)).ok
    phase = next(p for p in case.broker._ref(case.target.accounts[0]).phase_states if p.phase == "materialize")
    success = phase.last_success
    case.target._manifest.append({"product_id": "third", "revision_id": "r3", "title": "third", "format": "EPUB"})
    token = _prepare(case)
    monkeypatch.setattr(case.service, "kobo_adept_state", lambda **kwargs: {})
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.facts["status"] == "blocked" and result.changed == 0, result
    phase = next(p for p in case.broker._ref(case.target.accounts[0]).phase_states if p.phase == "materialize")
    assert phase.status == "blocked" and phase.last_success == success


def test_lock_path_failure_is_expurgated_at_the_public_boundary(case, monkeypatch):
    token = _prepare(case)
    def fail():
        raise PermissionError("PRIVATE state path")
    monkeypatch.setattr(materialize, "acquisition_lock_file", fail)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.facts["status"] == "materialization_failed"
    assert "PRIVATE" not in repr(result) and not case.downloaded


def test_new_local_candidate_after_preview_blocks_without_changing_original(case):
    token = _prepare(case)
    root = case.root / "EBooks/reader-2/kobo"
    # Use the real destination policy, then create a candidate bearing its
    # known filename. The preflight and executor must never overwrite it.
    item = case.target.normalize_item(case.target._manifest[0])
    path = case.service._kobo_destination(item, root, "epub")
    _write_epub(path)
    before = path.read_bytes()
    original = _dump(case)
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.facts["status"] == "reconciliation_required", result
    assert not case.downloaded and path.read_bytes() == before and _dump(case) == original


def test_final_phase_write_failure_does_not_hide_completed_files(case, monkeypatch):
    token = _prepare(case)
    original = SynchronizationBrokerImpl._set_materialization_state
    def state(self, source, status, attempted):
        if status == "success":
            raise OSError("PRIVATE status failure")
        original(self, source, status, attempted)
    monkeypatch.setattr(SynchronizationBrokerImpl, "_set_materialization_state", state)
    result = case.broker.materialize_source(case.source, token)
    assert result.ok and result.changed == 2 and result.facts["state_persistence_failed"]
    assert all(path.is_file() for path in case.downloaded) and "PRIVATE" not in repr(result)


@pytest.mark.parametrize("status", [[], {}, 42, "PRIVATE status"])
def test_untrusted_phase_projection_does_not_crash_or_echo_persisted_data(case, status):
    case.service.storage.metadata_set("sync.source.materialization." + case.source, json.dumps({
        "status": status, "last_attempt": "PRIVATE token", "last_success": ["PRIVATE path"]}))
    phases = case.broker._ref(case.target.accounts[0]).phase_states
    assert "PRIVATE" not in repr(phases)
    assert next(p for p in phases if p.phase == "materialize").last_success == ""


@pytest.mark.parametrize("field,value", [("record_id", "UNAPPROVED"), ("revision_id", "NEW"),
                                       ("source_credential", "vault-3"), ("owned", False), ("other_approved", None)])
def test_provider_normalization_cannot_change_the_confirmed_item_scope(case, field, value):
    token = _prepare(case)
    original = case.target.normalize_item
    def normalize(raw, **kwargs):
        result = original(raw, **kwargs)
        if field == "other_approved":
            # Even another approved book cannot replace this row's product.
            result.record_id = "PRIVATE-book-2" if raw["product_id"] == "PRIVATE-book-1" else "PRIVATE-book-1"
            result.revision_id = "r2" if raw["revision_id"] == "r1" else "r1"
        else:
            setattr(result, field, value)
        return result
    case.target.normalize_item = normalize
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.changed == 0 and not case.downloaded
    assert not any(s.resolve_calls for s in case.sessions)


def test_source_change_during_normalization_is_rejected_before_provider_resolution(case):
    token = _prepare(case)
    original = case.target.normalize_item
    def normalize(raw, **kwargs):
        result = original(raw, **kwargs)
        case.target.accounts[0].unique_id = "CHANGED"
        return result
    case.target.normalize_item = normalize
    result = case.broker.materialize_source(case.source, token)
    assert not result.ok and result.changed == 0 and not case.downloaded
    assert not any(s.resolve_calls for s in case.sessions)
