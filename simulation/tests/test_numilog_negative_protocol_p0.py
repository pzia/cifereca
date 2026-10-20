"""P0 #771: Numilog private-route negative HTTP contracts, strictly loopback.

Synthetic account, inventory and bytes only. No real Numilog provider, rights,
ACSM fulfillment, DRM, login, or network beyond an ephemeral localhost port.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from simulation.tests.test_fake_services_contract import NUMILOG, RunningServer


class NumilogNegativeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = NUMILOG.DatasetCache(Path(self.directory.name), 7, 20261020, True)
        app = NUMILOG.NumilogFakeApp(self.cache, "fake@example.test", "fake-password", 3)
        self.ctx = RunningServer(
            type("LocalNumilogPrivateHandler", (NUMILOG.Handler,), {"app": app}), app
        )
        self.http = self.ctx.__enter__()

    def tearDown(self):
        try:
            self.ctx.__exit__(None, None, None)
        finally:
            self.directory.cleanup()

    def get(self, route):
        try:
            return self.http.get(route)
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def login(self, route, password="fake-password"):
        request = Request(
            self.http.base + route, method="POST",
            data=json.dumps({"frmLogin": "fake@example.test", "frmPassword": password}).encode(),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=3) as response:
                return response.status, response.headers, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()

    def test_login_rejection_and_rate_limit_are_explicit_and_do_not_set_cookie(self):
        route = "/Login.Aspx/AuthUser"
        status, headers, result = self.login(route + "?ux_fault=auth_rejected")
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(result["d"])["AuthSuccess"])
        self.assertIsNone(headers.get("Set-Cookie"))
        status, headers, body = self.login(route + "?ux_fault=429")
        self.assertEqual(status, 429)
        self.assertEqual(headers.get("Retry-After"), "3")
        self.assertEqual(body, b"fake rate limit")
        status, headers, result = self.login(route)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(result["d"])["AuthSuccess"])
        self.assertIn("NUMILOG_FAKE", headers.get("Set-Cookie", ""))

    def test_inventory_zero_one_many_and_broken_next_are_deterministic(self):
        path = "/Pages/Compte/MesLivres.aspx"
        for page, count in ((1, 3), (2, 3), (3, 1), (4, 0)):
            status, headers, body = self.get(path + f"?page={page}")
            self.assertEqual(status, 200)
            self.assertEqual(body.count(b'class="book"'), count)
            self.assertEqual(headers.get_content_type(), "text/html")
        for fault, code in (("session_expired", 401), ("429", 429), ("503", 503)):
            status, _, body = self.get(path + "?ux_fault=" + fault)
            self.assertEqual(status, code)
            self.assertNotIn(b'class="book"', body)
        status, _, body = self.get(path + "?ux_fault=bad_inventory")
        self.assertEqual(status, 200)
        self.assertNotIn(b"id='myTable'", body)
        status, _, body = self.get(path + "?ux_fault=empty_inventory")
        self.assertEqual(status, 200)
        self.assertIn(b"id='myTable'", body)
        self.assertNotIn(b'class="book"', body)
        status, _, first = self.get(path + "?page=1&ux_fault=broken_next")
        self.assertEqual(status, 200)
        self.assertIn(b'rel="next"', first)
        status, _, _ = self.get(path + "?page=2&ux_fault=broken_next")
        self.assertEqual(status, 503)
        # An unsupported switch never changes the normal fixture.
        normal = self.get(path + "?page=1")[2]
        self.assertEqual(self.get(path + "?page=1&ux_fault=unknown")[2], normal)

    def test_delivery_expiration_missing_format_and_invalid_bytes_have_no_side_effect(self):
        read = "/Pages/Compte/readBook.ashx?lid=3"
        delivery = "/Services/Delivery.ashx?lid=3"
        for path in (read, delivery):
            status, _, _ = self.get(path + "&ux_fault=expired_link")
            self.assertEqual(status, 410)
            status, headers, _ = self.get(path + "&ux_fault=429")
            self.assertEqual(status, 429)
            self.assertEqual(headers.get("Retry-After"), "3")
        status, headers, body = self.get(delivery + "&ux_fault=missing_format")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "text/html")
        self.assertNotIn(b".epub", body)
        epub_path = self.cache.epub_path(3)
        self.assertFalse(epub_path.exists())
        for fault, mime, expected in (
            ("wrong_mime", "text/html", b"Not a book"),
            ("invalid_epub", "application/epub+zip", b"not a zip or EPUB"),
        ):
            status, headers, body = self.get("/downloads/3.epub?ux_fault=" + fault)
            self.assertEqual(status, 200)
            self.assertEqual(headers.get_content_type(), mime)
            self.assertIn(expected, body)
            self.assertFalse(epub_path.exists())
        status, headers, body = self.get("/downloads/7.acsm?ux_fault=invalid_acsm")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/vnd.adobe.adept+xml")
        self.assertEqual(body, b"<fulfillmentToken")
        self.assertFalse(epub_path.exists())
        # Normal delivery still produces a valid local file when explicitly requested.
        status, headers, body = self.get("/downloads/3.epub")
        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/epub+zip")
        self.assertTrue(body.startswith(b"PK"))
        self.assertTrue(epub_path.exists())

    def test_fault_scope_excludes_public_catalogue_and_health(self):
        status, _, _ = self.get("/health?ux_fault=503")
        self.assertEqual(status, 200)
        public = "/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query="
        self.assertEqual(self.get(public)[0], 200)
        # The independently validated public fixture rejects an unknown fault.
        # Private protocol switches do not expand its allowed parameters.
        self.assertEqual(self.get(public + "&ux_fault=invalid_epub")[0], 400)
        status, _, body = self.get("/Pages/Compte/MesLivres.aspx?page=1")
        self.assertEqual(status, 200)
        self.assertEqual(body.count(b'class="book"'), 3)


if __name__ == "__main__":
    unittest.main()
