"""P0 #1120: pure ACSM plan must preserve the v2 ADEPT runtime contract."""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.core.adept_acsm_plan import (
    build_fulfillment_request,
    select_acsm_format,
)


@pytest.mark.parametrize(
    ("detected", "expected"),
    [
        (None, ("epub", "fallback", ".epub")),
        ("epub", ("epub", "acsm", ".epub")),
        ("pdf", ("pdf", "acsm", ".pdf")),
        ("", ("", "acsm", ".epub")),
        ("other", ("other", "acsm", ".epub")),
    ],
)
def test_exact_format_fallback_and_suffix_are_preserved(detected, expected):
    assert select_acsm_format(detected) == expected


@pytest.mark.parametrize("source_format", ["pdf", "epub"])
def test_adept_request_keeps_v2_contract_no_force_or_hook_override(
    tmp_path, source_format,
):
    source = tmp_path / "staged.acsm"
    target = tmp_path / ("book." + source_format)
    request = build_fulfillment_request(
        acsm_path=source,
        destination=target,
        acsm_format=source_format,
        profile_name="shared-historical-profile",
        provider="paris",
        title="Livre prêté",
        loan_id="loan-123",
    )
    assert request == {
        "schema_version": 2,
        "source": {
            "type": "acsm",
            "provider": "paris",
            "path": str(source),
            "headers": {},
        },
        "destination": str(target),
        "format": source_format,
        "profile": "shared-historical-profile",
        "metadata": {"title": "Livre prêté", "loan_id": "loan-123"},
        "options": {"force": False},
    }
    assert "run_hook" not in request["options"]
    assert source.exists() is False
    assert target.exists() is False
    assert not list(tmp_path.iterdir())


def test_request_has_no_credentials_headers_or_implicit_identity_selection(tmp_path):
    payload = build_fulfillment_request(
        acsm_path=tmp_path / "a.acsm",
        destination=tmp_path / "b.epub",
        acsm_format="epub",
        profile_name="explicitly-selected-profile",
        provider="paris",
        title="",
        loan_id="transaction-other-identity",
    )
    assert payload["source"]["headers"] == {}
    assert payload["profile"] == "explicitly-selected-profile"
    assert payload["metadata"]["loan_id"] == "transaction-other-identity"
    assert "credentials" not in payload
    assert "identity_id" not in payload
    assert payload["options"] == {"force": False}


def test_each_plan_returns_fresh_mutable_subdicts(tmp_path):
    opts = {
        "acsm_path": tmp_path / "a.acsm",
        "destination": tmp_path / "a.epub",
        "acsm_format": "epub",
        "profile_name": "profile",
        "provider": "paris",
        "title": "Title",
        "loan_id": "loan",
    }
    first = build_fulfillment_request(**opts)
    second = build_fulfillment_request(**opts)
    first["metadata"]["loan_id"] = "changed"
    first["options"]["force"] = True
    first["source"]["headers"]["Authorization"] = "should-not-leak"
    assert second["metadata"] == {"title": "Title", "loan_id": "loan"}
    assert second["options"] == {"force": False}
    assert second["source"]["headers"] == {}
