from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, SearchResult
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage
from cifereca.providers import ProviderConfigurationError


def _write_epub(path: Path, marker: bytes = b"book") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        # Independent copies of this synthetic edition must be byte-identical
        # even when tests run across ZIP's two-second DOS clock boundary.
        # Otherwise a cross-profile SHA equality test fails at random.
        for name, payload in (
            ("mimetype", "application/epub+zip"),
            ("META-INF/container.xml", "<container/>"),
            ("content.xhtml", marker),
        ):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            archive.writestr(info, payload)


class KoboSession(ProviderSession):
    api_version = "2.3"
    supports_acquisition_display_profile = False
    supports_acquisition_acsm = False

    def __init__(self, *args, manifest=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._manifest = list(manifest or [])

    def connect(self):
        self.connected = True
        self._accounts = [
            Account(
                display_name="Kobo",
                unique_id="account",
                provider_id="kobo",
                credential_id=self.credential_id,
                identity_id="alice",
            )
        ]
        return self.accounts

    def close(self):
        self.connected = False

    def backup_manifest(self):
        return list(self._manifest)

    def cover_url(self, _raw):
        return None

    def normalize_item(self, raw, default_collection="library"):
        return SearchResult(
            id=f"kobo:{self.credential_id}:{raw['product_id']}",
            record_id=str(raw["product_id"]),
            docbase="KOBO",
            medium="digital",
            title=str(raw.get("title") or raw["product_id"]),
            author="Author",
            type="Livre numérique Kobo",
            format=str(raw.get("format") or "EPUB"),
            publisher=None,
            published_at=None,
            description=None,
            thumbnail_url=None,
            friendly_url=None,
            scenario="KOBO",
            source_provider="kobo",
            source_credential=self.credential_id,
            content_type="ebook",
            source_collection=default_collection,
            owned=True,
            downloadable=True,
            revision_id=str(raw.get("revision_id") or "") or None,
            metadata={},
        )


def _manifest_row():
    return {
        "product_id": "book-1",
        "revision_id": "rev-1",
        "title": "Book One",
        "authors": ["Author"],
        "format": "EPUB",
        "content_type": "ebook",
        "owned": True,
        "downloadable": True,
    }


def _ready_state():
    return {
        "available": True,
        "compatible": True,
        "operational": True,
        "download_available": True,
        "acsm_available": False,
        "kobo_delegated_available": False,
        "kobo_pinned_delivery_candidate": False,
        "kobo_separate_content_keys_replay": False,
        "kobo_kdrm_available": False,
        "kobo_formats": ["epub", "pdf"],
        "contract_version": 8,
        "strict_json_stdout": True,
        "software_version": "test",
        "error": None,
    }


def _service(tmp_path: Path):
    config = Config(tmp_path / "config.json")
    config.set("library.directory", str(tmp_path / "EBooks"))
    config.set(
        "identities",
        [
            {"id": "alice", "label": "Alice", "directory_name": "alice"},
            {"id": "bob", "label": "Bob", "directory_name": "bob"},
        ],
    )
    config.set("providers.accounts", [])

    service = LibraryService(
        config,
        Storage(tmp_path / "db.sqlite3"),
        Diagnostics(level="detailed"),
    )
    connection_id = service.storage.provider_connection_put({
        "identity_id": "alice",
        "provider_id": "kobo",
        "credential_id": "vault-1",
        "label": "Kobo",
    })
    service.storage.provider_account_put({
        "connection_id": connection_id, "identity_id": "alice",
        "provider_id": "kobo", "credential_id": "vault-1",
        "account_id": "account",
    })

    descriptor = ProviderDescriptor(
        id="kobo",
        display_name="Kobo",
        description="test",
        state="ready",
        capabilities=ProviderCapabilities(search=True, backup=True, personal_library=True),
    )
    session = KoboSession(
        descriptor=descriptor,
        credential_id="vault-1",
        label="Kobo",
        credentials={},
        options={},
        manifest=[_manifest_row()],
    )
    session.connect()
    service._sessions[service._session_key("kobo", "vault-1")] = session
    service.kobo_adept_state = lambda **kwargs: _ready_state()
    service.runtime_diagnostic = lambda _plugin_id: {}
    return service


def _add_local_catalog(service: LibraryService, path: Path, *, identity_id: str, item_id: str) -> None:
    service.storage.catalog_entry_put({
        "catalog_entry_id": f"local:{identity_id}:{item_id}",
        "title": item_id,
        "identity_id": identity_id,
        "provider_id": "local",
        "item_id": item_id,
        "local_state": "local",
        "media_format": "epub",
        "metadata": {"path": str(path)},
    })


@pytest.mark.parametrize("local", [
    "none", "unlinked", "catalog_other_identity", "outside_root",
    "symlink", "dangling_symlink", "invalid_epub", "two_candidates",
    "matching_title_only", "different_revision",
])
@pytest.mark.parametrize("status", ["pending", "acquiring", "complete"])
def test_legacy_w2_never_downloads_or_mutates_hostile_local_candidates(
    tmp_path: Path, monkeypatch, local, status,
):
    """Retired W2 cannot inspect/relabel local candidates as a delivery.

    The active canonical A→B file admission is covered by the independent
    atomic-placement and crash/recovery suites, not the 0.7 executor.
    """
    service = _service(tmp_path)
    try:
        root = service.library_identity_provider_dir("alice", "kobo")
        target = root / "Book One.epub"
        foreign = service.library_identity_provider_dir("bob", "kobo") / "Book One.epub"
        if local in {"unlinked", "two_candidates", "matching_title_only",
                     "different_revision"}:
            _write_epub(target)
        elif local == "catalog_other_identity":
            _write_epub(foreign)
            _add_local_catalog(
                service, foreign, identity_id="bob", item_id="book-1",
            )
        elif local == "outside_root":
            _write_epub(tmp_path / "outside.epub")
        elif local == "invalid_epub":
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"not-an-epub")
        elif local in {"symlink", "dangling_symlink"}:
            root.mkdir(parents=True, exist_ok=True)
            if local == "symlink":
                _write_epub(tmp_path / "foreign.epub")
            target.symlink_to(
                tmp_path / ("foreign.epub" if local == "symlink"
                            else "missing.epub")
            )
        if local == "two_candidates":
            _write_epub(root / "Author - Book One.epub")
        if local == "matching_title_only":
            _add_local_catalog(service, target, identity_id="alice",
                               item_id="unrelated-book")

        legacy_path = (
            str(tmp_path / "outside.epub") if local == "outside_root"
            else str(target) if local not in {"none", "catalog_other_identity"}
            else None
        )
        service.storage.backup_put({
            "provider_id": "kobo", "credential_id": "vault-1",
            "identity_id": "alice", "account_id": "account",
            "item_id": "book-1", "revision_id":
                "rev-old" if local == "different_revision" else "rev-1",
            "title": "Book One", "format": "EPUB", "status": status,
            "local_path": legacy_path,
        })
        before = service.storage.backup_get("kobo", "vault-1", "book-1")
        observed = {
            path: path.read_bytes() for path in (
                target, foreign, root / "Author - Book One.epub",
                tmp_path / "foreign.epub",
            ) if path.is_file()
        }
        session = service._sessions[service._session_key("kobo", "vault-1")]
        placements_before = service.storage.placements()
        monkeypatch.setattr(
            session, "backup_manifest",
            lambda: pytest.fail("retired W2 must not fetch manifest"),
        )
        monkeypatch.setattr(
            service, "_execute_kobo_acquisition",
            lambda *_a, **_kw: pytest.fail("retired W2 must not GET"),
        )
        monkeypatch.setattr(
            service.storage, "backup_put",
            lambda *_a, **_kw: pytest.fail("retired W2 must not write"),
        )
        for _ in range(2):
            with pytest.raises(
                ProviderConfigurationError,
                match="kobo_legacy_w2_retired_use_canonical_provider",
            ):
                service.sync_kobo_backup()
        assert service.storage.backup_get("kobo", "vault-1", "book-1") == before
        assert service.storage.placements() == placements_before
        assert all(path.read_bytes() == content
                   for path, content in observed.items())
        if local == "dangling_symlink":
            assert target.is_symlink() and not target.exists()
    finally:
        service.close()


