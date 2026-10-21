"""P0 #2657: private causes must follow the actual evidence scope.

No vendor request, repair, file mutation, or cross-account disclosure.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import filesystem


def _right(session, suffix):
    result = session.page(provider='numilog', state='rights_without_verified_file')
    return next(c for c in result.cases if f'item-{suffix}' in c.details)


def _run(root, database):
    return LocalInspection(audit_library(database, {'alice': (root,)}),
                           lambda: True, 'alice', roots=(root,))


@pytest.mark.parametrize(('placement_path', 'expected'), [
    ('outside', 'hors des racines autorisées'),
    ('missing', 'absent lors du contrôle sécurisé'),
    ('present_unscanned', 'présent au contrôle ponctuel'),
    ('symlink', 'non inspectable'),
])
def test_private_right_distinguishes_recorded_path_state(
    six_numilog, tmp_path, placement_path, expected,
):
    root, database = six_numilog
    if placement_path == 'outside':
        target = tmp_path / 'PRIVATE-other-root.epub'
    elif placement_path == 'missing':
        target = root / 'PRIVATE-not-there.epub'
    elif placement_path == 'present_unscanned':
        target = root / 'PRIVATE-local-note.txt'
        target.write_text('PRIVATE text, not an EPUB')
    else:
        target = root / 'PRIVATE-symlink.epub'
        target.symlink_to(tmp_path / 'PRIVATE-other-root.epub')
    with sqlite3.connect(database) as db:
        db.execute(
            'INSERT INTO placements '
            '(placement_id,artifact_id,path,identity_id,provider_id,source_item_id,state) '
            'VALUES (?,?,?,?,?,?,?)',
            ('PRIVATE-place-3', 'PRIVATE-artifact-3', str(target),
             'alice', 'numilog', 'item-3', 'present'),
        )
    before = filesystem(tmp_path)
    audit = audit_library(database, {'alice': (root,)})
    session = LocalInspection(audit, lambda: True, 'alice', roots=(root,))
    right = _right(session, 3)
    assert expected in right.details
    assert 'aucun' in right.details.lower() or 'Aucun' in right.details
    assert 'aucun rattachement' not in right.details
    assert right.provider == 'numilog'
    assert 'PRIVATE-other-root.epub' not in json.dumps(audit.redacted())
    assert filesystem(tmp_path) == before
    session.close()


def test_foreign_ledger_never_counts_as_right_acquisition_or_leaks(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute(
            'INSERT INTO acquisition_ledger '
            '(acquisition_id,entitlement_id,provider_id,credential_id,'
            'account_id,item_id,identity_id,status,artifact_id) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            ('PRIVATE-foreign-acq', 'right-3', 'numilog',
             'PRIVATE-FOREIGN-CONNECTION', 'PRIVATE-FOREIGN-ACCOUNT',
             'item-3', 'bob', 'complete', 'PRIVATE-FOREIGN-ARTIFACT'),
        )
    before = filesystem(tmp_path)
    session = _run(root, database)
    right = _right(session, 3)
    assert 'preuve contradictoire' in right.details
    assert 'Aucune acquisition liée' in right.details
    assert 'PRIVATE-FOREIGN' not in right.details
    assert 'Acquisition enregistrée :' not in right.details
    assert filesystem(tmp_path) == before
    session.close()


def test_own_ledger_with_wrong_artifact_explains_broken_chain(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    book = root / 'code-3.epub'
    data = book.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO artifacts VALUES (?,?,?,?)',
                   ('PRIVATE-placement-artifact', digest, len(data), 'epub'))
        db.execute(
            'INSERT INTO placements '
            '(placement_id,artifact_id,path,identity_id,provider_id,source_item_id,state) '
            'VALUES (?,?,?,?,?,?,?)',
            ('PRIVATE-place-3', 'PRIVATE-placement-artifact', str(book),
             'alice', 'numilog', 'item-3', 'present'),
        )
        db.execute(
            'INSERT INTO acquisition_ledger '
            '(acquisition_id,entitlement_id,provider_id,credential_id,'
            'account_id,item_id,identity_id,status,artifact_id) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            ('PRIVATE-acq-3', 'right-3', 'numilog', 'cred-alice',
             'account-alice', 'item-3', 'alice', 'complete',
             'PRIVATE-different-ledger-artifact'),
        )
    before = filesystem(tmp_path)
    session = _run(root, database)
    right = _right(session, 3)
    assert 'Artefact de l’acquisition différent de celui du placement' in right.details
    assert 'Acquisition enregistrée :' in right.details
    assert 'ne prouvent ni l’absence physique' in right.details
    assert filesystem(tmp_path) == before
    session.close()


def test_local_exclusions_are_private_identity_scoped_and_revocable(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    audit = audit_library(database, {'alice': (root,)})
    audit.inventory.unavailable_directories.extend([
        ('alice', str(root / 'PRIVATE-inaccessible')),
        ('bob', '/PRIVATE-other-account/DO-NOT-DISCLOSE'),
    ])
    audit.inventory.reasons.update({
        'entry_budget', 'directory_unavailable', 'unexpected_PRIVATE_marker',
    })
    audit.inventory.skipped_symlinks = 4
    audit.inventory.skipped_special = 2
    session = LocalInspection(audit, lambda: True, 'alice', roots=(root,))
    coverage = session.overview()
    assert coverage['roots'] == (str(root),)
    assert coverage['unavailable_directories'] == (
        str(root / 'PRIVATE-inaccessible'),
    )
    assert coverage['unavailable_directories_total'] == 1
    assert coverage['skipped_symlinks'] == 4
    assert coverage['skipped_special'] == 2
    assert any('entrées' in line for line in coverage['inventory_limit_notes'])
    assert any('Autre limite' in line for line in coverage['inventory_limit_notes'])
    assert 'unexpected_PRIVATE_marker' not in str(coverage)
    assert 'DO-NOT-DISCLOSE' not in str(coverage)
    assert 'PRIVATE-inaccessible' not in json.dumps(audit.redacted())
    assert 'DO-NOT-DISCLOSE' not in json.dumps(audit.redacted())
    assert 'PRIVATE-inaccessible' not in json.dumps(session.summary())
    session.close()
    assert session.overview() == {}


def test_private_exclusions_truncate_paths_without_undercounting(
    six_numilog,
):
    root, database = six_numilog
    audit = audit_library(database, {'alice': (root,)})
    audit.inventory.unavailable_directories.extend(
        ('alice', str(root / f'PRIVATE-excluded-{i}'))
        for i in range(131)
    )
    session = LocalInspection(audit, lambda: True, 'alice', roots=(root,))
    coverage = session.overview()
    assert coverage['unavailable_directories_total'] == 131
    assert len(coverage['unavailable_directories']) == 128
    assert coverage['unavailable_directories'][-1].endswith('PRIVATE-excluded-127')
    assert 'PRIVATE-excluded-130' not in json.dumps(audit.redacted())
    session.close()
    assert session.overview() == {}
