"""Ephemeral read scopes measure equivalence candidates, never authorize reuse."""
from types import SimpleNamespace
import subprocess

import pytest

import cifereca_kobo_adept_runtime as module
from cifereca_kobo_adept_runtime import KoboAdeptRuntime


def runtime(events):
    return KoboAdeptRuntime(SimpleNamespace(
        config={'command': ['PRIVATE_BINARY', '--home=PRIVATE_DIRECTORY']},
        report_diagnostic=lambda *args, **kwargs: events.append(kwargs),
        logger=None,
    ))


def test_read_scope_is_stable_only_within_instance_configuration_and_profile(monkeypatch):
    calls, events = [], []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b'{"ok":true}', b'')
    monkeypatch.setattr(module.subprocess, 'run', run)
    first, second = runtime(events), runtime([])
    commands = [
        ['contract', '--json'], ['capabilities', '--json'],
        ['profile', 'list', '--json'], ['loans', 'list', '--json'],
        ['loans', 'list', '--profile', 'PRIVATE_A', '--json'],
        ['loans', 'list', '--profile', 'PRIVATE_B', '--json'],
        ['loans', 'list', '--profile', 'PRIVATE_A', '--json'],
    ]
    refs = []
    for command in commands:
        assert first.run_json(command) == {'ok': True}
        refs.append(events[-1]['detail']['read_scope_ref'])
    assert refs[0] == refs[1] == refs[2]  # Same runtime-wide configuration.
    assert len({refs[0], refs[3], refs[4], refs[5]}) == 4
    assert refs[4] == refs[6]
    second.run_json(commands[4])
    assert second.last_run_diagnostic['read_scope_ref'] != refs[4]
    first.config['command'] = ['PRIVATE_OTHER_BINARY']
    first.run_json(commands[4])
    assert events[-1]['detail']['read_scope_ref'] != refs[4]
    assert len(calls) == 9  # No additional probe, profile list or cache hit.
    assert 'PRIVATE' not in repr(events)
    assert all(len(ref) == 27 and ref.startswith('rs_') for ref in refs)


@pytest.mark.parametrize('args,payload', [
    (['download', 'request', '-', '--json'], {'secret': 'PRIVATE_LICENSE'}),
    (['profile', 'list', '--json'], {'secret': 'PRIVATE_LICENSE'}),
    (['profile', 'show', 'PRIVATE_PROFILE', '--json'], None),
    (['loans', 'expired', '--json'], None),
    (['contract', '--json', 'PRIVATE_EXTRA'], None),
])
def test_other_operations_and_payloads_have_no_read_scope(monkeypatch, args, payload):
    events = []
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, b'{"ok":true}', b''))
    obj = runtime(events)
    obj.run_json(['contract', '--json'])
    assert 'read_scope_ref' in obj.last_run_diagnostic
    obj.run_json(args, payload=payload)
    assert 'read_scope_ref' not in obj.last_run_diagnostic
    assert 'read_scope_ref' not in events[-1]['detail']
    assert 'PRIVATE' not in repr(events)


def test_read_failure_retains_scope_without_reusing_previous_success(monkeypatch):
    events = []
    obj = runtime(events)
    args = ['loans', 'list', '--profile', 'PRIVATE_A', '--json']
    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 1, output=b'PRIVATE_RESPONSE')
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    with pytest.raises(module.KoboAdeptError) as caught:
        obj.run_json(args)
    reference = events[-1]['detail']['read_scope_ref']
    assert caught.value.code == 'kobo_adept_timeout'
    assert 'PRIVATE' not in repr(events)
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, b'{"ok":true}', b''))
    obj.run_json(args)
    assert events[-1]['detail']['read_scope_ref'] == reference
    assert 'error_code' not in events[-1]['detail']


def test_optional_scope_failure_and_invalid_export_reference_do_not_change_read(monkeypatch):
    events = []
    def unavailable(_size):
        raise OSError('PRIVATE_RANDOM_FAILURE')
    monkeypatch.setattr(module.secrets, 'token_bytes', unavailable)
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kwargs:
                        subprocess.CompletedProcess(argv, 0, b'{"ok":true}', b''))
    obj = runtime(events)
    assert obj.run_json(['contract', '--json']) == {'ok': True}
    assert 'read_scope_ref' not in events[-1]['detail']
    for invalid in ('PRIVATE_PROFILE', 'rs_' + 'g' * 24, None, 12):
        obj.last_run_diagnostic['read_scope_ref'] = invalid
        obj._report('read')
        assert 'read_scope_ref' not in events[-1]['detail']
    assert 'PRIVATE' not in repr(events)
