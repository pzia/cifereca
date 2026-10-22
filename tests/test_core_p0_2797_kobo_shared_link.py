"""P0 #2797: an inspected Kobo right must use Kobo's OWN locked receipt.

All books, accounts and SQLite are synthetic and local. The shared GUI sees
only closed statuses. No client API, token, path or hash crosses this boundary.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.core import kobo_local_reconciliation as kobo
from tests.test_core_1345_confirmed_local_reconciliation import case as kobo_case
from tests.test_library_reconciliation import epub, filesystem


@pytest.fixture
def selected(kobo_case, tmp_path, monkeypatch):
    # Convert the low-level hash fixture to two structurally valid EPUBs,
    # so the *real* scanner can provide independently checked observations.
    case = kobo_case
    epub(case.original, title="PRIVATE fictional title")
    case.target.write_bytes(case.original.read_bytes())
    sha = hashlib.sha256(case.target.read_bytes()).hexdigest()
    case.sha = sha
    case.options = {**case.options, "expected_sha256": sha}
    case.storage.backup_put({
        "provider_id": "kobo", "credential_id": "vault-1",
        "account_id": "PRIVATE-account", "identity_id": "default",
        "item_id": "PRIVATE-product", "revision_id": "r1",
        "title": "PRIVATE fictional title", "format": "epub",
        "local_path": str(case.original), "status": "reconcile_required",
        "size": case.target.stat().st_size, "sha256": sha,
        "entitlement_type": "owned",
    })
    scan = audit_library(case.storage.path, {"default": (case.target.parent,)})
    assert scan.redacted()["status"] == "complete", scan.redacted()["reasons"]
    session = LocalInspection(
        scan, lambda: True, "default",
        roots=(case.target.parent,), service=case.service,
    )
    rights = session.page(provider="kobo", state="rights_without_verified_file").cases
    assert len(rights) == 1
    files = session.unconfirmed_files(query="PRIVATE-copy", limit=10)
    assert files.total == 1
    yield case, session, rights[0], files.cases[0]
    session.close()


def _dump(case):
    with case.storage._lock, case.storage._connect() as db:
        return {table: db.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
                for table in (*kobo._KEYS, "entitlements", "artifacts")}


def test_kobo_shared_preview_confirm_and_undo_require_three_user_actions(selected):
    case, session, right, file = selected
    before = _dump(case)
    book_bytes = case.target.read_bytes()
    preview = session.preflight_manual_link(right.key, file.key)
    assert preview.status == "eligible_for_fresh_validation"
    assert preview.read_only and not preview.attachment_authorized
    assert "kobo_fresh_proof_required" in preview.codes
    # Clicking the preflight alone performs no IO to the writer.
    assert _dump(case) == before
    result = session.preview_manual_attachment(right.key, file.key)
    assert result == {
        "status": "preview", "read_only": True,
        "requires_explicit_confirmation": True,
    }
    assert "PRIVATE" not in json.dumps(result)
    assert _dump(case) == before
    # False/missing human confirmation consumes the private preview token.
    assert session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=False,
    )["status"] == "unavailable"
    assert _dump(case) == before
    assert session.preview_manual_attachment(right.key, file.key)["status"] == "preview"
    committed = session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )
    assert committed == {
        "status": "applied", "read_only": False, "undo_available": True,
    }
    assert _dump(case)["entitlements"] == before["entitlements"]
    assert _dump(case)["artifacts"] == before["artifacts"]
    assert case.target.read_bytes() == book_bytes
    assert session.preview_manual_undo(right.key, file.key)["status"] == "undo_preview"
    assert session.confirm_manual_undo(right.key, file.key, user_confirmed=False)["status"] == "unavailable"
    assert session.preview_manual_undo(right.key, file.key)["status"] == "undo_preview"
    undone = session.confirm_manual_undo(right.key, file.key, user_confirmed=True)
    assert undone["status"] == "undone"
    assert _dump(case) == before
    assert case.target.read_bytes() == book_bytes


@pytest.mark.parametrize("kind", ["changed_bytes", "right_revoked", "revision", "cancelled"])
def test_kobo_shared_stale_or_cancelled_confirmation_fails_closed(selected, kind):
    case, session, right, file = selected
    assert session.preview_manual_attachment(right.key, file.key)["status"] == "preview"
    if kind == "changed_bytes":
        case.target.write_bytes(b"changed bytes")
    elif kind in ("right_revoked", "revision"):
        with case.storage._lock, case.storage._connect() as db:
            db.execute(
                "UPDATE entitlements SET " + (
                    "state='revoked'" if kind == "right_revoked" else "revision_id='changed'"
                ) + " WHERE provider_id='kobo'"
            )
    before = _dump(case)
    outcome = session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
        cancelled=(lambda: kind == "cancelled"),
    )
    assert outcome["status"] == "blocked"
    assert _dump(case) == before


def test_kobo_shared_rejects_cross_provider_and_late_confirmation(selected):
    case, session, right, file = selected
    original = _dump(case)
    assert session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )["status"] == "unavailable"
    assert session.preview_manual_attachment(right.key, file.key)["status"] == "preview"
    session.revoke_manual_attachment_preview()
    assert session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )["status"] == "unavailable"
    assert _dump(case) == original
    session.close()
    assert session.preview_manual_attachment(right.key, file.key)["status"] == "unavailable"
    assert _dump(case) == original


def test_kobo_writer_stamp_callback_occurs_only_after_durable_commit(kobo_case):
    case = kobo_case
    callbacks = []
    preview = kobo.reconcile_kobo_local(case.service, **case.options,
                                        on_committed=lambda: callbacks.append("wrong"))
    assert preview["status"] == "preview" and not callbacks
    with pytest.raises(kobo.ReconciliationRejected, match="reconciliation_cancelled"):
        kobo.reconcile_kobo_local(
            case.service, **case.options,
            confirmation=preview["plan_token"],
            cancelled=lambda: True,
            on_committed=lambda: callbacks.append("wrong"),
        )
    assert not callbacks
    receipt = kobo.reconcile_kobo_local(
        case.service, **case.options, confirmation=preview["plan_token"],
        on_committed=lambda: callbacks.append("committed"),
    )
    assert receipt["status"] == "applied"
    assert callbacks == ["committed"]
