from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = ROOT / "src" / "plugins" / "provider-kobo"
DATASET_PATH = ROOT / "simulation" / "kobo" / "pseudo_kobo_services.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from cifereca_kobo import KoboSession

spec = importlib.util.spec_from_file_location("dataset_kobo_provider", DATASET_PATH)
dataset = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = dataset
spec.loader.exec_module(dataset)


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


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


def test_kobo_provider_against_local_vault_catalog_and_acquisition():
    with tempfile.TemporaryDirectory() as tmp:
        cache = dataset.DatasetCache(Path(tmp) / "cache", 80, 20, 20261006, True)
        app = dataset.KoboFakeApp(
            cache,
            service="combined",
            token=dataset.DEFAULT_TOKEN,
            accept_any_token=False,
        )

        with RunningServer(app) as server:
            session = KoboSession(
                descriptor=__import__("cifereca_kobo").DESCRIPTOR,
                credential_id="kobo-main",
                label="Kobo Test",
                credentials={"api_token": dataset.DEFAULT_TOKEN},
                options={"base_url": server.base, "display_profile": "Android"},
                config={},
                storage={},
                diagnostics=Diagnostics(),
            )
            try:
                accounts = session.connect()
                assert len(accounts) == 1
                assert session.api_version == "2.3"
                assert "library" in session.capability_names

                results = session.search("", page=0)
                assert results
                assert all(item.source_provider == "kobo" for item in results)
                # The simulator publishes HTTP covers on 127.0.0.1. Such
                # URLs must not be projected into a UI that loads images
                # automatically; public CDN/relative covers are tested in
                # test_kobo_public_cover_url_p1 instead.
                assert all(item.thumbnail_url is None for item in results)
                assert all("catalog_cover" not in item.metadata for item in results)

                library = session.library()
                assert len(library) == 20
                owned = next(
                    item for item in library
                    if item.owned
                    and item.downloadable is not False
                    and not bool(item.metadata.get("archived"))
                )

                resolved = session.resolve_acquisition(
                    owned.record_id,
                    revision_id=owned.revision_id,
                    mode="descriptor",
                    include_acsm=True,
                )
                acquisition = resolved["acquisition"]
                assert acquisition["product_id"] == owned.record_id
                assert acquisition["candidates"]
                candidate = acquisition["candidates"][0]
                assert candidate["kind"] in {"http", "acsm"}
                assert candidate["drm_type"] in {"SignedNoDrm", "AdobeDrm", "KDRM"}
                assert "content_keys" in acquisition

                holdings = session.holdings(owned)
                assert holdings.availability_status in {"owned", "attention_required"}
            finally:
                session.close()
