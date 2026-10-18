"""Admission traces reveal no source/borrower coordinates or raw guard code."""
from dataclasses import replace
import logging
from pathlib import Path
import re

import pytest
from cifereca.core.loan_acquisition_admission import resolve
from cifereca.core.loan_safe_trace import loan_trace_ref
from tests.test_automatic_loan_admission import _setup, _forbid_work

PRIVATE = 'PRIVATE_BORROWER_VALUE'


def _events(caplog):
    events = [r for r in caplog.records if Path(r.pathname).name == 'loan_acquisition_admission.py']
    assert events
    raw = '\n'.join(r.getMessage() + repr(r.args) for r in events)
    assert PRIVATE not in raw
    for coordinate in ('source-a', 'default', 'loan-0', 'edition-0', 'Synthetic'):
        assert coordinate not in raw
    assert all('ref=' in r.getMessage() for r in events)
    return [r.getMessage() for r in events]


@pytest.mark.parametrize('count', [0, 1, 4])
def test_success_keeps_exact_owner_right_and_correlates_request_with_private_loan_ref(tmp_path, monkeypatch, caplog, count):
    caplog.set_level(logging.INFO)
    service, loan = _setup(tmp_path)
    loan = replace(loan, id=PRIVATE+'loan', record_id=PRIVATE+'edition', title=PRIVATE+'title')
    try:
        if count:
            service._persist_active_loans('default', [loan if i == 0 else replace(loan, id=loan.id+str(i), record_id=loan.record_id+str(i))
                                                     for i in range(count)])
        before = service.storage.entitlements(provider_id='paris', state='active')
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=logging.getLogger('test'))
        assert result.identity.identity_id == 'default' and result.refusal is None
        expected = next((r['entitlement_id'] for r in before if r['loan_id'] == loan.id), None)
        assert result.entitlement_id == expected
        assert service.storage.entitlements(provider_id='paris', state='active') == before
        assert service.current_identity.identity_id == 'alex'
        messages = _events(caplog)
        assert len(messages) == 1
        assert 'request_ref='+loan_trace_ref(loan, None) in messages[0]
        assert 'loan_ref='+loan_trace_ref(loan, 'default') in messages[0]
        assert len(re.findall(r'[a-f0-9]{20}', messages[0])) == 2
    finally:
        service.close()


@pytest.mark.parametrize('scenario', ['unbound', 'borrower', 'foreign_ledger', 'foreign_right'])
def test_refusals_preserve_public_codes_without_putting_them_or_coordinates_into_trace(tmp_path, monkeypatch, caplog, scenario):
    caplog.set_level(logging.INFO)
    service, loan = _setup(tmp_path, bound=scenario != 'unbound')
    try:
        if scenario == 'borrower':
            monkeypatch.setattr(service, '_paris_adept_identity_for_loan',
                                lambda *a, **kw: (None, 'https://private.invalid?auth='+PRIVATE))
        elif scenario == 'foreign_ledger':
            service.storage.acquisition_put({'acquisition_id': 'prior', 'provider_id': 'paris',
                'credential_id': 'source-b', 'account_id': 'primary', 'identity_id': 'alex',
                'item_id': loan.record_id, 'status': 'complete', 'strategy': 'loan_auto',
                'metadata': {'provider_loan_id': loan.id}})
        elif scenario == 'foreign_right':
            service._persist_active_loans('alex', [replace(loan, source_credential='source-b', identity_id='alex')])
        before = service.storage.acquisitions()
        _forbid_work(service, monkeypatch)
        result = resolve(service, loan, _TRACE_LOG=logging.getLogger('test'))
        code = ('adept_identity_unproven' if scenario == 'unbound' else
                'https://private.invalid?auth='+PRIVATE if scenario == 'borrower' else
                'adept_loan_identity_conflict')
        assert result.refusal['code'] == code
        assert result.refusal['status'] == 'attention_required' and result.refusal['retry'] == 'explicit_only'
        assert service.storage.acquisitions() == before
        assert service.current_identity.identity_id == 'alex'
        events = _events(caplog)
        assert all('http' not in message for message in events)
        assert len(events) == (1 if scenario == 'unbound' else 2)
    finally:
        service.close()
