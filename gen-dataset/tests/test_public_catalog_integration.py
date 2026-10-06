from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "src"))

for plugin_dir in (
    REPO / "plugins" / "ebooksgratuits",
    REPO / "plugins" / "provider-bnf-catalogue",
    REPO / "plugins" / "provider-gallica",
    REPO / "plugins" / "provider-gutenberg",
):
    sys.path.insert(0, str(plugin_dir))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"impossible de charger {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


OPDS = load_module("integration_fake_opds", ROOT / "opds" / "pseudo_opds_server.py")
BNF = load_module("integration_fake_bnf", ROOT / "bnf" / "pseudo_bnf_sru_service.py")
EGS = load_module("integration_fake_ebooksgratuits", ROOT / "ebooksgratuits" / "pseudo_ebooksgratuits_service.py")

EG = load_module("integration_provider_ebooksgratuits", REPO / "plugins" / "ebooksgratuits" / "cifereca_ebooksgratuits.py")
BNF_PROVIDER = load_module("integration_provider_bnf", REPO / "plugins" / "provider-bnf-catalogue" / "cifereca_bnf_catalogue.py")
GALLICA = load_module("integration_provider_gallica", REPO / "plugins" / "provider-gallica" / "cifereca_gallica.py")
GUTENBERG = load_module("integration_provider_gutenberg", REPO / "plugins" / "provider-gutenberg" / "cifereca_gutenberg.py")


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class RunningServer:
    def __init__(self, handler, app):
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


def session_kwargs(options: dict):
    return {
        "credential_id": "integration",
        "label": "Integration",
        "credentials": {},
        "options": options,
    }


class PublicCatalogIntegrationTests(unittest.TestCase):
    def test_ebooksgratuits_author_category_and_genre(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = EGS.DatasetCache(Path(tmp), 80, 20261006, True)
            row = cache.get(1)
            with RunningServer(EGS.Handler, EGS.EbooksGratuitsApp(cache)) as server:
                session = EG.EbooksSession(
                    config={}, diagnostics=Diagnostics(),
                    **session_kwargs({"base_url": server.base + "/", "search_page_size": 50}),
                )
                try:
                    session.connect()
                    by_author = session.search(str(row["author"]), field="author")
                    self.assertTrue(by_author)
                    self.assertTrue(all(item.author == row["author"] for item in by_author))

                    by_category = session.search(str(row["category"]), field="category")
                    self.assertTrue(by_category)

                    by_genre = session.search(str(row["genre"]), field="genre")
                    self.assertTrue(by_genre)
                finally:
                    session.close()

    def test_bnf_series_and_collection_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = BNF.DatasetCache(Path(tmp), 80, 20261006, True)
            row = cache.connect().execute("SELECT * FROM books ORDER BY book_id LIMIT 1").fetchone()
            with RunningServer(BNF.Handler, BNF.BnfSruApp(cache)) as server:
                session = BNF_PROVIDER.BnfCatalogueSession(
                    diagnostics=Diagnostics(),
                    **session_kwargs({"sru_url": server.base + "/api/SRU", "page_size": 50}),
                )
                try:
                    session.connect()
                    series = session.search(str(row["subject"]), field="series")
                    self.assertTrue(series)
                    self.assertTrue(any(
                        str(row["subject"]).casefold() in str(value).casefold()
                        for item in series for value in item.metadata.get("series", [])
                    ))

                    collections = session.search(str(row["subject"]), field="collection")
                    self.assertTrue(collections)
                    self.assertTrue(any(
                        str(row["subject"]).casefold() in str(value).casefold()
                        for item in collections for value in item.metadata.get("collections", [])
                    ))
                finally:
                    session.close()

    def test_gallica_pdf_is_returned_by_public_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = OPDS.DatasetCache(Path(tmp), 30, 20261006, True)
            row = cache.get("gallica", 2)
            with RunningServer(OPDS.Handler, OPDS.FakeOPDSApp(cache)) as server:
                session = GALLICA.GallicaSession(
                    config={}, diagnostics=Diagnostics(),
                    **session_kwargs({
                        "opds_url": server.base + "/gallica/opds",
                        "epub_search_url": server.base + "/gallica/services/engine/search/opds",
                        "include_audio": False,
                    }),
                )
                try:
                    session.connect()
                    rows = session.search(str(row["title"]), field="title", content_types=("pdf",))
                    self.assertTrue(rows)
                    self.assertTrue(any(item.format == "PDF" for item in rows))
                    self.assertTrue(any(str(item.download_url).endswith(".pdf") for item in rows))
                finally:
                    session.close()

    def test_gutenberg_language_and_bookshelf_navigation(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = OPDS.DatasetCache(Path(tmp), 80, 20261006, True)
            row = cache.connect().execute(
                "SELECT * FROM books WHERE source='gutenberg' AND language='fr' ORDER BY book_id LIMIT 1"
            ).fetchone()
            self.assertIsNotNone(row)
            with RunningServer(OPDS.Handler, OPDS.FakeOPDSApp(cache)) as server:
                session = GUTENBERG.GutenbergSession(
                    config={}, diagnostics=Diagnostics(),
                    **session_kwargs({
                        "contact": "integration@example.invalid",
                        "opds_search_url": server.base + "/gutenberg/ebooks/search.opds/",
                    }),
                )
                try:
                    session.connect()
                    french = session.search("lang:fr")
                    self.assertTrue(french)
                    self.assertTrue(all(item.metadata.get("language") == "fr" for item in french))

                    shelf = session.search("bookshelf:" + str(row["subject"]))
                    self.assertTrue(shelf)
                    self.assertTrue(any(
                        str(row["subject"]).casefold() in " ".join(item.metadata.get("bookshelves", [])).casefold()
                        for item in shelf
                    ))
                finally:
                    session.close()


if __name__ == "__main__":
    unittest.main()
