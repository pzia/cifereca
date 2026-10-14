"""P1 #1346: complete Kobo manifest pagination is prerequisite to rights policy.

No live Vault calls, direct acquisition or filesystem access in these tests.
"""
from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from cifereca.plugin_api import Account
from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_catalog import KoboCatalogService
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


@pytest.mark.parametrize("bad_cursor", [
    0, False, True, 34, {}, [], ["PRIVATE_TOKEN"],
    " ", "next page", "abc\nBearer PRIVATE_TOKEN",
    "abc\rPRIVATE_ID", "abc\tPRIVATE_ID", "abc\x7fPRIVATE_ID",
    "abc\u202ePRIVATE_ID", "c" * 2049,
])
def test_invalid_page_cursor_is_a_structured_error_not_an_authoritative_end(bad_cursor):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/v2/library/manifest"
        return httpx.Response(200, json={
            "items": [{"product_id": "private-book", "title": "PRIVATE_TITLE"}],
            "next_cursor": bad_cursor,
        })

    client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as raised:
            client.library_manifest()
        assert raised.value.code == "invalid_pagination"
        assert len(calls) == 1  # no unsafe second GET with an invented cursor
        for private in ("PRIVATE_TOKEN", "PRIVATE_TITLE", "private-book"):
            assert private not in str(raised.value)
    finally:
        client.close()


@pytest.mark.parametrize("last_cursor", [None, ""])
def test_missing_or_empty_cursor_is_a_valid_end_of_manifest(last_cursor):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={
            "items": [{"product_id": "owned-1"}],
            "next_cursor": last_cursor,
        })

    client = KoboVaultClient(
        "https://vault.example", "fixture", transport=httpx.MockTransport(handler),
    )
    try:
        assert [row["product_id"] for row in client.library_manifest()] == ["owned-1"]
        assert len(calls) == 1
        assert client.last_manifest_rejections == []
    finally:
        client.close()


def test_opaque_printable_cursor_paginates_without_modifying_its_value():
    calls = []
    cursor = "c+/==_-.~"

    def handler(request):
        calls.append(request)
        assert request.url.path == "/api/v2/library/manifest"
        if len(calls) == 1:
            assert "cursor" not in request.url.params
            return httpx.Response(200, json={
                "items": [{"product_id": "first"}], "next_cursor": cursor,
            })
        assert request.url.params.get("cursor") == cursor
        return httpx.Response(200, json={
            "items": [{"product_id": "second"}], "next_cursor": None,
        })

    client = KoboVaultClient(
        "https://vault.example", "fixture", transport=httpx.MockTransport(handler),
    )
    try:
        assert [item["product_id"] for item in client.library_manifest()] == [
            "first", "second",
        ]
        assert len(calls) == 2
    finally:
        client.close()


def test_repeated_cursor_fails_closed_without_logging_private_pagination_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "items": [{"product_id": f"row-{len(seen)}"}],
            "next_cursor": "PRIVATE_CURSOR_TOKEN",
        })

    client = KoboVaultClient(
        "https://vault.example", "fixture", transport=httpx.MockTransport(handler),
    )
    try:
        with pytest.raises(KoboVaultError) as raised:
            client.library_manifest()
        assert raised.value.code == "invalid_pagination"
        assert len(seen) == 2
        assert "PRIVATE_CURSOR_TOKEN" not in str(raised.value)
    finally:
        client.close()


def test_session_policy_clears_previous_complete_snapshot_on_invalid_cursor():
    phases = ["complete", "bad_cursor"]
    calls = []

    def handler(request):
        calls.append(request)
        if phases[0] == "complete":
            return httpx.Response(200, json={
                "items": [{"product_id": "PRIVATE_BOOK", "owned": True}],
                "next_cursor": None,
            })
        return httpx.Response(200, json={
            "items": [{"product_id": "PRIVATE_BOOK", "owned": True}],
            "next_cursor": False,
        })

    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="PRIVATE_CREDENTIAL", label="Kobo",
        credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example"},
        config={}, storage={},
        diagnostics=SimpleNamespace(add=lambda *_a, **_k: None),
        runtime_capabilities=SimpleNamespace(acquisition=lambda *_a: None),
    )
    session.client.close()
    session.client = KoboVaultClient(
        "https://vault.example", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(handler),
    )
    session.catalog = KoboCatalogService(
        session.client, credential_id=session.credential_id,
        provider_id=session.provider_id,
    )
    session.connected = True
    session._accounts = [
        Account(
            unique_id="PRIVATE_ACCOUNT", provider_id="kobo",
            credential_id=session.credential_id, display_name="Private reader",
        ),
    ]
    try:
        assert len(session.backup_manifest()) == 1
        assert session.backup_manifest_policy["status"] == "classified"
        assert session.backup_manifest_policy["manifest_authoritative"] is True

        phases[0] = "bad_cursor"
        with pytest.raises(KoboVaultError) as raised:
            session.backup_manifest()
        assert raised.value.code == "invalid_pagination"
        assert len(calls) == 2
        assert session.backup_manifest_policy["status"] == "invalid_scope"
        assert session.backup_manifest_policy["manifest_authoritative"] is False
        assert session.backup_manifest_policy["classified_items"] == 0
        assert session.backup_manifest_policy["acquisition_authorized"] is False
        diagnostic = str(session.diagnostic_state()["manifest_materialization_policy"])
        for secret in ("PRIVATE_BOOK", "PRIVATE_ACCOUNT", "PRIVATE_TOKEN", "PRIVATE_CREDENTIAL"):
            assert secret not in diagnostic
    finally:
        session.close()
