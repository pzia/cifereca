"""#1599: subsequent GTK identity refreshes cannot resurrect another Youth profile.

Only synthetic profile lists and dropdowns; no network, files or real user data.
"""
from tests.test_youth_profile_reload_p1 import make_view


def test_deleted_profile_remains_unselected_after_next_overview_update():
    new = [{"identity_id": "bob"}]
    view, calls = make_view(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        new,
    )
    view.reload_identities()
    assert view._profile_selection_required is True
    assert view.identity_id() == ""

    new[:] = [{"identity_id": "charlie"}, {"identity_id": "bob"}]
    view.reload_identities()
    assert view.identity.get_selected() == 0xFFFFFFFF
    assert view.identity_id() == ""
    assert calls == [
        ("filters", ""), ("diagnostic", ""),
        ("filters", ""), ("diagnostic", ""),
    ]


def test_deleted_profile_does_not_resurrect_when_identity_list_recovers():
    new = []
    view, calls = make_view([{"identity_id": "alice"}], new)
    view.reload_identities()
    assert view._profile_selection_required is True

    new[:] = [{"identity_id": "alice"}]
    view.reload_identities()
    assert view.identity_id() == ""
    assert view.identity.get_selected() == 0xFFFFFFFF
    assert calls[-2:] == [("filters", ""), ("diagnostic", "")]


def test_new_explicit_profile_choice_clears_missing_requirement():
    new = [{"identity_id": "bob"}]
    view, calls = make_view(
        [{"identity_id": "alice"}, {"identity_id": "bob"}],
        new,
    )
    view.reload_identities()
    view.identity.set_selected(0)  # User explicitly chooses Bob.
    assert view.identity_id() == "bob"

    new[:] = [{"identity_id": "charlie"}, {"identity_id": "bob"}]
    view.reload_identities()
    assert view._profile_selection_required is False
    assert view.identity_id() == "bob"
    assert view.identity.get_selected() == 1
    assert calls[-2:] == [("filters", "bob"), ("diagnostic", "bob")]


def test_initially_empty_youth_view_may_use_first_added_profile():
    new = []
    view, calls = make_view([], new, selected=0xFFFFFFFF)
    view._profile_selection_required = False  # Headless __new__ omits __init__.
    new[:] = [{"identity_id": "bob"}]
    view.reload_identities()
    assert view.identity_id() == "bob"
    assert view._profile_selection_required is False
    assert calls == [("filters", "bob"), ("diagnostic", "bob")]
