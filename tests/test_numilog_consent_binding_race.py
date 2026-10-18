"""A confirmation cannot transfer an earlier file preview to a new binding."""
from types import SimpleNamespace as NS
import pytest
from tests.test_core_p0_numilog_auto_materialization import _setup
from cifereca.core.numilog_materialization_consent import mode


@pytest.mark.parametrize('stage', ['prepare','confirm'])
@pytest.mark.parametrize('mutation', ['session','root','owner'])
def test_changed_binding_during_verified_preview_cannot_create_policy(tmp_path, monkeypatch, stage, mutation):
    service, account, session, calls, broker, sid = _setup(tmp_path)
    try:
        proposal = broker.prepare_numilog_materialization_consent(sid) if stage == 'confirm' else None
        if proposal is not None:assert proposal.ok
        original = broker.preview_local_materialization
        def change(_broker, *args, **kwargs):
            result=original(*args, **kwargs)
            if mutation == 'session':
                service._sessions[service._session_key('numilog',account.credential_id)] = NS(**vars(session))
            if mutation == 'root':service.config.set('library.directory',str(tmp_path/'PRIVATE-new-root'))
            if mutation == 'owner':service.storage.provider_connection_put({
                'provider_id':'numilog','credential_id':account.credential_id,'identity_id':'alex'})
            return result
        monkeypatch.setattr(type(broker),'preview_local_materialization',change)
        result = (broker.prepare_numilog_materialization_consent(sid) if stage == 'prepare' else
                  broker.confirm_numilog_materialization_consent(sid,proposal.facts['confirmation_token'],mode='on_start'))
        assert not result.ok
        assert mode(broker,sid) == 'manual'
        assert calls == [] and 'PRIVATE' not in repr(result)
        if stage == 'prepare':assert not broker._numilog_consent_plans.entries
        else:assert proposal.facts['confirmation_token'] not in broker._numilog_consent_plans.entries
    finally:service.close()
