"""Explicit return uses the recorded origin and fresh inventory, never UI scope."""
from pathlib import Path
from types import SimpleNamespace as NS
import pytest
from cifereca.plugin_api import AttentionRequired
from tests.test_adept_return import _service, _acquisition, _loan


@pytest.mark.parametrize('count', [0, 2])
def test_missing_or_ambiguous_exact_registry_entry_never_mutates(tmp_path, count):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        row = _loan(returned=False, token_present=True)
        service._kobo_adapter = lambda: NS(loans=lambda profile: {'loans': [dict(row) for _ in range(count)]})
        service.execute_drm_mutation = lambda *_: pytest.fail('ambiguous inventory cannot authorize return')
        path = Path(service.storage.acquisition_get(aid)['metadata']['path'])
        before = path.read_bytes()
        with pytest.raises(AttentionRequired) as error:
            service.return_adept_acquisition(aid)
        assert error.value.code == 'adept_return_verification_required'
        assert path.read_bytes() == before
        binding = service.storage.acquisition_get(aid)['metadata']['adept_binding']
        assert binding['return_verification'] == 'verification_failed'
        assert binding['return_available'] is False
    finally:
        service.close()


def test_repeated_return_uses_one_mutation_and_original_profile(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        service.current_identity = NS(identity_id='other')
        events = []
        returned = False
        def inventory(profile):
            events.append(('inventory', profile))
            return {'loans': [_loan(returned=returned, token_present=not returned)]}
        service._kobo_adapter = lambda: NS(loans=inventory)
        def mutate(request):
            nonlocal returned
            events.append(('return', request.identity_id, dict(request.metadata)))
            returned = True
            return {'ok': True, 'metadata': {'runtime_result': {
                'ok': True, 'operation': 'loan_return', 'loan_id': 'registry-id', 'profile': 'hb'}}}
        service.execute_drm_mutation = mutate
        path = Path(service.storage.acquisition_get(aid)['metadata']['path'])
        before = path.read_bytes()
        assert service.return_adept_acquisition(aid)['verification'] == 'confirmed'
        assert service.return_adept_acquisition(aid)['already_returned'] is True
        assert events == [
            ('inventory', 'hb'),
            ('return', 'default', {'adept_loan_id': 'registry-id', 'adept_profile_name': 'hb'}),
            ('inventory', 'hb'), ('inventory', 'hb')]
        assert path.read_bytes() == before
    finally:
        service.close()


def test_missing_origin_profile_never_reads_or_mutates_runtime(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        before = service.storage.acquisition_get(aid)
        service.kobo_profiles = lambda: []
        service._kobo_adapter = lambda: pytest.fail('no alternate runtime or profile')
        with pytest.raises(AttentionRequired) as error:
            service.return_adept_acquisition(aid)
        assert error.value.code == 'adept_origin_profile_missing'
        assert service.storage.acquisition_get(aid) == before
    finally:
        service.close()
