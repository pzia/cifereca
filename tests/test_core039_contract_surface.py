from dataclasses import dataclass
from pathlib import Path

import pytest

from cifereca.brokers import build_public_brokers
from cifereca.gui.shell_model import contextual_actions, page_actions
from cifereca.plugin_api import (
    AccountRef, ArtifactFormat, ArtifactRef, BookRef, MaintenanceContext,
    MaintenancePlan, MaintenanceResult, MaintenanceToolInfo,
    SyncRequest, UiActionContext, UiActionContribution, UiPageContribution,
    normalize_language_tag,
)
from cifereca.plugins.categories import CORE_CATEGORIES


@dataclass
class Account:
    identity_id: str
    provider_id: str
    credential_id: str
    unique_id: str
    display_name: str = ""
    provider_label: str = ""


class Caps:
    loans = True
    personal_library = False
    catalog = True
    history = False
    bookings = False
    direct_download = True


class Session:
    connected = True
    capabilities = Caps()


class Storage:
    def __init__(self): self.values = {}
    def metadata_get(self, key, default=None): return self.values.get(key, default)
    def metadata_set(self, key, value):
        import json
        self.values[key] = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
    def artifacts(self): return []


class Diagnostics:
    def snapshot(self): return [{"status": "ok", "detail": {"token": "<redacted>"}}]
    def text(self, version, extra=None): return f"cifereca v{version}\n"


class Config:
    def get(self, key, default=None): return default


class Acquisition:
    @staticmethod
    def _safe_filename(value): return value.replace('/', '-').strip()


class FakeService:
    def __init__(self):
        self.accounts = [
            Account("alice", "paris", "card-1", "a1", "Carte 1"),
            Account("alice", "paris", "card-2", "a2", "Carte 2"),
        ]
        self._sessions = {self._session_key(a.provider_id, a.credential_id): Session() for a in self.accounts}
        self.storage = Storage(); self.diagnostics = Diagnostics(); self.plugin_manager = None
        self.config = Config(); self.acquisition = Acquisition()
        self.calls = []
    @staticmethod
    def _session_key(provider, credential): return provider + ':' + credential
    def accounts_for_identity(self, identity_id=None):
        return [a for a in self.accounts if identity_id is None or a.identity_id == identity_id]
    def select_identity(self, identity_id): return identity_id
    def can_direct_download(self, item): return True
    def acquire_direct_item(self, item):
        self.calls.append(item.source_credential); return {"credential": item.source_credential}
    def import_acsm(self, path, **options): return {"path": str(path)}
    def synchronize_account_source(self, account, *, deep=False, control=None):
        self.calls.append((account.unique_id, deep)); return {"loans": 2}
    def preview_library_filename(self, **kwargs): return "Auteur - Titre.epub"


class Item:
    source_provider = "paris"
    source_credential = ""


def test_public_api_category_and_language_contract():
    assert "maintenance.tool" in {row.id for row in CORE_CATEGORIES}
    assert normalize_language_tag("fre") == "fr"


def test_sync_sources_are_account_aware_and_persistent():
    service = FakeService(); sync = build_public_brokers(service)["sync"]
    rows = sync.sources("alice")
    assert [r.account_id for r in rows] == ["a1", "a2"]
    assert all(r.enabled for r in rows)
    sync.set_enabled(rows[0].source_id, False)
    assert sync.sources("alice")[0].enabled is False
    skipped = sync.synchronize_source(rows[0].source_id)
    assert skipped.ok and skipped.facts["skipped"] is True
    result = sync.synchronize(SyncRequest(identity_id="alice", deep=True))
    assert result.changed == 2
    assert service.calls == [("a2", True)]


def test_acquisition_requires_explicit_account_when_ambiguous():
    service = FakeService(); broker = build_public_brokers(service)["acquisition"]
    item = Item()
    refs = broker.eligible_accounts(item, identity_id="alice")
    assert tuple(r.account_id for r in refs) == ("a1", "a2")
    assert all(isinstance(r, AccountRef) for r in refs)
    with pytest.raises(ValueError, match="account selection required"):
        broker.acquire(item, identity_id="alice")
    result = broker.acquire(item, identity_id="alice", account_id="a2")
    assert result["credential"] == "card-2"


def test_diagnostics_and_naming_are_public_brokers(tmp_path, monkeypatch):
    journal = tmp_path / "cifereca.log"
    journal.write_text(
        '2026-10-25 13:00:00,000 INFO cifereca DIAG_JSON '
        '{"schema_version":1,"category":"TEST","operation":"public_broker",'
        '"status":"ok","message":"ready"}\n'
    )
    monkeypatch.setattr("cifereca.paths.log_file", lambda: journal)
    service = FakeService(); brokers = build_public_brokers(service)
    snapshot = brokers["diagnostics"].snapshot()
    assert len(snapshot) == 1 and snapshot[0]["status"] == "ok"
    assert snapshot[0]["source"] == "journal"
    assert brokers["library.naming"].preview(title="Titre", author="Auteur") == "Auteur - Titre.epub"


def test_book_detail_contextual_actions_are_not_page_actions():
    seen = []
    action = UiActionContribution(
        "transform", "Transformer…", lambda host: seen.append(host.action_context().artifact.artifact_id),
        target="book.detail", visible_when=lambda ctx: ctx.artifact is not None,
    )
    page = UiPageContribution("optimizer", "Optimizer", factory=lambda host: object(), actions=(action,))
    artifact = ArtifactRef("art-1", "book-1", ArtifactFormat("application/epub+zip", "epub"), 10, "abc", filename="x.epub")
    context = UiActionContext("book.detail", book=BookRef("book-1", "Titre"), artifact=artifact)
    rows = contextual_actions((page,), "book.detail", context)
    assert len(rows) == 1 and rows[0][2] is True
    assert all(a.id != "transform" for a in page_actions(page))


def test_maintenance_contract_models_are_immutable_and_structured():
    info = MaintenanceToolInfo("hash.verify", "Vérifier les SHA-256", danger_level="read-only")
    ctx = MaintenanceContext(scope="library", identity_id="alice")
    plan = MaintenancePlan(info.id, "2 artefacts à vérifier", ("A", "B"))
    result = MaintenanceResult(True, changed=2, summary="Vérification terminée")
    assert ctx.identity_id == "alice" and plan.tool_id == info.id and result.changed == 2
    with pytest.raises(Exception):
        info.title = "x"
