"""Real WAL/subprocess invariants; all rights and files are synthetic."""
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from cifereca.core.library_reconciliation import read_library_snapshot, read_quiescent_snapshot
from cifereca.core.library_wal_image import _Unavailable, _capture, _replay
from tests.test_library_reconciliation import fixture_db, filesystem


@pytest.fixture
def wal(tmp_path):
    path = tmp_path / 'PRIVATE.sqlite'
    fixture_db(path)
    db = sqlite3.connect(path)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute("INSERT INTO artifacts VALUES ('committed','private-hash',123,'epub')")
    db.commit()
    try:
        yield path, db
    finally:
        db.close()


def test_committed_wal_only_row_and_source_bytes_are_preserved(wal, tmp_path):
    path, _ = wal
    assert read_quiescent_snapshot(path).reasons == {'snapshot_live_journal'}
    before = filesystem(tmp_path)
    result = read_library_snapshot(path)
    assert not result.reasons
    assert result.tables['artifacts'][0]['artifact_id'] == 'committed'
    assert filesystem(tmp_path) == before
    assert 'PRIVATE' not in repr(result)


def test_uncommitted_rows_excluded_and_parent_write_lock_survives_child(wal):
    path, db = wal
    db.execute('BEGIN IMMEDIATE')
    db.execute("INSERT INTO artifacts VALUES ('not-committed','hash',456,'epub')")
    result = read_library_snapshot(path)
    assert not result.reasons
    assert [r['artifact_id'] for r in result.tables['artifacts']] == ['committed']
    # Different PROCESS: same-process sqlite handles can share POSIX locks.
    code = '''import sqlite3,sys
db=sqlite3.connect(sys.argv[1],timeout=0)
try:
    db.execute('BEGIN IMMEDIATE')
except sqlite3.OperationalError:
    sys.exit(0)
sys.exit(1)
'''
    assert subprocess.run([sys.executable, '-B', '-c', code, str(path)],
                          capture_output=True, timeout=5).returncode == 0
    db.rollback()


def test_budget_deadline_cancel_and_frozen_runtime_fail_closed(wal):
    path, _ = wal
    assert read_library_snapshot(path, max_bytes=100).reasons == {'snapshot_byte_budget_or_invalid'}
    assert read_library_snapshot(path, seconds=0.000001).reasons == {'snapshot_time_budget'}
    assert read_library_snapshot(path, cancelled=lambda: True).reasons == {'snapshot_cancelled'}
    with patch('sys.frozen', True, create=True):
        assert read_library_snapshot(path).reasons == {'snapshot_wal_reader_unavailable'}


@pytest.mark.parametrize('suffix', ['-wal', '-shm'])
def test_symlink_sidecars_refused_without_creating_files(wal, tmp_path, suffix):
    path, _ = wal
    sidecar = Path(str(path)+suffix)
    saved = tmp_path / 'PRIVATE-saved'
    sidecar.rename(saved)
    sidecar.symlink_to(saved)
    before = filesystem(tmp_path)
    try:
        result = read_library_snapshot(path)
        assert result.reasons
        assert not result.tables
        assert filesystem(tmp_path) == before
    finally:
        sidecar.unlink()
        saved.rename(sidecar)


@pytest.mark.parametrize('position', [0, 4, 24, 32, 40, 48, -1])
def test_corrupt_committed_wal_never_falls_back_to_older_rights(wal, position):
    path, _ = wal
    sidecar = Path(str(path)+'-wal')
    original = sidecar.read_bytes()
    raw = bytearray(original)
    raw[position] ^= 1
    sidecar.write_bytes(raw)
    try:
        result = read_library_snapshot(path)
        assert result.reasons
        assert not result.tables
    finally:
        sidecar.write_bytes(original)


def test_unpublished_wal_tail_does_not_appear_as_committed_evidence(wal):
    path, _ = wal
    sidecar = Path(str(path)+'-wal')
    original = sidecar.read_bytes()
    # Incomplete/unpublished data after mxFrame is outside the committed image.
    with sidecar.open('ab') as stream:
        stream.write(b'PRIVATE-unpublished-frame-tail')
    try:
        result = read_library_snapshot(path)
        assert not result.reasons
        assert [r['artifact_id'] for r in result.tables['artifacts']] == ['committed']
    finally:
        sidecar.write_bytes(original)


def test_concurrent_pair_commits_are_coherent_or_explicitly_unavailable(wal):
    path, _ = wal
    stop = threading.Event()
    errors = []
    def writer():
        try:
            with sqlite3.connect(path, timeout=2) as other:
                for i in range(30):
                    if stop.is_set():
                        break
                    with other:
                        other.executemany('INSERT INTO artifacts VALUES (?,?,?,?)',
                                          [(f'pair-{i}-a', 'h', 1, 'epub'),
                                           (f'pair-{i}-b', 'h', 1, 'epub')])
                    time.sleep(0.005)
        except Exception as exc:
            errors.append(type(exc).__name__)
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        for _ in range(3):
            result = read_library_snapshot(path)
            if result.reasons:
                assert not result.tables
                continue
            ids = {row['artifact_id'] for row in result.tables['artifacts']}
            for i in range(30):
                assert (f'pair-{i}-a' in ids) == (f'pair-{i}-b' in ids)
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert not errors
    assert not read_library_snapshot(path).reasons


