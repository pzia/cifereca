from __future__ import annotations

import os
import tempfile
from pathlib import Path

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import Account, Loan, SearchResult
from cifereca.providers import ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderRegistry, ProviderSession
from cifereca.services import LibraryService
from cifereca.storage import Storage


def test_core_provider_registry_starts_empty():
    registry = ProviderRegistry()
    assert registry.descriptors() == []


def test_config_migrates_legacy_username_and_saves_0600():
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "config.json"
        path.write_text('{"username":"reader@example.test"}\n', encoding="utf-8")
        cfg = Config(path)
        accounts = cfg.provider_accounts("paris")
        assert len(accounts) == 1
        assert accounts[0]["credentials"]["username"] == "reader@example.test"
        assert accounts[0]["credentials"]["password"] == ""
        cfg.upsert_provider_account({
            "id": "paris-two", "provider": "paris", "enabled": True,
            "credentials": {"username": "two", "password": "clear-local"},
        })
        cfg.save()
        assert len(cfg.provider_accounts("paris")) == 2
        if os.name == "posix":
            assert path.stat().st_mode & 0o777 == 0o600


class FakeSession(ProviderSession):
    def connect(self):
        self.connected = True
        self._accounts = [Account(
            display_name=self.label, unique_id="remote", is_primary=True,
            provider_id=self.provider_id, credential_id=self.credential_id,
            provider_label=self.descriptor.display_name,
        )]
        return self.accounts

    def close(self):
        self.connected = False

    def loans(self, account):
        return [Loan(
            id=f"{self.provider_id}:loan", provider="FAKE", medium="digital",
            record_id="r", title=f"Loan {self.provider_id}",
            source_provider=self.provider_id, source_credential=self.credential_id,
        )]

    def search(self, query, scope="all", page=0):
        return [SearchResult(
            id=f"{self.provider_id}:r", record_id="r", docbase="FAKE",
            medium="digital", title=f"{query} {self.provider_id}", author=None,
            type=None, format=None, publisher=None, published_at=None,
            description=None, thumbnail_url=None, friendly_url=None, scenario="FAKE",
            source_provider=self.provider_id, source_credential=self.credential_id,
        )]


class FakePlugin(ProviderPlugin):
    def __init__(self, provider_id: str):
        self.descriptor = ProviderDescriptor(
            id=provider_id, display_name=provider_id.upper(), description="test",
            state="ready", capabilities=ProviderCapabilities(search=True, loans=True),
        )

    def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
        return FakeSession(
            descriptor=self.descriptor, credential_id=credential_id, label=label,
            credentials=credentials, options=options,
        )


def test_library_service_routes_accounts_and_federates_ready_plugins():
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(Path(td) / "config.json")
        cfg.set("providers.accounts", [
            {"id": "a1", "provider": "alpha", "label": "A", "enabled": True, "credentials": {}, "options": {}},
            {"id": "b1", "provider": "beta", "label": "B", "enabled": True, "credentials": {}, "options": {}},
        ])
        service = LibraryService(cfg, Storage(Path(td) / "cache.sqlite3"), Diagnostics(level="detailed"))
        service.registry.register(FakePlugin("alpha"))
        service.registry.register(FakePlugin("beta"))
        try:
            result = service.connect_configured()
            assert len(result["connected"]) == 2
            assert {a.provider_id for a in service.accounts} == {"alpha", "beta"}
            assert service.last_loans[0].source_provider == "alpha"
            rows = service.search("book", force=True)
            assert {x.source_provider for x in rows} == {"alpha", "beta"}
        finally:
            service.close()


def test_loan_borrower_identity_is_not_inferred_from_current_account():
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(Path(td) / "config.json")
        service = LibraryService(cfg, Storage(Path(td) / "cache.sqlite3"), Diagnostics())
        primary = Account(
            display_name="Primary", unique_id="", is_primary=True,
            provider_id="paris", credential_id="legacy",
        )
        linked = Account(
            display_name="Linked", unique_id="linked-42", is_primary=False,
            provider_id="paris", credential_id="legacy",
        )
        service.accounts = [primary, linked]
        service.current_account = primary
        loan = Loan(
            id="loan-linked", provider="DILICOM", medium="digital", record_id="42",
            title="Linked loan", source_provider="paris", source_credential="legacy",
            source_account_id="linked-42",
        )
        try:
            assert service._account_for_loan(loan) is linked
        finally:
            service.close()


