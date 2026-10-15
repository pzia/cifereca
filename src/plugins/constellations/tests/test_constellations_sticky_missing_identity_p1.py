"""#1599: a missing Constellations identity never silently revives as another.

Synthetic GTK dropdown and identity broker; no real account, files or network.
"""
from tests.test_constellations_profile_reload_p1 import view_with_profiles


def test_missing_profile_remains_unselected_after_second_model_change():
    new = [{"identity_id": "bob"}]
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        new, selected=0,
    )
    view.invalidate()
    assert dropdown.selected == 0xFFFFFFFF
    assert view._profile_selection_required is True

    new[:] = [{"identity_id": "charlie"}, {"identity_id": "bob"}]
    view.invalidate()
    assert dropdown.selected == 0xFFFFFFFF
    assert view.identity_id() == ""
    assert loads == [(True, ""), (True, "")]


def test_missing_profile_stays_unselected_when_rows_empty_and_then_reappear():
    new = []
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}], new, selected=0,
    )
    view.invalidate()
    assert view._profile_selection_required is True

    new[:] = [{"identity_id": "alice"}, {"identity_id": "bob"}]
    view.invalidate()
    assert dropdown.selected == 0xFFFFFFFF
    assert view.identity_id() == ""
    assert loads == [(True, ""), (True, "")]


def test_explicit_new_selection_revokes_sticky_requirement():
    new = [{"identity_id": "bob"}]
    view, dropdown, loads = view_with_profiles(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        new, selected=0,
    )
    view.invalidate()
    assert view._profile_selection_required is True
    dropdown.set_selected(0)  # Explicit user selection of Bob.
    assert view.identity_id() == "bob"

    new[:] = [{"identity_id": "charlie"}, {"identity_id": "bob"}]
    view.invalidate()
    assert view.identity_id() == "bob"
    assert dropdown.selected == 1
    assert view._profile_selection_required is False
    assert loads[-1] == (True, "bob")


def test_initially_empty_profile_list_may_use_first_new_identity():
    new = []
    view, dropdown, loads = view_with_profiles([], new, selected=0xFFFFFFFF)
    new[:] = [{"identity_id": "bob"}]
    view.invalidate()
    assert view.identity_id() == "bob"
    assert dropdown.selected == 0
    assert view._profile_selection_required is False
    assert loads == [(True, "bob")]
