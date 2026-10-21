"""Isolated explicit inventory; no diagnostic, binding or shared state changes."""
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

import pytest

from cifereca_kobo_adept_runtime import KoboAdeptRuntime
from cifereca_adept_readonly_inventory import _Stopped, _transport


@pytest.fixture
def runtime(tmp_path):
    script=tmp_path/'cli.py'
    script.write_text('''import sys,json
from pathlib import Path
root=Path(__file__).parent
with (root/'calls.jsonl').open('a') as log:log.write(json.dumps(sys.argv[1:])+'\\n')
args=sys.argv[1:]
if args==['profile','list','--json']:
 value=json.loads((root/'profiles.json').read_text())
elif args==['contract','--json']:
 value={'name':'kobo-adept','contract_version':8}
elif args==['loans','list','--profile','PRIVATE_NAME','--json']:
 print((root/'loans.json').read_text())
 raise SystemExit(0)
else:raise SystemExit(99)
print(json.dumps(value))
''')
    (tmp_path/'profiles.json').write_text(json.dumps({'ok':True,'profiles':[{'profile_id':'PRIVATE_ID','name':'PRIVATE_NAME'}]}))
    (tmp_path/'loans.json').write_text(json.dumps({'loans':[{'id':'PRIVATE_LOAN','expired':False,'returned':False,'token_present':True,'title':'PRIVATE_TITLE','local_book':'/PRIVATE/book.epub'}]}))
    reports=[]
    r=KoboAdeptRuntime(NS(config={'command':[sys.executable,str(script)]},report_diagnostic=lambda *a,**kw:reports.append((a,kw))))
    r.last_run_diagnostic={'original':True}
    r.contract_version=17
    return r,tmp_path,reports


def calls(root):
    p=root/'calls.jsonl'
    return [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []


@pytest.mark.parametrize('consent',[False,None,1,'yes'])
def test_without_exact_consent_no_process_is_started(runtime,consent):
    r,root,_=runtime
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=consent)
    assert result.reason=='consent_required' and not calls(root)
    assert 'loans' not in result.redacted()


@pytest.mark.parametrize('reference',['',None,'-bad',' bad','a\nb','x'*129])
def test_invalid_reference_never_starts_a_process(runtime,reference):
    r,root,_=runtime
    assert r.loan_inventory_readonly(reference,consented=True).reason=='profile_required'
    assert not calls(root)


def test_exact_profile_name_contract_and_loan_projection_are_private_and_isolated(runtime):
    r,root,reports=runtime
    before={p.name:p.read_bytes() for p in root.glob('*.json')}
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True)
    assert result.status=='complete' and result.profile_name=='PRIVATE_NAME'
    assert len(result.loans)==1 and result.loans[0]['id']=='PRIVATE_LOAN'
    assert calls(root)==[['profile','list','--json'],['contract','--json'],['loans','list','--profile','PRIVATE_NAME','--json']]
    assert not reports and r.last_run_diagnostic=={'original':True} and r.contract_version==17
    assert before=={p.name:p.read_bytes() for p in root.glob('*.json')}
    assert 'PRIVATE' not in repr(result)+json.dumps(result.redacted())
    assert result.redacted()['loans']==1


@pytest.mark.parametrize('profiles,reason',[
    ([], 'profile_unavailable'),
    ([{'id':'PRIVATE_ID','name':'PRIVATE_NAME'},{'name':'PRIVATE_ID'}], 'profile_ambiguous'),
    ([{'id':'PRIVATE_ID'}], 'profile_unavailable'),
])
def test_no_profile_guess_fallback_or_cross_namespace_ambiguity(runtime,profiles,reason):
    r,root,reports=runtime
    (root/'profiles.json').write_text(json.dumps({'ok':True,'profiles':profiles}))
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True)
    assert result.reason==reason and calls(root)==[['profile','list','--json']] and not reports


@pytest.mark.parametrize('loans',[
    {'loans':[{'id':'one','expired':False,'returned':False,'token_present':True}],'profile':'OTHER'},
    {'loans':[],'ok':False}, {'loans':[],'ok':1}, {'loans':[],'error':'PRIVATE'},
    {'loans':[{'id':'one','expired':False,'returned':False,'token_present':'PRIVATE'}]},
    {'loans':[{'id':'one','expired':False,'returned':False,'token_present':True}]*2},
])
def test_untrusted_loans_never_become_empty_or_successful_inventory(runtime,loans):
    r,root,reports=runtime
    (root/'loans.json').write_text(json.dumps(loans))
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True)
    assert result.status=='unavailable' and not result.loans
    assert 'loans' not in result.redacted() and 'PRIVATE' not in repr(result)+json.dumps(result.redacted())
    assert not reports


def test_cancelled_consent_never_launches_cli(runtime):
    r,root,_=runtime
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True,control=NS(cancelled=lambda:True))
    assert result.reason=='read_cancelled' and not calls(root)


@pytest.mark.skipif(os.name!='posix',reason='isolated POSIX process transport')
@pytest.mark.parametrize('mode,reason',[
    ('timeout','read_time_budget'),('stdout','read_output_budget'),('stderr','read_output_budget'),('cancel','read_cancelled')])
def test_transport_enforces_live_budgets_and_reaps_child(tmp_path,mode,reason):
    pidfile=tmp_path/'pid'
    script='import os,time;from pathlib import Path;Path('+repr(str(pidfile))+').write_text(str(os.getpid()));'
    if mode=='stdout':script+="os.write(1,b'x'*100000);time.sleep(20)"
    elif mode=='stderr':script+="os.write(2,b'x'*100000);time.sleep(20)"
    else:script+='time.sleep(20)'
    start=time.monotonic()
    control=NS(cancelled=lambda:mode=='cancel' and pidfile.exists())
    with pytest.raises(_Stopped,match=reason):
        _transport([sys.executable,'-c',script],[],control=control,deadline=start+.4,limit=1024)
    assert time.monotonic()-start<2
    pid=int(pidfile.read_text())
    with pytest.raises(ChildProcessError):os.waitpid(pid,os.WNOHANG)


@pytest.mark.parametrize('raw',[
    '{"loans":[],"loans":[]}', '{"loans":NaN}', 'PRIVATE malformed JSON',
])
def test_duplicate_or_nonstandard_json_is_not_a_successful_inventory(runtime,raw):
    r,root,reports=runtime
    (root/'loans.json').write_text(raw)
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True)
    assert result.status=='unavailable' and not reports
    assert 'PRIVATE' not in repr(result)+json.dumps(result.redacted())


def test_control_exception_cannot_inject_a_private_reason(runtime):
    r,root,_=runtime
    def cancelled():raise _Stopped('PRIVATE')
    result=r.loan_inventory_readonly('PRIVATE_ID',consented=True,control=NS(cancelled=cancelled))
    assert result.reason=='read_failed' and not calls(root)
