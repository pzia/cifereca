"""P0 #6366/#3104: never keep a public plugin selection on another source.

Two distinct supplier/edition rows can have the same plugin key and
different action rights. A reordering or a fresh source snapshot must not
select a different item just because GTK kept the old presentation key.
"""
from __future__ import annotations

from tests.test_core_p1_1598_public_book_distinct_rows import adapter, book


class Selection:
    def __init__(self, current=None):
        self.current = current
        self.clears = []

    def get_selected_item(self):
        return self.current

    def set_selected(self, index):
        self.clears.append(index)
        assert index == 0xFFFFFFFF
        self.current = None


def bind_selection_simulator(view, selection):
    """Model the real MasterDetailPage key-based selection restore."""
    view._page.selection = selection

    def show(rows, total_count=None):
        old_key = selection.current.key if selection.current else None
        view._page.rows = rows
        view._page.total_count = total_count
        if old_key is not None:
            selection.current = next(
                (item for item in rows if item.key == old_key), None,
            )

    view._page.show_rows = show


def test_duplicate_reorder_revokes_old_source_before_key_restore():
    view = adapter()
    alice = book("same", "A - private offer", "paris-A")
    bob = book("same", "B - different offer", "paris-B")
    view.show_rows([alice, bob])
    selection = Selection(view._page.rows[0])
    bind_selection_simulator(view, selection)
    view.show_rows([bob, alice])
    assert selection.clears == [0xFFFFFFFF]
    assert selection.current is None
    assert view._rows[view._page.rows[0].key] is bob


def test_replacing_duplicate_row_with_equal_key_or_title_revokes_selection():
    view = adapter()
    alice = book("same", "One", "paris")
    bob = book("same", "One", "paris")
    view.show_rows([alice, bob])
    selection = Selection(view._page.rows[1])
    bind_selection_simulator(view, selection)
    clone = book("same", "One", "paris")
    view.show_rows([alice, clone])
    assert selection.clears == [0xFFFFFFFF]
    assert selection.current is None
    assert view._rows[view._page.rows[1].key] is clone


def test_unchanged_duplicate_source_objects_keep_selection_fast_path():
    view = adapter()
    a, b = book("same", "A", "x"), book("same", "B", "y")
    view.show_rows([a, b])
    selection = Selection(view._page.rows[1])
    bind_selection_simulator(view, selection)
    view.show_rows([a, b])
    assert selection.clears == []
    assert selection.current is view._page.rows[1]
    assert view._rows[selection.current.key] is b


def test_unique_rows_and_real_suffix_key_are_not_accidentally_revoked():
    view = adapter()
    a, real = book("item", "A", "x"), book("item::row-2", "Real", "y")
    duplicate = book("item", "B", "z")
    view.show_rows([a, real, duplicate])
    chosen_key = view._page.rows[1].key
    assert chosen_key == "item::row-2"
    selection = Selection(view._page.rows[1])
    bind_selection_simulator(view, selection)
    view.show_rows([duplicate, real, a])
    # A real supplier key which happens to match a generated suffix must
    # still select only that original exact supplier object.
    assert selection.clears == []
    assert selection.current.key == chosen_key
    assert view._rows[chosen_key] is real


def test_duplicate_disappearing_must_not_restore_first_key_to_second_book():
    view = adapter()
    a, b = book("same", "A", "x"), book("same", "B", "y")
    view.show_rows([a, b])
    selection = Selection(view._page.rows[0])
    bind_selection_simulator(view, selection)
    view.show_rows([b])
    assert selection.clears == [0xFFFFFFFF]
    assert selection.current is None
