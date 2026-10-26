"""P0 #2797: user statements are visible on rights without attesting books."""
import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_declared_links import (
    _PREFIX, declare_local_link, withdraw_declared_link,
)
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_library_declared_links import rig


def _inspect(service, root):
    audit = audit_library(service.storage.path, {"default": (root,)})
    return LocalInspection(
        audit, lambda: True, "default", roots=(root,), service=service,
    )


@pytest.mark.parametrize("rig", ["kobo", "numilog"], indirect=True)
def test_manual_statement_is_a_separate_unverified_right_facet(rig):
    service, book, root, right, options = rig
    initial = _inspect(service, root)
    try:
        row, = initial.page(state="rights_without_verified_file").cases
        assert "declared_link_recorded" not in row.states
    finally:
        initial.close()

    preview = declare_local_link(service, **options)
    done = declare_local_link(service, confirmation=preview.plan_token, **options)
    assert done.status == "declared"
    try:
        for _ in range(2):
            service.storage.entitlement_put(right)
            inspection = _inspect(service, root)
            try:
                cases = inspection.page(
                    state="declared_link_recorded"
                ).cases
                rights = [case for case in cases if case.key.startswith("right:")]
                files = [case for case in cases if case.key.startswith("file:")]
                assert len(rights) == len(files) == 1
                row, = rights
                file_case, = files
                assert "rights_without_verified_file" in row.states
                assert "Association manuelle enregistrée" in row.label
                assert "Association déclarée par vous" in row.details
                assert "aucun reçu ni placement attesté" in row.details
                assert "Association enregistrée (à revérifier)" in file_case.label
                assert "Fichier et droit à revérifier" in file_case.details
                assert "confirmed_link" not in file_case.states
                assert inspection.overview()["confirmed_files"] == 0
                assert inspection.page(state="confirmed_link").total == 0
            finally:
                inspection.close()
        # The historical user statement survives disappearance of the EPUB,
        # but still NEVER becomes a fresh-file presence attestation.
        book.unlink()
        stale = _inspect(service, root)
        try:
            row, = stale.page(state="declared_link_recorded").cases
            assert "rights_without_verified_file" in row.states
            assert "restent à revérifier" in row.details
        finally:
            stale.close()
        undo = withdraw_declared_link(
            service, identity_id="default", relation_id=done.relation_id,
        )
        assert undo.status == "revoke_preview"
        reverted = withdraw_declared_link(
            service, identity_id="default", relation_id=done.relation_id,
            confirmation=undo.plan_token,
        )
        assert reverted.status == "revoked"
        after = _inspect(service, root)
        try:
            assert after.page(state="declared_link_recorded").total == 0
            assert after.page(state="rights_without_verified_file").total == 1
        finally:
            after.close()
    finally:
        assert not service.storage.placements()


@pytest.mark.parametrize("rig", ["paris"], indirect=True)
def test_inactive_loan_does_not_label_file_as_current_declaration(rig):
    service, _book, root, right, options = rig
    preview = declare_local_link(service, **options)
    assert declare_local_link(
        service, confirmation=preview.plan_token, **options,
    ).status == "declared"
    service.storage.entitlement_put({**right, "state": "expired"})
    inspection = _inspect(service, root)
    try:
        # Historical consent survives, but the closed loan is not an active
        # association and cannot mark a current file positively.
        assert inspection.page(state="declared_link_recorded").total == 0
        files = [case for case in inspection.page().cases
                 if case.key.startswith("file:")]
        assert len(files) == 1
        assert "declared_link_recorded" not in files[0].states
    finally:
        inspection.close()


@pytest.mark.parametrize("rig", ["kobo"], indirect=True)
def test_invalid_declaration_store_does_not_invent_displayed_links(rig):
    service, _book, root, _right, options = rig
    preview = declare_local_link(service, **options)
    assert declare_local_link(
        service, confirmation=preview.plan_token, **options,
    ).status == "declared"
    service.storage.metadata_set(_PREFIX + "f" * 32, "{private malformed")
    session = _inspect(service, root)
    try:
        assert session.page(state="declared_link_recorded").total == 0
        assert session.page(state="rights_without_verified_file").total == 1
    finally:
        session.close()
