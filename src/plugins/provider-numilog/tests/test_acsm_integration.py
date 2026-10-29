from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
import zipfile

from cifereca.plugin_api import Account, AccountRef, AcquisitionBroker, IdentityBroker

ROOT = Path(__file__).resolve().parents[4]
PLUGIN_ROOT = ROOT / "src" / "plugins" / "provider-numilog"
DATASET_PATH = ROOT / "simulation" / "numilog" / "pseudo_numilog_service.py"

if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from cifereca_numilog import NumilogSession

spec = importlib.util.spec_from_file_location("dataset_numilog_acsm", DATASET_PATH)
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
        self.base = f"http://{host}:{port}/"
        return self

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class FakeIdentityBroker:
    def __init__(self, account_id: str):
        self.account_id = account_id

    def accounts(self, identity_id=None):
        if identity_id not in {None, "alexandre"}:
            return ()
        return (
            AccountRef(
                account_id=self.account_id,
                identity_id="alexandre",
                provider_id="numilog",
                label="Numilog Alexandre",
            ),
        )


class FakeAcquisitionBroker:
    def __init__(self, root: Path):
        self.root = root
        self.last_request = None
        self.requests = []

    def import_acsm(self, path, **options):
        source = Path(path)
        assert source.is_file()
        identity_id = str(options.get("identity_id") or "")
        provider_id = str(options.get("provider_id") or "")
        assert identity_id == "alexandre"
        assert provider_id == "numilog"

        request = {
            "source": {
                "type": "acsm",
                "path": str(source),
                "provider": provider_id,
            },
            "identity_id": identity_id,
            "profile": "alexandre",
            "title": options.get("title"),
        }
        self.last_request = request
        self.requests.append(request)

        destination = self.root / "library" / identity_id / provider_id / "numilog-fulfilled.epub"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr(
                "META-INF/container.xml",
                '<?xml version="1.0"?><container version="1.0"></container>',
            )
            archive.writestr("EPUB/content.xhtml", "<html><body>fulfilled</body></html>")

        request["status"] = "complete"
        request["path"] = str(destination)
        return {
            "status": "complete",
            "path": str(destination),
            "profile": "alexandre",
        }


def test_acsm_download_reaches_public_broker_and_simulated_adept_fulfillment():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = dataset.DatasetCache(root / "dataset", 14, 20261006, True)
        row = cache.get(7)
        assert row is not None

        credential_id = "numilog-main"
        scope = hashlib.sha256(credential_id.encode()).hexdigest()[:20]
        account_id = "numilog:" + scope
        acquisition: AcquisitionBroker = FakeAcquisitionBroker(root / "core")
        identities: IdentityBroker = FakeIdentityBroker(account_id)

        with RunningServer(dataset.NumilogFakeApp(
            cache, dataset.DEFAULT_USERNAME, dataset.DEFAULT_PASSWORD, 20
        )) as server:
            session = NumilogSession(
                credential_id=credential_id,
                label="Numilog Alexandre",
                credentials={
                    "email": dataset.DEFAULT_USERNAME,
                    "password": dataset.DEFAULT_PASSWORD,
                },
                options={"base_url": server.base},
                config={},
                diagnostics=Diagnostics(),
                acquisition_broker=acquisition,
                identity_broker=identities,
            )
            try:
                session.connect()
                session._accounts[0].identity_id = "alexandre"
                item = next(book for book in session.library() if book.title == row["title"])
                produced, source = session.download_direct(item, root / "provider-downloads")

                assert produced.is_file()
                assert produced.suffix == ".epub"
                assert zipfile.is_zipfile(produced)
                assert source == session.library_url

                assert acquisition.last_request is not None
                assert acquisition.last_request["source"]["type"] == "acsm"
                assert Path(acquisition.last_request["source"]["path"]).suffix == ".acsm"
                assert acquisition.last_request["source"]["provider"] == "numilog"
                assert acquisition.last_request["identity_id"] == "alexandre"
                assert acquisition.last_request["profile"] == "alexandre"
                assert acquisition.last_request["status"] == "complete"

                # Numilog's transport staging is cleaned after the broker consumed it.
                assert not list((root / "provider-downloads").glob("*.acsm"))
                assert not list((root / "provider-downloads").glob("*.part"))
            finally:
                session.close()
