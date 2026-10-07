from types import SimpleNamespace

from recommendations_v024 import UxRecommendationsView


def ranked(title, score=50, provider="a"):
    return SimpleNamespace(
        item={"title": title, "author": "Auteur", "source_provider": provider},
        score=score,
        reasons=("raison",),
        sources=(provider,),
    )


def test_partial_order_keeps_existing_rows_stable():
    view = object.__new__(UxRecommendationsView)
    view._provisional_order = {}
    first = view._stable_partial([ranked("A"), ranked("B")])
    assert [row.item["title"] for row in first] == ["A", "B"]

    # A later batch may score B before A; provisional UX must not jump.
    second = view._stable_partial([ranked("B", 90), ranked("A", 40), ranked("C", 80)])
    assert [row.item["title"] for row in second] == ["A", "B", "C"]


def test_provider_ids_are_never_user_facing_when_label_is_known():
    view = object.__new__(UxRecommendationsView)
    view._provider_labels = {"": "Tous les catalogues", "a": "Catalogue A"}
    assert view._display_source("a") == "Catalogue A"
    assert view._display_source("unknown") == "unknown"
