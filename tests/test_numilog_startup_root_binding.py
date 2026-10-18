"""A queued startup must not transfer its purchase preview to a new root."""
from pathlib import Path
import pytest
from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_kobo_backup_fault_isolation import _write_epub


@pytest.mark.parametrize('stage', ['manifest', 'locked_admission'])
def test_root_change_during_startup_blocks_before_delivery(tmp_path, monkeypatch, stage):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    delivered = []
    try:
        _authorize(broker, sid)
        row = _row(account.credential_id, 0)
        def move_root():
            service.config.set('library.directory', str(tmp_path/'new-root'))
        def library():
            if stage == 'manifest': move_root()
            return [row]
        session.library = library
        def download(item, directory):
            delivered.append(item.record_id)
            path = Path(directory)/'exact.epub'
            _write_epub(path)
            return path, ''
        session.download_direct = download
        monkeypatch.setattr(service.acquisition, 'run_post_download', lambda *_a: None)
        if stage == 'locked_admission':
            original = service.acquire_direct_item
            def after_queue(item, **kwargs):
                move_root()
                return original(item, **kwargs)
            monkeypatch.setattr(service, 'acquire_direct_item', after_queue)
        result = execute(broker, account.identity_id, 'startup')
        assert not result.ok and result.changed == 0
        assert delivered == [] and service.storage.acquisitions(provider_id='numilog') == []
        if stage == 'manifest':
            assert service.storage.entitlements(provider_id='numilog') == []
        assert not list((tmp_path/'new-root').rglob('*.epub'))
        assert 'new-root' not in repr(result)
    finally:
        service.close()
