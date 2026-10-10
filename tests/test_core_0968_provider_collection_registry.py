"""P1 #968: Core alone materializes collection children; deterministic and offline."""
from __future__ import annotations

from types import SimpleNamespace as NS
from threading import Event, current_thread
from dataclasses import asdict
import json
from cifereca.core.provider_collection_cache import ProviderCollectionSnapshotStore

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
        self.callbacks = []

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
        self.callbacks.append(callback)
        def unsubscribe():
            if callback in self.callbacks:
                self.callbacks.remove(callback)
        return unsubscribe

    def notify(self):
        for callback in tuple(self.callbacks):
            callback()


class FakeManager:
    def __init__(self, collection):
        self.collection = collection
        self.live = True
        self.enabled = {}
        self.callbacks = []
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

    def subscribe_services(self, category, callback):
        assert category == "provider.collection"
        self.callbacks.append(callback)
        def unsubscribe():
            if callback in self.callbacks:
                self.callbacks.remove(callback)
        return unsubscribe

    def notify_services(self):
        for callback in tuple(self.callbacks):
            callback()


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


def test_disabled_or_retired_ids_cannot_be_claimed_by_another_parent():
    """A vanished public provider must retain its owner to protect history."""
    reg = ProviderRegistry()
    original = Collection(snapshot(child("alpha")))
    reg.reconcile_collection(PARENT, original)

    # The child is absent from the live plugin registry, but the accepted
    # inventory must still reserve its stable provider ID to this parent.
    original.value = snapshot(child("alpha", status="disabled"), generation=2)
    assert reg.reconcile_collection(PARENT, original) == ()
    assert reg.maybe_get("cifericaopds.alpha") is None

    other_parent = "org.cifereca.integration.other"
    other_snapshot = snapshot(child("alpha"))
    other_snapshot["parent_plugin_id"] = other_parent
    other_snapshot["children"][0]["parent_plugin_id"] = other_parent
    other = Collection(other_snapshot)
    with pytest.raises(ProviderCollectionValidationError, match="collision"):
        reg.reconcile_collection(other_parent, other)
    assert reg.virtual_snapshot(other_parent) is None

    # Disabling/removing the parent must not free its historic namespace.
    assert reg.retire_collection(PARENT) == ()
    with pytest.raises(ProviderCollectionValidationError, match="collision"):
        reg.reconcile_collection(other_parent, other)

    # Only the original owner can bring its source back under the same ID.
    original.value = snapshot(child("alpha", revision="r2", generation=3), generation=3)
    assert reg.reconcile_collection(PARENT, original) == ("cifericaopds.alpha",)
    assert reg.get("cifericaopds.alpha").descriptor.display_name.endswith("(r2)")


