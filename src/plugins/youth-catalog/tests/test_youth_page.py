from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from youth_catalog import YouthBook, YouthAgeRange
from youth_page import (
    FilterState,
    YouthPagePlugin,
    YouthView,
    age_detail,
    age_text,
    book_ref,
    detail_presentation,
    fetch_source_page,
    list_entry,
    page_parts,
    parse_themes,
    public_url,
    source_info,
)


def youth_book(**changes):
    values = {
        "record_id": "42",
        "source_id": "booktrust",
        "title": "Un livre",
        "authors": ("Autrice",),
        "identifiers": {"isbn": "9780306406157", "provider": "booktrust", "source_item_id": "42"},
        "notice_url": "https://example.org/book/42",
        "friendly_url": "https://example.org/book/42",
        "age_ranges": (YouthAgeRange(8, 10, "interest", "Interest age 8-10", "booktrust", 0.95, ("booktrust",)),),
        "language": "fr",
        "formats": ("epub",),
        "book_type": "fiction",
        "themes": ("aventure",),
        "provenance": ("booktrust",),
    }
    values.update(changes)
    return YouthBook(**values)


def test_age_text_and_detail_keep_kind_source_label_and_confidence():
    row = YouthAgeRange(8, 10, "interest", "Interest age 8-10", "booktrust", 0.95, ("booktrust",))
    assert age_text(row) == "Âge d’intérêt · 8–10 ans"
    detail = age_detail(row)
    assert "source booktrust" in detail
    assert "Interest age 8-10" in detail
    assert "95 %" in detail


def test_upper_and_open_age_labels_are_readable():
    assert age_text(YouthAgeRange(0, 6)) == "Âge recommandé · jusqu’à 6 ans"
    assert age_text(YouthAgeRange(9, None)) == "Âge recommandé · à partir de 9 ans"


def test_list_and_detail_expose_age_and_public_notice():
    book = youth_book()
    row = list_entry(book)
    assert row.title == "Un livre"
    assert "8–10 ans" in row.meta
    assert row.badge == "Âge d’intérêt · 8–10 ans"
    detail = detail_presentation(book)
    assert detail.title == "Un livre"
    assert detail.links[0].url == "https://example.org/book/42"
    assert detail.sections[0].title == "Âges attestés"
    assert "booktrust" in detail.sections[0].fields[0].value


def test_document_link_rejects_credentials_and_non_http():
    assert public_url("https://example.org/book")
    assert not public_url("https://user:secret@example.org/book")
    assert not public_url("file:///tmp/book")


def test_book_ref_preserves_stable_identifiers_and_youth_metadata():
    ref = book_ref(youth_book())
    assert isinstance(ref, BookRef)
    assert ref.book_id == "42"
    assert ref.identifiers["isbn"] == "9780306406157"
    assert ref.metadata["youth_age_ranges"][0]["age_kind"] == "interest"


def test_page_parts_binds_pagination_and_freshness():
    rows, cursor, fresh = page_parts({"items": [{"title": "A"}], "next_cursor": "2", "fresh_for_seconds": "60"})
    assert rows == ({"title": "A"},)
    assert cursor == "2"
    assert fresh == 60
    assert page_parts({"items": "bad"}) == ((), "", 0)


class BrowseSource:
    def __init__(self):
        self.calls = []

    def source_info(self):
        return {"source_id": "special", "display_name": "Special"}

    def browse(self, **kwargs):
        self.calls.append(kwargs)
        return {"items": []}


class SearchSource:
    def __init__(self):
        self.calls = []

    def source_info(self):
        return {"source_id": "plain", "display_name": "Plain"}

    def search(self, query, *, cursor=None, limit=50):
        self.calls.append((query, cursor, limit))
        return {"items": []}


def test_explicit_source_fetch_prefers_age_aware_browse_extension():
    source = BrowseSource()
    fetch_source_page(source, "dragons", age=9, themes=("aventure",), cursor="2", limit=500)
    assert source.calls == [{
        "age": 9,
        "themes": ("aventure",),
        "text_filter": "dragons",
        "cursor": "2",
        "limit": 200,
    }]


def test_explicit_source_fetch_falls_back_to_contract_search():
    source = SearchSource()
    fetch_source_page(source, "dragons", age=9, themes=("aventure",), cursor=None, limit=100)
    assert source.calls == [("dragons", None, 100)]


def test_source_info_and_theme_normalization_are_stable():
    row = NS(plugin_id="youth.source.special", service=BrowseSource())
    assert source_info(row) == ("special", "Special")
    assert parse_themes("Aventure, Mer; aventure") == ("aventure", "mer")


def test_context_action_only_navigates_and_never_collects():
    plugin = YouthPagePlugin().activate(NS())
    selected = BookRef("b", "Titre")
    navigations = []
    plugin.pages()[0].actions[0].callback(NS(
        action_context=lambda: NS(book=selected),
        navigate=navigations.append,
    ))
    assert plugin.pending is selected
    assert navigations == ["youth"]


class Handle:
    def __init__(self, operation):
        self.operation, self.callback, self.value, self.error = operation, None, None, None
        self.control = NS(cancelled=lambda: False)
        self.cancelled = False

    def cancel(self):
        self.cancelled = True

    def result(self):
        if self.error:
            raise self.error
        return self.value

    def add_done_callback(self, callback):
        self.callback = callback

    def finish(self):
        try:
            self.value = self.operation(self.control)
        except Exception as exc:
            self.error = exc
        self.callback(self)


class Tasks:
    def __init__(self):
        self.handles = []

    def submit(self, operation, **kwargs):
        handle = Handle(operation)
        self.handles.append(handle)
        return handle


def test_stale_remote_completion_cannot_mutate_current_view():
    view = object.__new__(YouthView)
    tasks, dispatch, completed = Tasks(), [], []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=lambda value: None)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "source.one"
    view.filter_state = lambda: FilterState(query="new", age=9, themes=("sea",))
    view.run_remote(lambda control: {"items": [1]}, completed.append, ("alice", "source.one", "old", 9, ("sea",)))
    tasks.handles[0].finish()
    dispatch.pop()()
    assert completed == []