def test_capture_cannot_be_called_in_application_process(wal):
    path, _ = wal
    with pytest.raises(_Unavailable, match='snapshot_wal_reader_unavailable'):
        _capture(path, 128*1024*1024, time.monotonic()+10)


def test_cancel_after_child_spawn_reaps_child_and_releases_source_locks(wal):
    path, db = wal
    children = []
    real_popen = subprocess.Popen
    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child
    def cancelled():
        return bool(children)
    with patch('cifereca.core.library_wal_image.subprocess.Popen', side_effect=spawn):
        result = read_library_snapshot(path, cancelled=cancelled)
    assert result.reasons == {'snapshot_cancelled'}
    assert len(children) == 1
    assert children[0].poll() is not None
    assert children[0].stdout.closed
    db.execute("INSERT INTO artifacts VALUES ('after-cancel','hash',1,'epub')")
    db.commit()
    assert not read_library_snapshot(path).reasons


def test_busy_shared_memory_locks_fail_closed_without_waiting(wal):
    import fcntl
    path, _ = wal
    fd = os.open(str(path)+'-shm', os.O_RDWR)
    try:
        fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB, 2, 123, os.SEEK_SET)
        result = read_library_snapshot(path, seconds=2)
        assert result.reasons == {'snapshot_unavailable'}
        assert not result.tables
    finally:
        os.close(fd)


def test_corrupt_duplicated_shm_commit_header_is_not_trusted(wal):
    path, _ = wal
    shm = Path(str(path)+'-shm')
    original = shm.read_bytes()
    raw = bytearray(original)
    raw[16] ^= 1
    raw[64] ^= 1
    shm.write_bytes(raw)
    try:
        assert read_library_snapshot(path).reasons == {'snapshot_changed'}
    finally:
        shm.write_bytes(original)


@pytest.mark.parametrize('options', [{'seconds': 'private'}, {'max_rows': 0}, {'max_bytes': True}])
def test_invalid_snapshot_options_are_rejected_before_capture(wal, options):
    with pytest.raises(ValueError):
        read_library_snapshot(wal[0], **options)


def test_missing_shm_and_rollback_journal_are_unavailable(wal, tmp_path):
    path, _ = wal
    shm = Path(str(path)+'-shm')
    saved = tmp_path / 'saved-shm'
    shm.rename(saved)
    try:
        before = filesystem(tmp_path)
        assert read_library_snapshot(path).reasons
        assert filesystem(tmp_path) == before
    finally:
        saved.rename(shm)
    journal = Path(str(path)+'-journal')
    journal.write_bytes(b'PRIVATE')
    before = filesystem(tmp_path)
    assert read_library_snapshot(path).reasons
    assert filesystem(tmp_path) == before


def test_native_replay_vfs_does_not_modify_copied_images(wal):
    path, _ = wal
    image = path.read_bytes()
    log = Path(str(path)+'-wal').read_bytes()
    result = _replay(image, log, 128*1024*1024, time.monotonic()+5)
    assert image == path.read_bytes()
    assert log == Path(str(path)+'-wal').read_bytes()
    db = sqlite3.connect(':memory:')
    try:
        db.deserialize(result[:18]+b'\x01\x01'+result[20:])
        assert db.execute('SELECT artifact_id FROM artifacts').fetchall() == [('committed',)]
    finally:
        db.close()


