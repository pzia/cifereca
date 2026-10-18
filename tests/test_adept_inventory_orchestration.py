"""Inventory reconciliation keeps cache scope and does not acquire or return."""
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from tests.test_adept_inventory_binding import _service, _artifact, _acquisition


def test_batch_reconciliation_reuses_profiles_and_inventory_for_distinct_artifacts(tmp_path):
    service = _service(tmp_path)
    try:
        paths = [_artifact(tmp_path)]
        paths.append(paths[0].with_name('Second.epub'))
        paths[1].write_bytes(b'second-local-artifact')
        rows = [service.storage.acquisition_get(_acquisition(service, p, acquisition_id=f'acq-{i}'))
                for i, p in enumerate(paths)]
        calls = []
        def profiles():
            calls.append('profiles')
            return [{'profile_id': 'stable-profile', 'name': 'origin'}]
        def inventory(names):
            calls.append(tuple(names))
            return {'complete': True, 'profiles': [{'profile': 'origin', 'loans': [
                {'id': f'loan-{i}', 'local_book': str(p), 'returned': False,
                 'expired': False, 'token_present': True} for i, p in enumerate(paths)]}]}
        service.kobo_profiles = profiles
        service._kobo_adapter = lambda: NS(loan_inventories=inventory)
        service.execute_drm_mutation = lambda *_: pytest.fail('inventory cannot mutate DRM')
        cache = {}
        for i, row in enumerate(rows):
            result = service._reconcile_adept_acquisition(row, cache=cache)
            assert result['status'] == 'resolved'
            binding = service.storage.acquisition_get(row['acquisition_id'])['metadata']['adept_binding']
            assert binding['adept_loan_id'] == f'loan-{i}'
            assert binding['adept_profile_id'] == 'stable-profile'
        assert calls == ['profiles', ('origin',)]
        assert [p.read_bytes() for p in paths] == [b'existing-local-artifact', b'second-local-artifact']
    finally:
        service.close()


def test_inventory_failure_retains_origin_ids_and_artifact(tmp_path):
    service = _service(tmp_path)
    try:
        path = _artifact(tmp_path)
        aid = _acquisition(service, path)
        row = service.storage.acquisition_get(aid)
        row['metadata']['adept_binding']['adept_loan_id'] = 'original-loan'
        service.storage.acquisition_put(row)
        service.kobo_profiles = lambda: [{'profile_id': 'stable-profile', 'name': 'origin'}]
        def unavailable(_names): raise RuntimeError('synthetic inventory unavailable')
        service._kobo_adapter = lambda: NS(loan_inventories=unavailable)
        with pytest.raises(RuntimeError): service.reconcile_adept_acquisition(aid)
        binding = service.storage.acquisition_get(aid)['metadata']['adept_binding']
        assert binding['adept_loan_id'] == 'original-loan'
        assert binding['adept_profile_id'] == 'stable-profile'
        assert binding['inventory_status'] == 'inventory_error'
        assert binding['return_available'] is False
        assert path.read_bytes() == b'existing-local-artifact'
    finally:
        service.close()


def test_missing_artifact_never_reads_runtime_or_changes_ledger(tmp_path):
    service = _service(tmp_path)
    try:
        path = _artifact(tmp_path)
        aid = _acquisition(service, path)
        before = service.storage.acquisition_get(aid)
        path.unlink()
        service._kobo_adapter = lambda: pytest.fail('missing artifact must remain a local result')
        result = service.reconcile_adept_acquisition(aid)
        assert result['status'] == 'artifact_missing'
        assert service.storage.acquisition_get(aid) == before and not path.exists()
    finally:
        service.close()
