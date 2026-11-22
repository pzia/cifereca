"""P1 #6168: History source-scoped N1/N2 list/detail and stale-repaint gates."""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.history_cover_decision_bridge import (
    HistoryCoverDecisionBridge, exact_history_cover_scope,
    history_cover_presentation,
)
from cifereca.gui.secondary_results_controller import populate_history
from cifereca.models import HistoryItem

IMAGE = "https://covers.example.org/history.png"
IMAGE2 = "https://covers.example.org/updated.png"
ROOT = Path(__file__).resolve().parents[1] / "src/cifereca/gui"


def occurrence(**changes):
    attrs = dict(
        id="history-occ-1", provider="paris", medium="digital",
        record_id="provider-edition-1", title="Le livre identique",
        author="Auteur", thumbnail_url=IMAGE,
        default_thumbnail_url="https://covers.example.org/fallback.png",
        source_provider="paris", source_credential="card-a",
        source_account_id="borrower-a", identity_id="alice",
    )
    attrs.update(changes)
    return HistoryItem(**attrs)


def host(item, *, epoch=12, revision=7):
    obj = NS(
        service=NS(
            current_identity=NS(identity_id="alice"),
            _account_context_epoch=epoch,
            current_account=NS(is_primary=False),
        ),
        history_objects={item.id: item},
        _history_cover_revision=revision,
        search_controls=object(),
        _attach_row_facets=lambda *_a, **_kw: None,
    )
    return obj


def main_method(name):
    source = ROOT / "main_window.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    owner = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                 and n.name == "MainWindow")
    func = next(n for n in owner.body if isinstance(n, ast.FunctionDef)
                and n.name == name)
    namespace = {
        "__name__": "cifereca.gui.main_window",
        "__package__": "cifereca.gui",
        "HistoryItem": HistoryItem, "BookListItem": Row,
        "fr_date": lambda value: str(value or ""),
    }
    exec(compile(
        ast.fix_missing_locations(ast.Module(
            body=[ast.ImportFrom(
                module="__future__",
                names=[ast.alias(name="annotations")], level=0,
            ), func],
            type_ignores=[],
        )),
        str(source), "exec",
    ), namespace)
    return namespace[name]


class Row:
    def __init__(self, key, title, subtitle, meta, badge, thumbnail_url,
                 **extras):
        self.key = key
        self.title = title
        self.subtitle = subtitle
        self.meta = meta
        self.badge = badge
        self.thumbnail_url = thumbnail_url
        for k, v in extras.items():
            setattr(self, k, v)


def test_source_exact_history_decision_shared_without_mutating_loan_occurrence():
    item = occurrence()
    original = item.thumbnail_url
    window = host(item)
    first = history_cover_presentation(window, item)
    second = history_cover_presentation(window, item)
    assert first is item and second is item
    cache = window._history_cover_decisions
    scope = exact_history_cover_scope(item, identity_id="alice")
    assert scope.kind == "catalogue"
    assert scope.source_key.startswith("history-exact:")
    assert cache.stats() == {"occurrences": 1}
    decision = cache.resolve(
        item, identity_id="alice", account_epoch=12, history_revision=7,
    )
    assert decision.status == "selected" and decision.uri == IMAGE
    assert cache.resolve(
        item, identity_id="alice", account_epoch=12, history_revision=7,
    ) == decision
    assert item.thumbnail_url == original


def test_real_mainwindow_history_row_and_selected_detail_share_n2_image():
    item = occurrence()
    window = host(item)
    window.history_page = NS(detail=NS(
        show_history=lambda value: window._shown.append(value),
    ))
    window._shown = []
    window._add_facets = lambda *_a: None
    window._add_local_book_actions = lambda *_a: None
    row = main_method("_history_book_row")(window, item)
    assert row.thumbnail_url == IMAGE
    assert row._public_row_content[6] == IMAGE
    main_method("_history_selected")(window, row)
    assert len(window._shown) == 1
    assert window._shown[0].thumbnail_url == row.thumbnail_url
    assert window._shown[0] is item
    assert window._history_cover_decisions.stats()["occurrences"] == 1


def test_provider_update_with_same_history_id_repaints_list_and_detail():
    original = occurrence()
    window = host(original)
    first = main_method("_history_book_row")(window, original)
    assert first.thumbnail_url == IMAGE
    replacement = replace(original, thumbnail_url=IMAGE2)
    window.history_objects = {replacement.id: replacement}
    window._history_cover_revision += 1
    second = main_method("_history_book_row")(window, replacement)
    assert second.key == first.key and second.thumbnail_url == IMAGE2
    assert second._public_row_content != first._public_row_content
    assert original.thumbnail_url == IMAGE
    current = window._history_cover_decisions.resolve(
        replacement, identity_id="alice", account_epoch=12,
        history_revision=window._history_cover_revision,
    )
    assert current.uri == IMAGE2 and current.revision == 2


