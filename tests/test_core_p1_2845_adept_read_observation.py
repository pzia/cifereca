"""Runtime read measurements survive Core publication without acquiring authority."""
import json
import importlib
from pathlib import Path

import pytest

from cifereca.core.diagnostic_adept import (
    from_runtime, public_fields, public_log_fields, log_message,
)
from cifereca.core.diagnostic_event_feed import query_diagnostic_events
from tests.test_adept_shared_diagnostics import install_runtime, application_log


REFERENCE = 'rs_' + 'ab' * 12


def read(operation='profile list', **extra):
    return dict(operation=operation, caller_category='unattributed',
                read_scope_ref=REFERENCE, duration_ms=12, returncode=0, **extra)


@pytest.mark.parametrize('operation', ['contract', 'capabilities', 'profile list', 'loans list'])
@pytest.mark.parametrize('status', ['ok', 'error'])
def test_measured_reads_roundtrip_both_success_and_failure(operation, status):
    payload = read(operation)
    if status == 'error':
        payload.update(error_code='kobo_adept_timeout', returncode=1)
    fields = from_runtime(payload, status)
    assert fields['read_scope_ref'] == REFERENCE
    assert fields['caller_category'] == 'unattributed'
    assert fields['status'] == ('success' if status == 'ok' else 'failed')
    assert public_log_fields(log_message(fields)) == fields


@pytest.mark.parametrize('category', ['activation', 'core_probe', 'unattributed'])
def test_installed_context_retains_identical_read_facts_on_both_channels(
    tmp_path, application_log, category,
):
    manager, context, diagnostics = install_runtime(tmp_path)
    payload = read()
    payload.update(caller_category=category, account='PRIVATE', argv='PRIVATE',
                   profile='PRIVATE', task_ref='cccccccccccc')
    try:
        context.report_diagnostic('PRIVATE', 'ok', detail=payload, level='normal')
        rows = [row for row in query_diagnostic_events(
            diagnostics, log_path=application_log,
        )['events'] if row.get('detail', {}).get('operation_kind') == 'kobo_adept']
        assert {row['source'] for row in rows} == {'core_events', 'app_log'}
        assert len(rows) == 2
        assert rows[0]['detail'] == rows[1]['detail']
        assert rows[0]['detail']['caller_category'] == category
        assert rows[0]['detail']['read_scope_ref'] == REFERENCE
        assert 'task_ref' not in rows[0]['detail']
        assert 'PRIVATE' not in json.dumps(rows)
        assert 'PRIVATE' not in application_log.read_text()
    finally:
        manager.close()


@pytest.mark.parametrize('key,bad', [
    ('caller_category', 'PRIVATE'), ('caller_category', True),
    ('caller_category', ['activation']), ('caller_category', {'account': 'PRIVATE'}),
    ('read_scope_ref', 'PRIVATE'), ('read_scope_ref', 'rs_' + 'a' * 23),
    ('read_scope_ref', 'rs_' + 'a' * 25), ('read_scope_ref', 'rs_' + 'A' * 24),
    ('read_scope_ref', REFERENCE + '\n'), ('read_scope_ref', True),
    ('read_scope_ref', {'profile': 'PRIVATE'}),
])
def test_hostile_optional_measurements_are_omitted_and_public_schema_rejects_them(key, bad):
    payload = read()
    payload[key] = bad
    fields = from_runtime(payload, 'ok')
    assert key not in fields
    assert 'PRIVATE' not in json.dumps(fields)
    assert public_fields({**fields, key: bad}) == {}


@pytest.mark.parametrize('operation', [
    'download request', 'acsm return', 'profile show', 'loans expired',
    'status', 'other', 'unknown',
])
def test_measurement_fields_do_not_classify_mutations_or_other_operations(operation):
    fields = from_runtime(read(operation), 'ok')
    assert 'caller_category' not in fields and 'read_scope_ref' not in fields
    assert public_fields({**fields, 'read_scope_ref': REFERENCE}) == {}
    assert public_fields({**fields, 'caller_category': 'activation'}) == {}


def test_legacy_read_without_optional_observations_remains_valid():
    fields = from_runtime({'operation': 'profile list', 'returncode': 0}, 'ok')
    assert fields and public_fields(fields) == fields
    assert 'caller_category' not in fields and 'read_scope_ref' not in fields


def test_real_runtime_reporter_preserves_read_measurements(tmp_path, monkeypatch):
    manager, context, diagnostics = install_runtime(tmp_path)
    plugin = Path(__file__).resolve().parents[1] / 'src/plugins/runtime-kobo-adept'
    monkeypatch.syspath_prepend(str(plugin))
    runtime_type = importlib.import_module('cifereca_kobo_adept_runtime').KoboAdeptRuntime
    runtime = runtime_type(context)
    runtime.last_run_diagnostic = read()
    try:
        runtime._report('PRIVATE', 'ok', level='normal')
        rows = query_diagnostic_events(
            diagnostics, source='core_events', log_path=tmp_path / 'missing.log',
        )['events']
        event = next(row for row in rows
                     if row.get('detail', {}).get('operation_kind') == 'kobo_adept')
        assert event['detail']['read_scope_ref'] == REFERENCE
        assert event['detail']['caller_category'] == 'unattributed'
    finally:
        manager.close()


def test_unverified_producers_cannot_attest_read_observations(tmp_path):
    manager, context, diagnostics = install_runtime(tmp_path, package_id='org.test.unverified')
    try:
        context.report_diagnostic('PRIVATE', 'ok', detail=read())
        diagnostics.add('ADEPT', 'kobo adept', 'ok', detail=from_runtime(read(), 'ok'))
        rows = query_diagnostic_events(
            diagnostics, source='core_events', log_path=tmp_path / 'missing.log',
        )['events']
        assert not any(row.get('detail', {}).get('read_scope_ref') for row in rows)
        assert REFERENCE not in json.dumps(rows)
    finally:
        manager.close()
