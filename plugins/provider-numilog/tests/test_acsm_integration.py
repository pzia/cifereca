from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import zipfile

from cifereca.brokers import AcquisitionBrokerImpl, IdentityBrokerImpl
from cifereca.services import LibraryService

ROOT = Path(__file__).resolve().parents[3]
PLUGIN_ROOT = ROOT / "plugins" / "provider-numilog"
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


class Storage:
    def __init__(self):
        self.acquisitions = {}

    def acquisition_get(self, acquisition_id):
        row = self.acquisitions.get(acquisition_id)
        return dict(row) if row else None

    def acquisition_put(self, row):
        self.acquisitions[row["acquisition_id"]] = dict(row)

    def acquisition_update_state(self, acquisition_id, state, **fields):
        row = self.acquisitions.setdefault(acquisition_id, {"acquisition_id": acquisition_id})
        row["status"] = state
        row.update(fields)


class CoreFulfillmentService:
    _import_acsm_impl = LibraryService._import_acsm_impl
    import_acsm = LibraryService.import_acsm

    def __init__(self, root: Path, account_id: str):
        self.root = root
        self.storage = Storage()
        self.identity = SimpleNamespace(identity_id="alexandre")
        self.current_identity = self.identity
        self.account = SimpleNamespace(
            identity_id="alexandre",
            provider_id="numilog",
            credential_id="numilog-main",
            unique_id=account_id,
            display_name="Numilog Alexandre",
            provider_label="Numilog",
        )
        self._sessions = {
            self._session_key("numilog", "numilog-main"): SimpleNamespace(
                connected=True,
                capabilities=SimpleNamespace(
                    catalog=True,
                    loans=False,
                    history=False,
                    bookings=False,
                    direct_download=True,
                    personal_library=True,
                ),
            )
        }
        self.last_request = None
        self.actions = []

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential

    def accounts_for_identity(self, identity_id=None):
        return [self.account] if identity_id in {None, "alexandre"} else []

    def identity_by_id(self, identity_id):
        return self.identity if identity_id == "alexandre" else None

    def library_identity_provider_dir(self, identity_id, provider_id):
        path = self.root / "library" / identity_id / provider_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _loan_destination_name(self, title, suffix):
        return "numilog-fulfilled" + suffix

    def resolve_adept_profile_for_identity(self, identity_id):
        assert identity_id == "alexandre"
        return "alexandre", "test"

    def _originating_adept_profile_id(self, identity_id, profile_name):
        assert (identity_id, profile_name) == ("alexandre", "alexandre")
        return "profile-alexandre"

    def execute_acquisition_request(self, request):
        self.last_request = dict(request)
        destination = Path(request["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(destination, "w") as archive:
            archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
            archive.writestr("META-INF/container.xml", "<container/>")
            archive.writestr("EPUB/content.xhtml", "<html><body>fulfilled</body></html>")
        return {
            "ok": True,
            "profile": request["profile"],
            "loan_id": "loan-numilog-7",
            "artifact": {"path": str(destination)},
        }

    def catalog_index_path(self, path, **options):
        return {"status": "indexed", "artifact_id": "artifact-numilog-7"}

    def _record_action(self, identity_id, action, outcome, **options):
        self.actions.append((identity_id, action, outcome, options))


def test_acsm_download_reaches_core_broker_and_simulated_adept_fulfillment():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cache = dataset.DatasetCache(root / "dataset", 14, 20261006, True)
        row = cache.get(7)
        assert row is not None

        credential_id = "numilog-main"
        scope = hashlib.sha256(credential_id.encode()).hexdigest()[:20]
        core = CoreFulfillmentService(root / "core", "numilog:" + scope)
        acquisition = AcquisitionBrokerImpl(core)
        identities = IdentityBrokerImpl(core)

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
                item = next(book for book in session.library() if book.title == row["title"])

                with patch("cifereca.services.data_dir", return_value=root / "core-data"):
                    produced, source = session.download_direct(item, root / "provider-downloads")

                assert produced.is_file()
                assert produced.suffix == ".epub"
                assert zipfile.is_zipfile(produced)
                assert source == session.library_url

                assert core.last_request is not None
                assert core.last_request["source"]["type"] == "acsm"
                assert Path(core.last_request["source"]["path"]).suffix == ".acsm"
                assert core.last_request["source"]["provider"] == "numilog"
                assert core.last_request["profile"] == "alexandre"
                assert core.last_request["metadata"]["identity_id"] == "alexandre"

                acquisition_row = next(iter(core.storage.acquisitions.values()))
                assert acquisition_row["status"] == "complete"
                assert acquisition_row["provider_id"] == "numilog"
                assert core.actions[-1][0:3] == ("alexandre", "import", "success")

                # Numilog's transport staging is cleaned after Core has copied it.
                assert not list((root / "provider-downloads").glob("*.acsm"))
                assert not list((root / "provider-downloads").glob("*.part"))
            finally:
                session.close()
