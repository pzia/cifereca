"""#4048: ADEPT origin remains readable but every voluntary return is blocked."""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import AttentionRequired
from tests.test_adept_return import _service, _acquisition, _loan


@pytest.mark.parametrize("count", [0, 2])
def test_missing_or_ambiguous_registry_entry_never_dispatches_return(tmp_path, count):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        row = _loan(returned=False, token_present=True)
        service._kobo_adapter = lambda: pytest.fail("registry read would be unnecessary")
        service.execute_drm_mutation = lambda *_: pytest.fail("ADEPT return forbidden")
        artifact = Path(service.storage.acquisition_get(aid)["metadata"]["path"])
        before = artifact.read_bytes()
        binding = service.storage.acquisition_get(aid)
        with pytest.raises(AttentionRequired) as error:
            service.return_adept_acquisition(aid)
        assert error.value.code == "loan_return_removed"
        assert artifact.read_bytes() == before
        assert service.storage.acquisition_get(aid) == binding
    finally:
        service.close()


def test_repeated_return_never_uses_original_or_selected_profile(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        service.current_identity = NS(identity_id="other")
        service._kobo_adapter = lambda: pytest.fail("no registry access")
        service.execute_drm_mutation = lambda *_: pytest.fail("no CLI")
        artifact = Path(service.storage.acquisition_get(aid)["metadata"]["path"])
        before = artifact.read_bytes()
        for _ in range(2):
            with pytest.raises(AttentionRequired) as error:
                service.return_adept_acquisition(aid)
            assert error.value.code == "loan_return_removed"
        assert artifact.read_bytes() == before
        assert service.storage.acquisition_get(aid)["metadata"]["adept_binding"][
            "adept_profile_name"
        ] == "hb"
    finally:
        service.close()


def test_missing_origin_profile_preserves_binding_without_mutating(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        before = service.storage.acquisition_get(aid)
        service.kobo_profiles = lambda: []
        service._kobo_adapter = lambda: pytest.fail("no alternate profile lookup")
        with pytest.raises(AttentionRequired) as error:
            service.return_adept_acquisition(aid)
        assert error.value.code == "loan_return_removed"
        assert service.storage.acquisition_get(aid) == before
    finally:
        service.close()
