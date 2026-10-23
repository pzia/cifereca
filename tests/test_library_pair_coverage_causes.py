"""#2841: explain the exact attested-pair gate, without relaxing it."""
from dataclasses import replace

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import (
    LocalInspection, _pair_evidence_gaps, _pair_evidence_complete,
)
from tests.test_core_p0_2714_numilog_manual_transaction import rig, _rows
from tests.test_core_p0_2841_isolated_partial_pair import _numilog, _bad_files
from tests.test_core_1345_confirmed_local_reconciliation import case as kobo_case
from tests.test_core_p0_2797_kobo_shared_link import selected as kobo_selected


@pytest.mark.parametrize('section,reason,code,label', [
    ('snapshot', 'snapshot_row_budget', 'snapshot_incomplete', 'SQLite/WAL'),
    ('snapshot', 'snapshot_changed', 'snapshot_incomplete', 'SQLite/WAL'),
    ('inventory', 'entry_budget', 'inventory_incomplete', 'Parcours'),
    ('inventory', 'directory_changed', 'inventory_incomplete', 'Parcours'),
    ('reconciliation', 'comparison_budget', 'comparison_incomplete', 'Comparaison'),
    ('stored_paths', 'stored_path_budget', 'path_coverage_incomplete', 'chemins'),
    ('audit', 'audit_time_budget', 'audit_scope_incomplete', 'budget global'),
])
def test_one_missing_coverage_phase_is_named_and_still_blocks_writer(rig, section, reason, code, label):
    def altered(audit):
        (audit if section == 'audit' else getattr(audit, section)).reasons.add(reason)
    audit, session, right, file = _numilog(rig, altered=altered)
    try:
        assert _pair_evidence_gaps(audit) == (code,)
        assert not _pair_evidence_complete(audit)
        report = session.preflight_manual_link(right, file)
        assert report.status == 'blocked' and 'audit_incomplete' in report.codes
        assert code in report.codes and label in report.explanation
        assert report.read_only and not report.attachment_authorized
        assert 'PRIVATE' not in report.explanation
        assert session.preview_manual_attachment(right, file)['status'] == 'unavailable'
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()
    assert session._manual_pair_coverage_gaps == ()


@pytest.mark.parametrize('state', [
    'structure_byte_or_time_budget', 'structure_byte_or_entry_budget', 'structure_cancelled',
])
def test_shared_crc_budget_is_distinct_from_unrelated_invalid_epub(rig, state):
    def altered(audit):
        audit.inventory.files[0] = replace(audit.inventory.files[0], structure_validation=state)
        audit.inventory.reasons.add('structure_limit_or_invalid')
        audit.reconciliation.reasons.add('structure_limit_or_invalid')
    audit, session, right, file = _numilog(rig, altered=altered)
    try:
        assert _pair_evidence_gaps(audit) == ('structure_global_budget',)
        report = session.preflight_manual_link(right, file)
        assert report.status == 'blocked'
        assert 'structure_global_budget' in report.codes
        assert 'EPUB/CRC' in report.explanation
        assert session.preview_manual_attachment(right, file)['status'] == 'unavailable'
    finally:
        session.close()


def test_missing_tables_and_unfinished_inventory_have_explicit_causes(rig):
    def altered(audit):
        audit.snapshot.tables.pop('acquisition_ledger')
        audit.inventory.files_started += 1
    audit, session, right, file = _numilog(rig, altered=altered)
    try:
        assert _pair_evidence_gaps(audit) == ('inventory_incomplete', 'snapshot_incomplete')
        assert {'inventory_incomplete', 'snapshot_incomplete'} <= set(
            session.preflight_manual_link(right, file).codes)
    finally:
        session.close()


def test_invalid_coverage_and_untrusted_reason_never_become_exception_prose(rig):
    audit, session, _, _ = _numilog(rig)
    session.close()
    audit.inventory.reasons.add('PRIVATE raw reason /secret/path')
    assert _pair_evidence_gaps(audit) == ('inventory_incomplete',)
    audit.snapshot.reasons = ['PRIVATE malformed reason']
    assert _pair_evidence_gaps(audit) == ('coverage_unavailable',)
    assert not _pair_evidence_complete(audit)


def test_unrelated_invalid_epub_and_bibliographic_budget_keep_pair_eligible(rig):
    _bad_files(rig[1].parent)
    def altered(audit):
        audit.metadata_candidates.reasons.add('metadata_candidate_time_budget')
    audit, session, right, file = _numilog(rig, altered=altered)
    try:
        assert _pair_evidence_gaps(audit) == ()
        assert _pair_evidence_complete(audit)
        report = session.preflight_manual_link(right, file)
        assert report.status == 'eligible_for_fresh_validation'
        assert 'audit_incomplete' not in report.codes
        assert session.preview_manual_attachment(right, file)['status'] == 'preview'
        assert not _rows(rig[0], 'placements')
    finally:
        session.close()


def test_kobo_uses_same_closed_coverage_causes_and_keeps_own_writer(kobo_selected):
    case, old, _right, _file = kobo_selected
    service = case.service
    root, = old._roots
    audit = audit_library(service.storage.path, {'default': (root,)})
    audit.reconciliation.reasons.add('comparison_budget')
    session = LocalInspection(audit, lambda: True, 'default', roots=(root,), service=service)
    try:
        right, = session.page(provider='kobo', state='rights_without_verified_file').cases
        file, = session.unconfirmed_files(query='PRIVATE-copy', limit=10).cases
        report = session.preflight_manual_link(right.key, file.key)
        assert report.status == 'blocked'
        assert 'comparison_incomplete' in report.codes
        assert 'kobo_fresh_proof_required' in report.codes
        assert 'Numilog' not in report.explanation
        assert session.preview_manual_attachment(right.key, file.key)['status'] == 'unavailable'
    finally:
        session.close()
