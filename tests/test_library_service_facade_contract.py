"""Freeze the pre-extraction facade without depending on physical method bodies."""
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.synthetic_lending_sessions import enable_synthetic_paris_opening
from cifereca.services import LibraryService

CONTRACTS = json.loads((Path(__file__).parent / 'fixtures/library_service_facade.json').read_text())


@pytest.mark.parametrize('name,contract', CONTRACTS.items())
def test_service_facade_keeps_descriptor_and_callable_signature(name, contract):
    descriptor = inspect.getattr_static(LibraryService, name)
    kind = ('staticmethod' if isinstance(descriptor, staticmethod) else
            'classmethod' if isinstance(descriptor, classmethod) else
            'property' if isinstance(descriptor, property) else 'method')
    function = (descriptor.fget if kind == 'property' else
                descriptor.__func__ if kind != 'method' else descriptor)
    assert kind == contract['kind']
    assert str(inspect.signature(function)) == contract['signature']


def test_service_and_cli_import_without_pygobject():
    code = '''
import importlib.abc, sys
class NoGtk(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'gi' or fullname.startswith('gi.'):
            raise AssertionError('Core/CLI must not require PyGObject')
sys.meta_path.insert(0, NoGtk())
from cifereca.services import LibraryService
from cifereca.cli import main
assert callable(LibraryService) and callable(main)
assert not any(name == 'gi' or name.startswith('gi.') for name in sys.modules)
'''
    result = subprocess.run([sys.executable, '-c', code], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


def test_opening_partial_continues_other_loans_and_recovers_without_ui_identity_leak(tmp_path, monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace
    from tests.test_paris_borrower_adept_binding import service_with_linked_borrower

    service, original = service_with_linked_borrower(tmp_path)
    try:
        service.accounts = [service.accounts[0]]
        loans = [replace(original, id='first', source_account_id='primary',
                         ebook_url='https://example.invalid/first.acsm', due_at='2099-01-01'),
                 replace(original, id='second', source_account_id='primary',
                         ebook_url='https://example.invalid/second.acsm', due_at='2099-01-01')]
        service.current_identity = service.identity_by_id('bob')
        monkeypatch.setattr(service, '_loans_for_account', lambda _a, force=False: loans)
        monkeypatch.setattr(service, '_kobo_adapter', lambda: SimpleNamespace(available=lambda: False))
        monkeypatch.setattr(service, 'kobo_backup_enabled', lambda: False)
        monkeypatch.setattr(service, 'mirror_personal_libraries', lambda: {})
        monkeypatch.setattr(service, '_reconcile_identity_adept_inventory', lambda _i: {})
        outcomes = {'first': 'attention_required', 'second': 'complete'}
        calls = []

        def acquire(loan):
            calls.append((loan.id, loan.identity_id))
            return {'status': outcomes[loan.id], 'loan_id': loan.id}

        monkeypatch.setattr(service, '_acquire_active_loan_automatically', acquire)
        enable_synthetic_paris_opening(service)
        first = service.opening_sync()
        alice = next(row for row in first['identities'] if row['identity_id'] == 'alice')
        assert alice['status'] == 'partial'
        assert alice['attention_required'] == 1 and alice['new_acquisitions'] == 1
        assert calls == [('first', 'alice'), ('second', 'alice')]
        assert service.current_identity.identity_id == 'bob'
        assert service.storage.latest_sync_run('alice')['status'] == 'partial'
        outcomes['first'] = 'complete'
        enable_synthetic_paris_opening(service)
        second = service.opening_sync()
        alice = next(row for row in second['identities'] if row['identity_id'] == 'alice')
        assert alice['status'] == 'success' and alice['new_acquisitions'] == 2
        assert service.storage.latest_sync_run('alice')['status'] == 'success'
        assert calls[-2:] == [('first', 'alice'), ('second', 'alice')]
        assert service.current_identity.identity_id == 'bob'
    finally:
        service.close()
