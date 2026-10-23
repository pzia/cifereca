"""P0 #2055: voluntarily saved .bin can use existing manual ADEPT import.

All input tokens and runtime outputs are synthetic. No BnF Web authentication,
book loan, provider API, license upload or real ADEPT runtime is involved.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.providers import ProviderConfigurationError
from tests.test_acsm_import_preservation import importer
from tests.test_core_p0_2055_saved_bnf_bin_acsm import _token


def test_user_chosen_bin_stages_same_bytes_and_only_explicit_adept_call(tmp_path, importer):
    service, requests = importer
    downloaded = tmp_path / "cifereca-telechargement-01.bin"
    raw = _token(fmt="application/pdf", expiration="2099-01-01T00:00:00+00:00")
    downloaded.write_bytes(raw)
    result = service.import_acsm(downloaded, title="Livre fictif")
    assert result["status"] == "complete"
    assert downloaded.read_bytes() == raw
    assert len(requests) == 1
    request = requests[0]
    assert request["source"]["type"] == "acsm"
    assert Path(request["source"]["path"]).name == "source.acsm"
    assert request["format"] == "pdf"
    assert request["metadata"]["identity_id"] == service.current_identity.identity_id
    assert Path(result["path"]).is_file()
    assert Path(result["path"]).suffix == ".pdf"
    assert "FICTIONAL_PRIVATE_SIGNATURE" not in str(
        service.storage.acquisitions()
    )
    assert service.import_acsm(downloaded)["status"] == "unchanged"
    assert len(requests) == 1  # duplicate does not repeat any fulfillment


@pytest.mark.parametrize("data", [
    b"<html>invalid</html>",
    b"<fulfillmentToken>wrong namespace</fulfillmentToken>",
    b"<!DOCTYPE x><x/>",
    _token(expiration="2000-01-01T00:00:00+00:00"),
    b"not an XML document",
])
def test_invalid_expired_or_untrusted_bin_rejected_before_staging(tmp_path, importer, data):
    service, requests = importer
    downloaded = tmp_path / "fictional.bin"
    downloaded.write_bytes(data)
    with pytest.raises(ProviderConfigurationError, match="Jeton ADEPT"):
        service.import_acsm(downloaded)
    assert not requests
    assert service.storage.acquisitions() == []
    assert not (tmp_path / "data").exists()
    assert downloaded.read_bytes() == data


def test_symlink_to_adept_bin_is_rejected_before_following_target(tmp_path, importer):
    service, requests = importer
    real = tmp_path / "real.bin"
    real.write_bytes(_token(expiration="2099-01-01T00:00:00+00:00"))
    shortcut = tmp_path / "shortcut.bin"
    shortcut.symlink_to(real)
    with pytest.raises(ProviderConfigurationError, match="Jeton ADEPT"):
        service.import_acsm(shortcut)
    assert not requests
    assert shortcut.is_symlink()
    assert service.storage.acquisitions() == []
