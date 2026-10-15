"""#1599: no Jeunesse data or detail action crosses transient GTK identities."""
from types import SimpleNamespace as NS

import pytest

from youth_page import YouthView


class DropDown:
    def __init__(self, selected=0):
        self.selected = selected
        self.callback = None
        self.notifications = 0

    def get_selected(self):
        return self.selected

    def set_selected(self, value):
        self.selected = value
        self._notify()

    def set_model(self, model):
        self.model = model
        self.selected = 0 if model else 0xFFFFFFFF
        self._notify()

    def _notify(self):
        self.notifications += 1
        if self.callback:
            self.callback()


def make_view(old, new, selected=0):
    view = object.__new__(YouthView)
    view.disposed = False
    view._reloading_identities = False
    view.identities = old
    view.identity = DropDown(selected)
    view.Gtk = NS(StringList=NS(new=lambda labels: list(labels)))
    view._identity_rows = lambda: list(new)
    calls = []
    view.identity_changed = lambda: calls.append(("notification", view.identity_id()))
    view.identity.callback = (
        lambda: None if view._reloading_identities else view.identity_changed()
    )
    view.filters_changed = lambda: calls.append(("filters", view.identity_id()))
    view.refresh_source_diagnostics = lambda: calls.append(("diagnostic", view.identity_id()))
    return view, calls


@pytest.mark.parametrize("invalid", (-1, 0xFFFFFFFF, 8, None, "0", True))
def test_invalid_index_never_selects_last_or_other_profile(invalid):
    view, _ = make_view(
        [{"identity_id": "alice"}, {"identity_id": "bob"}], [],
        selected=invalid,
    )
    assert view.identity_id() == ""


def test_removed_profile_requires_new_explicit_choice():
    view, calls = make_view(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        [{"identity_id": "bob"}],
    )
    view.reload_identities()
    assert view.identity.get_selected() == 0xFFFFFFFF
    assert view.identity_id() == ""
    assert view.identity.notifications == 2
    assert calls == [("filters", ""), ("diagnostic", "")]


def test_reordering_does_not_load_intermediate_profile():
    view, calls = make_view(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        [{"identity_id": "bob"}, {"identity_id": "alice"}],
    )
    view.reload_identities()
    assert view.identity.get_selected() == 1
    assert view.identity_id() == "alice"
    assert calls == [("filters", "alice"), ("diagnostic", "alice")]


def test_first_profile_can_be_selected_on_initial_empty_view():
    view, calls = make_view([], [{"identity_id": "bob"}], selected=0xFFFFFFFF)
    view.reload_identities()
    assert view.identity_id() == "bob"
    assert calls == [("filters", "bob"), ("diagnostic", "bob")]


def test_zero_profiles_never_clamp_to_index_zero():
    view, calls = make_view([{"identity_id": "alice"}], [], selected=0)
    view.reload_identities()
    assert view.identity_id() == ""
    assert view.identity.get_selected() == 0xFFFFFFFF
    assert calls == [("filters", ""), ("diagnostic", "")]


def test_cache_empty_profile_revokes_stale_book_details():
    view, _ = make_view([{"identity_id": "alice"}], [], selected=0xFFFFFFFF)
    view.catalog = NS(search_cached=lambda *a, **kw: pytest.fail("wrong profile cache read"))
    view.rows = {"old": object()}
    view.selected_book = object()
    view._detail_selection_generation = 5
    states, details, refreshes = [], [], []
    view.master = NS(
        set_list_state=lambda *args: states.append(args),
        empty_detail=lambda: details.append("empty"),
    )
    view.update_action_availability = lambda: refreshes.append(True)
    view.render_cached()
    assert view.rows == {} and view.selected_book is None
    assert view._detail_selection_generation == 6
    assert len(details) == len(refreshes) == 1
    assert states[0][0] == "Aucun profil disponible"