def test_inventory_notification_connects_delta_without_restarting_other_sources(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    coll = Collection(snapshot(child("alpha")))
    mgr = FakeManager(coll)
    notifications = []
    done = Event()
    unsubscribe = svc.subscribe_virtual_sources(
        lambda: (notifications.append(current_thread().name), done.set())
    )
    try:
        svc.attach_plugin_manager(mgr)
        svc.connect_configured()
        original = svc._sessions["cifericaopds.alpha::__public__"]
        assert original.connected and len(coll.callbacks) == 1
        assert len(mgr.callbacks) == 1
        # Initial attachment publishes an inventory synchronously. Reset the
        # event so only the *next* asynchronous notification can unblock wait().
        assert done.is_set()
        notifications.clear()
        done.clear()
        # A plugin-local cache update, never a Core HTTP discovery request.
        coll.value = snapshot(child("alpha"), child("beta"), generation=2)
        coll.notify()
        assert done.wait(5), "worker did not deliver the inventory change"
        assert svc._sessions["cifericaopds.alpha::__public__"] is original
        beta = svc._sessions["cifericaopds.beta::__public__"]
        assert beta.connected
        assert all("cifereca-search" in name for name in notifications)
        done.clear()
        coll.value = snapshot(child("alpha"), child("beta", status="disabled"), generation=3)
        coll.notify()
        assert done.wait(5)
        assert not beta.connected
        assert svc.registry.maybe_get("cifericaopds.beta") is None
        assert svc._sessions["cifericaopds.alpha::__public__"] is original
        # No duplicate change notifications for an identical snapshot.
        done.clear()
        count = len(notifications)
        svc.refresh_virtual_collections()
        assert len(notifications) == count
        assert not done.is_set()
        # Refreshing the provider-category list retires a missing parent.
        done.clear()
        mgr.live = False
        mgr.notify_services()
        assert done.wait(5)
        assert not original.connected
        assert svc.registry.descriptors() == []
        assert coll.callbacks == []
    finally:
        unsubscribe()
        svc.close()
    assert not mgr.callbacks


def test_search_cache_is_versioned_by_virtual_catalog_generation(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    coll = Collection(snapshot(child("alpha")))
    try:
        svc.attach_plugin_manager(FakeManager(coll))
        svc.connect_configured()
        session = svc._sessions["cifericaopds.alpha::__public__"]
        queries = []
        original = session.search
        session.search = lambda *args, **kw: (queries.append(args), original(*args, **kw))[1]
        svc.search("catalog", force=False)
        count = len(queries)
        assert count > 0
        svc.search("catalog", force=False)
        assert len(queries) == count
        coll.value = snapshot(child("alpha", generation=2), generation=2)
        svc.refresh_virtual_collections()
        assert svc._sessions["cifericaopds.alpha::__public__"] is session
        svc.search("catalog", force=False)
        assert len(queries) > count
    finally:
        svc.close()


def test_notification_failure_preserves_last_good_sources_and_no_false_update(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    coll = Collection(snapshot(child("alpha")))
    mgr = FakeManager(coll)
    seen = []
    svc.subscribe_virtual_sources(lambda: seen.append("changed"))
    try:
        svc.attach_plugin_manager(mgr)
        assert seen == ["changed"]
        coll.value = snapshot(child("alpha"), child("alpha"), generation=2)
        assert svc.refresh_virtual_collections() == []
        assert seen == ["changed"]
        assert svc.registry.maybe_get("cifericaopds.alpha") is not None
        assert svc.registry.virtual_snapshot(PARENT).discovery_generation == 1
    finally:
        svc.close()


def test_synthetic_merged_default_search_prefers_origins_and_is_explicitly_selectable(tmp_path):
    """Global search does not query an aggregate together with its originals."""
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    collection = Collection(snapshot(
        child("alpha"), child("beta"), child("merged", kind="synthetic.merged"),
    ))
    manager = FakeManager(collection)
    try:
        svc.attach_plugin_manager(manager)
        svc.connect_configured()
        svc._mirror_remote_results = lambda _rows, **kw: 0
        calls = {}
        for session in svc._sessions.values():
            if not session.provider_id.startswith("cifericaopds."):
                continue
            original = session.search
            provider_id = session.provider_id
            def counted(*args, _original=original, _provider_id=provider_id, **kwargs):
                calls[_provider_id] = calls.get(_provider_id, 0) + 1
                return _original(*args, **kwargs)
            session.search = counted

        # All three remain available as explicit filter options.
        assert {x for x, _ in svc.searchable_providers()} >= {
            "cifericaopds.alpha", "cifericaopds.beta", "cifericaopds.merged",
        }
        rows = svc.search("search term", force=True)
        assert {row.source_provider for row in rows} >= {
            "cifericaopds.alpha", "cifericaopds.beta",
        }
        assert "cifericaopds.merged" not in calls

        # Exact selection still makes the aggregate usable.
        before = calls.copy()
        rows = svc.search("search term", force=True, provider_id="cifericaopds.merged")
        assert {r.source_provider for r in rows} == {"cifericaopds.merged"}
        assert calls["cifericaopds.merged"] > before.get("cifericaopds.merged", 0)

        # Deliberate opt-in includes all three in the same query.
        before = calls["cifericaopds.merged"]
        rows = svc.search("search term", force=True, include_redundant_merged=True)
        assert {r.source_provider for r in rows} >= {
            "cifericaopds.alpha", "cifericaopds.beta", "cifericaopds.merged",
        }
        assert calls["cifericaopds.merged"] > before

        # Losing one original does not suppress the other; losing both
        # automatically brings back the available merged aggregate.
        manager.enabled[("cifericaopds.alpha", "default")] = False
        manager.enabled[("cifericaopds.beta", "default")] = False
        merged_before = calls["cifericaopds.merged"]
        rows = svc.search("search term", force=True)
        assert "cifericaopds.merged" in {r.source_provider for r in rows}
        assert calls["cifericaopds.merged"] > merged_before
    finally:
        svc.close()


def test_synthetic_merged_progressive_follows_same_default_and_cache_key(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    collection = Collection(snapshot(
        child("alpha"), child("merged", kind="synthetic.merged"),
    ))
    manager = FakeManager(collection)
    try:
        svc.attach_plugin_manager(manager)
        svc.connect_configured()
        svc._mirror_remote_results = lambda _rows, **kw: 0
        batches = []
        first = svc.search_progressive(
            "search term", force=False, on_batch=batches.append,
        )
        assert first.providers_total >= 1
        assert "cifericaopds.alpha" in {b.provider_id for b in batches}
        assert "cifericaopds.merged" not in {b.provider_id for b in batches}

        # Even if provider_ids names the default set, suppress duplication.
        batches.clear()
        all_sources = ("cifericaopds.alpha", "cifericaopds.merged")
        svc.search_progressive(
            "search term", force=True, provider_ids=all_sources,
            on_batch=batches.append,
        )
        assert "cifericaopds.merged" not in {b.provider_id for b in batches}

        batches.clear()
        svc.search_progressive(
            "search term", force=True, provider_ids=("cifericaopds.merged",),
            on_batch=batches.append,
        )
        assert "cifericaopds.merged" in {b.provider_id for b in batches}

        batches.clear()
        svc.search_progressive(
            "search term", force=True, provider_ids=all_sources,
            include_redundant_merged=True, on_batch=batches.append,
        )
        assert {b.provider_id for b in batches} >= set(all_sources)

        # After per-Identity suppression of the source, only merged remains.
        manager.enabled[("cifericaopds.alpha", "default")] = False
        batches.clear()
        svc.search_progressive(
            "search term", force=True, on_batch=batches.append,
        )
        assert "cifericaopds.merged" in {b.provider_id for b in batches}
    finally:
        svc.close()


def test_synthetic_merged_does_not_mask_unrelated_or_unsupported_sources(tmp_path):
    cfg = Config(tmp_path / "config.json")
    svc = LibraryService(cfg, Storage(tmp_path / "cache.sqlite3"), Diagnostics())
    collection = Collection(snapshot(
        child("alpha"), child("merged", kind="synthetic.merged"),
    ))
    try:
        svc.attach_plugin_manager(FakeManager(collection))
        svc.connect_configured()
        originals = [
            session for session in svc._sessions.values()
            if session.provider_id in {"cifericaopds.alpha", "cifericaopds.merged"}
        ]
        # Both initially in the registry, with the default selector excluding
        # the synthetic sibling for ordinary queries.
        assert [s.provider_id for s in svc._without_redundant_merged(
            originals, field="all", scope="all",
        )] == ["cifericaopds.alpha"]
        # If the original cannot support a field but merged can, retain merged.
        from cifereca.providers import ProviderSearchCapabilities
        alpha = next(s for s in originals if s.provider_id == "cifericaopds.alpha")
        from dataclasses import replace
        alpha.descriptor = replace(
            alpha.descriptor,
            search_capabilities=ProviderSearchCapabilities(fields=("title",)),
        )
        selected = svc._without_redundant_merged(originals, field="isbn", scope="all")
        assert "cifericaopds.merged" in {s.provider_id for s in selected}
    finally:
        svc.close()


def test_cached_inventory_restores_after_restart_when_parent_is_offline(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db_path = tmp_path / "cache.sqlite3"
    original = Collection(snapshot(
        child("alpha"), child("beta", revision="r3", generation=7), generation=4,
    ))
    first = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        first.attach_plugin_manager(FakeManager(original))
        first.connect_configured()
        saved = ProviderCollectionSnapshotStore(first.storage).load(PARENT)
        assert saved is not None and saved.discovery_generation == 4
        assert {x.plugin_id for x in saved.children} == {
            "cifericaopds.alpha", "cifericaopds.beta",
        }
    finally:
        first.close()

    class OfflineCollection(Collection):
        def snapshot(self):
            raise ConnectionError("server unreachable, no cached plugin inventory")

    offline = OfflineCollection(original.value)
    second = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        attached = second.attach_plugin_manager(FakeManager(offline))
        assert set(attached) == {"cifericaopds.alpha", "cifericaopds.beta"}
        assert offline.calls == [("create", "alpha"), ("create", "beta")]
        assert second.registry.virtual_snapshot(PARENT) == saved
        second.connect_configured()
        rows = second.search("offline", force=True)
        assert {x.source_provider for x in rows} == set(attached)
        assert second.storage.metadata_get(ProviderCollectionSnapshotStore.key(PARENT))
    finally:
        second.close()


def test_saved_checkpoint_rejects_stale_server_replay_without_erasing_it(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db_path = tmp_path / "cache.sqlite3"
    first = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        first.attach_plugin_manager(FakeManager(Collection(snapshot(
            child("alpha", revision="r5", generation=12),
            generation=5,
        ))))
    finally:
        first.close()

    stale = Collection(snapshot(
        child("alpha", revision="r1", generation=1), generation=2,
    ))
    second = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        ids = second.attach_plugin_manager(FakeManager(stale))
        assert ids == ["cifericaopds.alpha"]
        recovered = second.registry.virtual_snapshot(PARENT)
        assert recovered.discovery_generation == 5
        assert recovered.children[0].source_revision == "r5"
        assert ProviderCollectionSnapshotStore(second.storage).load(PARENT) == recovered

        # A subsequent higher-generation revision may replace the checkpoint.
        stale.value = snapshot(child("alpha", revision="r6", generation=13), generation=6)
        second.refresh_virtual_collections()
        latest = ProviderCollectionSnapshotStore(second.storage).load(PARENT)
        assert latest.discovery_generation == 6
        assert latest.children[0].source_revision == "r6"
    finally:
        second.close()


def test_corrupted_snapshot_never_materializes_or_overwrites_known_good_data(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db_path = tmp_path / "cache.sqlite3"
    storage = Storage(db_path)
    key = ProviderCollectionSnapshotStore.key(PARENT)
    storage.metadata_set(key, '{"format":1,"digest":"bad","snapshot":{}}')
    storage.close()

    class OfflineCollection(Collection):
        def snapshot(self):
            raise ConnectionError("offline")

    svc = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        assert svc.attach_plugin_manager(FakeManager(OfflineCollection(snapshot(child("alpha"))))) == []
        assert svc.registry.descriptors() == []
        assert svc.storage.metadata_get(key).startswith('{"format":1')
        # A new valid inventory heals an invalid or corrupted checkpoint.
        online = Collection(snapshot(child("alpha"), generation=4))
        svc.attach_plugin_manager(FakeManager(online))
        assert svc.registry.maybe_get("cifericaopds.alpha") is not None
        loaded = ProviderCollectionSnapshotStore(svc.storage).load(PARENT)
        assert loaded.discovery_generation == 4
    finally:
        svc.close()


def test_recovery_preserves_disabled_children_and_never_revives_inactive_parent(tmp_path):
    cfg = Config(tmp_path / "config.json")
    db_path = tmp_path / "cache.sqlite3"
    initial = snapshot(
        child("alpha"), child("beta", status="disabled"), generation=3,
    )
    first = LibraryService(cfg, Storage(db_path), Diagnostics())
    try:
        first.attach_plugin_manager(FakeManager(Collection(initial)))
    finally:
        first.close()

    class OfflineCollection(Collection):
        def snapshot(self):
            raise ConnectionError("offline")

    second = LibraryService(cfg, Storage(db_path), Diagnostics())
    manager = FakeManager(OfflineCollection(initial))
    manager.live = False
    try:
        assert second.attach_plugin_manager(manager) == []
        assert second.registry.descriptors() == []
        manager.live = True
        assert second.refresh_virtual_collections() == ["cifericaopds.alpha"]
        assert second.registry.maybe_get("cifericaopds.beta") is None
    finally:
        second.close()


def test_saved_inventory_contains_only_validated_snapshot_fields(tmp_path):
    storage = Storage(tmp_path / "storage.sqlite3")
    repo = ProviderCollectionSnapshotStore(storage)
    try:
        from cifereca.plugin_api.provider_collection import validate_provider_collection_snapshot
        payload = snapshot(child("alpha"), generation=7)
        payload["untrusted_secret"] = "should-not-be-persisted"
        payload["children"][0]["untrusted_secret"] = "also-not-persisted"
        clean = validate_provider_collection_snapshot(payload, parent_plugin_id=PARENT)
        repo.save(clean)
        raw = storage.metadata_get(repo.key(PARENT))
        assert "should-not-be-persisted" not in raw
        assert "also-not-persisted" not in raw
        assert asdict(repo.load(PARENT)) == asdict(clean)
        record = json.loads(raw)
        record["snapshot"]["children"][0]["source_revision"] = "tampered"
        storage.metadata_set(repo.key(PARENT), json.dumps(record))
        with pytest.raises(ProviderCollectionValidationError, match="checksum"):
            repo.load(PARENT)
    finally:
        storage.close()
