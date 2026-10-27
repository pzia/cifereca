"""Automatic BnF fulfillment needs a durable per-account opt-in."""
from pathlib import Path
from types import SimpleNamespace as NS

import cifereca.services as facade
import httpx
import pytest
from cifereca.brokers import SynchronizationBrokerImpl
from cifereca.core.bnf_auto_materialization import enabled, materialize
from tests.test_core_p0_2055_bnf_explicit_stage import connected_core


def test_bnf_auto_default_denies_get_then_imports_once(connected_core, tmp_path, monkeypatch):
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert not enabled(service, account)
    assert materialize(service, account, loan)['status'] == 'skipped'
    assert not downloaded
    monkeypatch.setattr(facade, 'data_dir', lambda: tmp_path / 'data')
    monkeypatch.setattr(service, 'resolve_adept_profile_for_identity', lambda _: ('synthetic', 'identity'))
    monkeypatch.setattr(service, '_originating_adept_profile_id', lambda *a: None)
    requests = []
    def fulfill(request):
        requests.append(request)
        path = Path(request['destination'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'%PDF-1.4\n% synthetic book\n%%EOF\n')
        return {'ok': True, 'artifact': {'path': str(path)}}
    monkeypatch.setattr(service, 'execute_acquisition_request', fulfill)
    assert broker.set_bnf_auto_download(sid, True) is True
    service.current_identity = NS(identity_id='another-visible-identity')
    assert materialize(service, account, loan) == {'status': 'complete'}
    assert len(downloaded) == len(requests) == 1
    assert materialize(service, account, loan)['status'] == 'unchanged'
    assert len(downloaded) == len(requests) == 1
    row, = service.storage.acquisitions(provider_id='manual')
    assert row['metadata']['bnf_auto_ref'] and '/bnf-pret/acsm-' in row['metadata']['path']
    assert not service.storage.entitlements(provider_id='bnf-pret')
    assert broker.set_bnf_auto_download(sid, False) is False
    assert materialize(service, account, loan)['status'] == 'skipped'


def test_bnf_auto_rejects_changed_account_before_http(connected_core):
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    account.unique_id = 'different-account'
    assert materialize(service, account, loan)['status'] in {'attention_required', 'skipped'}
    assert not downloaded


def test_targeted_sync_dispatches_only_after_opt_in(connected_core, monkeypatch):
    from cifereca.core import bnf_auto_materialization
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    calls = []
    def fake_materialize(_service, _account, row, *, control=None, physical_scan_cache=None):
        calls.append(row.id)
        return {'status': 'complete'}
    monkeypatch.setattr(bnf_auto_materialization, 'materialize', fake_materialize)
    first = broker.synchronize_source(sid)
    assert first.facts['status'] == 'partial' and not calls
    assert broker.set_bnf_auto_download(sid, True)
    second = broker.synchronize_source(sid)
    assert second.facts['status'] == 'partial' and calls == [loan.id]
    assert not downloaded  # synthetic acquisition stub only


def test_opening_sync_dispatches_opted_in_bnf_loan(connected_core, monkeypatch):
    from cifereca.core import bnf_auto_materialization
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    calls = []
    monkeypatch.setattr(bnf_auto_materialization, 'materialize',
                        lambda _service, _account, row, **_kwargs: calls.append(row.id) or {'status': 'complete'})
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    service.current_identity = NS(identity_id='another-visible-identity')
    service.opening_sync()
    assert calls == [loan.id]


@pytest.mark.parametrize('status', [403, 500, 302])
def test_refused_signed_get_never_starts_adept(connected_core, monkeypatch, status):
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    calls=[]
    monkeypatch.setattr(service,'import_acsm',lambda *a,**kw:calls.append(kw))
    original=session.client.http._transport.handler
    def reply(request):
        if request.url.path.startswith('/loans/'):
            downloaded.append(request)
            return httpx.Response(status,content=b'<html>synthetic refusal</html>',
                                  headers={'location':'https://other.invalid/redirect'})
        return original(request)
    session.client.http._transport.handler=reply
    assert broker.set_bnf_auto_download(broker._source_id(account),True)
    assert materialize(service,account,loan)['status']=='attention_required'
    assert len(downloaded)==1 and not calls and not service.storage.acquisitions()


def test_revoked_policy_during_get_blocks_adept(connected_core, monkeypatch):
    service, session, account, loan, state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert broker.set_bnf_auto_download(sid, True)
    original = session.download_digital
    calls=[]
    monkeypatch.setattr(service, 'import_acsm', lambda *a, **kw: calls.append(kw))
    def revoke(*args, **kwargs):
        candidate = original(*args, **kwargs)
        broker.set_bnf_auto_download(sid, False)
        return candidate
    monkeypatch.setattr(session, 'download_digital', revoke)
    assert materialize(service, account, loan)['status'] == 'attention_required'
    assert len(downloaded) == 1 and not calls

def test_bnf_auto_skips_other_manual_acquisitions_before_inspecting_files(
        connected_core, monkeypatch):
    """An opted-in sync must never read unrelated local manual EPUB/PDFs."""
    from cifereca.core import bnf_auto_materialization as auto

    service, session, account, loan, _state, _downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    monkeypatch.setattr(
        service.storage, 'acquisitions', lambda **_kwargs: [
            {'identity_id': 'other-identity',
             'metadata': {'bnf_auto_ref': 'other-ref', 'path': '/unrelated/private.pdf'}},
            {'identity_id': account.identity_id,
             'metadata': {'bnf_auto_ref': 'other-ref', 'path': '/different/manual.epub'}},
        ],
    )
    inspections = []
    monkeypatch.setattr(
        auto, 'inspect_artifact', lambda path: inspections.append(path)
        or (_ for _ in ()).throw(AssertionError('unrelated-file-inspected')),
    )
    attempted = []
    def stop_before_get(*_args, **_kwargs):
        attempted.append(True)
        raise RuntimeError('synthetic-stop-before-get')
    monkeypatch.setattr(session, 'download_digital', stop_before_get)
    assert auto.materialize(service, account, loan) == {
        'status': 'attention_required', 'code': 'bnf_auto_acquisition_failed',
    }
    assert attempted == [True]
    assert inspections == []


@pytest.mark.parametrize(("field", "replacement"), [
    ("state", "returned"),
    ("medium", "physical"),
    ("identity_id", "other-identity"),
    ("source_provider", "paris"),
    ("source_credential", "other-credential"),
    ("source_account_id", "other-account"),
])
def test_auto_bnf_rechecks_loan_scope_after_signed_get_before_adept(
    connected_core, monkeypatch, field, replacement,
):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    sid = broker._source_id(account)
    assert broker.set_bnf_auto_download(sid, True)
    original = session.download_digital
    imports = []
    monkeypatch.setattr(service, "import_acsm", lambda *a, **kw: imports.append((a, kw)))
    def mutate_after_get(*args, **kwargs):
        candidate = original(*args, **kwargs)
        setattr(loan, field, replacement)
        return candidate
    monkeypatch.setattr(session, "download_digital", mutate_after_get)
    outcome = materialize(service, account, loan)
    assert outcome == {"status": "attention_required", "code": "bnf_loan_scope_unproven"}
    assert len(downloaded) == 1
    assert imports == []
    assert service.storage.acquisitions(provider_id="manual") == []


@pytest.mark.parametrize(("field", "replacement"), [
    ("state", "returned"), ("medium", "physical"),
    ("source_provider", "other"), ("source_account_id", "foreign"),
])
def test_auto_bnf_rejects_mutated_loan_before_get(
    connected_core, field, replacement,
):
    service, session, account, loan, _state, downloaded = connected_core
    broker = SynchronizationBrokerImpl(service)
    assert broker.set_bnf_auto_download(broker._source_id(account), True)
    setattr(loan, field, replacement)
    result = materialize(service, account, loan)
    assert result["status"] == "attention_required"
    assert downloaded == []