def test_wal_child_stdout_closed_but_process_still_alive_is_time_budget(
    wal, monkeypatch,
):
    """A child can close stdout before it exits: wait() deadline still counts."""
    from cifereca.core import library_wal_image as capture
    real_popen = subprocess.Popen
    children = []

    def stalled_child(*_args, **_kwargs):
        # Synthetic child only; does not open the user's source database.
        child = real_popen(
            [sys.executable, '-B', '-c',
             'import os,time; os.close(1); time.sleep(3)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True,
        )
        children.append(child)
        return child

    monkeypatch.setattr(capture.subprocess, 'Popen', stalled_child)
    started = time.monotonic()
    image, reason = capture.read_wal_image(wal[0], max_bytes=1024*1024,
                                           seconds=0.35)
    assert image is None and reason == 'snapshot_time_budget'
    assert time.monotonic() - started < 2.0
    assert len(children) == 1 and children[0].poll() is not None
    assert children[0].stdout.closed


def test_real_wal_snapshot_of_524_rows_keeps_all_source_bytes(wal, tmp_path):
    """Exercise the actual Linux child/WAL in a 524-entry synthetic catalog."""
    import json
    path, db = wal
    rows = [(f'synthetic-{i:04}', 'alice', 'kobo', f'item-{i:04}',
             json.dumps({'identifiers': [{'scheme': 'isbn13',
                                          'value': '9780306406157'}]}))
            for i in range(524)]
    db.executemany(
        'INSERT INTO catalog_entries '
        '(catalog_entry_id, identity_id, provider_id, item_id, metadata_json) '
        'VALUES (?, ?, ?, ?, ?)', rows,
    )
    db.commit()
    before = filesystem(tmp_path)
    start = time.monotonic()
    result = read_library_snapshot(path, seconds=10)
    duration = time.monotonic()-start
    assert not result.reasons
    assert len(result.tables['catalog_entries']) == 524
    assert duration < 10
    assert filesystem(tmp_path) == before


def test_snapshot_trace_distinguishes_direct_and_wal_outcomes_without_paths(
    wal, monkeypatch, caplog,
):
    """Only closed statuses and bounded durations enter user debug logs."""
    import logging
    from cifereca.core import library_reconciliation as reconciliation
    path, _ = wal
    caplog.set_level(logging.INFO, logger='cifereca')
    assert not read_library_snapshot(path).reasons
    rows = [line for line in caplog.messages
            if 'TRACE reconciliation_snapshot' in line]
    assert any('phase=start stage=direct' in row for row in rows)
    assert any('phase=handoff stage=wal' in row for row in rows)
    assert any('phase=finish stage=wal code=complete duration_ms=' in row
               for row in rows)
    assert 'PRIVATE' not in '\n'.join(rows)
    caplog.clear()
    monkeypatch.setattr(
        reconciliation, 'read_quiescent_snapshot',
        lambda *_a, **_kw: reconciliation.EvidenceSnapshot(
            reasons={'snapshot_live_journal'},
        ),
    )
    from cifereca.core import library_wal_image
    monkeypatch.setattr(
        library_wal_image, 'read_wal_image',
        lambda *_a, **_kw: (None, 'snapshot_time_budget'),
    )
    failure = read_library_snapshot(path)
    assert failure.reasons == {'snapshot_time_budget'}
    assert any('phase=finish stage=wal code=snapshot_time_budget' in line
               for line in caplog.messages)
    assert 'PRIVATE' not in '\n'.join(caplog.messages)


def test_snapshot_trace_never_logs_untrusted_failure_details(
    wal, monkeypatch, caplog,
):
    import logging
    from cifereca.core import library_reconciliation as reconciliation
    caplog.set_level(logging.INFO, logger='cifereca')
    monkeypatch.setattr(
        reconciliation, 'read_quiescent_snapshot',
        lambda *_a, **_kw: reconciliation.EvidenceSnapshot(
            reasons={'/PRIVATE/account?token=secret'},
        ),
    )
    assert read_library_snapshot(wal[0]).reasons
    lines = [line for line in caplog.messages
             if 'TRACE reconciliation_snapshot' in line]
    assert any('code=incomplete' in line for line in lines)
    assert 'PRIVATE' not in '\n'.join(lines)
    assert 'secret' not in '\n'.join(lines)


def test_wal_child_reports_only_closed_stages_without_source_paths(
    wal, caplog,
):
    """Real child stages identify capture/verify/replay/emit without DB data."""
    import logging
    caplog.set_level(logging.INFO, logger='cifereca')
    outcome = read_library_snapshot(wal[0])
    assert not outcome.reasons
    entries = [line for line in caplog.messages if
               'TRACE reconciliation_wal phase=stage' in line]
    expected = ('capture', 'validate', 'replay', 'serialize', 'emit')
    assert [entry.split('step=')[1].split()[0] for entry in entries] == list(expected)
    assert all('elapsed_ms=' in line for line in entries)
    assert 'PRIVATE' not in '\n'.join(entries)
    assert str(wal[0]) not in '\n'.join(entries)


def test_wal_child_timeout_stage_is_visible_without_external_error_text(
    wal, monkeypatch, caplog,
):
    """The timeout identifies the last child phase, never untrusted stderr."""
    import logging
    from cifereca.core import library_wal_image as capture
    caplog.set_level(logging.INFO, logger='cifereca')
    real_popen = subprocess.Popen
    def stalled_child(*_args, **_kwargs):
        return real_popen(
            [sys.executable, '-B', '-c',
             'import os,sys,time; '
             'sys.stderr.write("PRIVATE_TOKEN\\nCIFERECA_WAL_PHASE:capture\\n'
             'CIFERECA_WAL_PHASE:unknown\\n"); sys.stderr.flush(); '
             'os.close(1); time.sleep(3)'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True,
        )
    monkeypatch.setattr(capture.subprocess, 'Popen', stalled_child)
    image, reason = capture.read_wal_image(wal[0], max_bytes=1024*1024,
                                           seconds=0.35)
    assert image is None and reason == 'snapshot_time_budget'
    entries = [line for line in caplog.messages if
               'TRACE reconciliation_wal phase=stage' in line]
    assert len(entries) == 1
    assert 'step=capture' in entries[0]
    assert 'PRIVATE_TOKEN' not in '\n'.join(caplog.messages)
    assert 'step=unknown' not in '\n'.join(caplog.messages)
