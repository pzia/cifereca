"""#2565: scoped in-memory projection without losing foreign owner conflicts.

Synthetic committed SQLite (including active WAL) is never mutated by audit.
"""
from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from cifereca.core.library_evidence_scope import scope_evidence
from cifereca.core.library_reconciliation import (
    read_library_snapshot, read_quiescent_snapshot,
)
from tests.test_library_audit_broker import scoped
from tests.test_library_reconciliation import fixture_db, filesystem


@pytest.mark.parametrize('wal', [False, True])
def test_active_identity_filtered_inside_sqlite_but_conflict_ledger_preserved(
    tmp_path, wal,
):
    path = tmp_path / 'PRIVATE.sqlite'
    fixture_db(path)
    db = sqlite3.connect(path)
    try:
        if wal:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA wal_autocheckpoint=0')
        db.execute(
            "INSERT INTO entitlements "
            "(entitlement_id,identity_id,provider_id,credential_id,item_id,state,"
            "entitlement_type,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
            ('right-1', 'alice', 'numilog', 'cred-1', 'edition-1',
             'active', 'owned', json.dumps({'source': 'numilog.authenticated_library'})),
        )
        db.execute(
            "INSERT INTO entitlements (entitlement_id,identity_id,provider_id,state) "
            "VALUES ('FOREIGN_RIGHT','bob','kobo','active')"
        )
        db.executemany(
            "INSERT INTO acquisition_ledger "
            "(acquisition_id,entitlement_id,identity_id,provider_id,credential_id,item_id,status) "
            "VALUES (?,?,?,?,?,?,?)", [
                ('A', 'right-1', 'alice', 'numilog', 'cred-1', 'edition-1', 'complete'),
                ('FOREIGN_CONFLICT', 'right-1', 'bob', 'numilog', 'cred-1', 'edition-1', 'complete'),
                ('FOREIGN_UNRELATED', 'other', 'bob', 'kobo', 'other', 'other', 'complete'),
            ]
        )
        db.executemany(
            "INSERT INTO catalog_entries "
            "(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) "
            "VALUES (?,?,?,?,?)", [
                (f'FOREIGN-{i}', 'bob', 'kobo', f'item-{i}',
                 json.dumps({'title': f'PRIVATE_FOREIGN_{i}',
                             'identifiers': [{'scheme': 'isbn13', 'value': '9780306406157'}]}))
                for i in range(1024)
            ],
        )
        db.execute(
            "INSERT INTO catalog_entries "
            "(catalog_entry_id,identity_id,provider_id,item_id,metadata_json) "
            "VALUES ('OWN','alice','numilog','edition-1',?)",
            (json.dumps({'identifiers': [
                {'scheme': 'isbn13', 'value': '9780306406157'}]}),),
        )
        db.commit()
        before = filesystem(tmp_path)
        limited = read_library_snapshot(path, identity_id='alice', max_rows=128)
        assert not limited.reasons
        assert [r['catalog_entry_id'] for r in limited.tables['catalog_entries']] == ['OWN']
        assert [r['entitlement_id'] for r in limited.tables['entitlements']] == ['right-1']
        # Foreign receipts are restricted inside the SQLite image before
        # spending the global row budget, not only in scope_evidence().
        assert {r['acquisition_id'] for r in limited.tables['acquisition_ledger']} == {
            'A', 'FOREIGN_CONFLICT',
        }
        scoped_snapshot = scope_evidence(limited, ('alice',))
        assert {r['acquisition_id'] for r in scoped_snapshot.tables['acquisition_ledger']} == {
            'A', 'FOREIGN_CONFLICT',
        }
        assert 'PRIVATE_FOREIGN' not in repr(scoped_snapshot)
        assert 'FOREIGN_UNRELATED' not in repr(scoped_snapshot)
        assert read_library_snapshot(path, max_rows=128).reasons == {'snapshot_row_budget'}
        assert filesystem(tmp_path) == before
    finally:
        db.close()


def test_public_broker_skips_unrelated_catalog_pressure_without_leaks(scoped, tmp_path):
    from cifereca.core.library_audit_broker import LibraryReconciliationReadBrokerImpl
    service, path, _root = scoped
    with sqlite3.connect(path) as db:
        db.executemany(
            'INSERT INTO catalog_entries (catalog_entry_id,identity_id,provider_id,metadata_json) '
            'VALUES (?,?,?,?)',
            [(f'FOREIGN-{i}', 'bob', 'kobo', '{"title":"PRIVATE_BOB"}') for i in range(600)],
        )
    before = filesystem(tmp_path)
    public = LibraryReconciliationReadBrokerImpl(service).audit(identity_id='alice')
    assert public['status'] == 'complete'
    assert public['reasons'] == []
    assert public['inventory']['files'] == 1
    assert 'PRIVATE' not in json.dumps(public)
    assert filesystem(tmp_path) == before


def test_projection_stage_trace_distinguishes_wal_replay_from_sql_projection(
    tmp_path, caplog,
):
    path = tmp_path / 'PRIVATE.sqlite'
    fixture_db(path)
    db = sqlite3.connect(path)
    caplog.set_level(logging.INFO, logger='cifereca')
    try:
        assert not read_library_snapshot(path, identity_id='alice').reasons
        messages = '\n'.join(caplog.messages)
        assert 'phase=projection stage=direct' in messages
        assert 'phase=finish stage=direct code=complete' in messages
        caplog.clear()
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO catalog_entries "
                   "(catalog_entry_id,identity_id,metadata_json) "
                   "VALUES ('PRIVATE','alice','{}')")
        db.commit()
        assert not read_library_snapshot(path, identity_id='alice').reasons
        messages = '\n'.join(caplog.messages)
        assert 'phase=handoff stage=wal' in messages
        assert 'phase=projection stage=wal' in messages
        assert 'phase=finish stage=wal code=complete' in messages
        assert 'PRIVATE' not in messages
        assert str(path) not in messages
    finally:
        db.close()


@pytest.mark.parametrize('identity_id', ['', 7, True, [], 'a'*1025])
def test_invalid_identity_scope_rejected_before_source_read(tmp_path, identity_id):
    missing = tmp_path / 'PRIVATE-missing.sqlite'
    with pytest.raises(ValueError, match='invalid_snapshot_limits'):
        read_library_snapshot(missing, identity_id=identity_id)
    with pytest.raises(ValueError, match='invalid_snapshot_limits'):
        read_quiescent_snapshot(missing, identity_id=identity_id)
    assert not missing.exists()
