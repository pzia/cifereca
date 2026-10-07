from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"impossible de charger {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OPDS = load_module("dataset_fake_opds", "opds/pseudo_opds_server.py")
KOBO = load_module("dataset_fake_kobo", "kobo/pseudo_kobo_services.py")
PARIS = load_module("dataset_fake_paris", "paris/pseudo_paris_service.py")
NUMILOG = load_module("dataset_fake_numilog", "numilog/pseudo_numilog_service.py")
BNF = load_module("dataset_fake_bnf", "bnf/pseudo_bnf_sru_service.py")


class RunningServer:
    def __init__(self, handler, app) -> None:
        handler.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path: str, *, headers: dict[str, str] | None = None):
        request = Request(self.base + path, headers=headers or {})
        with urlopen(request, timeout=3) as response:
            return response.status, response.headers, response.read()

    def json(self, path: str, *, headers: dict[str, str] | None = None):
        status, response_headers, body = self.get(path, headers=headers)
        return status, response_headers, json.loads(body.decode("utf-8"))


class FakeServicesContractTests(unittest.TestCase):
    def test_opds_health_search_and_png_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = OPDS.DatasetCache(Path(tmp), 8, 1234, True)
            with RunningServer(OPDS.Handler, OPDS.FakeOPDSApp(cache)) as server:
                status, _, health = server.json("/health")
                self.assertEqual(status, 200)
                self.assertEqual(health["counts"], {"gallica": 8, "gutenberg": 8})

                status, _, feed = server.get(
                    "/gallica/services/engine/search/opds"
                    "?operation=searchRetrieve&version=1.2&query=&maximumRecords=2"
                )
                self.assertEqual(status, 200)
                self.assertIn(b"/gallica/covers/", feed)
                self.assertIn(b".png", feed)
                self.assertIn(b'type="image/png"', feed)

                status, headers, cover = server.get("/gallica/covers/1.png")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), "image/png")
                self.assertTrue(cover.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_kobo_contract_language_filter_dynamic_account_and_png_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = KOBO.DatasetCache(Path(tmp), 40, 7, 1234, True)
            app = KOBO.KoboFakeApp(cache, service="combined", token="kvt_test", accept_any_token=False)
            auth = {"Authorization": "Bearer kvt_test"}
            with RunningServer(KOBO.Handler, app) as server:
                status, _, service_status = server.json("/api/v2/status")
                self.assertEqual(status, 200)
                self.assertEqual(service_status["api_version"], "2.3")

                status, _, account = server.json("/api/v2/account", headers=auth)
                self.assertEqual(status, 200)
                self.assertIn("7 livres", account["account"]["display_name"])

                status, _, result = server.json(
                    "/api/v2/catalog/search?q=&field=all&limit=3&page=0&language=fr",
                    headers=auth,
                )
                self.assertEqual(status, 200)
                self.assertLessEqual(len(result["items"]), 3)
                self.assertGreater(result["total"], 0)
                self.assertTrue(all(item["language"] == "fr" for item in result["items"]))
                self.assertTrue(all(item["cover"]["thumbnail"].endswith(".png") for item in result["items"]))

                product_id = result["items"][0]["product_id"]
                status, headers, cover = server.get(f"/assets/covers/{product_id}.png")
                self.assertEqual(status, 200)
                self.assertEqual(headers.get_content_type(), "image/png")
                self.assertTrue(cover.startswith(b"\x89PNG\r\n\x1a\n"))

    def test_deterministic_asset_faults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = OPDS.DatasetCache(Path(tmp), 4, 4321, True)
            app = OPDS.FakeOPDSApp(cache, broken_assets_every=2)
            with RunningServer(OPDS.Handler, app) as server:
                with self.assertRaises(HTTPError) as error:
                    server.get("/gallica/covers/2.png")
                self.assertEqual(error.exception.code, 503)

        with tempfile.TemporaryDirectory() as tmp:
            cache = KOBO.DatasetCache(Path(tmp), 8, 2, 4321, True)
            app = KOBO.KoboFakeApp(
                cache,
                service="combined",
                token="kvt_test",
                accept_any_token=False,
                broken_assets_every=2,
            )
            with RunningServer(KOBO.Handler, app) as server:
                with self.assertRaises(HTTPError) as error:
                    server.get("/assets/covers/kobo-fake-00002.png")
                self.assertEqual(error.exception.code, 503)

    def test_other_fake_services_health_contracts(self):
        cases = []
        tempdirs = []
        try:
            td = tempfile.TemporaryDirectory()
            tempdirs.append(td)
            paris_cache = PARIS.DatasetCache(Path(td.name), 20, 2, 3, 1, 0.7, 1234, True)
            cases.append((PARIS.Handler, PARIS.ParisFakeApp(paris_cache, "paris-test", "test")))

            td = tempfile.TemporaryDirectory()
            tempdirs.append(td)
            numilog_cache = NUMILOG.DatasetCache(Path(td.name), 12, 1234, True)
            cases.append((NUMILOG.Handler, NUMILOG.NumilogFakeApp(numilog_cache, "numilog-test@example.test", "test", 5)))

            td = tempfile.TemporaryDirectory()
            tempdirs.append(td)
            bnf_cache = BNF.DatasetCache(Path(td.name), 12, 1234, True)
            cases.append((BNF.Handler, BNF.BnfSruApp(bnf_cache)))

            for handler, app in cases:
                with self.subTest(handler=handler.__module__):
                    with RunningServer(handler, app) as server:
                        status, _, health = server.json("/health")
                        self.assertEqual(status, 200)
                        self.assertTrue(health["ok"])
        finally:
            for td in tempdirs:
                td.cleanup()


if __name__ == "__main__":
    unittest.main()
