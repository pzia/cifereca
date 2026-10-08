from __future__ import annotations

import pytest

from cifereca.kobo.acquisition import KoboOfferAdapter
from tests.test_kobo_executor_policy import (
    _artifact_result,
    _candidate,
    _item,
    _service,
    _state,
)


@pytest.mark.parametrize(
    "nested,root,expected",
    [
        (False, True, False),  # An explicit nested value takes precedence.
        (True, False, True),
        ("false", True, False),
        (" FALSE ", True, False),
        ("0", True, False),
        ("no", True, False),
        ("off", True, False),
        ("true", False, True),
        ("1", False, True),
        ("on", False, True),
        (None, True, True),
        ("", True, True),
        ("   ", "true", True),
        (None, "false", False),
        ("unexpected", False, True),  # Ambiguous flags are fail-closed.
    ],
)
def test_kobo_candidate_authorization_flag_normalization(nested, root, expected):
    row = {
        "requires_kobo_authorization": root,
        "kobo": {"requires_kobo_authorization": nested},
    }
    assert KoboOfferAdapter.candidate_requires_authorization(row) is expected


@pytest.mark.parametrize(
    "nested,root,expected_modes,expected_mode",
    [
        ("false", True, ["descriptor"], "acsm-descriptor"),
        (None, True, ["descriptor", "delegated"], "acsm-delegated"),
        ("true", False, ["descriptor", "delegated"], "acsm-delegated"),
        (False, True, ["descriptor"], "acsm-descriptor"),
    ],
)
def test_acsm_authorization_flag_controls_delegated_resolution(
    tmp_path, monkeypatch, nested, root, expected_modes, expected_mode
):
    def payload_factory(_product_id, _revision_id, mode):
        candidate = _candidate(
            kind="acsm",
            url="https://cdn.example/book.acsm",
            media_format="acsm",
            source_format="ACSM",
            handling="adobe_adept",
            drm="AdobeDrm",
            source="library_download_link",
            requires_auth=False,
            headers={"Authorization": "Bearer test-secret"} if mode == "delegated" else {},
        )
        candidate["kobo"]["requires_kobo_authorization"] = nested
        candidate["requires_kobo_authorization"] = root
        return {
            "schema_version": "1.4",
            "acquisition": {"validity": {}, "candidates": [candidate]},
        }

    service, session = _service(
        tmp_path, payload_factory=payload_factory, supports_acsm=True,
    )
    service.kobo_adept_state = lambda **kwargs: _state(acsm=True, delegated=True)
    service.resolve_adept_profile = lambda **kwargs: ("hb", "identity")
    sent = []
    monkeypatch.setattr(
        service, "execute_acquisition_request",
        lambda request: sent.append(request) or _artifact_result(request),
    )
    try:
        result = service.acquire_direct_item(_item())
        assert result.path and result.path.is_file()
        assert session.acquisition_modes == expected_modes
        assert result.metadata["acquisition_mode"] == expected_mode
        assert sent[0]["source"]["type"] == "acsm"
        if expected_mode == "acsm-delegated":
            assert sent[0]["source"]["headers"] == {"Authorization": "Bearer test-secret"}
        else:
            assert sent[0]["source"]["headers"] == {}
    finally:
        service.close()
