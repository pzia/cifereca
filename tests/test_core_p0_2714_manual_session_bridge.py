"""#2714: CoreUI private bridge cannot leak or replay a selected-pair token."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2714_numilog_manual_transaction import rig, _rows


@pytest.fixture
def selected(rig):
    service, book, _options = rig
    root = book.parent
    audit = audit_library(
        Path(service.storage.path), {"default": (root,)},
    )
    assert audit.redacted()["status"] == "complete"
    session = LocalInspection(
        audit, lambda: True, "default", roots=(root,), service=service,
    )
    right = session.page(state="rights_without_verified_file",
                         provider="numilog").cases
    files = session.unconfirmed_files(query="PRIVATE_edition", limit=5)
    assert len(right) == 1 and files.total == 1
    yield session, right[0].key, files.cases[0].key, service
    session.close()


def test_private_previews_hide_plan_tokens_and_require_second_explicit_action(selected):
    session, right, file, service = selected
    local = session.preflight_manual_link(right, file)
    assert local.status == "eligible_for_fresh_validation"
    before = _rows(service, "placements")
    view = session.preview_manual_attachment(right, file)
    assert view == {
        "status": "preview", "read_only": True,
        "requires_explicit_confirmation": True,
    }
    assert _rows(service, "placements") == before
    assert "plan_token" not in view
    refused = session.confirm_manual_attachment(right, file, user_confirmed=False)
    assert refused["status"] == "unavailable"
    assert not _rows(service, "placements")
    # Explicit retry and then explicit consent for the *same selected pair*.
    assert session.preview_manual_attachment(right, file)["status"] == "preview"
    applied = session.confirm_manual_attachment(right, file, user_confirmed=True)
    assert applied["status"] == "applied"
    # The opaque journal reference is retained inside Core for undo, not
    # returned through the CoreUI/GTK response.
    assert applied["undo_available"] is True
    assert "operation_id" not in applied
    assert session._manual_undo is not None
    assert "PRIVATE" not in json.dumps(applied)
    assert len(_rows(service, "placements")) == 1
    assert session.confirm_manual_attachment(right, file,
                                             user_confirmed=True)["status"] == "unavailable"


def test_selection_change_or_close_revoke_pending_placement_confirmation(selected):
    session, right, file, service = selected
    assert session.preview_manual_attachment(right, file)["status"] == "preview"
    session.revoke_manual_attachment_preview()
    assert session.confirm_manual_attachment(right, file,
                                             user_confirmed=True)["status"] == "unavailable"
    assert not _rows(service, "placements")
    assert session.preview_manual_attachment(right, file)["status"] == "preview"
    session.close()
    assert session.confirm_manual_attachment(right, file,
                                             user_confirmed=True)["status"] == "unavailable"
    assert not _rows(service, "placements")
    assert session._manual_service is None
    assert session._manual_file_observations == {}
    assert session._manual_right_scopes == {}


def test_wrong_pair_and_missing_service_fail_without_exposing_receipt(selected, rig):
    session, right, file, service = selected
    assert session.preview_manual_attachment(file, right)["status"] == "unavailable"
    assert session.preview_manual_attachment(right, "file:999999")["status"] == "unavailable"
    assert not _rows(service, "placements")
    # Recreated private projection without the service capability remains
    # inspection-only. A mere summary is not a mutation authority.
    audit = audit_library(Path(service.storage.path),
                          {"default": (rig[1].parent,)})
    inert = LocalInspection(audit, lambda: True, "default",
                            roots=(rig[1].parent,))
    assert inert.preview_manual_attachment(right, file)["status"] == "unavailable"
    inert.close()


def test_inflight_preview_revoked_by_selection_change_never_retains_token(
    selected, monkeypatch,
):
    from cifereca.core import numilog_manual_link_transaction as engine

    session, right, file, service = selected
    original = engine.reconcile_numilog_manual

    def revoke_while_running(*args, **kwargs):
        outcome = original(*args, **kwargs)
        session.revoke_manual_attachment_preview()
        return outcome

    monkeypatch.setattr(engine, "reconcile_numilog_manual", revoke_while_running)
    assert session.preview_manual_attachment(right, file)["status"] == "unavailable"
    assert session._manual_pending is None
    assert not _rows(service, "placements")