def test_digital_acquisition_carries_borrower_acsm_and_drm():
    class DigitalSession:
        provider_id = "paris"

        def download_digital(self, loan, account, target_dir):
            assert account.unique_id == "linked-42"
            path = target_dir / "book.acsm"
            path.write_text("<fulfillmentToken/>", encoding="utf-8")
            return path, "https://example.test/book.acsm"

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        cfg.set("downloads.directory", str(root / "downloads"))
        service = LibraryService(cfg, Storage(root / "cache.sqlite3"), Diagnostics())
        primary = Account(
            display_name="Primary", unique_id="", is_primary=True,
            provider_id="paris", credential_id="legacy",
        )
        linked = Account(
            display_name="Linked", unique_id="linked-42", is_primary=False,
            provider_id="paris", credential_id="legacy",
        )
        service.accounts = [primary, linked]
        service.current_account = primary
        loan = Loan(
            id="loan-linked", provider="DILICOM", medium="digital", record_id="42",
            title="Linked loan", due_at="2026-10-01T00:00:00+00:00",
            source_provider="paris", source_credential="legacy",
            source_account_id="linked-42",
        )
        original = service._session_for_source
        service._session_for_source = lambda provider_id, credential_id=None: DigitalSession()  # type: ignore[method-assign]
        try:
            acquisition = service.acquire_digital_loan(loan)
            assert acquisition.account_id == "linked-42"
            assert acquisition.kind == "acsm"
            assert acquisition.drm == "adept"
            assert acquisition.acsm_path and acquisition.acsm_path.name == "book.acsm"
            assert acquisition.artifact_path is None
            assert acquisition.expires_at == loan.due_at
            assert acquisition.mime_type == "application/vnd.adobe.adept+xml"
        finally:
            service._session_for_source = original  # type: ignore[method-assign]
            service.close()


def test_search_query_variants_make_author_title_queries_tolerant():
    variants = LibraryService._search_query_variants("Zola Germinal")
    assert variants[0] == "Zola Germinal"
    assert "Germinal" in variants
    assert "Zola" in variants

    variants = LibraryService._search_query_variants("saint exupery petit prince")
    assert variants[0] == "saint exupery petit prince"
    assert "saint exupery" in variants
    assert "petit prince" in variants


def test_federated_search_falls_back_and_ranks_title_author_match_first():
    class FlexibleSession(FakeSession):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.queries: list[str] = []

        def search(self, query, scope="all", page=0):
            self.queries.append(query)
            if query == "Zola Germinal":
                return []
            if query == "Germinal":
                return [SearchResult(
                    id="flex:germinal", record_id="germinal", docbase="FAKE",
                    medium="digital", title="Germinal", author="Émile Zola",
                    type="Livre numérique", format="EPUB", publisher=None,
                    published_at=None, description=None, thumbnail_url=None,
                    friendly_url=None, scenario="FAKE", source_provider=self.provider_id,
                    source_credential=self.credential_id,
                )]
            if query == "Zola":
                return [SearchResult(
                    id="flex:other", record_id="other", docbase="FAKE",
                    medium="digital", title="Thérèse Raquin", author="Émile Zola",
                    type="Livre numérique", format="EPUB", publisher=None,
                    published_at=None, description=None, thumbnail_url=None,
                    friendly_url=None, scenario="FAKE", source_provider=self.provider_id,
                    source_credential=self.credential_id,
                )]
            return []

    class FlexiblePlugin(ProviderPlugin):
        def __init__(self):
            self.descriptor = ProviderDescriptor(
                id="flex", display_name="Flexible", description="test",
                state="ready", capabilities=ProviderCapabilities(search=True),
            )
            self.session: FlexibleSession | None = None

        def create_session(self, *, credential_id, label, credentials, options, config, storage, diagnostics):
            self.session = FlexibleSession(
                descriptor=self.descriptor, credential_id=credential_id, label=label,
                credentials=credentials, options=options,
            )
            return self.session

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = Config(root / "config.json")
        cfg.set("providers.accounts", [
            {"id": "flex-1", "provider": "flex", "label": "F", "enabled": True, "credentials": {}, "options": {}},
        ])
        service = LibraryService(cfg, Storage(root / "cache.sqlite3"), Diagnostics(level="detailed"))
        plugin = FlexiblePlugin()
        service.registry.register(plugin)
        try:
            service.connect_configured()
            rows = service.search("Zola Germinal", force=True)
            assert plugin.session is not None
            assert plugin.session.queries[:2] == ["Zola Germinal", "Germinal"]
            assert [row.title for row in rows][:2] == ["Germinal", "Thérèse Raquin"]
        finally:
            service.close()


