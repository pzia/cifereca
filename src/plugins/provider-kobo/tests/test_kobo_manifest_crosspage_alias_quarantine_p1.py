"""#1346: rejected Kobo edition aliases taint same product across manifest pages.

All responses are synthetic; acquisition endpoints must never be called.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_kobo_vault import KoboVaultClient
from tests.test_kobo_manifest_policy_p1 import session_with_vault


@pytest.mark.parametrize("invalid_page", [1, 2])
def test_conflicting_alias_quarantines_other_page_before_core_receives_manifest(invalid_page):
    calls = []
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v2/library/manifest"
        assert request.headers.get("authorization") == "Bearer PRIVATE_TOKEN"
        calls.append(request)
        page = len(calls)
        assert page <= 2
        if page == 1:
            assert request.url.params.get("cursor") is None
        else:
            assert request.url.params.get("cursor") == "next"

        conflicted = {
            "product_id": "PRIVATE_PRODUCT_P",
            "id": "PRIVATE_PRODUCT_Q",
            "owned": True,
        }
        valid = [
            {"product_id": "PRIVATE_PRODUCT_P", "owned": True},
            {"product_id": "PRIVATE_PRODUCT_Q", "owned": True},
        ]
        items = ([conflicted] if page == invalid_page else valid)
        items += [{"product_id": f"safe-{page}", "owned": True}]
        return httpx.Response(200, json={
            "items": items,
            "next_cursor": "next" if page == 1 else None,
        })

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    session = session_with_vault(vault)
    try:
        safe = session.backup_manifest()
        assert [row["product_id"] for row in safe] == ["safe-1", "safe-2"]
        assert session.backup_manifest_rejection_count == 3
        assert vault.last_manifest_rejections == [
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
            {"type": "invalid_shape", "reason": "tainted_product_id"},
            {"type": "invalid_shape", "reason": "tainted_product_id"},
        ]
        policy = session.backup_manifest_policy
        assert policy["status"] == "incomplete"
        assert policy["manifest_authoritative"] is False
        assert policy["manifest_rejections"] == 3
        assert policy["classified_items"] == 2
        assert policy["acquisition_authorized"] is False
        assert policy["downloads_authorized"] is False
        diagnostic = json.dumps(session.diagnostic_state()["manifest_materialization_policy"])
        for secret in ("PRIVATE_PRODUCT_P", "PRIVATE_PRODUCT_Q", "PRIVATE_TOKEN", "vault.example"):
            assert secret not in diagnostic
        assert len(calls) == 2
    finally:
        session.close()


def test_alias_quarantine_is_fresh_for_each_manifest_fetch():
    state = {"bad": True}
    calls = []
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/api/v2/library/manifest"
        calls.append(request)
        if state["bad"]:
            return httpx.Response(200, json={"items": [
                {"product_id": "book-p", "id": "book-q"},
                {"product_id": "book-p"},
            ], "next_cursor": None})
        return httpx.Response(200, json={"items": [
            {"product_id": "book-p", "owned": True},
        ], "next_cursor": None})

    vault = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    session = session_with_vault(vault)
    try:
        assert session.backup_manifest() == []
        assert session.backup_manifest_rejection_count == 2
        assert session.backup_manifest_policy["status"] == "incomplete"
        state["bad"] = False
        assert session.backup_manifest() == [{"product_id": "book-p", "owned": True}]
        assert session.backup_manifest_rejection_count == 0
        assert session.backup_manifest_policy["status"] == "classified"
        assert session.backup_manifest_policy["manifest_authoritative"] is True
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        assert len(calls) == 2
    finally:
        session.close()
