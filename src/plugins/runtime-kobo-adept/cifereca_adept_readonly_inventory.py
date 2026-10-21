"""Explicit, bounded loan-list read without diagnostics or shared runtime state.

The caller owns identity binding and one-shot consent. This module grants no
fulfillment, return, profile mutation, acquisition or persistence capability.
"""
from dataclasses import dataclass
import json
import os
import selectors
import signal
import subprocess
import time
from types import SimpleNamespace

from cifereca_kobo_adept_loan_envelope import attested_readonly_loans_list


@dataclass(frozen=True, repr=False)
class ReadOnlyInventory:
    status: str
    reason: str = ''
    profile_reference: str | None = None
    profile_name: str | None = None
    loans: tuple = ()

    def redacted(self):
        result={'schema':1,'read_only':True,'status':self.status,'reason':self.reason,
                'repair_authorized':False,'acquisition_authorized':False}
        if self.status=='complete':
            result['loans']=len(self.loans)
            result['expired']=sum(row['expired'] for row in self.loans)
            result['returned']=sum(row['returned'] for row in self.loans)
        return result


_REASONS=frozenset({'consent_required','profile_required','profile_unavailable','profile_ambiguous',
    'runtime_unavailable','read_cancelled','read_time_budget','read_output_budget','read_invalid_response','read_failed'})


class _Stopped(Exception):
    pass


def _check(control,deadline):
    if control is not None and control.cancelled():
        raise _Stopped('read_cancelled')
    if time.monotonic()>=deadline:
        raise _Stopped('read_time_budget')


def _transport(command,args,*,control,deadline,limit):
    """Bound both streams while draining; reap the isolated child on all paths."""
    _check(control,deadline)
    if os.name!='posix':
        raise _Stopped('runtime_unavailable')
    process=None
    parts=[]
    sizes={'stdout':0,'stderr':0}
    try:
        process=subprocess.Popen([*command,*args],stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True)
        with selectors.DefaultSelector() as selector:
            for stream,name in ((process.stdout,'stdout'),(process.stderr,'stderr')):
                os.set_blocking(stream.fileno(),False)
                selector.register(stream,selectors.EVENT_READ,name)
            while selector.get_map():
                _check(control,deadline)
                for key,_mask in selector.select(min(.05,max(0,deadline-time.monotonic()))):
                    chunk=os.read(key.fd,65536)
                    if not chunk:
                        selector.unregister(key.fileobj)
                        continue
                    name=key.data
                    sizes[name]+=len(chunk)
                    if sizes[name]>(limit if name=='stdout' else 65536):
                        raise _Stopped('read_output_budget')
                    if name=='stdout':parts.append(chunk)
        while True:
            _check(control,deadline)
            try:
                code=process.wait(timeout=.05)
                break
            except subprocess.TimeoutExpired:
                pass
        if code!=0:
            raise _Stopped('read_failed')
        return b''.join(parts)
    except OSError:
        raise _Stopped('runtime_unavailable') from None
    finally:
        if process is not None:
            # Until wait() succeeds the PID remains reserved by this child;
            # signal only its own session, including pipe-holding descendants.
            if process.returncode is None:
                try:os.killpg(process.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                process.wait()
            for stream in (process.stdout,process.stderr):
                if stream is not None:stream.close()


def _unique(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate_key')
        result[key]=value
    return result


def _name(value):
    return (type(value) is str and 1<=len(value)<=128 and value==value.strip()
            and not value.startswith('-') and all(ord(c)>=32 and ord(c)!=127 for c in value))


def read_inventory(runtime,reference,*,consented=False,control=None):
    if consented is not True:
        return ReadOnlyInventory('unavailable','consent_required')
    if not _name(reference):
        return ReadOnlyInventory('unavailable','profile_required')
    deadline=time.monotonic()+30
    try:
        _check(control,deadline)
        # Import after the normal runtime module finished loading. The clone
        # cannot mutate shared contract/diagnostic/configuration state.
        from cifereca_kobo_adept_runtime import KoboAdeptRuntime
        class Reader(KoboAdeptRuntime):
            allowed_profile=None
            def _report(self,*_args,**_kwargs):pass
            def run_json(self,args,*,payload=None,timeout=30,max_output_bytes=None):
                contract=args==['contract','--json']
                profiles=args==['profile','list','--json']
                loans=(self.allowed_profile is not None and args==['loans','list','--profile',self.allowed_profile,'--json'])
                if payload is not None or not (contract or profiles or loans):
                    raise _Stopped('read_failed')
                limit=65536 if contract else 4*1024*1024
                raw=_transport(self.command,args,control=control,
                    deadline=min(deadline,time.monotonic()+timeout),limit=limit)
                def reject_constant(_value):raise ValueError('invalid_constant')
                value=json.loads(raw.decode('utf-8'),object_pairs_hook=_unique,parse_constant=reject_constant)
                if type(value) is not dict or any(k in value for k in ('error','errors','failure','status','success')):
                    raise _Stopped('read_invalid_response')
                if loans and 'ok' not in value:
                    if not attested_readonly_loans_list(args,payload=None,returncode=0,value=value):
                        raise _Stopped('read_invalid_response')
                elif contract and 'ok' not in value:
                    if value.get('name')!='kobo-adept' or type(value.get('contract_version')) not in (str,int):
                        raise _Stopped('read_invalid_response')
                elif value.get('ok') is not True:
                    raise _Stopped('read_invalid_response')
                if loans and 'profile' in value and value['profile']!=self.allowed_profile:
                    raise _Stopped('read_invalid_response')
                return value

        reader=Reader(SimpleNamespace(config={'command':runtime.command}))
        payload=reader.profiles()
        rows=payload.get('profiles')
        if type(rows) is not list or len(rows)>128:
            raise _Stopped('read_invalid_response')
        matches=[row for row in rows if type(row) is dict
                 and any(row.get(key)==reference for key in ('profile_id','id','name','profile'))]
        if len(matches)!=1:
            raise _Stopped('profile_ambiguous' if matches else 'profile_unavailable')
        name=matches[0].get('name')
        if not _name(name):raise _Stopped('profile_unavailable')
        reader.allowed_profile=name
        # Reuse the existing strict contract and per-loan projection. No
        # inventory reconciliation facade, binding setter or diagnostics sink.
        result=reader.loans(name)
        _check(control,deadline)
        return ReadOnlyInventory('complete',profile_reference=reference,profile_name=name,loans=tuple(result['loans']))
    except _Stopped as exc:
        reason=exc.args[0] if exc.args else None
        return ReadOnlyInventory('unavailable',reason if type(reason) is str and reason in _REASONS else 'read_failed')
    except Exception:
        return ReadOnlyInventory('unavailable','read_invalid_response')
