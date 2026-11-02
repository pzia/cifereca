"""P0 #2797: a search refresh must not select another borrower's edition.

Exercises actual CoreUI selection logic without GTK/network/provider I/O.
"""
from __future__ import annotations

from types import SimpleNamespace

from cifereca.gui.search_snapshot_refresh import _selected_key


def _row(key, *, borrower="borrower-a", right="right-a", owner="alice",
         provider="numilog", item="edition", rights=None):
    records = rights
    if records is None:
        records = [(borrower, right)]
    return {
        "key": key,
        "source_refs": [{"identity_id": owner, "provider_id": provider,
                         "item_id": item}],
        "possessions": [
            {"identity_id": owner, "provider_id": provider, "item_id": item,
             "account_id": account, "entitlement_id": entitlement}
            for account, entitlement in records
        ],
    }


def _window(selected, previous):
    return SimpleNamespace(
        search_page=SimpleNamespace(
            selection=SimpleNamespace(
                get_selected_item=lambda: SimpleNamespace(key=selected),
            )
        ),
        _search_local_objects={selected: previous},
    )


def test_other_borrower_with_same_provider_edition_is_not_selected():
    original = _row("source:old", borrower="borrower-a", right="right-a")
    only_other = _row("source:other", borrower="borrower-b", right="right-b")
    window = _window("source:old", original)
    assert _selected_key(window, [only_other], "alice") is None
    assert _selected_key(window, [
        only_other, _row("artifact:verified", right="right-a"),
    ], "alice") == "artifact:verified"


def test_exact_right_moves_to_unique_local_card_without_guessing():
    selected = _row("source:old")
    local = _row("artifact:sha256:verified")
    assert _selected_key(
        _window("source:old", selected), [local], "alice",
    ) == "artifact:sha256:verified"


def test_ambiguous_same_entitlement_on_two_rows_fails_closed():
    selected = _row("source:old")
    choices = [_row("artifact:first"), _row("artifact:second")]
    assert _selected_key(_window("source:old", selected), choices, "alice") is None


def test_old_grouped_multi_account_row_does_not_choose_one_split_account():
    selected = _row(
        "source:old", rights=[
            ("borrower-a", "right-a"), ("borrower-b", "right-b"),
        ],
    )
    split = [
        _row("source:scoped:a", borrower="borrower-a", right="right-a"),
        _row("source:scoped:b", borrower="borrower-b", right="right-b"),
    ]
    assert _selected_key(_window("source:old", selected), split, "alice") is None
    single_merged = _row("artifact:all", rights=[
        ("borrower-a", "right-a"), ("borrower-b", "right-b"),
    ])
    assert _selected_key(
        _window("source:old", selected), [single_merged], "alice",
    ) == "artifact:all"


def test_legacy_unscoped_source_refs_are_not_proof_of_selection():
    old = {
        "key": "source:old",
        "source_refs": [{"identity_id": "alice", "provider_id": "numilog",
                         "item_id": "edition"}],
    }
    foreign = _row("source:foreign", borrower="other", right="other")
    assert _selected_key(_window("source:old", old), [foreign], "alice") is None
    alice = _row("source:alice")
    assert _selected_key(
        _window("source:old", alice), [alice], "bob",
    ) is None


def test_same_key_selection_is_stable_without_heuristic_remap():
    row = _row("source:scoped:stable")
    assert _selected_key(
        _window(row["key"], row), [row], "alice",
    ) == row["key"]
