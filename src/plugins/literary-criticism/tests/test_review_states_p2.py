"""#763: deterministic literary review empty-state and validation contracts."""
from types import SimpleNamespace as NS

import pytest
from criticism_page import ReviewView, review_view_state, validate_review_filters

@pytest.mark.parametrize("book,sources,valid,results,expected", [
    (False, False, True, False, "no_book"),
    (True, False, True, False, "no_sources"),
    (True, True, False, False, "invalid_filters"),
    (True, True, True, False, "no_matches"),
    (True, True, True, True, "results"),
])
def test_each_empty_state_is_distinct(book,sources,valid,results,expected):
    assert review_view_state(book_present=book, sources_present=sources,
                             filters_valid=valid, matches_present=results) == expected


@pytest.mark.parametrize("language,since", [
    ("fr","2026-10-11"), ("en",""), ("fr-FR","2026-02-28"), ("",""),
])
def test_valid_filter_dates_are_accepted(language,since):
    assert validate_review_filters(language,since) == ""


@pytest.mark.parametrize("language,since", [
    ("fr","2026-02-30"), ("fr","2026-2-3"), ("en","2026-13-01"),
    ("french","2026-10-01"), ("fr;drop","2026-10-01"),
])
def test_invalid_filter_values_explain_rejection(language,since):
    assert validate_review_filters(language,since)


class Widget:
    def __init__(self,text=""):
        self.text=text
        self.label=""
        self.children=[]
        self.visible=True
        self.sensitive=True
        self.tooltip=""
    def get_text(self):return self.text
    def set_text(self,value):self.text=value
    def set_label(self,value):self.label=value
    def set_sensitive(self,value):self.sensitive=value
    def set_visible(self,value):self.visible=value
    def set_tooltip_text(self,value):self.tooltip=value
    def append(self,child):self.children.append(child)
    def get_first_child(self):return self.children[0] if self.children else None
    def remove(self,child):self.children.remove(child)


class FakeGtk:
    class Orientation:
        VERTICAL = "vertical"
    @staticmethod
    def Label(**kwargs):
        return NS(**kwargs)
    @staticmethod
    def Box(**kwargs):
        return Widget()


def setup_view(*, book=None, sources=()):
    view=object.__new__(ReviewView)
    view.book=book
    view.heading=Widget()
    view.results=Widget()
    view.source=Widget()
    view.language=Widget()
    view.since=Widget()
    view.back_button=Widget()
    view.choose_button=Widget()
    view.refresh_button=Widget()
    view.Gtk=FakeGtk
    calls=[]
    view.plugin=NS(store=NS(
        sources=lambda _context: calls.append("catalogue-local") or tuple(sources),
        subscriptions={},
        index=NS(matches_for=lambda book:()),
    ),context=NS())
    return view,calls


def test_no_book_has_no_false_empty_search_and_no_refresh():
    view,calls=setup_view(book=None,sources=())
    view.render()
    assert view.heading.label.startswith("Aucun livre")
    assert view.back_button.visible is False
    assert view.choose_button.visible is True
    assert view.refresh_button.sensitive is False
    assert not view.source.sensitive and not view.since.sensitive
    assert len(view.results.children)==1
    assert "Choisissez un livre" in view.results.children[0].label
    assert "Aucune critique associée" not in view.results.children[0].label
    assert calls == ["catalogue-local"]


def test_invalid_calendar_day_is_not_reported_as_empty_cache():
    view,_=setup_view(book=NS(title="Livre"),sources=(object(),))
    view.since.text="2026-02-30"
    view.render()
    assert view.back_button.visible is True
    assert view.choose_button.visible is False
    assert "Date invalide" in view.results.children[0].label
    assert view.refresh_button.sensitive is True


def test_absent_sources_offer_explicit_management_without_implicit_http():
    view,_=setup_view(book=NS(title="Livre"),sources=())
    view.button=lambda label,fn: NS(label=label,action=fn)
    view.render()
    assert view.refresh_button.sensitive is False
    assert len(view.results.children)==2
    assert "Aucune source" in view.results.children[0].label
    assert view.results.children[1].label=="Ajouter un flux"
