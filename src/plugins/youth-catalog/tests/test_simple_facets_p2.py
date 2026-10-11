"""#1029: cached facets and honest published-age threshold."""
from types import SimpleNamespace as NS
import inspect
from youth_catalog import YouthCatalogIndex, YouthCatalogPlugin
from youth_page import YouthView, FilterState, age_text


def row(title, age="", **extra):
    return {"title": title, **({"age_label": age} if age else {}), **extra}


def test_published_minimum_differs_from_reader_suitability():
    index = YouthCatalogIndex()
    index.ingest("alice", "publisher", [
        row("8–12", "8–12 ans"), row("10–12", "10–12 ans"),
        row("12+", "12+"), row("Unknown"),
        row("Estimated", age_ranges=[{"age_min": 14, "kind": "recommended", "inferred": True}]),
    ])
    assert [r.title for r in index.search("alice", minimum_age=10)] == ["10–12", "12+"]
    assert [r.title for r in index.search("alice", age=10)] == ["8–12", "10–12"]
    assert [r.title for r in index.search("alice", minimum_age=12)] == ["12+"]
    assert [r.title for r in index.search("alice", minimum_age=10, include_inferred=True)] == ["10–12", "12+", "Estimated"]
    assert index.search("alice", minimum_age=18) == ()


class Dropdown:
    def __init__(self):
        self.selected, self.model = 0, []
    def get_selected(self):
        return self.selected
    def set_selected(self, value):
        self.selected = value
    def set_model(self, value):
        self.model = value


def test_facet_options_are_from_own_cache_only():
    catalog = YouthCatalogPlugin()
    catalog.ingest("alice", "milan", [
        row("A", "10+", language="fr", format=["epub"], book_type="fiction", themes=["mer"]),
        row("B", "8+", language="en", format=["pdf"], themes=["science"]),
    ])
    catalog.ingest("bob", "secret", [
        row("Secret", "7+", language="es", format=["kfx"], themes=["sensible"]),
    ])
    view = object.__new__(YouthView)
    view.catalog = catalog
    current = ["alice"]
    view.identity_id = lambda: current[0]
    view.Gtk = NS(StringList=NS(new=lambda values: list(values)))
    view._facet_values = {k: ("",) for k in ("language", "format", "book_type", "themes")}
    view._facet_labels = {k: "Toutes" for k in view._facet_values}
    for key in view._facet_values:
        setattr(view, key, Dropdown())
    view._refresh_facets()
    assert view.language.model == ["Toutes", "en", "fr"]
    assert view.themes.model == ["Toutes", "mer", "science"]
    view.language.set_selected(2)
    assert view._facet_value("language") == "fr"
    current[0] = "bob"
    view._refresh_facets()
    assert view.language.model == ["Toutes", "es"]
    assert view._facet_value("language") == ""


def test_ui_moves_to_query_plus_optional_facets_and_hides_technical_age():
    code = inspect.getsource(YouthView.__init__)
    assert "self.advanced_filters = Gtk.Expander" in code
    assert "self.query = Gtk.Entry(" in code
    for old in ("self.age_kind = Gtk.DropDown", "self.inferred = Gtk.CheckButton",
                "self.language = Gtk.Entry(", "self.themes = Gtk.Entry(",
                "self.book_type = Gtk.Entry(", "self.format = Gtk.Entry("):
        assert old not in code
    assert "12+ convient" in code


def test_filter_state_sets_minimum_but_not_suitability():
    view = object.__new__(YouthView)
    view.query = NS(get_text=lambda: "jeunesse")
    view.age, view.source, view.sources = Dropdown(), Dropdown(), ()
    view.age.set_selected(11)
    view._facet_values = {k: ("",) for k in ("language", "format", "book_type", "themes")}
    for key in view._facet_values:
        setattr(view, key, Dropdown())
    state = view.filter_state()
    assert state == FilterState(query="jeunesse", minimum_age=10)
    assert state.age_kind == "" and state.include_inferred is False
