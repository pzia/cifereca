"""#981: manifest GET accounting is bounded, private and observation-only.

Synthetic HTTPX, no provider account/real acquisition, no cache purging.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from cifereca_kobo import DESCRIPTOR, KoboSession
from cifereca_kobo_vault import KoboVaultClient, KoboVaultError


def test_manifest_attempts_count_real_pages_not_logical_cached_observations():
    calls = []
    state = {"bad": False}

    def transport(request):
        assert request.url.path == "/api/v2/library/manifest"
        calls.append(request)
        if state["bad"]:
            return httpx.Response(200, json={
                "items": [{"product_id": "PRIVATE_BOOK"}],
                "next_cursor": False,
            })
        if request.url.params.get("cursor") == "second":
            return httpx.Response(200, json={
                "items": [{"product_id": "second"}], "next_cursor": None,
            })
        return httpx.Response(200, json={
            "items": [{"product_id": "first"}], "next_cursor": "second",
        })

    client = KoboVaultClient(
        "https://vault.example/private", "PRIVATE_TOKEN",
        transport=httpx.MockTransport(transport),
    )
    try:
        assert len(client.library_manifest()) == 2
        assert (client.manifest_scans_started, client.manifest_scans_paginated,
                client.manifest_http_get_attempts, client.last_manifest_page_attempts,
                client.last_manifest_pagination_complete) == (1, 1, 2, 2, True)
        state["bad"] = True
        with pytest.raises(KoboVaultError) as err:
            client.library_manifest()
        assert err.value.code == "invalid_pagination"
        assert (client.manifest_scans_started, client.manifest_scans_paginated,
                client.manifest_http_get_attempts, client.last_manifest_page_attempts,
                client.last_manifest_pagination_complete) == (2, 1, 3, 1, False)
        state["bad"] = False
        assert len(client.library_manifest()) == 2
        assert (client.manifest_scans_started, client.manifest_scans_paginated,
                client.manifest_http_get_attempts, client.last_manifest_page_attempts,
                client.last_manifest_pagination_complete) == (3, 2, 5, 2, True)
        assert len(calls) == 5
    finally:
        client.close()


def test_manifest_metrics_export_never_contains_raw_provider_secrets():
    session = KoboSession(
        descriptor=DESCRIPTOR, credential_id="PRIVATE_CREDENTIAL",
        label="Kobo", credentials={"api_token": "PRIVATE_TOKEN"},
        options={"base_url": "https://vault.example/private"},
        config={}, storage={},
        diagnostics=SimpleNamespace(add=lambda *a, **kw: None),
    )
    try:
        session.client.manifest_scans_started = 4
        session.client.manifest_scans_paginated = 3
        session.client.manifest_http_get_attempts = 9
        session.client.last_manifest_page_attempts = 2
        session.client.last_manifest_pagination_complete = True
        public = session.diagnostic_state()["manifest_request_stats"]
        assert public == {
            "scans_started": 4, "scans_paginated": 3,
            "http_get_attempts": 9, "last_scan_page_attempts": 2,
            "last_scan_pagination_complete": True,
        }
        for private in ("PRIVATE_TOKEN", "PRIVATE_CREDENTIAL", "vault.example", "/private"):
            assert private not in json.dumps(public)
        # Even an injected/legacy client must not smuggle strings or booleans
        # through a raw counter field into Core diagnostic exports.
        session.client.manifest_http_get_attempts = "PRIVATE_TOKEN"
        session.client.manifest_scans_paginated = True
        session.client.last_manifest_pagination_complete = 1
        sanitized = session.diagnostic_state()["manifest_request_stats"]
        assert sanitized["http_get_attempts"] == 0
        assert sanitized["scans_paginated"] == 0
        assert sanitized["last_scan_pagination_complete"] is False
    finally:
        session.close()
