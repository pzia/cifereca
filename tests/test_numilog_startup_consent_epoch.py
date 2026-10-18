"""A stale startup cannot spend a newly reactivated consent's claim."""
import pytest
from cifereca.core.numilog_materialization_startup import execute
from tests.test_core_p0_numilog_auto_materialization import _setup, _authorize


def test_reactivated_consent_is_not_claimed_by_an_older_queued_job(tmp_path, monkeypatch):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker,sid)
        session.library=lambda:calls.append('library') or []
        old_epoch=service.storage.numilog_materialization_consent_revision(sid)
        original=service.storage.numilog_materialization_claim_startup
        def replace_grant(**kwargs):
            broker.revoke_numilog_materialization_consent(sid)
            _authorize(broker,sid)
            return original(**kwargs)
        monkeypatch.setattr(service.storage,'numilog_materialization_claim_startup',replace_grant)
        result=execute(broker,account.identity_id,'startup')
        assert result.changed == 0 and calls == []
        assert service.storage.numilog_materialization_consent_revision(sid) != old_epoch
        assert service.storage.metadata_get('sync.source.numilog.materialization.last_start.v1.'+sid) is None
        assert service.storage.metadata_get('sync.source.numilog.materialization.result.v1.'+sid) is None
        # The legitimate new grant can still run once at its own opening.
        monkeypatch.setattr(service.storage,'numilog_materialization_claim_startup',original)
        fresh=execute(broker,account.identity_id,'startup')
        assert fresh.ok and calls == ['library']
    finally:service.close()


@pytest.mark.parametrize('corrupt', ['missing','invalid','storage_failure'])
def test_bad_consent_epoch_does_not_starve_an_independent_account(tmp_path, monkeypatch, corrupt):
    from tests.test_core_p0_numilog_startup_account_isolation import _two_accounts, _safe_mock_downloads
    from tests.test_core_p0_1862_numilog_store_mirror import _row
    service, broker, first, second = _two_accounts(tmp_path)
    a,session_a,calls_a,sid_a=first
    b,session_b,calls_b,sid_b=second
    try:
        key='sync.source.numilog.materialization.epoch.v1.'+sid_a
        if corrupt == 'missing':
            with service.storage._connect() as db:db.execute('DELETE FROM metadata WHERE key=?',(key,))
        if corrupt == 'invalid':service.storage.metadata_set(key,'PRIVATE_INVALID_EPOCH')
        if corrupt == 'storage_failure':
            original=service.storage.numilog_materialization_consent_revision
            def fail(sid):
                if sid == sid_a:raise RuntimeError('PRIVATE storage source detail')
                return original(sid)
            monkeypatch.setattr(service.storage,'numilog_materialization_consent_revision',fail)
        session_a.library=lambda:pytest.fail('invalid consent cannot make a provider request')
        session_b.library=lambda:[_row(b.credential_id,0)]
        acquired=_safe_mock_downloads(monkeypatch,service)
        result=execute(broker,'default','startup')
        assert not result.ok and result.changed == 1 and result.facts['status'] == 'partial'
        assert result.facts['failed'] == 1
        assert [item.source_credential for item in acquired] == [b.credential_id]
        assert calls_a == [] and 'PRIVATE' not in repr(result)
        assert service.storage.metadata_get('sync.source.numilog.materialization.last_start.v1.'+sid_a) is None
    finally:service.close()


def test_claim_revision_is_compared_against_other_connection_in_same_transaction(tmp_path):
    from time import time
    from cifereca.storage import Storage
    service, account, session, calls, broker, sid = _setup(tmp_path)
    other=Storage(tmp_path/'db.sqlite3')
    try:
        _authorize(broker,sid)
        old=service.storage.numilog_materialization_consent_revision(sid)
        other.numilog_materialization_change_consent(sid,activate=False)
        revoked=other.numilog_materialization_consent_revision(sid)
        assert other.numilog_materialization_change_consent(sid,activate=True,expected_revision=revoked)
        new=other.numilog_materialization_consent_revision(sid)
        clock=int(time())
        assert not service.storage.numilog_materialization_claim_startup(source_id=sid,now=clock,expected_revision=old)
        assert other.metadata_get('sync.source.numilog.materialization.last_start.v1.'+sid) is None
        assert service.storage.numilog_materialization_claim_startup(source_id=sid,now=clock,expected_revision=new)
        assert not other.numilog_materialization_claim_startup(source_id=sid,now=clock,expected_revision=new)
        assert calls == []
    finally:other.close();service.close()


def test_cancelled_epoch_read_is_not_changed_into_a_source_failure(tmp_path, monkeypatch):
    from cifereca.plugin_api import Cancelled
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        _authorize(broker,sid)
        def stop(_sid):raise Cancelled()
        monkeypatch.setattr(service.storage,'numilog_materialization_consent_revision',stop)
        with pytest.raises(Cancelled):execute(broker,account.identity_id,'startup')
        assert calls == []
        assert service.storage.metadata_get('sync.source.numilog.materialization.last_start.v1.'+sid) is None
    finally:service.close()
