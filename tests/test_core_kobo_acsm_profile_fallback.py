from __future__ import annotations

import pytest

from tests.test_kobo_executor_policy import (
    _artifact_result,
    _candidate,
    _item,
    _service,
    _state,
)


def _acsm(*, requires_auth: bool = False) -> dict:
    return _candidate(
        kind="acsm",
        url="https://cdn.example/desktop.acsm",
        media_format="acsm",
        source_format="ACSM",
        handling="adobe_adept",
        drm="AdobeDrm",
        source="library_download_link",
        requires_auth=requires_auth,
    )


def _plain() -> dict:
    return _candidate(
        url="https://cdn.example/android.epub",
        media_format="epub",
        source_format="EPUB",
        handling="http_plain",
        drm="SignedNoDrm",
    )


@pytest.mark.parametrize("android_candidate", ["plain", "auth_acsm"])
def test_kobo_acsm_falls_back_to_desktop_before_plain_http(
    tmp_path, monkeypatch, android_candidate: str,
):
    service, session = _service(
        tmp_path,
        payload_factory=lambda *_args: None,
        supports_acsm=True,
    )
    session.supports_acquisition_display_profile = True
    session.display_profile = "Android"
    calls: list[tuple[str, str | None, bool | None]] = []

    def resolve(product_id, *, revision_id=None, mode="descriptor",
                display_profile=None, include_acsm=None):
        calls.append((mode, display_profile, include_acsm))
        if display_profile == "Desktop":
            candidates = [_acsm()]
        elif mode == "delegated":
            # The Android authenticated ACSM is unavailable after resolution.
            candidates = []
        elif android_candidate == "plain":
            candidates = [_plain()]
        else:
            candidates = [_acsm(requires_auth=True)]
        return {
            "schema_version": "1.4",
            "acquisition": {
                "product_id": product_id,
                "revision_id": revision_id,
                "validity": {},
                "candidates": candidates,
            },
        }

    monkeypatch.setattr(session, "resolve_acquisition", resolve)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=True, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("adept-profile", "identity")
    sent: list[dict] = []
    monkeypatch.setattr(
        service, "execute_acquisition_request",
        lambda request: sent.append(request) or _artifact_result(request),
    )

    try:
        result = service.acquire_direct_item(_item())
        assert result.path and result.path.is_file()
        expected_calls = [("descriptor", "Android", True)]
        if android_candidate == "auth_acsm":
            expected_calls.append(("delegated", "Android", True))
        expected_calls.append(("descriptor", "Desktop", True))
        assert calls == expected_calls
        assert len(sent) == 1
        assert sent[0]["source"]["type"] == "acsm"
        assert sent[0]["metadata"]["vault_display_profile"] == "Desktop"
        assert result.metadata["acquisition_mode"] == "acsm-descriptor"
        assert result.metadata["vault_display_profile"] == "Desktop"
    finally:
        service.close()
