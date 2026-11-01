"""#2847: Core private inspection binds only actual path/file cases to repair."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from cifereca.core import canonical_recovery
from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.storage import Storage
from tests.test_library_reconciliation import epub


class Config:
    def __init__(self, root):
        self.root = str(root)

    def get(self, key, default=None):
        return self.root if key == "library.directory" else default


class Service:
    def __init__(self, storage, root):
        self.storage = storage
        self.config = Config(root)
        self.current_identity = SimpleNamespace(identity_id="alice")

    def identity_by_id(self, identity_id):
        return self.storage.identity_get(identity_id)


@pytest.fixture
def inspected(tmp_path, monkeypatch):
    root = tmp_path / "library"
    owner = root / "alice"
    owner.mkdir(parents=True)
    candidate = owner / "found.epub"
    epub(candidate, title="Synthetic observed EPUB")
    old = owner / "missing.epub"
    data = candidate.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    storage = Storage(tmp_path / "db" / "books.sqlite3")
    storage.identity_put({
        "identity_id": "alice", "directory_name": "alice", "label": "Alice",
    })
    artifact_id = "sha256:" + digest
    storage.artifact_put({
        "artifact_id": artifact_id, "sha256": digest, "size": len(data),
        "media_format": "epub",
    })
    pid = storage.placement_put({
        "path": str(old), "identity_id": "alice",
        "provider_id": "kobo", "source_item_id": "book-a",
        "artifact_id": artifact_id, "state": "missing", "metadata": {},
    })
    lock = tmp_path / "acquisition.lock"
    monkeypatch.setattr(canonical_recovery, "acquisition_lock_file", lambda: lock)
    audit = audit_library(storage.path, {"alice": (owner,)})
    service = Service(storage, root)
    session = LocalInspection(
        audit, lambda: True, "alice", roots=(owner,), service=service,
    )
    yield storage, service, session, old, candidate, pid
    session.close()
    storage.close()


def _cases(session):
    path_key, = session._missing_case_placements
    file_key = next(iter(session._manual_file_observations))
    assert path_key.startswith("path:")
    assert file_key.startswith("file:")
    return path_key, file_key


def test_inspected_retirement_is_explicit_and_keeps_physical_copy(inspected):
    storage, service, session, old, candidate, pid = inspected
    assert session._manual_audit_complete
    path_key, file_key = _cases(session)
    assert session._missing_case_placements[path_key] == pid
    preview = session.preview_missing_retirement(path_key)
    assert preview["status"] == "ready"
    assert "plan_token" not in preview
    assert str(old) not in str(preview)
    assert session.confirm_missing_repair(path_key)["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"
    assert session.preview_missing_retirement(path_key)["status"] == "ready"
    result = session.confirm_missing_repair(path_key, user_confirmed=True)
    assert result["status"] == "retired"
    assert storage.placement_get(pid) is None
    assert candidate.is_file()
    assert not old.exists()
    assert session.preview_missing_retirement(path_key)["status"] == "blocked"


def test_inspected_relocation_rehashes_selected_file_and_restores_state(inspected):
    storage, service, session, old, candidate, pid = inspected
    path_key, file_key = _cases(session)
    preview = session.preview_missing_relocation(path_key, file_key)
    assert preview["status"] == "review_required"
    assert preview["requires_explicit_confirmation"]
    assert str(candidate) not in str(preview)
    assert "plan_token" not in str(preview)
    result = session.confirm_missing_repair(
        path_key, file_key=file_key, user_confirmed=True,
    )
    assert result["status"] == "relocated"
    assert storage.placement_get(pid)["path"] == str(candidate)
    assert storage.placement_get(pid)["state"] == "present"
    assert candidate.exists() and not old.exists()


def test_unobserved_cases_files_or_changed_selection_cannot_commit(inspected):
    storage, service, session, old, candidate, pid = inspected
    path_key, file_key = _cases(session)
    assert session.preview_missing_retirement("path:forged")["status"] == "blocked"
    assert session.preview_missing_relocation(path_key, "file:forged")["status"] == "blocked"
    assert session.preview_missing_relocation(path_key, file_key)["status"] == "review_required"
    assert session.confirm_missing_repair(
        path_key, file_key="file:forged", user_confirmed=True,
    )["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"
    assert session.preview_missing_retirement(path_key)["status"] == "ready"
    session.revoke_manual_attachment_preview()
    assert session.confirm_missing_repair(
        path_key, user_confirmed=True,
    )["reason"] == "confirmation_required"
    assert storage.placement_get(pid)["state"] == "missing"


def test_inspected_context_switch_is_a_strict_veto(inspected):
    storage, service, session, old, candidate, pid = inspected
    path_key, _file_key = _cases(session)
    assert session.preview_missing_retirement(path_key)["status"] == "ready"
    service.current_identity.identity_id = "bob"
    assert session.confirm_missing_repair(
        path_key, user_confirmed=True,
    )["status"] == "blocked"
    assert storage.placement_get(pid)["state"] == "missing"
