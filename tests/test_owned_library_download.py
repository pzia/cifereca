"""Library-owned Kobo target: real storage/engine, synthetic provider/runtime."""
import hashlib
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca import services
from cifereca.core.owned_library_download import OwnedDownloadRejected
from cifereca.locking import InterProcessLock
from tests.test_kobo_backup_fault_isolation import _service, _ready_adept_state, _write_epub


@pytest.fixture
def case(tmp_path, monkeypatch):
    service, session = _service(tmp_path, [])
    service.accounts = list(session.accounts)
    right = {"entitlement_id": "right-1", "provider_id": "kobo", "credential_id": "vault-1",
             "account_id": "account", "item_id": "book-1", "identity_id": "default",
             "revision_id": "r1", "entitlement_type": "owned", "state": "active",
             "metadata": {"title": "Fictional owned book", "media_format": "epub"}}
    service.storage.entitlement_put(right)
    entry = {"local": False, "state": "remote_owned", "possessions": [{
        "identity_id": "default", "provider_id": "kobo", "item_id": "book-1",
        "kind": "owned", "state": "active", "entitlement_id": "right-1"}]}
    calls = []
    monkeypatch.setattr(services, "acquisition_lock_file", lambda: tmp_path / "acquisition.lock")
    monkeypatch.setattr(service, "kobo_adept_state", lambda **kw: _ready_adept_state())
    def execute(request):
        calls.append(request)
        target = Path(request["destination"])
        _write_epub(target)
        return {"ok": True, "operation": "download", "artifact": {"path": str(target), "format": "epub",
            "drm": "none", "size": target.stat().st_size, "sha256": hashlib.sha256(target.read_bytes()).hexdigest()}}
    monkeypatch.setattr(service, "execute_acquisition_request", execute)
    monkeypatch.setattr(session, "backup_manifest", lambda: pytest.fail("No source batch/manifest"))
    yield NS(service=service, session=session, right=right, entry=entry, calls=calls, root=tmp_path)
    service.close()


def test_passive_target_then_exact_single_acquisition_and_index(case):
    before = case.service.storage.entitlements()
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None and not case.calls and not case.session.resolve_calls
    assert case.service.storage.entitlements() == before
    assert "vault-1" not in repr(target) and "book-1" not in repr(target)
    result = case.service.obtain_owned_library_item(target)
    assert result.path.is_file() and result.identity_id == "default"
    assert result.indexed["status"] == "indexed" and len(case.calls) == 1
    assert all(product == "book-1" for product, mode in case.session.resolve_calls)
    assert case.service.owned_library_download_target(case.entry) is None
    with pytest.raises(OwnedDownloadRejected):
        case.service.obtain_owned_library_item(target)
    assert len(case.calls) == 1


@pytest.mark.parametrize("field,value", [("state", "revoked"), ("identity_id", "another"), ("revision_id", "new"),
                                       ("account_id", "other"), ("entitlement_type", "loan"),
                                       ("expires_at", "2030-01-01"), ("loan_id", "loan")])
def test_stale_right_cannot_download_a_new_revision_or_another_owners_book(case, field, value):
    target = case.service.owned_library_download_target(case.entry)
    # The normal upsert deliberately keeps account/type immutable. Exercise
    # the defensive read boundary against an independently changed row.
    with case.service.storage._lock, case.service.storage._connect() as db:
        db.execute(f"UPDATE entitlements SET {field}=? WHERE entitlement_id='right-1'", (value,))
    with pytest.raises(OwnedDownloadRejected, match="owned_download_unavailable"):
        case.service.obtain_owned_library_item(target)
    assert not case.calls and not case.session.resolve_calls


