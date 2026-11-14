"""#5851: direct Core ACSM import is never authorized by UI navigation."""
from pathlib import Path

import pytest

from cifereca.core.manual_acsm_import import import_acsm
from cifereca.providers import ProviderConfigurationError


class _HostileIdentity(str):
    def __eq__(self, _other):
        raise AssertionError("untrusted identity equality executed")


class _InertService:
    def __init__(self):
        self.lookups = []

    @property
    def current_identity(self):
        raise AssertionError("Core ACSM import accessed UI navigation")

    def identity_by_id(self, identity_id):
        self.lookups.append(identity_id)
        return None


def _direct_import(service, path, *, identity_id):
    return import_acsm(
        service, path, identity_id=identity_id,
        Path=Path, ProviderConfigurationError=ProviderConfigurationError,
        sha256_file=lambda *_args: pytest.fail("unexpected import staging"),
        hashlib=None, data_dir=None, shutil=None, detect_acsm_format=None,
        AdeptProfileResolutionError=Exception, inspect_artifact=None, os=None,
    )


@pytest.mark.parametrize("identity_id", [
    None, "", 0, False, _HostileIdentity("reader"),
])
def test_direct_import_rejects_non_native_or_missing_identity_before_io(
    tmp_path, identity_id,
):
    source = tmp_path / "request.acsm"
    source.write_text("synthetic")
    service = _InertService()
    with pytest.raises(ProviderConfigurationError, match="Identité explicite"):
        _direct_import(service, source, identity_id=identity_id)
    assert service.lookups == []
    assert source.read_text() == "synthetic"


def test_direct_import_looks_up_only_the_explicit_identity(tmp_path):
    source = tmp_path / "request.acsm"
    source.write_text("synthetic")
    service = _InertService()
    with pytest.raises(ProviderConfigurationError, match="Aucune Identity active"):
        _direct_import(service, source, identity_id="reader-A")
    assert service.lookups == ["reader-A"]
