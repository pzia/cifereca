"""#2565: direct (noncached) local inspection must not outlive its SQLite image.

Synthetic EPUB/SQLite source changes only. Neither cached evidence nor a local
user statement is promoted to a provider entitlement. No network, private
diagnostics or implicit download and no unauthorised modification by Core.
"""
from __future__ import annotations

import json
import sqlite3

from cifereca.core import library_local_inspection as inspector
from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
from cifereca.core.library_local_inspection import LocalInspection, open_local_inspection
from tests.test_library_audit_broker import scoped
from tests.numilog_inspection_fixture import rig, _rows


def test_fresh_scan_revokes_on_followup_external_sqlite_commit(scoped):
    service, database, root = scoped
    session = open_local_inspection(service, identity_id="alice")
    assert isinstance(session, LocalInspection)
    case, = session.page().cases
    assert session.valid()
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO entitlements(entitlement_id, identity_id, state, provider_id)"
            " VALUES (?, ?, ?, ?)",
            ("PRIVATE-new-right", "alice", "active", "numilog"),
        )
    assert not session.valid()
    assert session.page().total == 0
    assert session.detail(case.key) is None
    assert not session._cases and not session._manual_right_proofs
    assert "PRIVATE" not in json.dumps(session.summary())
    second = open_local_inspection(service, identity_id="alice")
    assert isinstance(second, LocalInspection)
    assert second.valid()
    assert second.page(provider="numilog").total == 1
    second.close()


def test_private_projection_window_rejects_source_commit_during_case_building(
    scoped, monkeypatch,
):
    service, database, root = scoped
    original_constructor = inspector.LocalInspection
    triggered = []

    def race_after_projection(*args, **kwargs):
        new_session = original_constructor(*args, **kwargs)
        with sqlite3.connect(database) as db:
            db.execute(
                "INSERT INTO entitlements(entitlement_id, identity_id, state, provider_id)"
                " VALUES (?, ?, ?, ?)",
                ("PRIVATE-concurrent", "alice", "active", "kobo"),
            )
        triggered.append(True)
        return new_session

    monkeypatch.setattr(inspector, "LocalInspection", race_after_projection)
    result = inspector.open_local_inspection(service, identity_id="alice")
    assert triggered == [True]
    assert type(result) is dict
    assert result["status"] == "incomplete_scan"
    assert result["reasons"] == ["snapshot_changed"]
    assert "PRIVATE" not in json.dumps(result)
    assert str(root) not in json.dumps(result)
    monkeypatch.setattr(inspector, "LocalInspection", original_constructor)
    retry = open_local_inspection(service, identity_id="alice")
    assert isinstance(retry, LocalInspection)
    assert retry.page(provider="kobo").total == 1
    retry.close()


def test_direct_fresh_session_retired_numilog_link_is_readonly(rig):
    """Legacy Numilog attachment is fenced; local bytes and ledger stay intact."""
    service, book, _options = rig
    session = open_local_inspection(service, identity_id="default")
    assert isinstance(session, LocalInspection)
    assert session.from_audit_cache() is False and session.valid()
    rights = session.page(provider="numilog", state="rights_without_verified_file").cases
    files = session.unconfirmed_files(query="PRIVATE_edition", limit=10).cases
    assert len(rights) == len(files) == 1
    right, file = rights[0].key, files[0].key
    baseline = book.read_bytes()
    before = {name: _rows(service, name) for name in
              ("placements", "entitlements", "acquisition_ledger", "artifacts")}
    result = session.preview_manual_attachment(right, file)
    assert result == {"status": "blocked", "read_only": True,
                      "reason_code": "numilog_canonical_redownload_required"}
    assert session.confirm_manual_attachment(right, file, user_confirmed=True)["status"] == "unavailable"
    assert session.preview_manual_undo(right, file)["status"] == "unavailable"
    assert session.confirm_manual_undo(right, file, user_confirmed=True)["status"] == "unavailable"
    assert book.read_bytes() == baseline
    assert {name: _rows(service, name) for name in before} == before
    session.close()


def test_public_broker_still_emits_strictly_aggregate_report(scoped):
    service, _, root = scoped
    public = LibraryReconciliationReadBrokerImpl(service).audit(identity_id="alice")
    assert public["status"] == "complete"
    assert public["repair_authorized"] is False
    assert "PRIVATE" not in json.dumps(public)
    assert str(root) not in json.dumps(public)
