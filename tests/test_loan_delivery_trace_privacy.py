"""#1120: delivery/index TRACE keeps correlation, never original coordinates."""
import logging
from pathlib import Path
from types import SimpleNamespace as NS
import time
import re

import pytest
from cifereca.core import loan_provider_delivery as stage
from cifereca.providers import ProviderConfigurationError


PRIVATE = 'PRIVATE_COORDINATE'


def _loan():
    return NS(id=PRIVATE+'loan', record_id=PRIVATE+'edition', title=PRIVATE+'title',
              source_provider=PRIVATE+'provider', source_credential=PRIVATE+'credential',
              source_account_id=PRIVATE+'account', identity_id=PRIVATE+'identity')


def _messages(caplog):
    records = [r for r in caplog.records if Path(r.pathname).name == 'loan_provider_delivery.py']
    assert records
    assert PRIVATE not in '\n'.join(r.getMessage() + repr(r.args) for r in records)
    assert all('loan_ref=' in r.getMessage() for r in records)
    return [r.getMessage() for r in records]


@pytest.mark.parametrize('force', [False, True])
def test_provider_payload_and_force_option_stay_intact_while_traces_are_redacted(tmp_path, caplog, force):
    caplog.set_level(logging.INFO)
    loan = _loan(); path = tmp_path / (PRIVATE + '.epub'); path.write_bytes(b'fixture')
    acquisition = NS(kind=PRIVATE, path=path)
    calls = []
    service = NS(acquire_digital_loan=lambda received, **kw: (calls.append((received, kw)) or acquisition))
    result = stage.acquire_provider_loan(service, loan, force_refresh=force, time=time,
                                        _TRACE_LOG=logging.getLogger('test'), identity_id=loan.identity_id)
    assert result is acquisition and result.path == path
    assert calls == [(loan, {'force_refresh': True} if force else {})]
    logs = _messages(caplog)
    assert len(logs) == 2 and 'kind=unknown' in logs[-1]
    assert len(set(re.search(r'loan_ref=([a-f0-9]{20})', x)[1] for x in logs)) == 1


@pytest.mark.parametrize('status', ['indexed', PRIVATE])
def test_index_keeps_coordinates_and_refusal_but_logs_only_bounded_facts(tmp_path, caplog, status):
    caplog.set_level(logging.INFO)
    loan = _loan(); path = tmp_path / (PRIVATE + '.epub'); path.write_bytes(b'fixture')
    scan = {'status': status, 'reason': PRIVATE}
    calls = []
    service = NS(catalog_index_path=lambda received, **kw: (calls.append((received, kw)) or scan))
    inspection = NS(valid=True, media_format=PRIVATE, protected=PRIVATE)
    def execute():
        return stage.index_local_loan_artifact(service, loan, NS(identity_id=loan.identity_id), path,
            time=time, _TRACE_LOG=logging.getLogger('test'), inspect_artifact=lambda p: inspection,
            ProviderConfigurationError=ProviderConfigurationError)
    if status == 'indexed':
        assert execute() == (inspection, scan)
    else:
        with pytest.raises(ProviderConfigurationError, match="^Indexation locale impossible$"):
            execute()
    assert calls == [(path, {'title': loan.title, 'item_id': loan.record_id,
                           'identity_id': loan.identity_id, 'provider_id': loan.source_provider})]
    logs = _messages(caplog)
    assert len(logs) == 2 and 'format=unknown protected=None' in logs[0]
    assert f'indexed={status == "indexed"}' in logs[1]
    assert path.read_bytes() == b'fixture'


def test_provider_exception_is_propagated_without_tracing_its_private_message(caplog):
    caplog.set_level(logging.INFO)
    failure = RuntimeError('https://private.invalid?auth='+PRIVATE)
    def fail(*a, **kw): raise failure
    with pytest.raises(RuntimeError) as caught:
        stage.acquire_provider_loan(NS(acquire_digital_loan=fail), _loan(), force_refresh=False,
                                    time=time, _TRACE_LOG=logging.getLogger('test'))
    assert caught.value is failure and len(_messages(caplog)) == 1


def test_both_stages_correlate_under_resolved_owner_not_stale_loan_identity(tmp_path, caplog):
    from cifereca.core.loan_safe_trace import loan_trace_ref
    caplog.set_level(logging.INFO)
    loan = _loan(); path = tmp_path / (PRIVATE + '.pdf'); path.write_bytes(b'fixture')
    service = NS(acquire_digital_loan=lambda *a, **kw: NS(kind='direct', path=path),
                 catalog_index_path=lambda *a, **kw: {'status': 'indexed'})
    owner = 'resolved-owner'
    stage.acquire_provider_loan(service, loan, force_refresh=False, time=time,
                                _TRACE_LOG=logging.getLogger('test'), identity_id=owner)
    stage.index_local_loan_artifact(service, loan, NS(identity_id=owner), path,
        time=time, _TRACE_LOG=logging.getLogger('test'),
        inspect_artifact=lambda p: NS(valid=True, media_format='pdf', protected=None),
        ProviderConfigurationError=ProviderConfigurationError)
    expected = loan_trace_ref(loan, owner)
    assert all('loan_ref='+expected in x for x in _messages(caplog))
    assert expected != loan_trace_ref(loan, loan.identity_id)


@pytest.mark.parametrize('repair', [False, True])
def test_adept_publication_logs_no_paths_or_runtime_metadata_and_keeps_original_result(tmp_path, caplog, repair):
    import uuid
    from cifereca.artifact_inspection import inspect_artifact
    from cifereca.core import adept_acsm_plan, adept_artifact_publication
    from cifereca.core.loan_safe_trace import loan_trace_ref
    caplog.set_level(logging.INFO)
    loan = _loan()
    staged = tmp_path / (PRIVATE + '.epub')
    pdf = b'%PDF-1.4\n%%EOF\n'; staged.write_bytes(pdf)
    destination = tmp_path / (PRIVATE+'-final.epub')
    previous = tmp_path / (PRIVATE+'-previous.epub'); previous.write_bytes(b'previous')
    hook = {'message': PRIVATE}
    outcome = adept_artifact_publication.publish(
        result={'artifact': {'path': str(staged), 'format': PRIVATE, 'drm': PRIVATE}, 'hook': hook},
        adept_destination=staged, acsm_format='epub', loan=loan, repair_mode=repair,
        destination=destination, previous_path=previous, Path=Path,
        ProviderConfigurationError=ProviderConfigurationError, inspect_artifact=inspect_artifact,
        adept_acsm_plan=adept_acsm_plan, uuid=uuid, _TRACE_LOG=logging.getLogger('test'),
        identity_id='resolved-owner')
    assert outcome[0].read_bytes() == pdf and outcome[1] == PRIVATE and outcome[2] == 'pdf'
    assert outcome[4] is hook
    records = [r for r in caplog.records if Path(r.pathname).name == 'adept_artifact_publication.py']
    assert len(records) == (3 if repair else 2)
    expected = loan_trace_ref(loan, 'resolved-owner')
    for record in records:
        assert PRIVATE not in record.getMessage() + repr(record.args)
        assert 'loan_ref='+expected in record.getMessage()
    assert previous.exists() is (not repair)
