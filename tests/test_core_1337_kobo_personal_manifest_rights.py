"""#3196: synthetic Kobo origin / private inventory fixtures without supplier rights.

All data is synthetic. SQLite is real; no provider request, ADEPT, download,
filesystem scan or acquisition is permitted by inventory observation.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import tempfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _service(root: Path) -> LibraryService:
    config = Config(root / "config.json")
    config.set("library.directory", str(root / "EBooks"))
    config.set("identities", [
        {"id": "alice", "label": "Alice", "directory_name": "alice"},
        {"id": "bob", "label": "Bob", "directory_name": "bob"},
    ])
    config.set("providers.accounts", [])
    config.save()
    return LibraryService(config, Storage(root / "catalog.sqlite3"), Diagnostics())


def _row(*, product="owned-1", credential="vault-alice", owned=True, archived=False):
    return SearchResult(
        id=f"kobo:{credential}:{product}", record_id=product, docbase="KOBO",
        medium="digital", title="Livre acheté", author="Auteur",
        type="Livre numérique Kobo", format="EPUB", publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=None, scenario="KOBO", source_provider="kobo",
        source_credential=credential, source_collection="library",
        owned=owned, downloadable=False,
        metadata={"archived": archived},
    )



def test_catalogue_observation_never_persists_supplier_entitlement(tmp_path):
    service = _service(tmp_path)
    try:
        item = _row()
        assert service.storage.entitlements(provider_id="kobo") == []
        service._mirror_remote_results([item], identity_id="alice")
        assert len(service.storage.catalog_entries()) == 1
        assert service.storage.entitlements(provider_id="kobo") == []
        assert service.storage.placements() == []
        assert service.library_entries(identity_id="bob") == []
    finally:
        service.close()
