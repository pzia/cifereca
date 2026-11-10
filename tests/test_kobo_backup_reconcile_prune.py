from __future__ import annotations

import hashlib

import pytest
from pathlib import Path
import zipfile

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.kobo.vault import KoboVaultError
from cifereca.models import Account
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


def _write_epub(path: Path, marker: bytes = b"book") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", "<container/>")
        archive.writestr("content.xhtml", marker)


class BackupSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, manifest=None, manifest_error=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest or [])
        self._manifest_error = manifest_error

    def connect(self):
        self.connected = True
        self._accounts = [
            Account(
                display_name="Kobo",
                unique_id="account",
                provider_id="kobo",
                credential_id=self.credential_id,
                identity_id="default",
            )
        ]
        return self.accounts

    def close(self):
        self.connected = False

    def backup_manifest(self):
        if self._manifest_error is not None:
            raise self._manifest_error
        return list(self._manifest)

    def cover_url(self, _raw):
        return None


def _service(tmp_path: Path, *, manifest=None, manifest_error=None) -> LibraryService:
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [{
            "id": "default",
            "label": "Hugues",
            "directory_name": "default",
            "preferences": {"reading_languages": ["fr"]},
        }],
    )
    config.set("providers.accounts", [])

    service = LibraryService(config, Storage(tmp_path / "db.sqlite3"), Diagnostics(level="detailed"))
    service.storage.provider_connection_put({
        "identity_id": "default",
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "label": "Kobo",
    })

    descriptor = ProviderDescriptor(
        id="kobo",
        display_name="Kobo",
        description="test",
        state="ready",
        capabilities=ProviderCapabilities(search=True, backup=True, personal_library=True),
    )
    session = BackupSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=manifest,
        manifest_error=manifest_error,
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.kobo_unlinked_local_count = lambda identity_id: 0
    service.kobo_adept_state = lambda **kwargs: {
        "available": True,
        "compatible": True,
        "operational": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "software_version": "test",
        "error": None,
    }
    return service


def _manifest_row(item_id="book-1", title="Book One", revision_id="rev-1"):
    return {
        "product_id": item_id,
        "revision_id": revision_id,
        "title": title,
        "authors": ["Author"],
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": True,
    }


@pytest.mark.parametrize("status", [
    "acquiring", "error", "complete", "pending_acquisition",
    "attention_required", "reconcile_conflict", "reconcile_required",
])
@pytest.mark.parametrize("candidate", ["absent", "intact", "outside", "symlink"])
def test_retired_w2_does_not_reconcile_or_prune_legacy_receipts(
    tmp_path: Path, monkeypatch, status, candidate,
):
    from cifereca.providers import ProviderConfigurationError
    service = _service(tmp_path, manifest=[_manifest_row()])
    try:
        target = service.library_identity_provider_dir("default", "kobo") / "Book One.epub"
        outside = tmp_path / "outside" / "foreign.epub"
        if candidate == "intact":
            _write_epub(target, b"existing")
        elif candidate in {"outside", "symlink"}:
            _write_epub(outside, b"foreign identity bytes")
            if candidate == "symlink":
                target.symlink_to(outside)
        location = (str(outside) if candidate == "outside" else
                    str(target) if candidate in {"intact", "symlink"} else None)
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "default", "account_id": "account",
            "item_id": "book-1", "revision_id": "rev-1",
            "title": "Book One", "format": "EPUB", "status": status,
            "local_path": location,
        })
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        previous = service.storage.placements()
        prior_bytes = outside.read_bytes() if outside.exists() else None
        session = service._sessions[service._session_key("kobo", "vault-1")]
        def forbidden(*_a, **_kw):
            pytest.fail("retired W2 cannot read provider or mutate legacy")
        monkeypatch.setattr(session, "backup_manifest", forbidden)
        monkeypatch.setattr(service, "_execute_kobo_acquisition", forbidden)
        monkeypatch.setattr(service.storage, "backup_put", forbidden)
        monkeypatch.setattr(service.storage, "backup_prune_missing", forbidden)
        for _ in range(2):
            with pytest.raises(ProviderConfigurationError,
                               match="kobo_legacy_w2_retired_use_canonical_provider"):
                service.sync_kobo_backup()
            assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
        assert service.storage.placements() == previous
        if prior_bytes is not None:
            assert outside.read_bytes() == prior_bytes
        if candidate == "symlink":
            assert target.is_symlink() and target.resolve() == outside
    finally:
        service.close()
