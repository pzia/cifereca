"""#1599: public provider labels are validated before populating GTK filter.

Headless fake Gtk.StringList only; no network, profile or provider call.
"""
from __future__ import annotations

from recommendations import UxRecommendationsView


class Poison:
    def __str__(self):
        raise AssertionError("PRIVATE_BEARER /home/PRIVATE_PROFILE")


class UntrustedString(str):
    def __str__(self):
        raise AssertionError("PRIVATE_BEARER")


class ListModel:
    def __init__(self, values):
        self.values = list(values)

    def get_n_items(self):
        return len(self.values)

    def remove(self, index):
        self.values.pop(index)

    def append(self, label):
        assert type(label) is str
        self.values.append(label)


def view_fixture(*, with_kobo=False):
    view = object.__new__(UxRecommendationsView)
    names = ["Tous les catalogues"] + (["Kobo"] if with_kobo else [])
    view._provider_model = ListModel(names)
    view._provider_labels = {"": "Tous les catalogues"}
    if with_kobo:
        view._provider_labels["kobo"] = "Kobo"
    view._provider_ids = [""] + (["kobo"] if with_kobo else [])
    selected = [1 if with_kobo else 0]

    class DropDown:
        def get_selected(self):
            return selected[0]

        def set_selected(self, value):
            selected[0] = value

    view._provider = DropDown()
    return view, selected


def test_bad_provider_objects_never_reach_filter_or_public_detail():
    view, selected = view_fixture()
    view._reload_providers((
        ("kobo", "Kobo"),
        (Poison(), "PRIVATE_ID"),
        ("poison-label", Poison()),
        ("bad-control", "PRIVATE" + chr(10) + "SESSION"),
        ("bad-bidi", "PRIVATE" + chr(0x202e) + "ACCOUNT"),
        (UntrustedString("PRIVATE_SOURCE"), "Unsafe"),
        ("untrusted-label", UntrustedString("PRIVATE_PASSWORD")),
        ("bnf", "BnF"),
    ))

    assert view._provider_ids == [
        "", "kobo", "poison-label", "bad-control", "bad-bidi",
        "untrusted-label", "bnf",
    ]
    assert view._provider_model.values == [
        "Tous les catalogues", "Kobo", "poison-label", "bad-control",
        "bad-bidi", "untrusted-label", "BnF",
    ]
    assert view._display_source("bnf") == "BnF"
    assert view._display_source("poison-label") == "poison-label"
    assert selected == [0]
    assert "PRIVATE" not in repr((view._provider_model.values, view._provider_labels))


def test_malformed_shapes_do_not_iterate_keys_or_disrupt_valid_filters():
    view, _ = view_fixture()
    view._reload_providers({"PRIVATE_PROVIDER": "poison"})
    assert view._provider_ids == [""]
    assert view._provider_model.values == ["Tous les catalogues"]

    view._reload_providers((
        {"PRIVATE_LABEL": "kobo"},
        ("kobo", "Kobo", "PRIVATE_EXTRA"),
        "PRIVATE_NAME",
        (["kobo"], "Wrong"),
        ("kobo", "Kobo"),
        ("kobo", "Duplicate"),
        ("bnf", "BnF"),
    ))
    assert view._provider_ids == ["", "kobo", "bnf"]
    assert view._provider_model.values == ["Tous les catalogues", "Kobo", "BnF"]


def test_selected_source_survives_safe_reload_without_cross_provider_switch():
    view, selected = view_fixture(with_kobo=True)
    view._reload_providers((("bnf", "BnF"), ("kobo", "Kobo"), ("extra", "Extra")))
    assert view.selected_provider_id() == "kobo"
    assert view._provider_model.values == [
        "Tous les catalogues", "BnF", "Kobo", "Extra",
    ]
    assert selected == [2]


def test_provider_collection_is_bounded_before_accessing_poisoned_late_rows():
    view, _ = view_fixture()
    rows = [("kobo", "Kobo")] * 129 + [(Poison(), Poison())]
    view._reload_providers(rows)
    assert view._provider_ids == ["", "kobo"]
    assert view._provider_model.values == ["Tous les catalogues", "Kobo"]
