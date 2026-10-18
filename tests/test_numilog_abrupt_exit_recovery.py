"""Process-crash durability of the delivered-file receipt, not power loss.

The provider/file is synthetic; acquisition guards, locks, WAL, indexer and
recovery are real. os._exit skips Python cleanup and Storage.close().
"""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest
from tests.test_sync_state_contract import _service

ROOT = Path(__file__).resolve().parents[1]
CHILD = textwrap.dedent('''
    import os, sys
    from pathlib import Path
    from tests.test_sync_state_contract import _service
    from tests.test_core_p0_1862_numilog_store_mirror import _connect, _row
    from tests.test_kobo_backup_fault_isolation import _write_epub

    root, phase = Path(sys.argv[1]), sys.argv[2]
    service = _service(root)
    account, session, _ = _connect(service, number=0)
    item = _row(account.credential_id, 0)
    def download(selected, directory):
        with (root/'provider-calls').open('a') as counter:
            counter.write('one exact purchase\\n'); counter.flush(); os.fsync(counter.fileno())
        path = Path(directory)/'crash-proof.epub'
        _write_epub(path)
        return path, 'https://private.invalid?token=PRIVATE'
    session.download_direct = download
    service.acquisition.run_post_download = lambda *_a:None
    if phase == 'delivered':
        service.acquire_direct_item(item)
        os._exit(73)
    if phase == 'indexed':
        def stop_before_complete(*_a, **_k):os._exit(73)
        service.storage.numilog_checkpoint_complete = stop_before_complete
    if phase == 'uncommitted_complete':
        complete = service.storage.numilog_checkpoint_complete
        connect = service.storage._connect
        class CrashAfterUpdate:
            def __init__(self, db):self.db = db
            def __enter__(self):self.db.__enter__();return self
            def __exit__(self, *args):return self.db.__exit__(*args)
            def execute(self, sql, *args):
                result = self.db.execute(sql, *args)
                if sql.startswith("UPDATE acquisition_ledger SET status='complete'"):
                    assert self.db.in_transaction
                    os._exit(73)
                return result
        def stop_inside_transaction(*args, **kwargs):
            service.storage._connect = lambda:CrashAfterUpdate(connect())
            return complete(*args, **kwargs)
        service.storage.numilog_checkpoint_complete = stop_inside_transaction
    result = service.obtain_search_item(item)
    assert phase == 'complete' and result.indexed['status'] == 'indexed'
    os._exit(73)
''')


@pytest.mark.parametrize('phase', ['delivered', 'indexed', 'uncommitted_complete', 'complete'])
def test_abrupt_process_exit_resumes_same_bytes_without_provider_or_duplicate_artifact(tmp_path, monkeypatch, phase):
    # Parent and child share a dedicated process-lock namespace.
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path/'state'))
    child = subprocess.run([sys.executable, '-c', CHILD, str(tmp_path), phase],
                           cwd=ROOT, env=os.environ.copy(), capture_output=True, text=True, timeout=20)
    assert child.returncode == 73, child.stderr
    assert (tmp_path/'provider-calls').read_text().splitlines() == ['one exact purchase']
    service = _service(tmp_path)
    try:
        assert not any(a.provider_id == 'numilog' for a in service.accounts)
        assert not service._sessions
        rows = service.storage.acquisitions(provider_id='numilog')
        assert len(rows) == 1
        row = rows[0]
        assert row['status'] == ('complete' if phase == 'complete' else 'attention_required')
        assert row['metadata']['phase'] == ('indexed' if phase == 'complete' else 'index_pending')
        assert 'PRIVATE' not in repr(row)
        path = Path(row['metadata']['path'])
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = service.resume_numilog_materialization(row['acquisition_id'])
        assert result.path == path and result.identity_id == 'default'
        assert result.artifact_id == 'sha256:' + before
        assert result.indexed['artifact_new'] is (phase == 'delivered')
        assert service.storage.acquisition_get(row['acquisition_id'])['status'] == 'complete'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        again = service.resume_numilog_materialization(row['acquisition_id'])
        assert again.artifact_id == result.artifact_id and not again.indexed['artifact_new']
        with service.storage._connect() as db:
            assert db.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0] == 1
            assert db.execute("SELECT COUNT(*) FROM placements WHERE provider_id='numilog'").fetchone()[0] == 1
        assert (tmp_path/'provider-calls').read_text().splitlines() == ['one exact purchase']
    finally:service.close()
