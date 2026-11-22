"""#6937: never launch a reserved private stage via an ordinary Placement."""
import pytest

from cifereca.core.derived_variant_region_paths import PRIVATE_DERIVED_REGION
from cifereca.providers import ProviderConfigurationError
from tests.test_core0925_open_destinations import _service, _placement


@pytest.mark.parametrize("reserved", [True, False])
def test_local_open_does_not_export_private_stage(tmp_path, reserved):
    service = _service(tmp_path)
    path = tmp_path / "EBooks" / "alice" / (
        PRIVATE_DERIVED_REGION if reserved else PRIVATE_DERIVED_REGION + "-archive") / "book.epub"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"stage witness")
    placement = _placement(service, path)
    baseline = service.storage.placements()
    try:
        if reserved:
            with pytest.raises(ProviderConfigurationError):
                service.local_book_path(placement, identity_id="alice")
        else:
            assert service.local_book_path(placement, identity_id="alice") == path
        assert path.read_bytes() == b"stage witness"
        assert service.storage.placements() == baseline
    finally:
        service.close()
