from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "provider-bnf-catalogue"
DATASET_PATH = ROOT / "gen-dataset" / "bnf" / "pseudo_bnf_sru_service.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from cifereca_bnf_catalogue import BnfCatalogueSession

spec = importlib.util.spec_from_file_location("dataset_bnf_sru", DATASET_PATH)
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


def test_series_and_collection_search_against_local_sru():
    with tempfile.TemporaryDirectory() as tmp:
        cache = dataset.DatasetCache(Path(tmp) / "cache", 120, 20261006, True)
        row = cache.connect().execute(
            "SELECT * FROM books WHERE publisher = 'Maison Sillage' LIMIT 1"
        ).fetchone()
        if row is None:
            row = cache.connect().execute(
                "SELECT * FROM books WHERE publisher NOT LIKE '%é%' LIMIT 1"
            ).fetchone()
        assert row is not None

        with RunningServer(dataset.BnfSruApp(cache)) as server:
            session = BnfCatalogueSession(
                credential_id="public",
                label="",
                credentials={},
                options={"sru_url": server.base + "/api/SRU", "page_size": 100},
                diagnostics=Diagnostics(),
            )
            try:
                session.connect()

                series = session.search(str(row["subject"]), field="series")
                assert series
                assert any(
                    f"Série {row['subject']}" in item.metadata.get("series", [])
                    for item in series
                )

                collections = session.search(str(row["publisher"]), field="collection")
                assert collections
                assert any(
                    f"Collection {row['publisher']}" in item.metadata.get("collections", [])
                    for item in collections
                )

                # A Gallica URL carried by dc:relation must remain a documentary
                # link and never become a series label.
                assert all(
                    not any(value.startswith("http") for value in item.metadata.get("series", []))
                    for item in series
                )
            finally:
                session.close()
