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
        service.kobo_profiles = lambda: [
            {"profile_id": "stable-profile", "name": "origin"},
        ]
        with pytest.raises(Exception) as caught:
            service.reconcile_adept_acquisition(aid)
        assert getattr(caught.value, "code", None) == "adept_inventory_unavailable"
        assert "Kobo" not in str(caught.value)
        assert service.storage.acquisition_get(aid) == before
        assert artifact.read_bytes() == b"existing-local-artifact"
    finally:
        service.close()

 
def test_inventory_candidate_scope_never_uses_another_identity_or_ambient_selection(tmp_path):
    """Resolve exact persisted identity/profile and cache per scope."""
    service = _service(tmp_path)
    try:
        service.current_identity = None
        identity = service.identity_by_id("default")
        assert identity is not None
        row = {
            "identity_id": "default",
            "metadata": {"adept_binding": {"adept_profile_id": "stable-profile"}},
        }
        observed = []
        service._adept_profile_row = lambda ref: (
            observed.append(ref) or {"profile_id": ref, "name": "origin"}
        )
        cache = {}
        first = service.adept_inventory_candidates_for_acquisition(row, cache=cache)
        assert first == [{"profile_id": "stable-profile", "name": "origin"}]
        assert service.adept_inventory_candidates_for_acquisition(row, cache=cache) == first
        assert observed == ["stable-profile"]
        other_account = dict(row, credential_id="another-credential")
        assert service.adept_inventory_candidates_for_acquisition(
            other_account, cache=cache,
        ) == first
        assert observed == ["stable-profile", "stable-profile"]
        foreign = dict(row, identity_id="nonexistent-identity")
        with pytest.raises(Exception) as exc:
            service.adept_inventory_candidates_for_acquisition(foreign, cache=cache)
        assert getattr(exc.value, "code", None) == "adept_identity_unproven"
        assert observed == ["stable-profile", "stable-profile"]
    finally:
        service.close()


def test_inventory_candidates_reject_untyped_references_before_profile_read(tmp_path):
    service = _service(tmp_path)
    try:
        service._adept_profile_row = lambda *_: pytest.fail("bad reference must not read profiles")
        row = {"identity_id": "default", "metadata": {"adept_binding": {}}}
        with pytest.raises(Exception) as exc:
            service.adept_inventory_candidates_for_acquisition(
                row, profile_references=[{"untrusted": "profile"}],
            )
        assert getattr(exc.value, "code", None) == "adept_profile_candidates_invalid"
    finally:
        service.close()


def test_same_profile_name_from_other_account_cannot_reuse_inventory_cache(tmp_path):
    """A runtime inventory result is scoped to the persisted acquisition owner."""
    service = _service(tmp_path)
    try:
        path = _artifact(tmp_path)
        first = service.storage.acquisition_get(
            _acquisition(service, path, acquisition_id="acq-a")
        )
        # Create the second persisted acquisition with its final source
        # coordinates. Rebinding an existing ledger ID is deliberately
        # rejected by Storage's source-scope immutability guard.
        second = {
            **first, "acquisition_id": "acq-b",
            "credential_id": "another-credential",
            "account_id": "another-account",
        }
        service.storage.acquisition_put(second)
        inventories = []
        def inventory(names):
            inventories.append(tuple(names))
            return {"complete": True, "profiles": [{
                "profile": "origin", "loans": [{
                    "id": "loan-from-runtime", "local_book": str(path),
                    "returned": False, "expired": False, "token_present": True,
                }],
            }]}
        service.kobo_profiles = lambda: [
            {"profile_id": "stable-profile", "name": "origin"},
        ]
        service._kobo_adapter = lambda: NS(loan_inventories=inventory)
        cache = {}
        for row in (first, second):
            assert service._reconcile_adept_acquisition(
                row, cache=cache,
            )["status"] == "resolved"
        assert inventories == [("origin",), ("origin",)]
        assert path.read_bytes() == b"existing-local-artifact"
    finally:
        service.close()
