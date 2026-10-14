"""P1 #1346: the Vault adapter quarantines untrusted Kobo product IDs.

No live Kobo account, filesystem, ledger, acquisition or DRM is involved.
"""
from __future__ import annotations

import json
from types import SimpleNamespace as NS

import httpx

from cifereca.plugin_api import Account
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_vault import KoboVaultClient


def _client(handler):
    return KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )


def test_manifest_quarantines_conflicting_ids_before_core_sees_them():
    calls = []

    def handler(request):
        assert request.url.path == "/api/v2/library/manifest"
        assert request.headers["authorization"] == "Bearer PRIVATE_TOKEN"
        calls.append(request)
        if len(calls) == 1:
            assert "cursor" not in request.url.params
            return httpx.Response(200, json={
                "items": [
                    {"product_id": "good-1", "title": "PRIVATE_TITLE_ONE"},
                    {"id": "good-2", "title": "PRIVATE_TITLE_TWO"},
                    {"id": "other", "product_id": "conflicting", "title": "PRIVATE_SECRET"},
                    {"product_id": 0, "id": "masked", "title": "PRIVATE_MASK"},
                    {"product_id": "bad\\nPRIVATE_TOKEN"},
                    7,
                ], "next_cursor": "next",
            })
        assert len(calls) == 2
        assert request.url.params["cursor"] == "next"
        return httpx.Response(200, json={
            "items": [
                {"product_id": "good-3", "id": "good-3"},
                {"product_id": "good-4", "id": False},
            ], "next_cursor": None,
        })

    client = _client(handler)
    try:
        rows = client.library_manifest()
        assert [row.get("product_id") or row.get("id") for row in rows] == [
            "good-1", "good-2", "good-3",
        ]
        assert client.last_manifest_rejections == [
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
            {"type": "invalid_shape", "reason": "item_not_object"},
            {"type": "invalid_shape", "reason": "unverifiable_product_id"},
        ]
        assert len(calls) == 2
        report = json.dumps(client.last_manifest_rejections)
        for secret in ("PRIVATE", "good-1", "conflicting", "masked", "Bearer"):
            assert secret not in report
    finally:
        client.close()


def test_session_rejection_count_makes_valid_subset_non_authoritative_then_resets():
    state = {"invalid": True}
    calls = []

    def handler(request):
        assert request.url.path == "/api/v2/library/manifest"
        calls.append(request)
        rows = [{"product_id": "good-book", "owned": True}]
        if state["invalid"]:
            rows += [
                {"id": "different", "product_id": "spoof", "owned": True},
                {"product_id": 1234, "id": "coerced", "owned": True},
            ]
        return httpx.Response(200, json={"items": rows, "next_cursor": None})

    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="PRIVATE_CREDENTIAL",
        label="PRIVATE_LABEL", credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={},
        diagnostics=NS(add=lambda *_args, **_kwargs: None),
        runtime_capabilities=NS(acquisition=lambda *_args: None),
    )
    session.client.close()
    session.client = _client(handler)
    session.catalog = NS(cover_url=lambda _item: None)
    session.connected = True
    session._accounts = [
        Account(
            unique_id="account-1", display_name="PRIVATE_ACCOUNT",
            provider_id="kobo", credential_id=session.credential_id,
        )
    ]
    try:
        assert len(session.backup_manifest()) == 1
        assert session.backup_manifest_rejection_count == 2
        first = session.backup_manifest_policy
        assert first["status"] == "incomplete"
        assert first["manifest_rejections"] == 2
        assert first["counts"]["candidate_unverified"] == 1
        assert first["manifest_authoritative"] is False
        assert first["acquisition_authorized"] is False

        state["invalid"] = False
        assert len(session.backup_manifest()) == 1
        assert session.backup_manifest_rejection_count == 0
        second = session.backup_manifest_policy
        assert second["manifest_authoritative"] is True
        assert second["manifest_rejections"] == 0
        assert second["acquisition_authorized"] is False
        assert len(calls) == 2
        public = json.dumps(second) + json.dumps(first)
        for secret in ("PRIVATE", "good-book", "different", "spoof", "coerced"):
            assert secret not in public
    finally:
        session.close()
