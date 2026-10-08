from __future__ import annotations

import pytest

from cifereca.kobo.acquisition import KoboOfferAdapter
from cifereca.providers import ProviderConfigurationError
from tests.test_kobo_executor_policy import _item, _service, _state


def _acsm_candidate(*, url: str, transport: str = "https", method: str = "GET") -> dict:
    return {
        "kind": "acsm",
        "handling": "adobe_adept",
        "media_format": "acsm",
        "drm": {"raw_type": "AdobeDrm"},
        "transport": transport,
        "method": method,
        "url": url,
        "source": "library_download_link",
    }


def _payload(*candidates: dict) -> dict:
    return {"schema_version": "1.4", "acquisition": {"validity": {}, "candidates": list(candidates)}}


@pytest.mark.parametrize(
    "transport,method,url",
    [
        ("http", "GET", "https://cdn.example/book.acsm"),
        ("https", "POST", "https://cdn.example/book.acsm"),
        ("https", "GET", "http://cdn.example/book.acsm"),
        ("https", "GET", "https://"),
        ("https", "GET", "https://user:password@cdn.example/book.acsm"),
        ("https", "GET", "https://cdn.example:invalid/book.acsm"),
        ("https", "GET", "https://cdn.example/book.acsm\ninvalid"),
        ("https", "GET", "https://cdn.example/book.acsm\n"),
        ("https", "GET", " https://cdn.example/book.acsm"),
        ("https", "GET", "https://@cdn.example/book.acsm"),
        ("https", "GET", "https://cdn.example\\@evil.example/book.acsm"),
    ],
)
def test_acsm_selection_excludes_invalid_transport_and_url(transport, method, url):
    invalid = _acsm_candidate(transport=transport, method=method, url=url)
    valid = _acsm_candidate(url="https://cdn.example/valid.acsm")

    assert KoboOfferAdapter.acsm_candidate(_payload(invalid)) is None
    assert KoboOfferAdapter.acsm_candidate(_payload(invalid, valid)) == valid


def test_acsm_selection_keeps_library_download_priority():
    fallback = _acsm_candidate(url="https://cdn.example/fallback.acsm")
    fallback["source"] = "content_access_book"
    preferred = _acsm_candidate(url="https://cdn.example/preferred.acsm")
    assert KoboOfferAdapter.acsm_candidate(_payload(fallback, preferred)) == preferred


def test_acsm_request_guard_rejects_bad_candidate_even_if_selector_regresses(tmp_path, monkeypatch):
    invalid = _acsm_candidate(url="https://user:password@cdn.example/book.acsm")
    service, _session = _service(
        tmp_path,
        payload_factory=lambda _product, _revision, _mode: _payload(invalid),
        supports_acsm=True,
    )
    service.kobo_adept_state = lambda **kwargs: _state(acsm=True, delegated=False)
    service.resolve_adept_profile = lambda **kwargs: ("adept-profile", "identity")
    monkeypatch.setattr(service, "_kobo_acsm_candidate", lambda _payload: invalid)
    dispatched = []
    monkeypatch.setattr(
        service, "execute_acquisition_request",
        lambda request: dispatched.append(request),
    )
    try:
        with pytest.raises(ProviderConfigurationError, match="ACSM invalide.*HTTPS.*GET"):
            service.acquire_direct_item(_item())
        assert dispatched == []
    finally:
        service.close()
