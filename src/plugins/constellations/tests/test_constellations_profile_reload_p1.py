"""#1599: Constellations never auto-crosses profiles during GTK model reload."""
from types import SimpleNamespace as NS

import pytest

from constellations_page import View


from .constellations_test_fixtures import view_with_profiles


@pytest.mark.parametrize("invalid", (-1, 0xFFFFFFFF, 15, None, "0", True))
def test_invalid_gtk_selection_never_reads_another_profile(invalid):
    view, _, _ = view_with_profiles(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        [], selected=invalid,
    )
    assert view.identity_id() == ""


def test_deleted_profile_does_not_implicitly_select_another_identity():
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        [{"identity_id": "bob"}], selected=0,
    )
    assert view.invalidate() is False
    assert dropdown.notifications == 2
    assert dropdown.selected == 0xFFFFFFFF
    assert view.identity_id() == ""
    assert loads == [(True, "")]
    assert view.cache == {}


def test_reordered_identity_keeps_original_profile_and_single_load():
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        [{"identity_id": "bob"}, {"identity_id": "alice"}], selected=0,
    )
    view.invalidate()
    assert view.identity_id() == "alice"
    assert dropdown.selected == 1
    assert loads == [(True, "alice")]


def test_empty_initial_profile_can_explicitly_initialize_from_first_row():
    view, dropdown, loads = view_with_profiles(
        [], [{"identity_id": "bob"}], selected=0xFFFFFFFF,
    )
    view.invalidate()
    assert dropdown.selected == 0
    assert loads == [(True, "bob")]


def test_removed_last_profile_clears_selection():
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}], [], selected=0,
    )
    view.invalidate()
    assert dropdown.selected == 0xFFFFFFFF
    assert loads == [(True, "")]


def test_invalid_identity_cancels_previous_task_without_broker_calls():
    view, dropdown, _ = view_with_profiles(
        [{"identity_id": "alice"}], [], selected=0xFFFFFFFF,
    )
    cancelled, labels, rendered = [], [], []
    view.generation = 2
    view.task = NS(cancel=lambda: cancelled.append("cancel"))
    view.nodes = ("old",)
    view.local = ("old",)
    view.center = "old"
    view.status = NS(set_label=labels.append)
    view.render = lambda: rendered.append("render")
    View.load(view)
    assert view.generation == 3
    assert cancelled == ["cancel"]
    assert view.nodes == () and view.local == () and view.center is None
    assert rendered == ["render"]
    assert "Aucun profil" in labels[-1]