def test_paris_digital_available_is_labeled_as_borrowable_not_downloadable():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        service = LibraryService(
            Config(root / "config.json"), Storage(root / "cache.sqlite3"), Diagnostics()
        )
        item = SearchResult(
            id="paris:r", record_id="r", docbase="DILICOM", medium="digital",
            title="Germinal", author="Émile Zola", type="Livre numérique", format="EPUB",
            publisher=None, published_at=None, description=None, thumbnail_url=None,
            friendly_url=None, scenario="EBOOKS", source_provider="paris",
            source_credential="legacy",
        )
        from cifereca.models import ItemDetail
        detail = ItemDetail(
            record_id="r", docbase="DILICOM", title="Germinal", author="Émile Zola",
            type="Livre numérique", year="1885", thumbnail_url=None,
            availability_status="available", availability_label="Disponible", holdings=[],
            source_provider="paris", source_credential="legacy",
        )
        try:
            assert service.provider_display_name("paris") == "paris"
            assert service.availability_display_label(item, detail) == "Disponible à l’emprunt"
        finally:
            service.close()


def test_primary_paris_loan_uses_stable_provenance_but_resolves_real_primary_account():
    with tempfile.TemporaryDirectory() as td:
        cfg = Config(Path(td) / "config.json")
        service = LibraryService(cfg, Storage(Path(td) / "cache.sqlite3"), Diagnostics())
        primary = Account(
            display_name="Compte principal", unique_id="", is_primary=True,
            provider_id="paris", credential_id="paris-default",
        )
        service.accounts = [primary]
        loan = Loan(
            id="loan-primary", provider="DILICOM", medium="digital", record_id="9780000000000",
            title="Primary loan", source_provider="paris", source_credential="paris-default",
            source_account_id="primary",
        )
        try:
            assert service._account_for_loan(loan) is primary
            assert service._account_for_loan(loan).unique_id == ""
        finally:
            service.close()


def test_primary_paris_acquisition_keeps_primary_account_key(monkeypatch):
    import tempfile
    from pathlib import Path
    from cifereca.config import Config
    from cifereca.storage import Storage
    from cifereca.diagnostics import Diagnostics
    from cifereca.services import LibraryService
    from cifereca.models import Account, Loan

    with tempfile.TemporaryDirectory() as td:
        cfg = Config(Path(td) / "config.json")
        cfg.set("library.directory", str(Path(td) / "EBooks"))
        service = LibraryService(cfg, Storage(Path(td) / "cache.sqlite3"), Diagnostics())
        primary = Account(display_name="Compte principal", unique_id="", is_primary=True,
                          provider_id="paris", credential_id="paris-default")
        service.accounts = [primary]
        loan = Loan(id="loan-primary-acq", provider="DILICOM", medium="digital",
                    record_id="9780000000001", title="Primary loan",
                    source_provider="paris", source_credential="paris-default",
                    source_account_id="primary")

        class Session:
            provider_id = "paris"
            def download_digital(self, loan_arg, account_arg, target_dir):
                assert account_arg is primary
                assert account_arg.unique_id == ""
                target_dir.mkdir(parents=True, exist_ok=True)
                path = target_dir / "loan.acsm"
                path.write_text("<fulfillmentToken/>", encoding="utf-8")
                return path, "https://example.test/loan.acsm"

        monkeypatch.setattr(service, "_session_for_source", lambda *_: Session())
        monkeypatch.setattr(service, "identity_for_connection", lambda *_: type("I", (), {"identity_id":"default"})())
        monkeypatch.setattr(service, "library_identity_provider_dir", lambda *_: Path(td) / "EBooks" / "hugues" / "paris")
        monkeypatch.setattr(service.acquisition, "run_post_download", lambda *a, **k: None)
        try:
            acq = service.acquire_digital_loan(loan)
            assert acq.account_id == "primary"
        finally:
            service.close()
