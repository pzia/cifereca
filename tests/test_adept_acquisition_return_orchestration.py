"""#4048: the retired ADEPT return never enters historic orchestrators.

A stale borrower, conflicting inventory, repeated click or missing origin is
not permission to invoke a return, read ADEPT or write a claim/checkpoint.
"""
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.plugin_api import AttentionRequired
from tests.test_adept_return import _service, _acquisition


@pytest.mark.parametrize("legacy_inventory_count", [0, 1, 2])
def test_legacy_inventory_shape_cannot_start_return(tmp_path, legacy_inventory_count):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        path = Path(service.storage.acquisition_get(aid)["metadata"]["path"])
        before_bytes = path.read_bytes()
        before_row = service.storage.acquisition_get(aid)
        events = []

        def forbidden(*args, **kwargs):
            events.append((args, kwargs))
            raise AssertionError("retired return cannot read ADEPT inventory")
        service._kobo_adapter = forbidden
        service.execute_drm_mutation = forbidden
        for _ in range(legacy_inventory_count + 1):
            with pytest.raises(AttentionRequired) as caught:
                service.return_adept_acquisition(aid)
            assert caught.value.code == "loan_return_removed"
        assert events == []
        assert path.read_bytes() == before_bytes
        assert service.storage.acquisition_get(aid) == before_row
    finally:
        service.close()


def test_repeated_return_does_not_mutate_or_switch_origin_profile(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        service.current_identity = NS(identity_id="another")
        path = Path(service.storage.acquisition_get(aid)["metadata"]["path"])
        before = service.storage.acquisition_get(aid)
        original_bytes = path.read_bytes()
        service.kobo_profiles = lambda: pytest.fail("no ADEPT profile query")
        service._kobo_adapter = lambda: pytest.fail("no runtime or inventory")
        service.execute_drm_mutation = lambda *_: pytest.fail("no return")

        for _ in range(3):
            with pytest.raises(AttentionRequired) as caught:
                service.return_adept_acquisition(aid)
            assert caught.value.code == "loan_return_removed"
        assert service.storage.acquisition_get(aid) == before
        assert path.read_bytes() == original_bytes
    finally:
        service.close()


def test_missing_origin_profile_never_consults_runtime(tmp_path):
    service = _service(tmp_path)
    try:
        aid = _acquisition(service, tmp_path)
        before = service.storage.acquisition_get(aid)
        service.kobo_profiles = lambda: []
        service._kobo_adapter = lambda: pytest.fail("no runtime allowed")
        with pytest.raises(AttentionRequired) as caught:
            service.return_adept_acquisition(aid)
        assert caught.value.code == "loan_return_removed"
        assert service.storage.acquisition_get(aid) == before
    finally:
        service.close()
