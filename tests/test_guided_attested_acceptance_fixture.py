"""The GTK acceptance fixture uses the real proof/preview/commit/undo engine."""
import importlib.util
from pathlib import Path

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection


def _case(tmp_path):
    path = Path(__file__).resolve().parents[1] / 'e2e/library_declaration_acceptance.py'
    spec = importlib.util.spec_from_file_location('guided_attested_fixture', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    service, book, directory, digest = module._fixture(tmp_path, 'kobo', attested=True)
    service.storage.close()
    audit = audit_library(service.storage.path, {'default': (directory,)})
    assert audit.redacted()['status'] == 'complete'
    session = LocalInspection(audit, lambda: True, 'default', roots=(directory,),
                              service=service)
    right, = session.page(state='rights_without_verified_file').cases
    file, = session.unconfirmed_files(limit=10).cases
    return service, book, session, right.key, file.key


def _rows(service):
    with service.storage._lock, service.storage._connect() as db:
        return {table: db.execute('SELECT * FROM ' + table + ' ORDER BY 1').fetchall()
                for table in ('entitlements', 'acquisition_ledger', 'placements',
                              'catalog_entries', 'artifacts', 'backup_items')}


def test_gtk_fixture_historical_kobo_evidence_cannot_publish_positive_B(tmp_path):
    service, book, session, right, file = _case(tmp_path)
    try:
        before, contents = _rows(service), book.read_bytes()
        preflight = session.preflight_manual_link(right, file)
        assert preflight.status == 'eligible_for_fresh_validation'
        assert preflight.read_only and not preflight.attachment_authorized
        preview = session.preview_manual_attachment(right, file)
        assert preview == {
            'status': 'blocked', 'read_only': True,
            'reason_code': 'kobo_canonical_evidence_required',
        }
        assert session._manual_pending is None
        assert _rows(service) == before
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=True,
        )['status'] == 'unavailable'
        # Old GUI confirmation and undo coordinates cannot resurrect W4.
        session._manual_pending = (right, file, 'a' * 64)
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=True,
        )['status'] == 'blocked'
        session._manual_undo = (right, file, 'b' * 64)
        assert session.preview_manual_undo(right, file)['status'] == 'blocked'
        session._manual_undo_pending = (right, file, 'b' * 64, 'c' * 64)
        assert session.confirm_manual_undo(
            right, file, user_confirmed=True,
        )['status'] == 'blocked'
        assert _rows(service) == before
        assert service.storage.placement_for_path(book) is None
        assert book.read_bytes() == contents
    finally:
        session.close()
        service.close()


def test_changed_file_in_gtk_fixture_cannot_pass_fresh_preview(tmp_path):
    service, book, session, right, file = _case(tmp_path)
    try:
        before = _rows(service)
        book.write_bytes(b'synthetic replacement after the inventory')
        assert session.preview_manual_attachment(right, file)['status'] == 'blocked'
        assert _rows(service) == before
        assert service.storage.placement_for_path(book) is None
    finally:
        session.close()
        service.close()
