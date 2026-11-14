"""#4761 — local sort of a previously calculated projection, without supplier I/O.

Synthetic exact notices deliberately share a display key to prove that
sorting does not rebind a previous GTK selection to another edition.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendation_engine import RankedCandidate
from recommendations import UxRecommendationsView, _sort_visible_recommendations


def rank(title, author, score, *, record):
    return RankedCandidate(
        item={"title": title, "author": author, "record_id": record,
              "source_provider": "synthetic.public", "downloadable": False},
        score=score, sources=("synthetic.public",), reasons=("synthetic",),
    )


def test_view_sort_modes_are_stable_reversible_and_do_not_modify_ranked_results():
    rows = [
        (rank("Zèbre", "Zoé", 40, record="a"), "a"),
        (rank("Alpha", "Anna", 90, record="b"), "b"),
        (rank("Beta", "Béatrice", 90, record="c"), "c"),
        (rank("Omega", "Noir", 20, record="d"), "d"),
    ]
    before = tuple((id(row), key, row.score) for row, key in rows)
    assert [key for _, key in _sort_visible_recommendations(rows, "rank")] == [
        "a", "b", "c", "d",
    ]
    assert [key for _, key in _sort_visible_recommendations(rows, "affinity")] == [
        "b", "c", "a", "d",
    ]
    assert [key for _, key in _sort_visible_recommendations(rows, "title")] == [
        "b", "c", "d", "a",
    ]
    assert [key for _, key in _sort_visible_recommendations(rows, "author")] == [
        "b", "c", "d", "a",
    ]
    assert _sort_visible_recommendations(rows, "invalid") == rows
    assert tuple((id(row), key, row.score) for row, key in rows) == before
    assert all(not row.item["downloadable"] for row, _ in rows)


def test_bad_scores_and_nontext_bibliography_sort_last_without_stringifying():
    class Poison:
        def __str__(self):
            raise AssertionError("PRIVATE_ACCESS_TOKEN")
        def __repr__(self):
            raise AssertionError("PRIVATE_ACCESS_TOKEN")

    rows = [
        (rank("Éclair", "A", 50, record="ok"), "ok"),
        (rank(Poison(), Poison(), True, record="bad"), "bad"),
        (rank("Aube", "B", 5, record="low"), "low"),
    ]
    for mode, expected in (
        ("affinity", ["ok", "low", "bad"]),
        ("title", ["low", "ok", "bad"]),
        ("author", ["ok", "low", "bad"]),
    ):
        assert [key for _, key in _sort_visible_recommendations(rows, mode)] == expected


def test_gtk_row_keys_do_not_rebind_duplicate_editions_when_sort_changes():
    view = object.__new__(UxRecommendationsView)
    ranked = [
        rank("Zeta", "Z", 40, record="edition-a"),
        rank("Alpha", "A", 75, record="edition-b"),
        rank("Omega", "O", 20, record="edition-c"),
    ]
    view._ranked = ranked
    view._matches = lambda _row: True
    # An aggregated candidate key can collide with a real ':1' suffix.
    view._candidate_key = lambda row: (
        "edition:1" if row is ranked[2] else "edition"
    )
    view._generation = 1
    view._provisional_active = False
    view._provider_labels = {"synthetic.public": "Catalogue fictif"}
    view.selected_identity_id = lambda: "alice"
    view._busy = False
    view._disposed = False
    view._exact_notice_state = None
    selected = ["rank"]
    view._query_controls = NS(local_sort_key=lambda: selected[0])
    snapshots = []
    view._master = NS(show_rows=lambda rows, **kw: snapshots.append(
        tuple((row.key, row.title) for row in rows)
    ))
    # No summary/LLM hooks are needed; the real render builds the public rows.
    for mode in ("rank", "title", "affinity", "author", "rank"):
        selected[0] = mode
        view._render(preserve_summary=True)

    assert snapshots[0] == snapshots[-1]
    first = dict((title, key) for key, title in snapshots[0])
    assert len(set(first.values())) == 3
    assert first["Zeta"] == "edition"
    assert first["Alpha"] == "edition:2"  # never steal edition:1
    assert first["Omega"] == "edition:1"
    for snapshot in snapshots:
        assert dict((title, key) for key, title in snapshot) == first
    assert [title for _key, title in snapshots[1]] == [
        "Alpha", "Omega", "Zeta",
    ]
    assert [title for _key, title in snapshots[2]] == [
        "Alpha", "Zeta", "Omega",
    ]
    # The ranked candidate objects, source provenance and scores were not
    # rearranged; local sort changes only the presentation of a frozen list.
    assert [row.item["record_id"] for row in view._ranked] == [
        "edition-a", "edition-b", "edition-c",
    ]
    assert set(view._ranked_by_key) == set(first.values())
