from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import recommendation_bridge
from recommendations import RecommendationsPlugin


ROOT = Path(__file__).resolve().parents[1]


def book(title="Livre référence"):
    return SimpleNamespace(
        book_id="book-1",
        title=title,
        authors=("Alice Auteur",),
        identifiers={"provider": "kobo"},
        metadata={"subjects": ["histoire"], "language": "fr", "format": "epub"},
    )


def test_recommend_from_book_validates_stores_and_returns_public_route():
    plugin = RecommendationsPlugin()

    assert plugin.recommend_from_book(book()) == "recommendations"
    assert plugin._pending_reference.title == "Livre référence"


def test_recommend_from_book_updates_open_view_immediately():
    calls = []
    plugin = RecommendationsPlugin()
    plugin._view = SimpleNamespace(set_reference_book=lambda value: calls.append(value))

    selected = book("Deuxième livre")
    assert plugin.recommend_from_book(selected) == "recommendations"
    assert calls == [selected]


def test_recommend_from_book_rejects_reference_without_signal():
    plugin = RecommendationsPlugin()
    invalid = SimpleNamespace(
        book_id="empty", title="", authors=(), identifiers={}, metadata={"language": "fr"}
    )

    with pytest.raises(ValueError, match="pas assez de métadonnées"):
        plugin.recommend_from_book(invalid)
    assert plugin._pending_reference is None


def test_bridge_is_structural_and_delegates_to_ui_plugin(monkeypatch):
    calls = []
    monkeypatch.setattr(
        recommendation_bridge.UI_PLUGIN,
        "recommend_from_book",
        lambda value: calls.append(value) or "recommendations",
    )
    selected = book()

    assert recommendation_bridge.PLUGIN.recommend_from_book(selected) == "recommendations"
    assert calls == [selected]


def test_manifest_publishes_book_reference_recommendation_capability():
    manifest = json.loads((ROOT / "cifereca-plugin.json").read_text(encoding="utf-8"))
    services = [row for row in manifest["plugins"] if row["type"] == "recommendation"]

    assert len(services) == 1
    assert services[0]["entrypoint"] == "recommendation_bridge:PLUGIN"
    assert "recommendation.book-reference" in services[0]["capabilities"]
    assert services[0]["requires"] == [{"plugin": "ui.page.recommendations"}]
