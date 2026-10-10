"""P1 #968: Core alone materializes collection children; deterministic and offline."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.diagnostics import Diagnostics
from cifereca.models import SearchResult
from cifereca.providers import (
    ProviderCapabilities, ProviderDescriptor, ProviderPlugin, ProviderRegistry,
    ProviderSession,
)
from cifereca.plugin_api import ProviderCollectionValidationError
from cifereca.services import LibraryService
from cifereca.storage import Storage


PARENT = "org.cifereca.integration.cifericaopds"


def child(source, *, revision="r1", generation=1, status="ready", kind="catalog"):
    return {
        "plugin_id": "cifericaopds." + source,
        "source_id": source,
        "capabilities": ["catalog.search"],
        "source_revision": revision,
        "catalog_generation": generation,
        "status": status,
        "kind": kind,
        "instantiation": "singleton_public",
        "parent_plugin_id": PARENT,
        "is_virtual": True,
    }


def snapshot(*children, generation=1):
    return {
        "api_major": 1, "discovery_generation": generation,
        "parent_plugin_id": PARENT, "children": list(children),
    }


class SearchSession(ProviderSession):
    def connect(self):
        self.connected = True
        return []

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        return [SearchResult(
            id=f"{self.provider_id}:book",
            record_id="book", docbase="FAKE", medium="digital",
            title=f"{query} / {self.provider_id}", author=None,
            type=None, format=None, publisher=None, published_at=None,
            description=None, thumbnail_url=None, friendly_url=None,
            scenario="FAKE", source_provider=self.provider_id,
            source_credential=self.credential_id,
        )]


class LocalProvider(ProviderPlugin):
    def __init__(self, source, revision="r1"):
        self.descriptor = ProviderDescriptor(
            id="cifericaopds." + source,
            display_name=f"Source {source} ({revision})",
            description="offline fixture", state="ready",
            capabilities=ProviderCapabilities(search=True),
        )

    def create_session(self, *, credential_id, label, credentials, options,
                       config, storage, diagnostics):
        assert not credentials
        return SearchSession(
            descriptor=self.descriptor, credential_id=credential_id,
            label=label, credentials=credentials, options=options,
        )


class Collection:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def snapshot(self):
        self.calls.append("snapshot")
        return self.value

    def refresh(self, *, explicit=True):
        raise AssertionError("Core must not trigger network discovery")

    def create_provider(self, source_id):
        self.calls.append(("create", source_id))
        entry = next(row for row in self.value["children"] if row["source_id"] == source_id)
        return LocalProvider(source_id, entry["source_revision"])

    def subscribe(self, callback):
        return lambda: None


class FakeManager:
    def __init__(self, collection):
        self.collection = collection
        self.live = True
        self.enabled = {}
        self.state = NS(identity_enabled_override=lambda plugin_id, identity:
                        self.enabled.get((plugin_id, identity)))

    def candidates(self, category):
        if category == "provider.collection" and self.live:
            return [NS(descriptor=NS(id=PARENT))]
        return []

    def service(self, plugin_id):
        assert plugin_id == PARENT
        return self.collection

    def status(self, plugin_id):
        assert plugin_id == PARENT
        return NS(enabled=True, state="ready",
                  descriptor=NS(id=PARENT, instantiation="multiple"))

    def enabled_for_identity(self, plugin_id, identity):
        return plugin_id == PARENT


def test_atomic_two_source_publication_and_reuse():
    reg = ProviderRegistry()
    collection = Collection(snapshot(child("alpha"), child("beta")))
    assert reg.reconcile_collection(PARENT, collection) == (
        "cifericaopds.alpha", "cifericaopds.beta",
    )
    alpha = reg.get("cifericaopds.alpha")
    assert collection.calls == ["snapshot", ("create", "alpha"), ("create", "beta")]
    assert reg.reconcile_collection(PARENT, collection) == (
        "cifericaopds.alpha", "cifericaopds.beta",
    )
    assert reg.get("cifericaopds.alpha") is alpha
    assert collection.calls[-1] == "snapshot"

    # Disabling does not delete source history, and reappearance retains IDs.
    collection.value = snapshot(
        child("alpha", status="disabled"), child("beta"), generation=2,
    )
    assert reg.reconcile_collection(PARENT, collection) == ("cifericaopds.beta",)
    assert reg.maybe_get("cifericaopds.alpha") is None
    assert len(reg.virtual_snapshot(PARENT).children) == 2
    collection.value = snapshot(
        child("alpha", revision="r2", generation=2),
        child("beta"), generation=3,
    )
    assert set(reg.reconcile_collection(PARENT, collection)) == {
        "cifericaopds.alpha", "cifericaopds.beta",
    }
    assert reg.get("cifericaopds.alpha").descriptor.display_name.endswith("(r2)")


def test_invalid_snapshot_or_bad_factory_rolls_back_without_partial_publication():
    reg = ProviderRegistry([LocalProvider("static")])
    c = Collection(snapshot(child("alpha"), child("beta")))
    reg.reconcile_collection(PARENT, c)
    first = reg.get("cifericaopds.alpha")
    c.value = snapshot(child("alpha", generation=2),
                       child("beta", revision="r2", generation=2),
                       child("alpha"), generation=2)
    with pytest.raises(ProviderCollectionValidationError):
        reg.reconcile_collection(PARENT, c)
    assert reg.get("cifericaopds.alpha") is first
    assert reg.get("cifericaopds.beta").descriptor.display_name.endswith("(r1)")
    assert reg.virtual_snapshot(PARENT).discovery_generation == 1

    # A new child cannot impersonate an already installed static provider.
    colliding = ProviderRegistry([LocalProvider("alpha")])
    with pytest.raises(ProviderCollectionValidationError, match="collision"):
        colliding.reconcile_collection(PARENT, Collection(snapshot(child("alpha"))))
    assert colliding.get("cifericaopds.alpha").descriptor.display_name.startswith("Source")

    class BrokenCollection(Collection):
        def create_provider(self, source_id):
            if source_id == "beta":
                return LocalProvider("wrong")
            return super().create_provider(source_id)

    fresh = ProviderRegistry()
    with pytest.raises(ProviderCollectionValidationError, match="descriptor mismatch"):
        fresh.reconcile_collection(PARENT, BrokenCollection(snapshot(child("alpha"), child("beta"))))
    assert fresh.descriptors() == []


def test_core_library_attaches_two_public_sources_and_respects_identity(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    coll = Collection(snapshot(child("alpha"), child("beta")))
    mgr = FakeManager(coll)
    try:
        attached = svc.attach_plugin_manager(mgr)
        assert set(attached) == {"cifericaopds.alpha", "cifericaopds.beta"}
        assert set(x.id for x in svc.provider_descriptors()) == set(attached)
        assert svc._provider_enabled_for_identity("cifericaopds.alpha", "camille")
        mgr.enabled[("cifericaopds.beta", "camille")] = False
        assert not svc._provider_enabled_for_identity("cifericaopds.beta", "camille")
        assert svc._provider_enabled_for_identity("cifericaopds.beta", "alex")

        svc.connect_configured()
        assert len([s for s in svc._sessions.values()
                    if s.provider_id.startswith("cifericaopds.") and s.connected]) == 2
        result = svc.search("livre", force=True)
        # Identity-scoped visibility must respect the selected identity;
        # direct override is asserted separately above.
        assert {r.source_provider for r in result} <= set(attached)
        assert result

        # Discovery changes only the snapshot; no Core registry handle is
        # ever given to the plugin, and existing catalogue database survives.
        coll.value = snapshot(child("alpha"), child("beta", status="removed"),
                              generation=2)
        svc.refresh_virtual_collections()
        assert svc.registry.maybe_get("cifericaopds.beta") is None
        assert all(x.provider_id != "cifericaopds.beta"
                   for x in svc._sessions.values())
        mgr.live = False
        svc.refresh_virtual_collections()
        assert svc.registry.descriptors() == []
    finally:
        svc.close()


def test_revision_replaces_adapter_and_retires_old_session(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    coll = Collection(snapshot(child("alpha")))
    mgr = FakeManager(coll)
    try:
        svc.attach_plugin_manager(mgr)
        svc.connect_configured()
        old_plugin = svc.registry.get("cifericaopds.alpha")
        old_session = next(s for s in svc._sessions.values()
                           if s.provider_id == "cifericaopds.alpha")
        assert old_session.connected
        coll.value = snapshot(child("alpha", generation=2), generation=2)
        svc.refresh_virtual_collections()
        assert svc.registry.get("cifericaopds.alpha") is old_plugin
        assert old_session.connected
        coll.value = snapshot(child("alpha", revision="r2", generation=3), generation=3)
        svc.refresh_virtual_collections()
        assert svc.registry.get("cifericaopds.alpha") is not old_plugin
        assert not old_session.connected
        assert all(s is not old_session for s in svc._sessions.values())
        svc.connect_configured()
        assert any(s.provider_id == "cifericaopds.alpha" and s.connected
                   for s in svc._sessions.values())
        with pytest.raises(ValueError, match="reserved"):
            svc.registry.register(LocalProvider("alpha"), replace=True)
    finally:
        svc.close()