@pytest.mark.parametrize("changes", [
    {"source_provider": "other"},
    {"source_credential": "card-b"},
    {"source_account_id": "borrower-b"},
    {"record_id": "provider-edition-2"},
    {"id": "history-occ-2"},
    {"identity_id": "bob"},
    {"medium": "physical"},
])
def test_same_title_or_image_never_joins_distinct_history_occurrences(changes):
    first = occurrence()
    other = occurrence(**changes)
    a = exact_history_cover_scope(first, identity_id="alice")
    b = exact_history_cover_scope(other, identity_id="alice")
    if changes == {"identity_id": "bob"}:
        assert b is None
    else:
        assert b != a
    bridge = HistoryCoverDecisionBridge()
    first_decision = bridge.resolve(
        first, identity_id="alice", account_epoch=1, history_revision=1,
    )
    assert first_decision.status == "selected"
    second_decision = bridge.resolve(
        other, identity_id="alice", account_epoch=1, history_revision=1,
    )
    if changes == {"identity_id": "bob"}:
        assert second_decision is None
    else:
        assert second_decision.scope != first_decision.scope


def test_invalid_and_signed_upstream_urls_are_not_painted():
    for private in (
        "https://covers.example.org/signed.jpg?token=SECRET",
        "http://127.0.0.1/admin.png",
        "file:///tmp/foreign.png",
        "https://reader.local/image.png",
    ):
        item = occurrence(thumbnail_url=private)
        window = host(item)
        presented = history_cover_presentation(window, item)
        assert presented is not item
        assert presented.thumbnail_url is None
        assert item.thumbnail_url == private
        row = main_method("_history_book_row")(window, item)
        assert row.thumbnail_url is None
        assert row.default_thumbnail_url == item.default_thumbnail_url
        assert row._public_row_content[6] is None


def test_unqualified_owner_or_account_does_not_reuse_an_other_profile_cover():
    for changed in (
        {"identity_id": None},
        {"source_account_id": None},
        {"source_credential": ""},
        {"record_id": None},
    ):
        item = occurrence(**changed)
        window = host(item)
        assert history_cover_presentation(window, item) is item
        assert not hasattr(window, "_history_cover_decisions")


def test_switch_account_epoch_and_back_drops_previous_n2_decision():
    item = occurrence()
    bridge = HistoryCoverDecisionBridge()
    first = bridge.resolve(
        item, identity_id="alice", account_epoch=11, history_revision=2,
    )
    again = bridge.resolve(
        item, identity_id="alice", account_epoch=11, history_revision=2,
    )
    second = bridge.resolve(
        item, identity_id="alice", account_epoch=12, history_revision=2,
    )
    third = bridge.resolve(
        item, identity_id="alice", account_epoch=13, history_revision=2,
    )
    assert first == again and first is not again
    assert first.uri == second.uri == third.uri
    assert first.revision < second.revision < third.revision


def test_stale_history_object_after_refresh_cannot_replay_in_new_epoch():
    old = occurrence()
    window = host(old)
    assert history_cover_presentation(window, old) is old
    new = replace(old, thumbnail_url=IMAGE2)
    window.history_objects = {new.id: new}
    window._history_cover_revision += 1
    assert history_cover_presentation(window, old) is old
    current = history_cover_presentation(window, new)
    assert current.thumbnail_url == IMAGE2
    assert window._history_cover_decisions.resolve(
        new, identity_id="alice", account_epoch=12, history_revision=8,
    ).uri == IMAGE2


def test_direct_history_url_is_only_evidence_not_default_provider_image():
    item = occurrence(thumbnail_url=None)
    window = host(item)
    assert history_cover_presentation(window, item) is item
    bridge = window._history_cover_decisions
    decision = bridge.resolve(
        item, identity_id="alice", account_epoch=12, history_revision=7,
    )
    assert decision.status == "missing" and decision.uri == ""
    assert item.default_thumbnail_url is not None


def test_bounded_lru_for_history_occurrences_without_cross_source_reuse():
    bridge = HistoryCoverDecisionBridge(max_occurrences=2)
    items = [occurrence(id=f"history-{i}") for i in range(3)]
    initial = bridge.resolve(
        items[0], identity_id="alice", account_epoch=1, history_revision=1,
    )
    for item in items[1:]:
        bridge.resolve(
            item, identity_id="alice", account_epoch=1, history_revision=1,
        )
    assert bridge.stats() == {"occurrences": 2}
    next_decision = bridge.resolve(
        items[0], identity_id="alice", account_epoch=1, history_revision=1,
    )
    assert next_decision.revision > initial.revision


def test_populate_history_advances_revision_once_not_on_language_filter():
    rows = [occurrence()]
    window = NS(
        _closed=False, history_objects={},
        _history_page_size=10, history_languages=NS(values=lambda: ()),
        history_page=NS(show_rows=lambda *_a, **_kw: None),
        _history_book_row=lambda x: x,
        _history_loaded_count=0, _history_append_scheduled=False,
    )
    populate_history(window, rows)
    assert window._history_cover_revision == 1
    populate_history(window, list(rows), preserve_all=True)
    assert window._history_cover_revision == 1
    populate_history(window, list(rows))
    assert window._history_cover_revision == 2
    populate_history(window, [])
    assert window._history_cover_revision == 3


@pytest.mark.parametrize("capacity", [0, -1, True, 4097, "123"])
def test_exact_history_cache_capacity_is_bounded(capacity):
    with pytest.raises(ValueError):
        HistoryCoverDecisionBridge(max_occurrences=capacity)
