"""Private local cases retain uncertainty, ownership and context revocation."""
import json
import sqlite3
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_local_inspection import LocalInspection, open_local_inspection
from cifereca.core.library_metadata_candidates import MetadataCandidate
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import filesystem, epub


def test_actual_reader_is_private_offline_paginated_and_zero_write(scoped, tmp_path):
    service, _, _ = scoped
    before = filesystem(tmp_path)
    session = open_local_inspection(service, identity_id='alice')
    assert isinstance(session, LocalInspection)
    page = session.page()
    assert page.total == 1
    case = page.cases[0]
    assert case.provider == 'unattributed'
    assert 'PRIVATE.epub' in case.label
    assert 'PRIVATE_TITLE' in case.details
    assert 'aucun droit' not in case.details.lower()  # no unsupported absence claim
    assert 'PRIVATE' not in repr(session)+repr(page)+repr(case)+json.dumps(session.summary())
    assert filesystem(tmp_path) == before
    assert session.detail(case.key) is case
    assert session.page(provider='kobo').total == 0
    assert session.page(state='local_file_without_attested_right').total == 1


@pytest.mark.parametrize('change', ['epoch', 'identity', 'config', 'storage', 'path', 'identities', 'projection'])
def test_private_results_revoked_after_scope_change(scoped, change):
    service, _, _ = scoped
    session = open_local_inspection(service, identity_id='alice')
    key = session.page().cases[0].key
    if change == 'epoch': service._account_context_epoch += 1
    elif change == 'identity': service.current_identity = service.identities[1]
    elif change == 'config': service.config.data['library']['directory'] += '-changed'
    elif change == 'storage': service.storage = NS(path=service.storage.path)
    elif change == 'path': service.storage.path = service.storage.path.parent/'other.sqlite'
    elif change == 'identities': service.identities = list(service.identities)
    else: service.identities[0] = replace(service.identities[0],directory_name='changed')
    assert not session.valid()
    assert session.detail(key) is None and session.page().cases == () and session.summary() == {}
    assert not session._cases


def test_cancelled_foreign_and_migration_requests_remain_closed(scoped):
    service, path, _ = scoped
    denied = open_local_inspection(service, identity_id='bob')
    assert denied['reasons'] == ['reconciliation_identity_not_active']
    denied = open_local_inspection(service, identity_id='alice', control=NS(cancelled=lambda:True,report=lambda **kw:None))
    assert denied['reasons'] == ['audit_cancelled']
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE metadata(key TEXT,value TEXT)')
        db.execute("INSERT INTO metadata VALUES('identity-directory-migration-v1','PRIVATE')")
    denied = open_local_inspection(service, identity_id='alice')
    assert denied['reasons'] == ['snapshot_identity_migration']
    assert 'PRIVATE' not in json.dumps(denied)


def test_rights_without_file_and_candidates_never_attribute_file_provider(scoped):
    service, path, root = scoped
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO entitlements(entitlement_id,identity_id,state,provider_id,item_id) VALUES('RIGHT_PRIVATE','alice','active','numilog','EDITION_PRIVATE')")
    audit = audit_library(path, {'alice':(root/'a',)})
    # Synthetic explicit metadata candidate, with a foreign row also supplied
    # to verify the projector's defence independent of snapshot scoping.
    audit.snapshot.tables['catalog_entries'] = [
        dict(identity_id='alice',provider_id='kobo',catalog_entry_id='NOTICE_PRIVATE',
             bibliographic_identifiers=(('isbn13','9780306406157'),)),
        dict(identity_id='bob',account_id='FOREIGN_SECRET')]
    audit.metadata_candidates.candidates = [MetadataCandidate(0,0),MetadataCandidate(0,1)]
    audit.metadata_candidates.ambiguous_files = [0]
    session = LocalInspection(audit, lambda:True, 'alice')
    files = session.page(state='metadata_candidate').cases
    assert len(files) == 1 and files[0].provider == 'unattributed'
    assert 'ambiguous' in files[0].states
    assert 'FOREIGN_SECRET' not in files[0].details
    assert 'Identifiant notice (isbn13) : 9780306406157' in files[0].details
    rights = session.page(provider='numilog',state='rights_without_verified_file').cases
    assert len(rights) == 1
    assert 'pas preuve de livre absent' in rights[0].details
    assert not session.page(provider='kobo').cases


def test_stored_missing_path_is_separate_from_rights_and_file_inference(scoped):
    service, path, root = scoped
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO placements(placement_id,identity_id,path,provider_id) VALUES(?,?,?,?)',
                   ('MISSING_PRIVATE','alice',str(root/'a'/'gone.epub'),'kobo'))
    session = open_local_inspection(service,identity_id='alice')
    case = session.page(state='stored_path_missing').cases[0]
    assert case.provider == 'unattributed'
    assert 'gone.epub' in case.details
    assert not session.page(provider='kobo').cases


def test_pages_are_bounded_and_close_drops_private_projection(scoped):
    service, _, root = scoped
    for i in range(62): epub(root/'a'/f'{i}.epub')
    session = open_local_inspection(service,identity_id='alice')
    first, second = session.page(), session.page(offset=50)
    assert first.total == second.total == 63 and len(first.cases)==50 and len(second.cases)==13
    assert not set(c.key for c in first.cases) & set(c.key for c in second.cases)
    with pytest.raises(ValueError,match='invalid_inspection_page'): session.page(limit=51)
    session.close()
    assert not session.valid() and not session._cases


def test_projection_budget_and_mid_projection_revocation_are_visible(scoped):
    service, path, root = scoped
    audit = audit_library(path, {'alice':(root/'a',)})
    audit.inventory.files *= 10001
    session = LocalInspection(audit,lambda:True,'alice')
    assert session.summary()['inspection_limited']
    assert 0 < session.page().total <= 10000
    reads = [0]
    def valid():
        reads[0] += 1
        return reads[0] < 5
    revoked = LocalInspection(audit,valid,'alice')
    assert not revoked.valid() and not revoked._cases
