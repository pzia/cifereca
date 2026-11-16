"""P0 #2657: six Numilog EPUBs, three legacy chains, six unlinked cases.

Private fixtures only: no provider inference, acquisition, mutation or leaks.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection, PROVIDERS
from tests.test_library_reconciliation import epub, fixture_db, filesystem


@pytest.fixture
def six_numilog(tmp_path):
    root = tmp_path / 'PRIVATE-alice'
    root.mkdir()
    database = tmp_path / 'PRIVATE.sqlite'
    fixture_db(database)
    files = []
    for number in range(6):
        book = root / f'code-{number}.epub'
        epub(book, title=f'Titre caché volume-{number}', identifier=f'ISBN-TEST-{number}')
        files.append(book)
    with sqlite3.connect(database) as db:
        for number, book in enumerate(files):
            item = f'item-{number}'
            right = f'right-{number}'
            db.execute(
                "INSERT INTO entitlements "
                "(entitlement_id,provider_id,credential_id,account_id,item_id,"
                "identity_id,state,entitlement_type,metadata_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (right, 'numilog', 'cred-alice', 'account-alice', item,
                 'alice', 'active', 'owned',
                 json.dumps({'source': 'numilog.authenticated_library'})),
            )
            db.execute(
                "INSERT INTO catalog_entries "
                "(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) "
                "VALUES (?,?,?,?,?)",
                (f'catalog-{number}', 'alice', 'numilog', item,
                 json.dumps({'title': f'Titre notice {number}',
                             'authors': [f'Autrice Exemple {number}']})),
            )
            if number < 3:
                raw = book.read_bytes()
                digest = hashlib.sha256(raw).hexdigest()
                artifact = 'sha256:' + digest
                db.execute("INSERT INTO artifacts VALUES (?,?,?,?)",
                           (artifact, digest, len(raw), 'epub'))
                db.execute(
                    "INSERT INTO acquisition_ledger "
                    "(acquisition_id,entitlement_id,provider_id,credential_id,"
                    "account_id,item_id,identity_id,status,artifact_id) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (f'acquisition-{number}', right, 'numilog', 'cred-alice',
                     'account-alice', item, 'alice', 'complete', artifact),
                )
                db.execute(
                    "INSERT INTO placements "
                    "(placement_id,artifact_id,path,identity_id,provider_id,"
                    "source_item_id,state) VALUES (?,?,?,?,?,?,?)",
                    (f'placement-{number}', artifact, str(book), 'alice',
                     'numilog', item, 'present'),
                )
    return root, database


def test_six_files_all_legacy_rights_unverified_and_three_byte_placements(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    before = filesystem(tmp_path)
    audit = audit_library(database, {'alice': (root,)})
    session = LocalInspection(audit, lambda: True, 'alice', roots=(root,))
    counts = session.overview()
    assert counts['files'] == 6
    assert counts['confirmed_files'] == 0
    assert counts['local_without_right'] == 6
    assert counts['unattributed_files'] == 6
    assert counts['unverified_rights'] == 6
    # These are three distinct evidence populations, not a 1:1 join.
    assert counts['files'] == (
        counts['files_attested']
        + counts['files_without_attested_link']
        + counts['files_individually_unverified']
    )
    assert (counts['files_attested'], counts['files_without_attested_link'],
            counts['files_individually_unverified']) == (0, 6, 0)
    assert counts['rights_active'] == 6
    assert counts['rights_attested'] == 0
    assert counts['rights_unverified_total'] == 6
    assert counts['placements_registered'] == 3
    assert counts['roots'] == (str(root),)
    assert session.page(state='confirmed_link').total == 0
    assert session.page(provider='numilog', state='confirmed_link').total == 0
    assert session.page(provider='unattributed',
                        state='local_file_without_attested_right').total == 6
    assert session.page(provider='local').total == 0
    rights = session.page(provider='numilog', state='rights_without_verified_file')
    assert rights.total == 6
    for case in rights.cases:
        assert 'Titre notice' in case.label
        assert 'Autrice Exemple' in case.label
        assert 'ne prouvent ni l’absence physique' in case.details
        # Only the last three editions have no historical receipt/placement;
        # the first three remain visible but have no canonical OriginLink.
        if any(f'item-{i}' in case.details for i in range(3, 6)):
            assert 'Aucune acquisition liée' in case.details
            assert 'Aucun placement explicite' in case.details
    files = session.page(provider='unattributed')
    assert all('Titre caché' in case.label for case in files.cases)
    assert all('code-' in case.label for case in files.cases)
    # Search is local, accent-insensitive, and combines with state/provider.
    # Paths are searchable too: a bare digit can match every file when
    # pytest's parent directory contains it. Select an OPF-only term.
    assert session.page(query='titre cache volume-4', provider='unattributed').total == 1
    assert session.page(query='autrice exemple 4',
                        state='rights_without_verified_file').total == 1
    assert session.page(query='code-5', provider='numilog').total == 0
    assert 'PRIVATE' not in json.dumps(session.summary())
    assert 'Titre caché' not in json.dumps(audit.redacted())
    assert 'Titre notice' not in json.dumps(audit.redacted())
    assert str(root) not in json.dumps(audit.redacted())
    assert filesystem(tmp_path) == before
    session.close()
    assert session.overview() == {}


def test_three_canonical_purchase_origins_confirm_same_epubs_without_legacy_whitelist(
    six_numilog,
):
    """Only the canonical proof may restore the three historically linked books."""
    from cifereca.core.canonical_publication import _stable
    from cifereca.core.library_reconciliation import reconcile
    from cifereca.core.library_evidence_scope import group_evidence

    root, database = six_numilog
    audit = audit_library(database, {'alice': (root,)})
    assert audit.reconciliation.redacted()['counts'].get('confirmed_link', 0) == 0
    before = filesystem(root.parent)
    rights = {
        row['item_id']: row
        for row in audit.snapshot.tables['entitlements']
    }
    canonical = []
    for placement in audit.snapshot.tables['placements']:
        right = rights[placement['source_item_id']]
        origin = {
            'identity_id': 'alice', 'provider_id': 'numilog',
            'credential_id': right['credential_id'],
            'account_id': right['account_id'],
            'branch_id': 'personal_library',
            'edition_id': right['item_id'],
            'right_id': right['entitlement_id'],
            'reference_kind': 'purchase', 'state': 'active',
            'evidence_kind': 'provider_response',
            'evidence_ref': 'source-verified-' + right['entitlement_id'],
            'receipt_ref': 'completed-' + right['entitlement_id'],
            'observed_at': '2026-11-16T09:00:00Z',
            'artifact_id': placement['artifact_id'],
        }
        coordinates = (
            'identity_id', 'provider_id', 'credential_id', 'account_id',
            'branch_id', 'edition_id', 'right_id', 'reference_kind',
        )
        origin['source_reference_id'] = _stable(
            'source', *(origin[key] for key in coordinates),
        )
        origin['origin_link_id'] = _stable(
            'origin', 'provider', origin['identity_id'],
            origin['artifact_id'], origin['source_reference_id'],
            origin['receipt_ref'],
        )
        canonical.append(origin)
    audit.snapshot.tables['provider_origins'] = canonical
    audit.reconciliation = reconcile(audit.inventory, audit.snapshot)
    audit.groups = group_evidence(
        audit.inventory, audit.snapshot, audit.reconciliation, ('alice',),
    )
    session = LocalInspection(audit, lambda: True, 'alice', roots=(root,))
    assert session.overview()['confirmed_files'] == 3
    assert session.page(state='confirmed_link').total == 3
    assert session.unconfirmed_files().total == 3
    assert all(edge.table == 'provider_origins' for edge in
               audit.reconciliation.edges if edge.certainty == 'attested')
    assert filesystem(root.parent) == before
    assert audit.redacted()['repair_authorized'] is False
    session.close()


def test_search_and_nonnumeric_queries_cannot_leak_or_crash(six_numilog):
    root, database = six_numilog
    session = LocalInspection(audit_library(database, {'alice': (root,)}),
                              lambda: True, 'alice')
    for invalid in (None, 0, True, [], 'X' * 161):
        with pytest.raises(ValueError, match='invalid_inspection_page'):
            session.page(query=invalid)
    assert session.page(query='pas-de-livre').total == 0
    assert session.page(query='TITRE  CAChE').total == 6
    assert session.page(query='edition-absente').total == 0
    assert session.page(offset=50).total == 12
    assert 'PRIVATE' not in repr(session) + repr(session.page())


def test_foreign_notice_cannot_name_or_match_selected_right(six_numilog):
    root, database = six_numilog
    with sqlite3.connect(database) as db:
        db.execute(
            "INSERT INTO catalog_entries "
            "(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) "
            "VALUES (?,?,?,?,?)",
            ('FOREIGN-CATALOG', 'bob', 'numilog', 'item-5',
             json.dumps({'title': 'PRIVATE_FOREIGN_BOB',
                         'authors': ['PRIVATE_FOREIGN_AUTHOR']})),
        )
    session = LocalInspection(audit_library(database, {'alice': (root,)}),
                              lambda: True, 'alice')
    assert session.page(query='PRIVATE_FOREIGN_BOB').total == 0
    assert 'PRIVATE_FOREIGN_BOB' not in '\n'.join(
        case.details for case in session.page().cases)
    assert PROVIDERS['unattributed'] == 'Fichiers sans provenance attestée'
