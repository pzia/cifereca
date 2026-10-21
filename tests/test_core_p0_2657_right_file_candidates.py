"""P0 #2657: right -> local EPUB lead is metadata_only and never a pairing."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from cifereca.core.library_metadata_candidates import MetadataCandidate
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import epub, filesystem


def keys_by_filename(audit):
    """Inventory traversal order is intentionally NOT stable."""
    return {Path(item.path).name: index
            for index, item in enumerate(audit.inventory.files)}


def notice_index(audit, number):
    return next(index for index, row in enumerate(
        audit.snapshot.tables['catalog_entries']
    ) if row.get('catalog_entry_id') == f'catalog-{number}')


def file_case_ids(audit, *numbers):
    lookup = keys_by_filename(audit)
    return {f'file:{lookup[f"code-{number}.epub"]}' for number in numbers}


def make_candidates(six_numilog, tmp_path):
    root, database = six_numilog
    # Real synthetic EPUB OPF reads and two valid ISBN13 values. A shared
    # ISBN has two file candidates *and* two distinct Numilog rights.
    isbn_by_file = {3: '9780306406157', 4: '9780306406157',
                    5: '9780306406164'}
    for number, isbn in isbn_by_file.items():
        epub(root / f'code-{number}.epub',
             title=f'PRIVATE book {number}', identifier='urn:isbn:' + isbn)
    with sqlite3.connect(database) as db:
        for number, isbn in isbn_by_file.items():
            db.execute(
                'UPDATE catalog_entries SET metadata_json=? WHERE catalog_entry_id=?',
                (json.dumps({
                    'title': f'PRIVATE notice {number}',
                    'authors': ['PRIVATE author'],
                    'identifiers': [{'scheme': 'isbn13', 'value': isbn}],
                }), f'catalog-{number}'),
            )
        # A foreign right/notice with an identical ISBN must not be exposed
        # or used in candidate displays of the selected identity.
        db.execute(
            'INSERT INTO catalog_entries '
            '(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) '
            'VALUES (?,?,?,?,?)',
            ('PRIVATE-FOREIGN', 'bob', 'numilog', 'item-5',
             json.dumps({'title': 'PRIVATE_FOREIGN_SECRET',
                         'identifiers': [
                             {'scheme': 'isbn13', 'value': '9780306406164'}]})),
        )
    return root, database


def test_six_numilog_rights_have_candidates_but_never_a_bijection(
    six_numilog, tmp_path,
):
    root, database = make_candidates(six_numilog, tmp_path)
    before = filesystem(tmp_path)
    audit = audit_library(database, {'alice': (root,)})
    session = LocalInspection(audit, lambda: True, 'alice')
    assert session.overview()['confirmed_files'] == 3
    assert session.overview()['unverified_rights'] == 3
    assert session.overview()['unattributed_files'] == 3
    assert session.page(provider='numilog', state='confirmed_link').total == 3

    # Inverted metadata_only catalogue matches for the same exact edition:
    # two different rights can both show the same two EPUB candidates.
    left = session.candidate_files('right:3')
    also_left = session.candidate_files('right:4')
    other = session.candidate_files('right:5')
    expected = file_case_ids(audit, 3, 4)
    assert {c.key for c in left} == expected
    assert {c.key for c in also_left} == expected
    assert {c.key for c in other} == file_case_ids(audit, 5)
    for case in left + other:
        assert case.provider == 'unattributed'
        assert 'confirmed_link' not in case.states
    right = session.detail('right:3')
    assert 'non probant' in right.details
    assert 'Candidats multiples' in right.details
    assert 'PRIVATE book 3' in right.details
    assert 'PRIVATE book 4' in right.details
    assert 'PRIVATE_FOREIGN_SECRET' not in right.details
    assert 'Aucune acquisition liée' in right.details
    assert 'Droit sans fichier vérifié' in right.label
    assert session.candidate_files(next(iter(expected))) == ()
    assert session.candidate_files('right:0') == ()
    assert 'PRIVATE book' not in json.dumps(audit.redacted())
    assert 'PRIVATE_FOREIGN_SECRET' not in json.dumps(audit.redacted())
    assert filesystem(tmp_path) == before
    session.close()
    assert session.candidate_files('right:3') == ()


def test_foreign_or_non_metadata_only_candidates_never_enter_right_comparison(
    six_numilog, tmp_path,
):
    root, database = make_candidates(six_numilog, tmp_path)
    audit = audit_library(database, {'alice': (root,)})
    catalog = audit.snapshot.tables['catalog_entries']
    # scope_evidence normally removes foreign rows, but a buggy caller
    # may supply one; the inspection boundary must independently refuse it.
    catalog.append({'identity_id': 'bob',
                    'provider_id': 'numilog', 'item_id': 'item-3',
                    'display_title': 'FOREIGN_LEAK'})
    foreign = len(catalog)-1
    filenames = keys_by_filename(audit)
    fi3 = filenames['code-3.epub']
    fi4 = filenames['code-4.epub']
    fi5 = filenames['code-5.epub']
    ci5 = notice_index(audit, 5)
    audit.metadata_candidates.candidates = [
        c for c in audit.metadata_candidates.candidates
        if not (c.file_index == fi5 and c.catalog_row_index == ci5)
    ]
    audit.metadata_candidates.candidates.extend([
        MetadataCandidate(fi3, foreign),
        MetadataCandidate(fi4, foreign),
        # Even an attacker-supplied "attested" row may not become a lead:
        # the comparison accepts ONLY the shared metadata_only candidates.
        MetadataCandidate(fi5, ci5, certainty='attested'),
    ])
    session = LocalInspection(audit, lambda: True, 'alice')
    assert {case.key for case in session.candidate_files('right:3')} == file_case_ids(
        audit, 3, 4,
    )
    assert session.candidate_files('right:5') == ()
    assert 'FOREIGN_LEAK' not in session.detail('right:3').details
    assert 'FOREIGN_LEAK' not in '\n'.join(
        c.details for c in session.page().cases)


def test_single_file_can_be_candidate_for_multiple_rights_with_no_ownership(
    six_numilog, tmp_path,
):
    root, database = make_candidates(six_numilog, tmp_path)
    audit = audit_library(database, {'alice': (root,)})
    session = LocalInspection(audit, lambda: True, 'alice')
    keys = file_case_ids(audit, 3, 4)
    assert session.candidate_files('right:3')[0] == session.detail(
        session.candidate_files('right:3')[0].key,
    )
    assert {c.key for c in session.candidate_files('right:4')} == keys
    assert session.candidate_files('right:3')[0] in session.candidate_files('right:4')
    assert all('confirmed_link' not in session.detail(key).states for key in keys)
    assert 'rights_without_verified_file' in session.detail('right:3').states
