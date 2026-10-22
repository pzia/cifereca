"""P0 #2841: bad UNRELATED EPUBs must not veto a sound manual pair.

The full SQLite proof graph and all scanned-file traversal remain required.
Each writer independently revalidates exact receipt, rights and nofollow bytes
under lock before any consented commit. Fixtures are synthetic and offline.
"""
from __future__ import annotations

from dataclasses import replace
import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2714_numilog_manual_transaction import rig, _rows
from tests.test_core_p0_2797_kobo_shared_link import selected as kobo_selected
from tests.test_library_reconciliation import epub, filesystem


def _numilog(rig, *, altered=None):
    service, book, _ = rig
    root = book.parent
    audit = audit_library(service.storage.path, {'default': (root,)})
    if altered:
        altered(audit)
    session = LocalInspection(
        audit, lambda: True, 'default', roots=(root,), service=service,
    )
    rights = session.page(provider='numilog', state='rights_without_verified_file').cases
    files = session.unconfirmed_files(query='PRIVATE_edition', limit=10)
    assert len(rights) == 1 and files.total == 1
    return audit, session, rights[0].key, files.cases[0].key


def _bad_files(root):
    # One isolated EPUB has invalid OPF/ZIP metadata. The chosen book is
    # independent and passes all local SHA, OPF, structure and CRC checks.
    (root / 'synthetic-bad-other.epub').write_bytes(b'NOT_AN_EPUB')


def test_numilog_bad_unrelated_epub_does_not_block_precisely_attested_pair(rig):
    service, book, _ = rig
    _bad_files(book.parent)
    audit, session, right, file = _numilog(rig)
    try:
        assert audit.redacted()['status'] == 'incomplete_scan'
        assert 'epub_parser_limit_or_invalid' in audit.inventory.reasons
        preflight = session.preflight_manual_link(right, file)
        assert preflight.status == 'eligible_for_fresh_validation'
        assert preflight.read_only and not preflight.attachment_authorized
        assert 'global_partial_pair_reviewable' in preflight.codes
        assert 'audit_incomplete' not in preflight.codes
        assert 'PRIVATE' not in preflight.explanation
        before = _rows(service, 'placements')
        assert session.preview_manual_attachment(right, file) == {
            'status': 'preview', 'read_only': True,
            'requires_explicit_confirmation': True,
        }
        assert _rows(service, 'placements') == before
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=False,
        )['status'] == 'unavailable'
        assert _rows(service, 'placements') == before
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=True,
        )['status'] == 'applied'
        assert len(_rows(service, 'placements')) == len(before) + 1
        assert session.preview_manual_undo(right, file)['status'] == 'undo_preview'
        assert session.confirm_manual_undo(
            right, file, user_confirmed=True,
        )['status'] == 'undone'
        assert _rows(service, 'placements') == before
    finally:
        session.close()


def test_600_epubs_bad_unrelated_and_three_crc_only_incidents_keep_local_scope(rig):
    _service, book, _ = rig
    root = book.parent
    # The synthetic medium-sized corpus has 600 files including the selected
    # EPUB. Substituting bad parsing/CRC observations mirrors the completed
    # scanner's own closed per-file validation codes; none is the target file.
    for i in range(598):
        epub(root / f'other-{i:03d}.epub', title=f'Synthetic other {i}')
    _bad_files(root)
    audit, session, right, file = _numilog(rig)
    try:
        assert len(audit.inventory.files) == 600
        assert audit.inventory.files_started == 600
        assert audit.redacted()['status'] == 'incomplete_scan'
        assert 'epub_parser_limit_or_invalid' in audit.inventory.reasons
        assert session.preflight_manual_link(right, file).status == 'eligible_for_fresh_validation'
    finally:
        session.close()
    # Three additional CRC invalid outcomes are local evidence defects,
    # not a missing right or an incomplete SQLite/WAL / directory traversal.
    def three_crc_incidents(audit):
        from cifereca.core.library_reconciliation import reconcile
        seen = 0
        for i, observation in enumerate(audit.inventory.files):
            if observation.path.endswith('.epub') and 'other-' in observation.path:
                audit.inventory.files[i] = replace(
                    observation, structure_validation='structure_limit_or_invalid',
                )
                seen += 1
                if seen == 3:
                    break
        assert seen == 3
        audit.inventory.reasons.add('structure_limit_or_invalid')
        audit.reconciliation = reconcile(audit.inventory, audit.snapshot)
    audit, session, right, file = _numilog(rig, altered=three_crc_incidents)
    try:
        assert {'epub_parser_limit_or_invalid', 'structure_limit_or_invalid'} <= audit.inventory.reasons
        report = session.preflight_manual_link(right, file)
        assert report.status == 'eligible_for_fresh_validation'
        assert 'global_partial_pair_reviewable' in report.codes
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        assert session.confirm_manual_attachment(right, file, user_confirmed=False)['status'] == 'unavailable'
        assert not _rows(_service, 'placements')
    finally:
        session.close()


