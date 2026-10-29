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


def _measured_batch(tmp_path, monkeypatch, *, legacy=False, replace_runtime=False):
    """Count actual calls through the LibraryService capability facade."""
    from cifereca.services import LibraryService
    from cifereca.core import adept_inventory_reconciliation as module
    service = _service(tmp_path)
    counts = {'profiles': 0, 'inventories': 0, 'capabilities': 0}
    first = _artifact(tmp_path)
    paths = [first, first.with_name('Second.epub'), first.with_name('Third.epub')]
    for i, path in enumerate(paths):
        path.write_bytes(f'local-{i}'.encode())
        _acquisition(service, path, acquisition_id=f'acq-{i}')
    def profiles():
        counts['profiles'] += 1
        return [{'profile_id': 'stable-profile', 'name': 'origin'}]
    def inventory(names):
        counts['inventories'] += 1
        return {'complete': True, 'profiles': [{'profile': 'origin', 'loans': [
            {'id': f'loan-{i}', 'local_book': str(p), 'returned': False,
             'expired': False, 'token_present': True} for i, p in enumerate(paths)]}]}
    def capabilities():
        counts['capabilities'] += 1
        return {'capabilities': {'adept': {'loan_return': True}}}
    runtime = NS(loan_inventories=inventory, capabilities=capabilities)
    service._kobo_adapter = lambda: (
        NS(loan_inventories=inventory, capabilities=capabilities) if replace_runtime else runtime)
    service.kobo_profiles = profiles
    service._adept_return_capability = LibraryService._adept_return_capability.__get__(service)
    if legacy:
        monkeypatch.setattr(module, '_return_capability_for_batch',
                            lambda service, runtime, cache: service._adept_return_capability())
    return service, counts, paths


def test_batch_capabilities_before_after_counts_and_identical_bindings(tmp_path, monkeypatch):
    results = []
    observed_bindings = []
    for legacy in (True, False):
        with monkeypatch.context() as patcher:
            service, counts, paths = _measured_batch(tmp_path / str(legacy), patcher, legacy=legacy)
            try:
                summary = service._reconcile_identity_adept_inventory('default')
                assert summary == {'resolved': 3, 'unresolved': 0, 'errors': 0}
                bindings = [service.storage.acquisition_get(f'acq-{i}')['metadata']['adept_binding']
                            for i in range(3)]
                for i, binding in enumerate(bindings):
                    assert binding['adept_loan_id'] == f'loan-{i}'
                    assert binding['adept_profile_id'] == 'stable-profile'
                    assert binding['return_available'] is True
                    assert paths[i].read_bytes() == f'local-{i}'.encode()
                observed_bindings.append([
                    {key: value for key, value in binding.items() if key != "inventory_checked_at"}
                    for binding in bindings])
                results.append(dict(counts))
                if not legacy:
                    # A new batch is a fresh observation, not a global cache.
                    service._reconcile_identity_adept_inventory('default')
                    assert counts == {'profiles': 2, 'inventories': 2, 'capabilities': 2}
            finally:
                service.close()
    assert observed_bindings[0] == observed_bindings[1]
    assert results == [
        {'profiles': 1, 'inventories': 1, 'capabilities': 3},
        {'profiles': 1, 'inventories': 1, 'capabilities': 1},
    ]


def test_batch_capability_never_reuses_a_replaced_runtime(tmp_path, monkeypatch):
    service, counts, _ = _measured_batch(tmp_path, monkeypatch, replace_runtime=True)
    try:
        assert service._reconcile_identity_adept_inventory('default')['resolved'] == 3
        assert counts['capabilities'] == 3
    finally:
        service.close()


def test_batch_capability_does_not_cache_failed_probe_or_bypass_fresh_action(tmp_path, monkeypatch):
    service, counts, _ = _measured_batch(tmp_path, monkeypatch)
    runtime = service._kobo_adapter()
    def capabilities():
        counts['capabilities'] += 1
        if counts['capabilities'] == 1:
            raise RuntimeError('transient synthetic failure')
        return {'capabilities': {'adept': {'loan_return': True}}}
    runtime.capabilities = capabilities
    try:
        assert service._reconcile_identity_adept_inventory('default')['resolved'] == 3
        assert counts['capabilities'] == 2
        assert service.storage.acquisition_get('acq-0')['metadata']['adept_binding']['return_available'] is False
        assert service.storage.acquisition_get('acq-1')['metadata']['adept_binding']['return_available'] is True
        runtime.capabilities = lambda: {'capabilities': {'adept': {'loan_return': False}}}
        # The action's existing fresh guard does not consult the batch cache.
        assert service._adept_return_capability() is False
    finally:
        service.close()
