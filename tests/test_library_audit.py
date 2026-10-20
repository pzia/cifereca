"""Integration invariants for the shared private offline audit pipeline."""
import json
from pathlib import Path
import sqlite3
import threading
from unittest.mock import patch

import pytest

from cifereca.core.library_audit import audit_library
from cifereca.core.library_inventory import InventoryLimits
from test_library_reconciliation import epub, fixture_db, filesystem


@pytest.fixture
def library(tmp_path):
    root = tmp_path / 'PRIVATE_IDENTITY'
    root.mkdir()
    book = root / 'PRIVATE_BOOK.epub'
    epub(book)
    database = tmp_path / 'PRIVATE_DB.sqlite'
    fixture_db(database)
    return database, {'PRIVATE_IDENTITY': (root,)}, book


def test_all_phases_preserve_filesystem_and_redact_progress_and_result(library, tmp_path):
    database, roots, book = library
    before = filesystem(tmp_path)
    events = []
    result = audit_library(database, roots, progress=events.append)
    payload = result.redacted()
    assert payload['status'] == 'complete'
    assert payload['inventory']['files'] == 1
    assert payload['reconciliation']['counts']['local_file_without_attested_right'] == 1
    assert payload['repair_authorized'] is False
    assert payload['acquisition_authorized'] is False
    assert filesystem(tmp_path) == before
    phases = [event['phase'] for event in events]
    assert phases[0] == 'inventory'
    assert phases[-5:] == ['snapshot', 'reconciliation', 'metadata_candidates', 'stored_paths', 'finished']
    assert set(phases) == {'inventory', 'snapshot', 'reconciliation', 'metadata_candidates', 'stored_paths', 'finished'}
    public = json.dumps([payload, events])
    assert 'PRIVATE' not in public
    assert str(tmp_path) not in public
    assert result.inventory.files[0].sha256 not in public
    assert 'PRIVATE' not in repr(result)


@pytest.mark.parametrize('phase', [
    'inventory', 'snapshot', 'reconciliation', 'metadata_candidates', 'stored_paths',
])
def test_cancel_at_phase_boundary_is_visible_without_writes(library, tmp_path, phase):
    database, roots, _ = library
    stop = threading.Event()
    def progress(event):
        if event['phase'] == phase:
            stop.set()
    before = filesystem(tmp_path)
    result = audit_library(database, roots, cancelled=stop.is_set, progress=progress)
    assert result.redacted()['status'] == 'incomplete_scan'
    assert 'audit_cancelled' in result.redacted()['reasons']
    assert filesystem(tmp_path) == before


def test_shared_deadline_is_not_reset_at_snapshot(library, tmp_path):
    database, roots, _ = library
    clock = [0.0]
    def progress(event):
        if event['phase'] == 'snapshot':
            clock[0] = 20.0
    before = filesystem(tmp_path)
    with patch('cifereca.core.library_audit.monotonic', side_effect=lambda: clock[0]):
        result = audit_library(database, roots, seconds=1, progress=progress)
    assert 'audit_time_budget' in result.redacted()['reasons']
    assert not result.snapshot.tables
    assert filesystem(tmp_path) == before


def test_live_wal_is_incomplete_and_never_hidden_by_successful_inventory(library, tmp_path):
    database, roots, _ = library
    db = sqlite3.connect(database)
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute("INSERT INTO artifacts VALUES ('a','hash',123,'epub')")
        db.commit()
        before = filesystem(tmp_path)
        result = audit_library(database, roots)
        assert result.redacted()['inventory']['status'] == 'complete'
        assert result.redacted()['status'] == 'incomplete_scan'
        assert 'snapshot_live_journal' in result.redacted()['reasons']
        assert filesystem(tmp_path) == before
    finally:
        db.close()


def test_callback_cannot_expand_identity_capabilities(library, tmp_path):
    database, roots, _ = library
    foreign = tmp_path / 'FOREIGN'
    foreign.mkdir()
    epub(foreign / 'FOREIGN.epub')
    def progress(event):
        roots['FOREIGN'] = (foreign,)
    result = audit_library(database, roots, progress=progress)
    assert len(result.inventory.files) == 1
    assert result.groups.identities == ('PRIVATE_IDENTITY',)
    assert all(f.identity_id == 'PRIVATE_IDENTITY' for f in result.inventory.files)


def test_stored_path_failure_affects_global_status_after_other_phases(library):
    database, roots, _ = library
    with sqlite3.connect(database) as db:
        db.execute("INSERT INTO placements (placement_id,identity_id,path) VALUES (?,?,?)",
                   ('p', 'PRIVATE_IDENTITY', None))
    result = audit_library(database, roots)
    assert result.reconciliation.redacted()['status'] == 'complete'
    assert 'stored_path_unavailable' in result.redacted()['reasons']
    assert result.redacted()['status'] == 'incomplete_scan'


def test_inventory_budget_remains_visible(library):
    database, roots, book = library
    result = audit_library(database, roots, limits=InventoryLimits(file_bytes=1))
    assert 'file_byte_budget' in result.redacted()['reasons']
    assert result.redacted()['inventory']['hashed_files'] == 0


@pytest.mark.parametrize('seconds', [0, -1, 601, True, float('nan')])
def test_invalid_budget_precedes_io(library, seconds):
    database, roots, _ = library
    with pytest.raises(ValueError, match='invalid_library_audit_options'):
        audit_library(database, roots, seconds=seconds)


def test_invalid_roots_and_database_never_create_paths(tmp_path):
    database = tmp_path / 'MISSING.sqlite'
    absent = tmp_path / 'MISSING'
    with pytest.raises(ValueError):
        audit_library(database, {'i': (absent,), 'j': (absent / 'nested',)})
    with pytest.raises(ValueError, match='invalid_snapshot_path'):
        audit_library(Path('relative.sqlite'), {'i': (absent,)})
    assert not database.exists()
    assert not absent.exists()