@pytest.mark.parametrize('reason,section', [
    ('entry_budget', 'inventory'),
    ('file_budget', 'inventory'),
    ('hash_byte_budget', 'inventory'),
    ('directory_changed', 'inventory'),
    ('entry_unavailable', 'inventory'),
    ('audit_time_budget', 'audit'),
    ('snapshot_row_budget', 'snapshot'),
    ('snapshot_changed', 'snapshot'),
    ('comparison_budget', 'reconciliation'),
    ('stored_path_budget', 'stored_paths'),
])
def test_global_integrity_cannot_be_overridden_by_an_attested_pair(rig, reason, section):
    _bad_files(rig[1].parent)
    def sabotage(audit):
        target = audit if section == 'audit' else getattr(audit, section)
        target.reasons.add(reason)
    audit, session, right, file = _numilog(rig, altered=sabotage)
    try:
        assert audit.redacted()['status'] == 'incomplete_scan'
        report = session.preflight_manual_link(right, file)
        assert report.status == 'blocked'
        assert 'audit_incomplete' in report.codes
        assert session.preview_manual_attachment(right, file) == {
            'status': 'unavailable', 'read_only': True,
        }
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()


def test_missing_receipt_and_changed_selected_bytes_are_not_waived(rig):
    _bad_files(rig[1].parent)
    audit, session, right, file = _numilog(rig)
    try:
        # Revalidation is authoritative, not a cached read-only preview.
        assert session.preflight_manual_link(right, file).status == 'eligible_for_fresh_validation'
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        rig[1].write_bytes(b'modified-and-invalid')
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=True,
        )['status'] != 'applied'
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()
    # No complete ledger for another entitlement is ever fabricated.
    with rig[0].storage._lock, rig[0].storage._connect() as db:
        db.execute("DELETE FROM acquisition_ledger WHERE entitlement_id='private-right'")
    audit, session, right, file = _numilog(rig)
    try:
        reviewed = session.preflight_manual_link(right, file)
        assert reviewed.status != 'eligible_for_fresh_validation'
        assert 'acquisition_missing' in reviewed.codes
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()


def test_nonprobative_bibliographic_budget_does_not_block_valid_receipt(rig):
    def bibliographic_partial(audit):
        audit.metadata_candidates.reasons.add('metadata_candidate_time_budget')
    audit, session, right, file = _numilog(rig, altered=bibliographic_partial)
    try:
        assert audit.redacted()['status'] == 'incomplete_scan'
        preflight = session.preflight_manual_link(right, file)
        assert preflight.status == 'eligible_for_fresh_validation'
        assert 'global_partial_pair_reviewable' in preflight.codes
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()


def test_live_account_conflict_after_safe_preview_blocks_commit(rig):
    _bad_files(rig[1].parent)
    _audit, session, right, file = _numilog(rig)
    try:
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        # The account-scoped acquisition receipt changes AFTER the audit and
        # preview, but before the human clicks confirmation.
        with rig[0].storage._lock, rig[0].storage._connect() as db:
            db.execute(
                "UPDATE acquisition_ledger SET account_id='PRIVATE_FOREIGN' "
                "WHERE entitlement_id='private-right'"
            )
        result = session.confirm_manual_attachment(
            right, file, user_confirmed=True,
        )
        assert result['status'] != 'applied'
        assert not _rows(rig[0], 'placements')
        assert 'PRIVATE_FOREIGN' not in json.dumps(result)
    finally:
        session.close()


def test_kobo_requires_historical_backup_but_ignores_other_invalid_epub(kobo_selected):
    case, original, _right, _file = kobo_selected
    root = case.target.parent
    _bad_files(root)
    scan = audit_library(case.storage.path, {'default': (root,)})
    assert scan.redacted()['status'] == 'incomplete_scan'
    session = LocalInspection(
        scan, lambda: True, 'default', roots=(root,), service=case.service,
    )
    try:
        rights = session.page(provider='kobo', state='rights_without_verified_file').cases
        files = session.unconfirmed_files(query='PRIVATE-copy', limit=10)
        assert len(rights) == 1 and files.total == 1
        right, file = rights[0].key, files.cases[0].key
        report = session.preflight_manual_link(right, file)
        assert report.status == 'eligible_for_fresh_validation'
        assert 'global_partial_pair_reviewable' in report.codes
        before = filesystem(root)
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        assert session.confirm_manual_attachment(
            right, file, user_confirmed=False,
        )['status'] == 'unavailable'
        assert filesystem(root) == before
        assert 'PRIVATE' not in json.dumps({
            'status': report.status, 'codes': report.codes,
        })
    finally:
        session.close()
