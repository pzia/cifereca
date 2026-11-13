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


def test_batch_reconciliation_never_probes_retired_return_capabilities(tmp_path):
    from cifereca.core import adept_inventory_reconciliation as module
    service = _service(tmp_path)
    first = _artifact(tmp_path)
    second = first.with_name("Second.epub")
    second.write_bytes(b"second-local-artifact")
    _acquisition(service, first, acquisition_id="acq-0")
    _acquisition(service, second, acquisition_id="acq-1")
    observed = {"profiles": 0, "inventories": 0, "capabilities": 0}
    def profiles():
        observed["profiles"] += 1
        return [{"profile_id": "stable-profile", "name": "origin"}]
    def inventory(names):
        observed["inventories"] += 1
        return {"complete": True, "profiles": [{"profile": "origin", "loans": [
            {"id": "loan-0", "local_book": str(first), "returned": False,
             "expired": False, "token_present": True},
            {"id": "loan-1", "local_book": str(second), "returned": False,
             "expired": False, "token_present": True},
        ]}]}
    def capability():
        observed["capabilities"] += 1
        raise AssertionError("retired return capability must not be queried")
    service._kobo_adapter = lambda: NS(loan_inventories=inventory, capabilities=capability)
    service._adept_return_capability = capability
    service.kobo_profiles = profiles
    try:
        assert not hasattr(module, "_return_capability_for_batch")
        assert service._reconcile_identity_adept_inventory("default") == {
            "resolved": 2, "unresolved": 0, "errors": 0,
        }
        assert observed == {"profiles": 1, "inventories": 1, "capabilities": 0}
        for i in range(2):
            row = service.storage.acquisition_get(f"acq-{i}")
            binding = row["metadata"]["adept_binding"]
            assert binding["adept_loan_id"] == f"loan-{i}"
            assert binding["return_available"] is False
        assert first.read_bytes() == b"existing-local-artifact"
        assert second.read_bytes() == b"second-local-artifact"
    finally:
        service.close()


def test_missing_inventory_capability_fails_closed_without_mutating_ledger(tmp_path):
    """Missing ADEPT inventory capability is not a vendor-version check."""
    service = _service(tmp_path)
    try:
        artifact = _artifact(tmp_path)
        aid = _acquisition(service, artifact)
        before = service.storage.acquisition_get(aid)
        service._kobo_adapter = lambda: NS()
        service.kobo_profiles = lambda: pytest.fail("capability missing: no profile enumeration")
        with pytest.raises(Exception) as caught:
            service.reconcile_adept_acquisition(aid)
        assert getattr(caught.value, "code", None) == "adept_inventory_unavailable"
        assert "Kobo" not in str(caught.value)
        assert service.storage.acquisition_get(aid) == before
        assert artifact.read_bytes() == b"existing-local-artifact"
    finally:
        service.close()
