from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "provider-gutenberg"
DATASET_PATH = ROOT / "gen-dataset" / "opds" / "pseudo_opds_server.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from cifereca_gutenberg import GutenbergSession

spec = importlib.util.spec_from_file_location("dataset_opds_gutenberg", DATASET_PATH)
dataset = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = dataset
spec.loader.exec_module(dataset)


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class RunningServer:
    def __init__(self, app):
        dataset.Handler.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dataset.Handler)
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


def test_language_and_bookshelf_navigation_against_local_opds():
    with tempfile.TemporaryDirectory() as tmp:
        cache = dataset.DatasetCache(Path(tmp) / "cache", 120, 20261006, True)
        french = cache.connect().execute(
            "SELECT * FROM books WHERE source='gutenberg' AND language='fr' LIMIT 1"
        ).fetchone()
        assert french is not None

        shelf = cache.connect().execute(
            "SELECT * FROM books WHERE source='gutenberg' AND length(subject) >= 2 LIMIT 1"
        ).fetchone()
        assert shelf is not None

        with RunningServer(dataset.FakeOPDSApp(cache)) as server:
            session = GutenbergSession(
                credential_id="public",
                label="",
                credentials={},
                options={
                    "contact": "dataset@example.test",
                    "opds_search_url": server.base + "/gutenberg/ebooks/search.opds/",
                },
                config={},
                diagnostics=Diagnostics(),
            )
            try:
                session.connect()

                by_language = session.search("lang:fr")
                assert by_language
                assert all(item.metadata.get("language") == "fr" for item in by_language)

                by_bookshelf = session.search(f"bookshelf:{shelf['subject']}")
                assert by_bookshelf
                assert all(
                    str(shelf["subject"]).casefold()
                    in " ".join(item.metadata.get("bookshelves", [])).casefold()
                    for item in by_bookshelf
                )
            finally:
                session.close()
