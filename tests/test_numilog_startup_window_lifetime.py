"""Closing a host must stop a consented startup between exact books."""
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.plugin_api import Cancelled
from cifereca.gui.numilog_materialization_startup import dispatch_after_opening
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize
from tests.test_core_p0_1862_numilog_store_mirror import _row
from tests.test_kobo_backup_fault_isolation import _write_epub


@pytest.mark.parametrize('change', ['close','broker','none'])
def test_gui_lifetime_stops_after_inflight_book_without_next_provider_request(tmp_path, monkeypatch, change):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    tasks=[];progress=[];acquired=[];holder=[broker]
    window=NS(service=service,plugin_manager=NS(broker=lambda _name:holder[0]),
              runner=NS(submit=lambda *args,**kwargs:tasks.append((args,kwargs))),_closed=False)
    rows=[_row(account.credential_id,i) for i in range(2)]
    try:
        _authorize(broker,sid)
        session.library=lambda:rows
        def download(item,directory):
            acquired.append(item.record_id)
            path=Path(directory)/('exact-'+str(len(acquired))+'.epub');_write_epub(path)
            if change == 'close':window._closed=True
            elif change == 'broker':holder[0]=SynchronizationBrokerImpl(service)
            return path,'https://private.invalid?auth=PRIVATE'
        session.download_direct=download
        monkeypatch.setattr(service.acquisition,'run_post_download',lambda *_a:None)
        assert dispatch_after_opening(window) and len(tasks)==1
        work=tasks[0][0][1]
        cancellation=None
        try:work(progress.append)
        except Cancelled as error:cancellation=error
        expected=rows if change == 'none' else rows[:1]
        assert acquired == [item.record_id for item in expected] and calls == []
        receipt=broker.numilog_materialization_run_status(sid)
        assert receipt['status']==('complete' if change == 'none' else 'partial')
        assert receipt['downloaded']==receipt['attempted']==len(expected)
        ledger=service.storage.acquisitions(provider_id='numilog')
        assert len(ledger)==len(expected) and all(row['status']=='complete' for row in ledger)
        if change == 'none':
            assert cancellation is None and progress == ['Sauvegarde Numilog : 1 livre(s)','Sauvegarde Numilog : 2 livre(s)']
        else:
            assert isinstance(cancellation,Cancelled) and progress==[]
        assert tasks[0][1]['with_progress'] is True
        assert 'PRIVATE' not in repr(progress)
    finally:service.close()


def test_closed_queued_host_cannot_consume_a_startup_claim(tmp_path):
    service,account,session,calls,broker,sid=_setup(tmp_path)
    tasks=[]
    window=NS(service=service,plugin_manager=NS(broker=lambda _name:broker),
              runner=NS(submit=lambda *a,**kw:tasks.append((a,kw))),_closed=False)
    try:
        _authorize(broker,sid)
        assert dispatch_after_opening(window)
        window._closed=True
        with pytest.raises(Cancelled):tasks[0][0][1]()
        assert calls==[]
        assert service.storage.metadata_get('sync.source.numilog.materialization.last_start.v1.'+sid) is None
    finally:service.close()
