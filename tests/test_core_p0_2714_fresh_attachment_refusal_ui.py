"""#2714/#2797: exact Core refusals, never private exception prose in GTK.

The fixtures are synthetic; all supplier links, SQLite rights and EPUB bytes
are local. No request/download, plugin authority, or forged acquisition.
"""
from __future__ import annotations

import json

import pytest

from cifereca.core.library_attachment_refusal import (
    public_refusal_code, public_refusal_label,
)
from cifereca.core import kobo_local_reconciliation as kobo
from cifereca.core import numilog_manual_link_transaction as numilog
from cifereca.gui.library_inspection_view import _attachment_refusal_message
from tests.test_core_p0_2714_manual_session_bridge import selected as numilog_selected
from tests.test_core_p0_2797_kobo_shared_link import selected as kobo_selected
from tests.test_core_p0_2714_numilog_manual_transaction import (
    rig, _rows as numilog_rows,
)
from tests.test_core_1345_confirmed_local_reconciliation import case as kobo_case


_PRIVATE = "PRIVATE-account password=SECRET /home/private/BOOK.epub"


@pytest.mark.parametrize("provider,exception,expected", [
    ("numilog", numilog.ManualAttachmentRejected("purchase_provenance_unattested"),
     "purchase_provenance_unattested"),
    ("numilog", numilog.ManualAttachmentRejected("file_changed_before_commit"),
     "file_changed_before_commit"),
    ("kobo", kobo.ReconciliationRejected("backup_evidence_missing"),
     "backup_evidence_missing"),
    ("kobo", kobo.ReconciliationRejected("revision_unproven"),
     "revision_unproven"),
    ("kobo", kobo.ReconciliationRejected("file_hash_mismatch"),
     "file_hash_mismatch"),
])
def test_core_refusal_codes_are_closed_and_explained(provider, exception, expected):
    assert public_refusal_code(provider, exception) == expected
    response = {"status": "blocked", "read_only": True, "reason_code": expected}
    message = _attachment_refusal_message(response, provider, "Refus")
    assert message.startswith("Refus\nMotif vérifié : ")
    assert "PRIVATE" not in message
    assert "SECRET" not in message
    assert len(message) < 350
    assert public_refusal_label(provider, expected) in message


@pytest.mark.parametrize("exception", [
    numilog.ManualAttachmentRejected(_PRIVATE),
    numilog.ManualAttachmentRejected("FILE_CHANGED"),
    numilog.ManualAttachmentRejected({"PRIVATE": _PRIVATE}),
    numilog.ManualAttachmentRejected("source_scope_changed", _PRIVATE),
    kobo.ReconciliationRejected(_PRIVATE),
    kobo.ReconciliationRejected(True),
    RuntimeError(_PRIVATE),
])
def test_unknown_private_exceptions_never_appear_in_ui(exception):
    for provider in ("numilog", "kobo", "paris", _PRIVATE):
        assert public_refusal_code(provider, exception) is None
        assert _attachment_refusal_message(
            {"status": "blocked", "read_only": True, "reason_code": _PRIVATE},
            provider, "Refus",
        ) == "Refus"
    assert _PRIVATE not in repr(public_refusal_label("kobo", _PRIVATE))


def test_a_plugin_or_subclass_cannot_forge_core_refusal():
    class PluginSpoof(numilog.ManualAttachmentRejected):
        pass
    assert public_refusal_code(
        "numilog", PluginSpoof("purchase_provenance_unattested"),
    ) is None
    assert public_refusal_code(
        "kobo", numilog.ManualAttachmentRejected("acquisition_busy"),
    ) is None
    for malformed in (
        {"status": "blocked", "read_only": True, "reason_code": _PRIVATE},
        {"status": "blocked", "read_only": False,
         "reason_code": "purchase_provenance_unattested"},
        {"status": "preview", "read_only": True,
         "reason_code": "purchase_provenance_unattested"},
        {"status": "blocked", "read_only": True,
         "reason_code": ["purchase_provenance_unattested"]},
    ):
        assert _attachment_refusal_message(
            malformed, "numilog", "Refus",
        ) == "Refus"


def test_numilog_fresh_preview_reports_missing_receipt_code_without_mutation(
    numilog_selected, monkeypatch,
):
    session, right, file, service = numilog_selected
    before = numilog_rows(service, "placements")

    def refusal(*_args, **_kwargs):
        raise numilog.ManualAttachmentRejected("purchase_provenance_unattested")

    monkeypatch.setattr(numilog, "reconcile_numilog_manual", refusal)
    response = session.preview_manual_attachment(right, file)
    assert response == {
        "status": "blocked", "read_only": True,
        "reason_code": "purchase_provenance_unattested",
    }
    assert session._manual_pending is None
    assert numilog_rows(service, "placements") == before
    message = _attachment_refusal_message(
        response, "numilog", "Placement non effectué.",
    )
    assert "L’achat Numilog" in message
    assert _PRIVATE not in json.dumps(response) + message


def test_kobo_fresh_preview_reports_revision_code_without_mutation(
    kobo_selected, monkeypatch,
):
    case, session, right, file = kobo_selected
    with case.storage._lock, case.storage._connect() as db:
        before = db.execute("SELECT * FROM placements ORDER BY placement_id").fetchall()

    def refusal(*_args, **_kwargs):
        raise kobo.ReconciliationRejected("revision_unproven")

    monkeypatch.setattr(kobo, "reconcile_kobo_local", refusal)
    response = session.preview_manual_attachment(right.key, file.key)
    assert response == {
        "status": "blocked", "read_only": True,
        "reason_code": "revision_unproven",
    }
    assert session._manual_pending is None
    # Read-only refusal can neither modify rights nor write a placement.
    with case.storage._lock, case.storage._connect() as db:
        assert db.execute("SELECT * FROM placements ORDER BY placement_id").fetchall() == before


def test_numilog_confirmation_reports_fresh_evidence_failure_and_consumes_token(
    numilog_selected, monkeypatch,
):
    session, right, file, service = numilog_selected
    assert session.preview_manual_attachment(right, file)["status"] == "preview"

    def refusal(*_args, **_kwargs):
        raise numilog.ManualAttachmentRejected("file_changed_before_commit")

    monkeypatch.setattr(numilog, "reconcile_numilog_manual", refusal)
    result = session.confirm_manual_attachment(right, file, user_confirmed=True)
    assert result == {
        "status": "blocked", "read_only": True,
        "reason_code": "file_changed_before_commit",
    }
    assert session._manual_pending is None
    assert session.confirm_manual_attachment(right, file, user_confirmed=True)[
        "status"
    ] == "unavailable"
    assert not numilog_rows(service, "placements")


def test_kobo_confirmation_reports_refused_acquisition_without_authorization(
    kobo_selected, monkeypatch,
):
    case, session, right, file = kobo_selected
    assert session.preview_manual_attachment(right.key, file.key)["status"] == "preview"
    with case.storage._lock, case.storage._connect() as db:
        before = db.execute("SELECT * FROM placements ORDER BY placement_id").fetchall()

    def refusal(*_args, **_kwargs):
        raise kobo.ReconciliationRejected("acquisition_busy")

    monkeypatch.setattr(kobo, "reconcile_kobo_local", refusal)
    result = session.confirm_manual_attachment(
        right.key, file.key, user_confirmed=True,
    )
    assert result == {
        "status": "blocked", "read_only": True,
        "reason_code": "acquisition_busy",
    }
    assert session._manual_pending is None
    with case.storage._lock, case.storage._connect() as db:
        assert db.execute("SELECT * FROM placements ORDER BY placement_id").fetchall() == before