@pytest.mark.parametrize("mutation", ["selection", "disabled", "disconnected", "directory", "migration"])
def test_source_context_changes_are_revalidated_before_acquisition(case, monkeypatch, mutation):
    target = case.service.owned_library_download_target(case.entry)
    if mutation == "selection":
        case.service.current_identity = NS(identity_id="another")
    elif mutation == "disabled":
        case.service.storage.metadata_set("sync.source.enabled." + target.source_id, "0")
    elif mutation == "disconnected":
        case.session.connected = False
    elif mutation == "directory":
        case.service.config.set("library.directory", str(case.root / "elsewhere"))
    elif mutation == "migration":
        case.service.storage.metadata_set("identity-directory-migration-v1", "pending")
    with pytest.raises(OwnedDownloadRejected):
        case.service.obtain_owned_library_item(target)
    assert not case.calls and not case.session.resolve_calls


def test_admission_runs_inside_the_real_acquisition_lock(case, monkeypatch):
    target = case.service.owned_library_download_target(case.entry)
    class RevokeOnAcquire(InterProcessLock):
        def acquire(self):
            super().acquire()
            case.service.storage.entitlement_put({**case.right, "state": "revoked"})
    monkeypatch.setattr(services, "InterProcessLock", RevokeOnAcquire)
    with pytest.raises(OwnedDownloadRejected):
        case.service.obtain_owned_library_item(target)
    assert not case.calls and not case.session.resolve_calls


@pytest.mark.parametrize("mutation", ["absent", "two_possessions", "local", "unknown_owner", "duplicate_right"])
def test_ambiguous_missing_or_already_local_provenance_never_offers_a_target(case, mutation):
    if mutation == "absent":
        case.entry["possessions"] = []
    elif mutation == "two_possessions":
        case.entry["possessions"] *= 2
    elif mutation == "local":
        case.entry["local"] = True
    elif mutation == "unknown_owner":
        case.entry["possessions"][0]["identity_id"] = "another"
    elif mutation == "duplicate_right":
        case.service.storage.entitlement_put({**case.right, "entitlement_id": "right-2", "revision_id": "r2"})
    assert case.service.owned_library_download_target(case.entry) is None
    assert not case.calls and not case.session.resolve_calls



@pytest.mark.parametrize("status", [
    "reconcile_required", "reconcile_conflict", "attention_required",
    "complete", "acquiring", "downloading",
])
def test_historical_backup_status_without_exact_file_cannot_veto_explicit_get(
    case, status,
):
    # A legacy receipt can be complete while its historical local_path has
    # been moved/deleted. Only a verified current Core Placement could veto.
    # Simulate a retained historical v0.7 row next to an independently
    # observed canonical provider entitlement. Calling backup_put() would
    # *also* create a second legacy-mirrored active entitlement, and that
    # ambiguity is correctly a separate hard refusal.
    with case.service.storage._lock, case.service.storage._connect() as sql:
        sql.execute(
            "INSERT INTO backup_items(provider_id,credential_id,item_id,"
            "revision_id,title,local_path,status,updated_at,account_id,"
            "identity_id,entitlement_type) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            ("kobo", "vault-1", "book-1", "r1", "Synthetic",
             str(case.root / "vanished" / "old.epub"), status,
             123, "account", "default", "owned"),
        )
    assert not (case.root / "vanished" / "old.epub").exists()
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    # The target still needs the fresh account/entitlement context at
    # dispatch and inside the inter-process acquisition lock.
    result = case.service.obtain_owned_library_item(target)
    assert result.path.is_file()
    assert len(case.calls) == 1


def test_unlinked_different_book_in_profile_never_vetoes_this_exact_source(
    case, monkeypatch,
):
    calls = []
    monkeypatch.setattr(
        case.service, "kobo_unlinked_local_count",
        lambda _identity: calls.append(True) or 99,
    )
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    result = case.service.obtain_owned_library_item(target)
    assert result.path.is_file() and len(case.calls) == 1
    assert calls == []  # No unrelated global legacy presence probe.


def test_local_exact_published_placement_still_blocks_repeat_download(case):
    target = case.service.owned_library_download_target(case.entry)
    assert target is not None
    case.service.obtain_owned_library_item(target)
    assert len(case.calls) == 1
    assert case.service.owned_library_download_target(case.entry) is None
    with pytest.raises(OwnedDownloadRejected):
        case.service.obtain_owned_library_item(target)
    assert len(case.calls) == 1