@pytest.mark.parametrize("existing", [False, True])
def test_retired_direct_executor_cannot_overwrite_or_acquire(
    tmp_path: Path, existing,
):
    """D3 direct executor cannot bypass acquisition lock or destination guard."""
    from cifereca.core.kobo_backup import _execute_kobo_acquisition
    service = _service(tmp_path)
    try:
        target = service.library_identity_provider_dir("alice", "kobo") / "Book One.epub"
        if existing:
            _write_epub(target)
        before = target.read_bytes() if existing else None
        with pytest.raises(
            ProviderConfigurationError,
            match="kobo_legacy_direct_get_retired",
        ):
            _execute_kobo_acquisition(service, SimpleNamespace(), target.parent)
        if existing:
            assert target.read_bytes() == before
        else:
            assert not target.exists()
    finally:
        service.close()

def test_synthetic_kobo_epub_has_stable_bytes_across_zip_clock(tmp_path, monkeypatch):
    """The two independent lender/receiver witnesses share an exact SHA."""
    first = tmp_path / "first.epub"
    second = tmp_path / "second.epub"
    monkeypatch.setattr(
        zipfile.time, "localtime", lambda *_: (2020, 1, 1, 0, 0, 0, 0, 1, -1),
    )
    _write_epub(first, b"synthetic Kobo A book")
    monkeypatch.setattr(
        zipfile.time, "localtime", lambda *_: (2026, 11, 10, 23, 59, 58, 1, 315, -1),
    )
    _write_epub(second, b"synthetic Kobo A book")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.read("content.xhtml") == b"synthetic Kobo A book"
        assert all(member.date_time == (1980, 1, 1, 0, 0, 0)
                   for member in archive.infolist())


def test_synthetic_kobo_epub_has_stable_bytes_across_zip_clock(tmp_path, monkeypatch):
    """The two independent lender/receiver witnesses share an exact SHA."""
    first = tmp_path / "first.epub"
    second = tmp_path / "second.epub"
    monkeypatch.setattr(
        zipfile.time, "localtime", lambda *_: (2020, 1, 1, 0, 0, 0, 0, 1, -1),
    )
    _write_epub(first, b"synthetic Kobo A book")
    monkeypatch.setattr(
        zipfile.time, "localtime", lambda *_: (2026, 11, 10, 23, 59, 58, 1, 315, -1),
    )
    _write_epub(second, b"synthetic Kobo A book")
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.read("content.xhtml") == b"synthetic Kobo A book"
        assert all(member.date_time == (1980, 1, 1, 0, 0, 0)
                   for member in archive.infolist())
