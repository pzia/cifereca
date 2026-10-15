"""P0 #771: real synthetic Kobo HTTP negatives, including delegated auth.

Strictly loopback, fake token, fake account and catalogue. No provider network,
real credentials, DRM fulfillment, or acquisition of a real book.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from simulation.tests.test_fake_services_contract import KOBO, RunningServer


@pytest.fixture
def vault():
    with tempfile.TemporaryDirectory() as tmp:
        cache = KOBO.DatasetCache(Path(tmp) / "kobo", 40, 7, 20261015, True)
        app = KOBO.KoboFakeApp(
            cache, service="combined", token="kvt_fake_for_tests",
            accept_any_token=False,
        )
        with RunningServer(KOBO.Handler, app) as server:
            yield server


def _post(server, path, payload, headers=None):
    request = Request(
        server.base + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        return response.status, json.load(response)


def _denied(request, *, code, error="missing_token"):
    with pytest.raises(HTTPError) as caught:
        urlopen(request, timeout=3)
    assert caught.value.code == code
    body = json.loads(caught.value.read().decode("utf-8"))
    assert body["error"]["code"] == error
    assert "kvt_fake_for_tests" not in repr(body)
    return body


def test_kobo_private_account_inventory_and_delegated_proxy_reject_missing_token(vault):
    headers = {"Authorization": "Bearer kvt_fake_for_tests"}
    status, _, manifest = vault.json("/api/v2/library/manifest?limit=2", headers=headers)
    assert status == 200 and len(manifest["items"]) == 2
    product_id = manifest["items"][0]["product_id"]
    paths = (
        "/api/v2/account",
        "/api/v2/library/manifest?limit=2",
        "/api/v2/library/search?q=livre",
        f"/api/v2/library/{product_id}",
        f"/delegated/contentaccess/books/{product_id}",
    )
    for path in paths:
        _denied(Request(vault.base + path), code=401)
        _denied(
            Request(vault.base + path, headers={"Authorization": "Bearer wrong"}),
            code=401, error="invalid_token",
        )

    # POST requires the same authorization, before any acquisition resolver.
    acquisition = f"/api/v2/library/{product_id}/acquisition"
    for auth, expected in (({}, "missing_token"),
                           ({"Authorization": "Bearer wrong"}, "invalid_token")):
        request = Request(
            vault.base + acquisition, method="POST", data=b'{"mode":"descriptor"}',
            headers={"Content-Type": "application/json", **auth},
        )
        _denied(request, code=401, error=expected)

    # Vault status intentionally remains public, delegated data does not.
    public_status, _, public = vault.json("/api/v2/status")
    assert public_status == 200 and public["api_version"] == "2.3"
    authorized_status, _, delegated = vault.json(
        f"/delegated/contentaccess/books/{product_id}", headers=headers,
    )
    assert authorized_status == 200
    assert delegated["product_id"] == product_id
    assert delegated["content_keys"] == []


def test_kobo_negative_pagination_and_acquisition_modes_are_deterministic(vault):
    auth = {"Authorization": "Bearer kvt_fake_for_tests"}
    first_status, _, first = vault.json(
        "/api/v2/library/manifest?limit=2", headers=auth,
    )
    assert first_status == 200 and len(first["items"]) == 2
    assert first["next_cursor"]
    second_status, _, second = vault.json(
        "/api/v2/library/manifest?limit=2&cursor=" + first["next_cursor"],
        headers=auth,
    )
    assert second_status == 200 and len(second["items"]) == 2
    assert {book["product_id"] for book in first["items"]}.isdisjoint(
        {book["product_id"] for book in second["items"]}
    )
    invalid = _denied(
        Request(vault.base + "/api/v2/library/manifest?cursor=not-a-valid-cursor",
                headers=auth),
        code=400, error="invalid_request",
    )
    assert "not-a-valid-cursor" not in repr(invalid)
    product_id = first["items"][0]["product_id"]
    for payload, expected in (
        ({"mode": "inject-download"}, "invalid_mode"),
        ({"display_profile": "PrivateDevice"}, "invalid_display_profile"),
    ):
        request = Request(
            vault.base + f"/api/v2/library/{product_id}/acquisition",
            data=json.dumps(payload).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", **auth},
        )
        _denied(request, code=400, error=expected)

    missing = _denied(
        Request(vault.base + "/api/v2/library/unknown-fake-product/acquisition",
                data=b"{}", method="POST",
                headers={"Content-Type": "application/json", **auth}),
        code=404, error="not_found",
    )
    assert "kvt_fake_for_tests" not in repr(missing)
