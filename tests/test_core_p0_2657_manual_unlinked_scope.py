"""P0 #2657: unlinked-file browsing must remain independent from ISBN leads.

The interface uses the existing revocable LocalInspection.page() API with
limit=1, never a new provider/ownership matcher or unscoped disk traversal.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection
from tests.test_core_p0_2657_inspection_cases import six_numilog
from tests.test_library_reconciliation import epub, filesystem


def test_manual_side_by_side_remains_possible_without_exact_isbn(
    six_numilog, tmp_path,
):
    root, database = six_numilog
    bob = tmp_path / 'PRIVATE-bob'
    bob.mkdir()
    epub(bob / 'FOREIGN_SECRET.epub', title='PRIVATE_OTHER_IDENTITY',
         identifier='urn:isbn:9780306406157')
    before = filesystem(tmp_path)
    audit = audit_library(database, {'alice': (root,), 'bob': (bob,)})
    # Simulate one item whose scan could not calculate SHA: reconciliation
    # exposes it as unverified_file, NOT local_file_without_attested_right.
    missing_sha_index = next(
        i for i, item in enumerate(audit.inventory.files)
        if Path(item.path).name == 'code-5.epub'
    )
    original = audit.inventory.files[missing_sha_index]
    audit.inventory.files[missing_sha_index] = replace(
        original, sha256=None, validation='incomplete'
    )
    audit.reconciliation.files[missing_sha_index] = {'unverified_file'}
    session = LocalInspection(audit, lambda: True, 'alice')

    rights = session.page(provider='numilog', state='rights_without_verified_file')
    assert rights.total == 3
    assert all(session.candidate_files(case.key) == () for case in rights.cases)

    # Manual exploration is a plain state-scoped local list; no pairing and no
    # provider label are introduced even though the active right is Numilog.
    assert session.page(state='local_file_without_attested_right').total == 2
    first = session.unconfirmed_files(limit=1)
    assert first.total == 3 and len(first.cases) == 1
    assert first.cases[0].provider == 'unattributed'
    for offset in range(3):
        item = session.unconfirmed_files(offset=offset, limit=1)
        assert item.total == 3 and len(item.cases) == 1
        assert 'code-' in item.cases[0].label
        assert 'confirmed_link' not in item.cases[0].states

    unverified = session.unconfirmed_files(query='code-5', limit=1)
    assert unverified.total == 1 and 'unverified_file' in unverified.cases[0].states
    assert session.unconfirmed_files(query='code-0', limit=1).total == 0
    assert session.unconfirmed_files(query='FOREIGN_SECRET').total == 0
    assert all('PRIVATE_OTHER_IDENTITY' not in case.details
               for case in session.page().cases)
    assert filesystem(tmp_path) == before
    assert 'PRIVATE_OTHER_IDENTITY' not in str(audit.redacted())
    session.close()
    assert session.unconfirmed_files().cases == ()
