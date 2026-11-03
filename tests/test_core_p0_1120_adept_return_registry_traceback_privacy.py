"""P0 #1120/#1143: ADEPT inventory errors remain private even in traceback.

Only synthetic ADEPT registry/mutation responses and SQLite receipts are used.
No network, real DRM transaction, PNB return or artifact deletion.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace as NS
import traceback

import pytest

from cifereca.plugin_api import AttentionRequired
from tests.test_adept_return import _service, _acquisition, _loan


PRIVATE = "/home/reader/adept/profile/loan.acsm?auth=SECRET_ADOBE_SESSION"


class HostileInventoryError(RuntimeError):
    def __str__(self):
        raise AssertionError("Never stringify private ADEPT inventory failure")

    def __repr__(self):
        raise AssertionError("Never repr private ADEPT inventory failure")


@pytest.mark.parametrize("error", [
    OSError(PRIVATE), RuntimeError(PRIVATE), HostileInventoryError(PRIVATE),
])
@pytest.mark.parametrize("after_return", [False, True])
def test_retired_adept_return_never_exports_private_inventory_traceback(
    tmp_path, error, after_return,
):
    service = _service(tmp_path)
    acquisition_id = _acquisition(service, tmp_path)
    before = service.storage.acquisition_get(acquisition_id)
    local = Path(before["metadata"]["path"])
    before_bytes = local.read_bytes()
    seen = {"inventory": 0, "mutation": 0}

    def inventory(_profile):
        seen["inventory"] += 1
        raise error

    def mutate(_request):
        seen["mutation"] += 1
        raise error

    service._kobo_adapter = lambda: NS(loans=inventory)
    service.execute_drm_mutation = mutate
    try:
        with pytest.raises(AttentionRequired) as caught:
            service.return_adept_acquisition(acquisition_id)
        assert caught.value.code == "loan_return_removed"
        assert caught.value.__cause__ is None
        assert str(caught.value) == "Restitution volontaire retirée de Cifereca."
        rendered = "".join(traceback.format_exception(caught.value))
        assert "SECRET_ADOBE_SESSION" not in rendered
        assert "/home/reader/" not in rendered
        assert "HostileInventoryError" not in rendered
        assert service.storage.acquisition_get(acquisition_id) == before
        assert local.read_bytes() == before_bytes
        assert seen == {"inventory": 0, "mutation": 0}
    finally:
        service.close()
