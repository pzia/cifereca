from __future__ import annotations

import pytest

from tests.test_kobo_executor_policy import (
    _artifact_result,
    _candidate,
    _item,
    _service,
    _state,
)


def _candidate_for(*, handling: str, fmt: str = "epub", url: str) -> dict:
    return _candidate(
        url=url,
        media_format=fmt,
        source_format="KEPUB" if handling == "kobo_kdrm" else fmt.upper(),
        handling=handling,
        drm="KDRM" if handling == "kobo_kdrm" else "SignedNoDrm",
    )


@pytest.mark.parametrize("android_kind", ["kdrm", "unsupported"])
def test_plain_desktop_descriptor_precedes_android_kdrm_delegation(
    tmp_path, monkeypatch, android_kind,
):
    service, session = _service(
        tmp_path, payload_factory=lambda *_args: None, supports_acsm=False,
    )
    session.supports_acquisition_display_profile = True
    session.display_profile = "Android"
    seen: list[tuple[str, str | None]] = []

    def resolve(product_id, *, revision_id=None, mode="descriptor", display_profile=None):
        seen.append((mode, display_profile))
        if mode != "descriptor":
            pytest.fail("A plain Desktop descriptor must win before any delegation")
        if display_profile == "Android":
            if android_kind == "unsupported":
                candidates = [_candidate_for(
                    handling="http_plain", fmt="audio", url="https://cdn.example/audio",
                )]
            else:
                candidates = [_candidate_for(
                    handling="kobo_kdrm", fmt="kepub", url="https://cdn.example/kdrm",
                )]
        else:
            candidates = [_candidate_for(
                handling="http_plain", url="https://cdn.example/desktop.epub",
            )]
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
    service.kobo_adept_state = lambda **kwargs: _state(acsm=False, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")
    sent: list[dict] = []
    monkeypatch.setattr(
        service, "execute_acquisition_request",
        lambda request: sent.append(request) or _artifact_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())
        assert result.path and result.path.is_file()
        assert seen == [("descriptor", "Android"), ("descriptor", "Desktop")]
        assert len(sent) == 1
        assert sent[0]["source"]["type"] == "http"
        assert sent[0]["source"]["url"] == "https://cdn.example/desktop.epub"
        assert sent[0]["metadata"]["vault_display_profile"] == "Desktop"
        assert result.metadata["acquisition_mode"] == "descriptor-http"
        assert result.metadata["vault_display_profile"] == "Desktop"
    finally:
        service.close()


def test_first_profile_plain_descriptor_does_not_probe_other_profile(tmp_path, monkeypatch):
    service, session = _service(
        tmp_path, payload_factory=lambda *_args: None, supports_acsm=False,
    )
    session.supports_acquisition_display_profile = True
    session.display_profile = "Android"
    seen: list[tuple[str, str | None]] = []

    def resolve(product_id, *, revision_id=None, mode="descriptor", display_profile=None):
        seen.append((mode, display_profile))
        return {
            "schema_version": "1.4",
            "acquisition": {
                "validity": {},
                "candidates": [_candidate_for(
                    handling="http_plain", url="https://cdn.example/android.epub",
                )],
            },
        }

    monkeypatch.setattr(session, "resolve_acquisition", resolve)
    service.kobo_adept_state = lambda **kwargs: _state(acsm=False, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("", "none")
    monkeypatch.setattr(
        service, "execute_acquisition_request", _artifact_result,
    )
    try:
        result = service.acquire_direct_item(_item())
        assert result.path and result.path.is_file()
        assert seen == [("descriptor", "Android")]
        assert result.metadata["vault_display_profile"] == "Android"
    finally:
        service.close()
