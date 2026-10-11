"""P2 #700: LRU cache and context isolation for deterministic Constellations."""
from __future__ import annotations

from constellations import ConstellationService, ConstellationsPlugin


def entry(key):
    return {
        "book_id": "book-" + key,
        "title": "Titre " + key,
        "authors": ["Autrice " + key],
        "metadata": {"subject": "prose"},
    }


def test_freshness_cache_is_isolated_by_identity_and_recency():
    service = ConstellationService()
    a = service.graph_for("alice", (entry("a"),), freshness_token="v1")
    b = service.graph_for("bob", (entry("b"),), freshness_token="v1")
    assert service.graph_for("alice", (entry("replacement"),), freshness_token="v1") is a
    assert service.graph_for("bob", (), freshness_token="v1") is b
    assert tuple(service._cache) == ("bob", "alice")


def test_more_than_eight_identities_evicts_only_least_recently_used():
    service = ConstellationService()
    saved = {}
    for number in range(service.MAX_CACHED_IDENTITIES):
        identity = "person" + str(number)
        saved[identity] = service.graph_for(
            identity, (entry(str(number)),), freshness_token="v1",
        )
    assert len(service._cache) == service.MAX_CACHED_IDENTITIES
    service.graph_for("person0", (), freshness_token="v1")  # hot identity survives
    service.graph_for("person8", (entry("8"),), freshness_token="v1")
    assert len(service._cache) == 8
    assert "person1" not in service._cache and "person0" in service._cache
    assert service.graph_for("person0", (), freshness_token="v1") is saved["person0"]
    new = service.graph_for("person1", (entry("1-new"),), freshness_token="v1")
    assert new is not saved["person1"]


def test_refresh_or_no_freshness_does_not_reuse_stale_graph():
    service = ConstellationService()
    old = service.graph_for("alice", (entry("old"),), freshness_token="v1")
    changed = service.graph_for("alice", (entry("new"),), freshness_token="v2")
    assert changed is not old
    anonymous = service.graph_for("alice", (entry("anonymous"),))
    assert anonymous is not changed
    assert "alice" not in service._cache


def test_activate_and_deactivate_drop_graph_cache_even_when_token_reused():
    plugin = ConstellationsPlugin()
    plugin.activate(object())
    first = plugin.graph("alice", (entry("first"),), freshness_token="1")
    assert plugin.service._cache
    plugin.deactivate()
    assert not plugin.service._cache and plugin.context is None
    plugin.activate(object())
    second = plugin.graph("alice", (entry("second"),), freshness_token="1")
    assert second is not first
    assert {book.title for book in second.books.values()} == {"Titre second"}
    plugin.activate(object())  # reactivation even without explicit deactivate
    assert not plugin.service._cache


def test_invalidation_remains_targeted_and_full_cleanup_is_idempotent():
    service = ConstellationService()
    service.graph_for("alice", (entry("a"),), freshness_token="v1")
    service.graph_for("bob", (entry("b"),), freshness_token="v1")
    service.invalidate("alice")
    assert tuple(service._cache) == ("bob",)
    service.invalidate()
    service.invalidate()
    assert not service._cache
