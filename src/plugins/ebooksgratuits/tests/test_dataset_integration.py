from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = ROOT / "src" / "plugins" / "ebooksgratuits"
TRANSPORT_ROOT = ROOT / "src" / "plugins" / "transport-http"
DATASET_PATH = ROOT / "simulation" / "ebooksgratuits" / "pseudo_ebooksgratuits_service.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
if str(TRANSPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TRANSPORT_ROOT))

from cifereca_ebooksgratuits import EbooksSession
from cifereca_http_transport import HttpTransport

spec = importlib.util.spec_from_file_location("dataset_ebooksgratuits", DATASET_PATH)
dataset = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = dataset
spec.loader.exec_module(dataset)


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class TransportContext:
    config = {"timeout_seconds": 5, "max_download_bytes": 16 * 1024 * 1024, "max_redirects": 4}
    import logging
    logger = logging.getLogger("test.ebooksgratuits.transport")


class RunningServer:
    def __init__(self, app):
        dataset.Handler.app = app
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dataset.Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        host, port = self.server.server_address
        self.base = f"http://{host}:{port}/"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def test_local_dataset_search_filters_and_download():
    with tempfile.TemporaryDirectory() as tmp:
        cache = dataset.DatasetCache(Path(tmp) / "cache", 80, 20261006, True)
        first = cache.get(1)
        assert first is not None
        with RunningServer(dataset.EbooksGratuitsApp(cache)) as server:
            transport = HttpTransport(TransportContext())
            session = EbooksSession(
                credential_id="public",
                label="",
                credentials={},
                options={"base_url": server.base, "search_page_size": 20, "acquisition_backend": "html"},
                config={},
                diagnostics=Diagnostics(),
                transport=transport,
            )
            try:
                session.connect()
                by_title = session.search(str(first["title"]), field="title")
                assert by_title
                by_author = session.search(str(first["author"]), field="author")
                assert by_author and all(row.author == first["author"] for row in by_author)
                by_category = session.search(str(first["category"]), field="category")
                assert by_category
                by_genre = session.search(str(first["genre"]), field="genre")
                assert by_genre

                target = Path(tmp) / "download"
                path, _source = session.download_direct(by_title[0], target)
                assert path.exists()
                assert path.read_bytes().startswith(b"PK")
            finally:
                session.close()
                transport.close()
