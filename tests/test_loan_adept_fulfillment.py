"""One execution, exact pinned origin and lazy binding; no retries or fallback."""
from types import SimpleNamespace as NS
import logging

import pytest

from cifereca.core.loan_adept_fulfillment import fulfill
from cifereca.core.loan_safe_trace import trace_integer
from cifereca.plugin_api import UserFacingError

PRIVATE='PRIVATE_REQUEST_PATH_ACCOUNT_PROFILE'


def call(service, request, *, clock=None):
    ticks=iter((10.0,10.25))
    return fulfill(service,request,profile_name=PRIVATE,adept_borrower_identity_id='pinned-borrower',
        trace_ref='a'*20,time=clock or NS(monotonic=lambda:next(ticks)),
        _TRACE_LOG=logging.getLogger('fulfillment-test'),trace_integer=trace_integer)


def test_execution_capture_and_diagnostic_keep_exact_order_identity_objects_and_safe_trace(monkeypatch,caplog):
    calls=[]
    request={'source':PRIVATE,'destination':PRIVATE,'metadata':{'loan_id':PRIVATE}}
    result={'ok':True,'profile':PRIVATE,'loan_id':PRIVATE,'artifact':{'path':PRIVATE}}
    binding={'private':PRIVATE}
    def origin(owner,profile):
        assert owner=='pinned-borrower' and profile==PRIVATE
        calls.append('origin');return PRIVATE+'-origin'
    def execute(received):
        assert received is request;calls.append('execute');return result
    def capture(received,*,profile_name,profile_id):
        assert received is result and profile_name==PRIVATE and profile_id==PRIVATE+'-origin'
        calls.append('capture');return binding
    def diagnostic(runtime):
        assert runtime=='runtime.kobo-adept';calls.append('diagnostic')
        return {'returncode':PRIVATE,'duration_ms':True,'stdout':PRIVATE}
    monkeypatch.setattr('cifereca.adept_binding.capture_binding',capture)
    service=NS(_originating_adept_profile_id=origin,execute_acquisition_request=execute,runtime_diagnostic=diagnostic)
    with caplog.at_level(logging.INFO):actual=call(service,request)
    assert actual[0] is result and actual[1] is binding
    assert calls==['origin','execute','capture','diagnostic']
    assert PRIVATE not in '\n'.join(r.getMessage()+repr(r.args) for r in caplog.records)
    assert 'returncode=None duration_ms=None dt=0.250s' in caplog.text


@pytest.mark.parametrize('stage',['origin','execute','capture','diagnostic'])
def test_original_error_propagates_once_without_later_calls_or_retry(monkeypatch,stage):
    calls=[]
    error=UserFacingError(PRIVATE,code=PRIVATE)
    error.retryable=True
    def operation(name,result):
        def run(*a,**kw):
            calls.append(name)
            if name==stage:raise error
            return result
        return run
    monkeypatch.setattr('cifereca.adept_binding.capture_binding',operation('capture',{'binding':PRIVATE}))
    service=NS(_originating_adept_profile_id=operation('origin',PRIVATE),
        execute_acquisition_request=operation('execute',{'ok':True}),
        runtime_diagnostic=operation('diagnostic',{}))
    with pytest.raises(UserFacingError) as caught:call(service,{'private':PRIVATE})
    assert caught.value is error
    order=['origin','execute','capture','diagnostic']
    assert calls==order[:order.index(stage)+1]


def test_real_binding_validation_and_absent_loan_meaning_are_preserved():
    result={'ok':True,'profile':PRIVATE}
    service=NS(_originating_adept_profile_id=lambda *_:PRIVATE,
        execute_acquisition_request=lambda _:result,runtime_diagnostic=lambda _: {})
    actual,binding=call(service,{})
    assert actual is result and binding['adept_profile_name']==PRIVATE
    assert binding['adept_loan_id'] is None and binding['return_available'] is False
    result['profile']='other'
    with pytest.raises(ValueError,match='Profil ADEPT'):call(service,{})
