from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "provider-gallica"
TRANSPORT_ROOT = ROOT / "plugins" / "transport-http"
DATASET_PATH = ROOT / "gen-dataset" / "opds" / "pseudo_opds_server.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))
if str(TRANSPORT_ROOT) not in sys.path:
    sys.path.insert(0, str(TRANSPORT_ROOT))

from cifereca_gallica import GallicaSession
from cifereca_http_transport import HttpTransport

spec = importlib.util.spec_from_file_location("dataset_opds_gallica", DATASET_PATH)
dataset = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = dataset
spec.loader.exec_module(dataset)


class Diagnostics:
    def add(self, *args, **kwargs):
        return None


class TransportContext:
    config = {"timeout_seconds": 5, "max_download_bytes": 1024 * 1024, "max_redirects": 4}
    import logging
    logger = logging.getLogger("test.gallica.transport")


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


def test_pdf_search_and_download_against_local_opds():
    with tempfile.TemporaryDirectory() as tmp:
        cache = dataset.DatasetCache(Path(tmp) / "cache", 20, 20261006, True)
        row = cache.get("gallica", 2)
        assert row is not None

        with RunningServer(dataset.FakeOPDSApp(cache)) as server:
            transport = HttpTransport(TransportContext())
            session = GallicaSession(
                credential_id="public",
                label="",
                credentials={},
                options={
                    "opds_url": server.base + "/gallica/opds",
                    "epub_search_url": server.base + "/gallica/services/engine/search/opds",
                    "include_audio": False,
                },
                config={},
                diagnostics=Diagnostics(),
                transport=transport,
            )
            try:
                session.connect()
                results = session.search(str(row["title"]), field="title", content_types=("pdf",))
                pdfs = [item for item in results if item.format == "PDF"]
                assert pdfs
                assert pdfs[0].download_url.endswith("/gallica/pdf/2.pdf")

                target = Path(tmp) / "downloads"
                path, final_url = session.download_direct(pdfs[0], target)
                assert path.suffix == ".pdf"
                assert path.read_bytes().startswith(b"%PDF-")
                assert final_url.endswith("/gallica/pdf/2.pdf")
            finally:
                session.close()
                transport.close()
