"""Compatibility-only ACSM/binding hooks retain effects without leaking IDs."""
import logging
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

import cifereca.services as facade
from cifereca.services import LibraryService
from cifereca.diagnostics import Diagnostics
from cifereca.core.loan_safe_trace import loan_trace_ref, unbound_loan_trace_ref

PRIVATE = 'PRIVATE_COORDINATE'


def assert_clean(caplog):
    records = [record for record in caplog.records if record.name.startswith("privacy-")]
    assert records
    assert all(PRIVATE not in r.getMessage() + repr(r.args) for r in records)
    assert all('request_ref=' in r.getMessage() for r in records)


@pytest.mark.parametrize('failure', [None, 'unlink', 'forget', 'rmdir'])
def test_cleanup_keeps_exact_file_cache_and_directory_contracts(tmp_path, monkeypatch, caplog, failure):
    service = LibraryService.__new__(LibraryService)
    folder = tmp_path/PRIVATE
    folder.mkdir()
    path = folder/(PRIVATE+'.acsm')
    path.write_bytes(b'SYNTHETIC')
    forget = Mock()
    monkeypatch.setattr(facade.acsm_cache, 'forget', forget)
    monkeypatch.setattr(facade, '_TRACE_LOG', logging.getLogger('privacy-cleanup'))
    unlink, rmdir = Path.unlink, Path.rmdir
    class PrivateError(PermissionError): pass
    if failure == 'unlink':
        def refuse_unlink(p, *a, **kw):
            if p == path: raise PrivateError(PRIVATE)
            return unlink(p,*a,**kw)
        monkeypatch.setattr(Path,'unlink',refuse_unlink)
    elif failure == 'forget': forget.side_effect = PrivateError(PRIVATE)
    elif failure == 'rmdir':
        def refuse_rmdir(p, *a, **kw):
            if p == folder: raise PrivateError(PRIVATE)
            return rmdir(p,*a,**kw)
        monkeypatch.setattr(Path,'rmdir',refuse_rmdir)
    with caplog.at_level(logging.INFO):
        assert service._cleanup_acsm_after_success(path, loan_id=PRIVATE) is None
    assert path.exists() == (failure == 'unlink')
    assert folder.exists() == (failure is not None)
    if failure == 'unlink': forget.assert_not_called()
    else: forget.assert_called_once_with(path)
    assert_clean(caplog)
    assert caplog.records[-1].args == (unbound_loan_trace_ref(PRIVATE),)
    assert 'action=keep' in caplog.text if failure in {'unlink','forget'} else 'action=deleted' in caplog.text


def test_none_cleanup_allocates_no_reference_and_has_no_effect(monkeypatch):
    service = LibraryService.__new__(LibraryService)
    def forbidden(*a): raise AssertionError('must not allocate')
    monkeypatch.setattr('cifereca.core.acsm_staging.unbound_loan_trace_ref',forbidden)
    assert service._cleanup_acsm_after_success(None, loan_id=PRIVATE) is None


@pytest.mark.parametrize('flags', [(False,False,False),(True,False,False),(False,True,True),(True,True,True)])
def test_binding_diagnostics_contain_only_unbound_reference_and_presence_booleans(monkeypatch, caplog, flags):
    service = LibraryService.__new__(LibraryService)
    service.diagnostics = Diagnostics()
    monkeypatch.setattr(facade, '_TRACE_LOG', logging.getLogger('privacy-binding'))
    binding = dict(adept_loan_id=PRIVATE if flags[0] else '',
                   adept_profile_id=PRIVATE if flags[1] else None,
                   return_available=flags[2], path=PRIVATE, email=PRIVATE,
                   license=PRIVATE, url='https://private/?auth='+PRIVATE)
    original = dict(binding)
    with caplog.at_level(logging.INFO):
        assert service._diagnose_adept_binding(binding, provider_loan_id=PRIVATE) is None
    assert binding == original
    assert_clean(caplog)
    event, = service.diagnostics.snapshot()
    assert event['status'] == ('ok' if flags[2] else 'warning')
    assert event['detail'] == dict(request_ref=unbound_loan_trace_ref(PRIVATE),
        adept_loan_id_present=flags[0], origin_profile_id_present=flags[1], return_available=flags[2])
    assert PRIVATE not in service.diagnostics.text('test')


def test_unbound_reference_is_ephemeral_does_not_attest_identity_or_stringify_objects(monkeypatch):
    import cifereca.core.loan_safe_trace as trace
    ref = unbound_loan_trace_ref(PRIVATE)
    assert len(ref) == 20 and all(c in '0123456789abcdef' for c in ref)
    assert ref == unbound_loan_trace_ref(PRIVATE)
    assert ref != unbound_loan_trace_ref('different')
    assert ref != loan_trace_ref(NS(id=PRIVATE, source_credential='account'), None)
    assert ref != loan_trace_ref(NS(id=PRIVATE), 'owner')
    class Hostile:
        def __str__(self): raise AssertionError('must not stringify')
        def __repr__(self): raise AssertionError('must not repr')
    assert unbound_loan_trace_ref(Hostile()) == unbound_loan_trace_ref(None)
    monkeypatch.setattr(trace, '_PROCESS_KEY', b'X'*32)
    assert ref != unbound_loan_trace_ref(PRIVATE)
