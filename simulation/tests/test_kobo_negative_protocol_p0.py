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

import unittest

from simulation.tests.test_fake_services_contract import KOBO, RunningServer


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
    try:
        urlopen(request, timeout=3)
    except HTTPError as caught:
        assert caught.code == code, caught.code
        body = json.loads(caught.read().decode("utf-8"))
        assert body["error"]["code"] == error
        assert "kvt_fake_for_tests" not in repr(body)
        return body
    else:
        raise AssertionError("Private fixture endpoint unexpectedly succeeded")


class KoboNegativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        cache = KOBO.DatasetCache(
            Path(self.tempdir.name) / "kobo", 40, 7, 20261015, True,
        )
        app = KOBO.KoboFakeApp(
            cache, service="combined", token="kvt_fake_for_tests",
            accept_any_token=False,
        )
        self.cache = cache
        self.server_context = RunningServer(KOBO.Handler, app)
        self.vault = self.server_context.__enter__()

    def tearDown(self):
        try:
            self.server_context.__exit__(None, None, None)
        finally:
            self.tempdir.cleanup()

    def test_kobo_private_account_inventory_and_delegated_proxy_reject_missing_token(self):
        vault = self.vault
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

    def test_kobo_negative_pagination_and_acquisition_modes_are_deterministic(self):
        vault = self.vault
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


    def test_opt_in_catalogue_search_failures_are_auth_scoped_and_recover(self):
        vault = self.vault
        auth = {"Authorization": "Bearer kvt_fake_for_tests"}
        endpoint = "/api/v2/catalog/search?q=&field=all&limit=3&page=0"
        status, _, original = vault.json(endpoint, headers=auth)
        assert status == 200 and len(original["items"]) == 3
        # A public request parameter must not bypass Vault Bearer checks.
        _denied(Request(vault.base + endpoint + "&ux_fault=503"), code=401)
        for fault, code, reason in (
            ("429", 429, "rate_limited"),
            ("503", 503, "temporary_unavailable"),
            ("expired", 401, "expired_token"),
        ):
            req = Request(vault.base + endpoint + "&ux_fault=" + fault, headers=auth)
            rejected = _denied(req, code=code, error=reason)
            assert "kvt_fake_for_tests" not in repr(rejected)
            if fault == "429":
                with self.assertRaises(HTTPError) as caught:
                    urlopen(req, timeout=3)
                assert caught.exception.headers.get("Retry-After") == "2"
        status, headers, malformed = vault.get(
            endpoint + "&ux_fault=malformed_json", headers=auth,
        )
        assert status == 200 and headers.get_content_type() == "application/json"
        with self.assertRaises(json.JSONDecodeError):
            json.loads(malformed)
        assert not list(self.cache.epub_dir.glob("*.epub"))
        assert not list(self.cache.acsm_dir.glob("*.acsm"))
        # Unrecognised faults are ignored; a healthy request is still identical.
        assert vault.json(endpoint + "&ux_fault=unknown", headers=auth)[2] == original
        assert vault.json(endpoint, headers=auth)[2] == original

    def test_opt_in_manifest_http_failures_do_not_change_default_inventory(self):
        vault = self.vault
        auth = {"Authorization": "Bearer kvt_fake_for_tests"}
        endpoint = "/api/v2/library/manifest?limit=2"
        for fault, code, reason in (
            ("429", 429, "rate_limited"),
            ("503", 503, "temporary_unavailable"),
            ("expired", 401, "expired_token"),
        ):
            req = Request(vault.base + endpoint + "&ux_fault=" + fault, headers=auth)
            error = _denied(req, code=code, error=reason)
            assert "kvt_fake_for_tests" not in repr(error)
            if code == 429:
                with self.assertRaises(HTTPError) as caught:
                    urlopen(req, timeout=3)
                assert caught.exception.headers.get("Retry-After") == "2"

        # A publicly supplied fault flag cannot bypass Bearer authentication.
        _denied(Request(vault.base + endpoint + "&ux_fault=503"), code=401)

        # No fault must activate implicitly or mutate actual pages/cursors.
        status, _, rows = vault.json(endpoint, headers=auth)
        assert status == 200 and len(rows["items"]) == 2
        assert rows["next_cursor"]
        status, _, ignored = vault.json(
            endpoint + "&ux_fault=not_supported", headers=auth,
        )
        assert status == 200 and ignored == rows

    def test_opt_in_manifest_malformed_json_and_cursor_loop_are_reproducible(self):
        vault = self.vault
        auth = {"Authorization": "Bearer kvt_fake_for_tests"}
        endpoint = "/api/v2/library/manifest?limit=2"
        status, headers, raw = vault.get(
            endpoint + "&ux_fault=malformed_json", headers=auth,
        )
        assert status == 200 and headers.get_content_type() == "application/json"
        with self.assertRaises(json.JSONDecodeError):
            json.loads(raw.decode("utf-8"))
        assert b"kvt_fake_for_tests" not in raw

        status, _, first = vault.json(
            endpoint + "&ux_fault=cursor_loop", headers=auth,
        )
        assert status == 200 and len(first["items"]) == 2
        assert first["next_cursor"]
        status, _, again = vault.json(
            endpoint + "&ux_fault=cursor_loop&cursor=" + first["next_cursor"],
            headers=auth,
        )
        assert status == 200
        assert again["next_cursor"] == first["next_cursor"]
        assert again["items"] == first["items"]
