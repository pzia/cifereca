"""#3687: a partial ranking cannot silently unset an attested language filter.

Read-only/headless: no GTK instance, network, user account, rights or rescoring.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from recommendations import UxRecommendationsView


class LanguageControls:
    """Stand-in for MultiSelect: removing a choice unchecks a selection."""

    def __init__(self, selected=("fr",)):
        self.selected = tuple(selected)
        self.choices = ()

    def set_choices(self, *, sources=None, languages=None):
        if languages is not None:
            self.choices = tuple(languages)
            values = {key for key, _label in self.choices}
            self.selected = tuple(key for key in self.selected if key in values)

    def selected_languages(self):
        return self.selected

    def selected_sources(self):
        return ()

    def filter_text(self):
        return ""

    def matches_document(self, _item):
        return True


def ranked(language, *, title="Titre"):
    return NS(
        item={"title": title, "author": "Autrice", "language": language},
        sources=("kobo",), score=80,
    )


def view(selected=("fr",)):
    v = object.__new__(UxRecommendationsView)
    ctl = LanguageControls(selected)
    v._query_controls = ctl
    v._score = NS(get_value=lambda: 0)
    v._busy = False
    return v, ctl


def test_provisional_language_absence_cannot_broaden_selected_filter():
    v, ctl = view()
    v._reload_languages([ranked("fr")])  # Last complete ranking
    assert ctl.choices == (("fr", "Français"),)
    assert ctl.selected_languages() == ("fr",)

    v._busy = True
    v._reload_languages([ranked("en")])  # First provisional head only
    assert ctl.choices == (("en", "Anglais"), ("fr", "Français"))
    assert ctl.selected_languages() == ("fr",)
    assert not v._matches(ranked("en"))
    assert v._matches(ranked("fr"))

    v._busy = False
    v._reload_languages([ranked("en")])  # Final ranking, no French
    assert ctl.choices == (("en", "Anglais"),)
    assert ctl.selected_languages() == ()
    assert v._matches(ranked("en"))


def test_disconnected_or_unattested_language_never_enters_choices():
    v, ctl = view(selected=("private",))
    v._reload_languages([ranked("fr")])
    assert ctl.selected_languages() == ()

    ctl.selected = ("private",)
    v._busy = True
    v._reload_languages([ranked("en")])
    assert ctl.choices == (("en", "Anglais"),)
    assert ctl.selected_languages() == ()


def test_bad_language_adapter_does_not_crash_or_coerce_private_value():
    class Poison:
        def __str__(self):
            raise AssertionError("PRIVATE_TOKEN")

        def __repr__(self):
            raise AssertionError("PRIVATE_TOKEN")

    v, ctl = view()
    v._reload_languages([ranked("fr")])
    v._busy = True
    ctl.selected_languages = lambda: (Poison(),)
    v._reload_languages([ranked("en")])
    assert ctl.choices == (("en", "Anglais"),)

    ctl.selected_languages = lambda: (_ for _ in ()).throw(
        RuntimeError("PRIVATE_TOKEN")
    )
    v._reload_languages([ranked("de")])
    assert ctl.choices == (("de", "Allemand"),)


def test_active_filter_removed_on_profile_reset_not_transferred():
    v, ctl = view()
    v._reload_languages([ranked("fr")])
    ctl.set_choices(languages=())  # Profile switch clears current selection
    v._busy = True
    v._reload_languages([ranked("en")])
    assert ctl.choices == (("en", "Anglais"),)
    assert ctl.selected_languages() == ()
