from __future__ import annotations

import pytest

from cifereca.kobo.acquisition import KoboAcquisitionSelectionError, KoboOfferAdapter
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_executor_policy import _item, _service, _state


def _candidate(*, handling="http_plain", url="https://cdn.example/book.epub",
               transport="https", method="GET"):
    return {
        "kind": "http",
        "handling": handling,
        "media_format": "epub",
        "source_format": "EPUB",
        "drm": {"raw_type": "SignedNoDrm" if handling == "http_plain" else "KDRM"},
        "transport": transport,
        "method": method,
        "url": url,
    }


def _payload(*candidates):
    return {"acquisition": {"candidates": list(candidates)}}


@pytest.mark.parametrize(
    "transport,method,url",
    [
        ("http", "GET", "https://cdn.example/book.epub"),
        ("https", "POST", "https://cdn.example/book.epub"),
        ("https", "GET", "http://cdn.example/book.epub"),
        ("https", "GET", "https://"),
        ("https", "GET", "https://user:secret@cdn.example/book.epub"),
        ("https", "GET", "https://cdn.example:broken/book.epub"),
        ("https", "GET", "https://cdn.example/line\nbreak"),
    ],
)
def test_delegated_selection_ignores_invalid_plain_http(transport, method, url):
    invalid = _candidate(transport=transport, method=method, url=url)
    # KDRM delegated recipes are legal without a direct delivery URL.
    kdrm_recipe = _candidate(handling="kobo_kdrm", url="", transport="kobo", method="POST")

    assert KoboOfferAdapter.delegated_candidate(_payload(invalid, kdrm_recipe)) == kdrm_recipe
    with pytest.raises(KoboAcquisitionSelectionError, match="aucun candidat"):
        KoboOfferAdapter.delegated_candidate(_payload(invalid))


def test_delegated_selection_prefers_valid_https_plain_before_kdrm():
    kdrm_recipe = _candidate(handling="kobo_kdrm", url="", transport="kobo", method="POST")
    unsafe = _candidate(url="http://cdn.example/unsafe.epub")
    secure = _candidate(url="https://cdn.example/secure.epub")

    assert KoboOfferAdapter.delegated_candidate(_payload(unsafe, kdrm_recipe, secure)) == secure
    assert KoboOfferAdapter.direct_candidate(_payload(unsafe, secure)) == secure


def test_http_execution_boundary_rejects_invalid_selection(tmp_path, monkeypatch):
    bad = _candidate(url="http://cdn.example/unsafe.epub")

    service, _session = _service(
        tmp_path,
        payload_factory=lambda product_id, revision_id, mode: _payload(bad),
    )
    service.kobo_adept_state = lambda **kwargs: _state(acsm=False, delegated=False)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")
    # A provider/selector regression must not bypass the transport check.
    monkeypatch.setattr(service, "_kobo_candidate", lambda *_args: bad)
    dispatched = []
    monkeypatch.setattr(service, "execute_acquisition_request", lambda request: dispatched.append(request))
    try:
        with pytest.raises(ProviderConfigurationError, match="HTTPS.*GET"):
            service.acquire_direct_item(_item())
        assert dispatched == []
    finally:
        service.close()
