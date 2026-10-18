"""Local preparation preserves catalog order and stops before provider work."""
from types import SimpleNamespace as NS
import pytest
from cifereca.core.loan_local_preparation import prepare
from cifereca.services import decide_artifact_reuse, blocked_automatic_adept_retry


@pytest.mark.parametrize('count', [0, 1, 4])
def test_invalid_catalog_rows_are_invalidated_before_ledger_read(tmp_path, count):
    events = []
    rows = [{'identity_id': 'owner', 'provider_id': 'paris', 'item_id': 'edition',
             'local_state': 'local', 'metadata': {'path': str(tmp_path / f'{i}.epub')}}
            for i in range(count)]
    def catalog():
        events.append('catalog')
        return rows
    def invalidate(row):
        events.append(('invalidate', row['metadata']['path'], row['local_state']))
    def ledger(**kw):
        assert kw == {'provider_id': 'paris'}
        events.append('ledger')
        return []
    service = NS(storage=NS(catalog_entries=catalog, catalog_entry_put=invalidate, acquisitions=ledger))
    def forbidden(*a, **kw): pytest.fail('missing file must not be inspected')
    result = _prepare(service, forbidden)
    assert events == ['catalog', *[('invalidate', r['metadata']['path'], 'missing') for r in rows], 'ledger']
    assert result.outcome is None and result.decision.action == 'acquire'
    assert result.previous_state.row is None
    assert all(r['local_state'] == 'local' for r in rows)


@pytest.mark.parametrize('index_status', ['indexed', 'error'])
def test_complete_ledger_relinks_before_reuse_even_if_index_fails(tmp_path, index_status):
    path = tmp_path / 'kept.epub'
    path.write_bytes(b'synthetic local file')
    events = []
    row = {'identity_id': 'owner', 'credential_id': 'credential', 'account_id': 'primary',
           'item_id': 'edition', 'strategy': 'loan_auto', 'status': 'complete',
           'executor': 'provider', 'metadata': {'path': str(path)}}
    def index(received, **kw):
        assert received == path
        assert kw == {'title': 'Synthetic', 'item_id': 'edition', 'identity_id': 'owner', 'provider_id': 'paris'}
        events.append('index')
        return {'status': index_status}
    service = NS(storage=NS(catalog_entries=lambda: [], acquisitions=lambda **kw: [row]), catalog_index_path=index)
    def inspect(received, expected):
        assert received == path and expected == 'epub'
        events.append('inspect')
        return NS(valid=True, protected=False)
    result = _prepare(service, inspect)
    assert events == ['inspect', 'index']
    assert result.outcome == {'status': 'unchanged', 'loan_id': 'loan', 'decision': 'reuse',
                              'reason': 'valid_ledger_artifact', 'source': 'ledger'}
    assert result.previous_state.row is row
    assert path.read_bytes() == b'synthetic local file'
    assert row['status'] == 'complete'


def _prepare(service, inspector):
    loan = NS(id='loan', record_id='edition', title='Synthetic', source_provider='paris',
              source_credential='credential', source_account_id='primary')
    return prepare(service, loan, identity=NS(identity_id='owner'), entitlement_id=None,
                   force_refresh=False, started=0, inspect_artifact=inspector,
                   decide_artifact_reuse=decide_artifact_reuse,
                   blocked_automatic_adept_retry=blocked_automatic_adept_retry,
                   time=NS(monotonic=lambda: 1),
                   _TRACE_LOG=NS(info=lambda *a: None, warning=lambda *a: None))
