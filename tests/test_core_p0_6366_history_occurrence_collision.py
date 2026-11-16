"""#6366: History N2 must never bind an upstream ID reused by another source."""
from __future__ import annotations

from types import SimpleNamespace as NS

from cifereca.models import HistoryItem
from cifereca.gui.history_occurrence_guard import unambiguous_history_occurrences
from cifereca.gui.secondary_results_controller import populate_history


def history(key, *, credential="one", account="", identity="alice", url=None):
    return HistoryItem(
        id=key, provider="paris", medium="digital",
        record_id="catalogue-17", title="Même titre",
        thumbnail_url=url, source_provider="paris",
        source_credential=credential, source_account_id=account,
        identity_id=identity,
    )


def window():
    events = []
    w = NS(
        _closed=False,
        service=NS(current_identity=NS(identity_id="alice")),
        _history_page_size=50, _history_cover_revision=0,
        history_languages={}, history_objects={},
        history_page=NS(show_rows=lambda rows, *, total_count:
                        events.append((tuple(rows), total_count))),
        _history_book_row=lambda item: item,
    )
    return w, events


def test_distinct_provider_occurrences_with_same_id_are_never_last_write_wins():
    a = history("shared", credential="alice", url="https://example.org/a")
    b = history("shared", credential="bob", url="https://example.org/b")
    other = history("unique")
    assert unambiguous_history_occurrences([a, b, other]) == (other,)
    w, events = window()
    populate_history(w, [a, b, other])
    assert tuple(w.history_objects) == ("unique",)
    assert events[-1][1] == 1
    assert w._history_cover_revision == 1
    assert w._history_rows == [other]


def test_identical_object_replay_is_safe_but_distinct_equal_dto_is_not():
    a = history("shared")
    assert unambiguous_history_occurrences([a, a]) == (a,)
    b = history("shared")
    assert a == b and a is not b
    assert unambiguous_history_occurrences([a, b]) == ()


def test_cross_profile_replay_and_aba_does_not_rebind_history_detail():
    alice = history("shared", identity="alice")
    bob = history("shared", identity="bob")
    w, events = window()
    populate_history(w, [alice, bob])
    assert w.history_objects == {"shared": alice}
    w.service.current_identity.identity_id = "bob"
    populate_history(w, [alice, bob])
    assert w.history_objects == {"shared": bob}
    w.service.current_identity.identity_id = "alice"
    populate_history(w, [alice, bob])
    assert w.history_objects == {"shared": alice}
    assert w._history_cover_revision == 3


def test_empty_and_unbounded_sources_fail_closed():
    assert unambiguous_history_occurrences([history(""), history("valid")]) == (
        history("valid"),
    )
    assert unambiguous_history_occurrences(
        (history(str(i)) for i in range(10001)),
    ) == ()
