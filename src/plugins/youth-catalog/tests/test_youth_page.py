from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef
from youth_catalog import YouthBook, YouthAgeRange
from youth_page import (
    FilterState,
    YouthPagePlugin,
    YouthView,
    YouthEnterSequence,
    age_badge,
    age_detail,
    age_text,
    book_ref,
    detail_presentation,
    fetch_bibliography,
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
    assert row.badge == "Intérêt 8–10 ans"
    detail = detail_presentation(book)
    assert detail.title == "Un livre"
    assert detail.links[0].url == "https://example.org/book/42"
    assert detail.sections[0].title == "Âges attestés"
    assert "booktrust" in detail.sections[0].fields[0].value


def test_badges_fit_compact_age_chips_without_hiding_kind():
    cases = [
        (YouthAgeRange(0, 6), "0–6 ans"),
        (YouthAgeRange(8, 12, "interest"), "Intérêt 8–12 ans"),
        (YouthAgeRange(10, 12, "reading"), "Lecture 10–12 ans"),
        (YouthAgeRange(8, 12, "publisher"), "Éditeur 8–12 ans"),
        (YouthAgeRange(8, 12, "inferred"), "Inféré 8–12 ans"),
        (YouthAgeRange(17, None), "17+ ans"),
        (NS(age_min=None, age_max=6, age_kind="recommended"), "≤6 ans"),
        (YouthAgeRange(9, 9), "9 ans"),
        (NS(age_min=None, age_max=None, age_kind="recommended"), "Âge inconnu"),
    ]
    for row, expected in cases:
        assert age_badge(row) == expected
        assert len(age_badge(row)) <= 18


def test_compact_list_badge_keeps_full_source_age_and_provenance_in_detail():
    book = youth_book(age_ranges=(
        YouthAgeRange(10, 12, "reading", "Reading age 10-12", "booktrust", 0.9, ("booktrust",)),
    ))
    row = list_entry(book)
    assert row.badge == "Lecture 10–12 ans"
    assert "Âge de lecture · 10–12 ans" in row.meta
    field = detail_presentation(book).sections[0].fields[0].value
    assert "Reading age 10-12" in field
    assert "source booktrust" in field
    assert "confiance 90 %" in field
    assert list_entry(youth_book(age_ranges=())).badge == "Âge inconnu"


def test_homonymous_editions_display_distinct_verified_isbns_in_list_and_detail():
    same_title = dict(title="Livre miroir", authors=("Même autrice",))
    first = youth_book(
        **same_title,
        identifiers={"isbn": "0-306-40615-2", "source_item_id": "one"},
        age_ranges=(
            YouthAgeRange(5, 7, "interest", "5–7 ans", "booktrust"),
            YouthAgeRange(12, None, "publisher", "À partir de 12 ans", "milan"),
        ),
        provenance=("booktrust", "milan"),
    )
    second = youth_book(
        **same_title,
        record_id="other",
        identifiers={"isbn": "9780140328721", "source_item_id": "two"},
        age_ranges=(YouthAgeRange(8, None, "publisher", "À partir de 8 ans", "milan"),),
        provenance=("milan",),
    )
    rows = (list_entry(first), list_entry(second))
    assert rows[0].title == rows[1].title == "Livre miroir"
    assert rows[0].subtitle == rows[1].subtitle == "Même autrice"
    assert rows[0].meta.startswith("ISBN 9780306406157 ·")
    assert rows[1].meta.startswith("ISBN 9780140328721 ·")
    assert rows[0].badge == "Intérêt 5–7 ans"
    assert rows[1].badge == "Éditeur 8+ ans"
    assert rows[0].key != rows[1].key
    details = [detail_presentation(book) for book in (first, second)]
    fields = [
        {field.id: field.value for section in d.sections for field in section.fields}
        for d in details
    ]
    assert fields[0]["isbn"] == "9780306406157"
    assert fields[1]["isbn"] == "9780140328721"
    assert fields[0]["sources"] == "booktrust, milan"
    assert fields[1]["sources"] == "milan"
    assert len(details[0].sections[0].fields) == 2  # conflicting age attestations preserved
    assert len(details[1].sections[0].fields) == 1


def test_missing_or_invalid_isbn_uses_labeled_source_reference_without_inventing_edition():
    missing = youth_book(
        record_id="source-42", identifiers={"source_item_id": "public-42"},
        source_id="booktrust",
    )
    meta = list_entry(missing).meta
    assert meta.startswith("Référence source booktrust:public-42 ·")
    fields = {f.id: f.value for section in detail_presentation(missing).sections for f in section.fields}
    assert fields["source-reference"] == "booktrust:public-42"
    assert "isbn" not in fields

    invalid = youth_book(
        source_id="milan", record_id="other",
        identifiers={"isbn": "9780306406158"},
    )
    assert list_entry(invalid).meta.startswith("Référence source milan:other ·")
    assert not any(field.id == "isbn" for section in detail_presentation(invalid).sections for field in section.fields)
    neither = youth_book(source_id="", record_id="", identifiers={})
    assert "ISBN " not in list_entry(neither).meta
    assert "Référence source" not in list_entry(neither).meta


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


def test_broken_source_info_is_isolated():
    class Broken:
        def source_info(self):
            raise RuntimeError("broken")
    row = NS(plugin_id="youth.source.broken", service=Broken())
    assert source_info(row) == ("youth.source.broken", "youth.source.broken")


class BibliographySource:
    def __init__(self):
        self.calls = []

    def source_info(self):
        return {"source_id": "openlibrary", "display_name": "Open Library"}

    def lookup_isbn(self, isbn):
        self.calls.append(isbn)
        return ({"record_id": "OL1W"},)


def test_explicit_bibliography_fetch_requires_isbn_and_never_uses_title_search():
    source = BibliographySource()
    rows = fetch_bibliography(source, "0-306-40615-2")
    assert rows == ({"record_id": "OL1W"},)
    assert source.calls == ["9780306406157"]


def test_bibliographic_metadata_is_visible_without_changing_age_source():
    book = youth_book(
        publishers=("Open Publisher",),
        published_year=1999,
        cover_url="https://covers.openlibrary.org/b/id/42-M.jpg",
        bibliography_sources=("openlibrary",),
    )
    row = list_entry(book)
    assert row.thumbnail_url == "https://covers.openlibrary.org/b/id/42-M.jpg"
    detail = detail_presentation(book)
    assert detail.thumbnail_url == "https://covers.openlibrary.org/b/id/42-M.jpg"
    fields = {field.id: field.value for section in detail.sections for field in section.fields}
    assert fields["publishers"] == "Open Publisher"
    assert fields["published-year"] == "1999"
    assert fields["bibliography-sources"] == "openlibrary"
    assert fields["sources"] == "booktrust"


def test_stale_bibliography_completion_is_dropped_after_selection_change():
    view = object.__new__(YouthView)
    tasks, dispatch, completed = Tasks(), [], []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=lambda value: None)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_book = youth_book(record_id="one")
    view.run_bibliography(lambda control: ("ok",), completed.append, ("alice", "booktrust:one"))
    tasks.handles[0].finish()
    view.selected_book = youth_book(record_id="two")
    dispatch.pop()()
    assert completed == []


def test_enter_sequence_is_cache_first_and_remote_only_after_matching_explicit_selection():
    seq = YouthEnterSequence()
    token = ("alice", "youth.source.booktrust", FilterState(query="Forest", age=8))
    assert seq.advance(token=token, query="Forest", source_selected=True) == "local"
    assert seq.advance(token=token, query="Forest", source_selected=True) == "remote"
    assert seq.advance(token=token, query="Forest", source_selected=True) == "already_requested"

    changed_age = ("alice", "youth.source.booktrust", FilterState(query="Forest", age=9))
    assert seq.advance(token=changed_age, query="Forest", source_selected=True) == "local"
    changed_profile = ("bob", "youth.source.booktrust", FilterState(query="Forest", age=9))
    assert seq.advance(token=changed_profile, query="Forest", source_selected=True) == "local"
    seq.reset()
    assert seq.advance(token=changed_profile, query="Forest", source_selected=True) == "local"


def test_enter_sequence_never_fetches_all_sources_or_single_character_query():
    seq = YouthEnterSequence()
    local = ("alice", "", FilterState(query="Forest"))
    assert seq.advance(token=local, query="Forest", source_selected=False) == "local"
    assert seq.advance(token=local, query="Forest", source_selected=False) == "choose_source"
    assert seq.advance(token=local, query="Forest", source_selected=False) == "choose_source"
    short = ("alice", "youth.source.milan", FilterState(query="F"))
    assert seq.advance(token=short, query="F", source_selected=True) == "local"
    assert seq.advance(token=short, query="F", source_selected=True) == "short_query"
    assert seq.advance(token=short, query="F", source_selected=True) == "short_query"


def test_youth_entry_activate_routes_cache_then_one_selected_source_only():
    view = object.__new__(YouthView)
    messages, fetches, renders = [], [], []
    identity, source, query, age = ["alice"], [""], ["Forest"], [8]
    view.enter_sequence = YouthEnterSequence()
    view.identity_id = lambda: identity[0]
    view.selected_source_plugin = lambda: source[0]
    view.filter_state = lambda: FilterState(query=query[0], age=age[0])
    view.render_cached = lambda: renders.append("local")
    view.status = NS(set_label=messages.append)
    view.rows = {"one": NS(title="Forest")}
    view._remote_request = lambda cursor: fetches.append((identity[0], source[0], cursor))

    view.query_activated()
    assert messages[-1].startswith("Recherche en cache : 1 résultat(s).")
    assert len(renders) == 1 and fetches == []
    view.query_activated()
    assert "source Jeunesse précise" in messages[-1]
    assert fetches == []

    source[0] = "youth.source.booktrust"
    view.query_activated()
    assert len(renders) == 2 and fetches == []
    view.query_activated()
    assert fetches == [("alice", "youth.source.booktrust", "")]
    view.query_activated()
    assert len(fetches) == 1
    assert "déjà demandée" in messages[-1]

    age[0] = 9
    view.query_activated()
    assert len(renders) == 3 and len(fetches) == 1
    identity[0] = "bob"
    view.query_activated()
    assert len(renders) == 4 and len(fetches) == 1
    query[0] = "F"
    view.query_activated()
    view.query_activated()
    assert len(fetches) == 1
    assert "au moins 2 caractères" in messages[-1]


def test_youth_remote_submissions_are_idempotent_while_the_same_token_is_pending():
    view = object.__new__(YouthView)
    tasks = Tasks()
    messages, dispatch, completed = [], [], []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=messages.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.booktrust"
    view.filter_state = lambda: FilterState(query="Forest", age=8, themes=())
    token = ("alice", "youth.source.booktrust", "Forest", 8, ())
    view.run_remote(lambda control: {"items": [1]}, completed.append, token)
    view.run_remote(lambda control: {"items": [2]}, completed.append, token)
    assert len(tasks.handles) == 1
    assert messages[-1] == "Cette collecte est déjà en cours."
    tasks.handles[0].finish()
    assert view._remote_running_token == token
    dispatch.pop(0)()
    assert view._remote_running_token is None
    assert completed == [{"items": [1]}]
    view.run_remote(lambda control: {"items": [3]}, completed.append, token)
    assert len(tasks.handles) == 2


def test_youth_text_entry_is_connected_to_gtk_activate():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "youth_page.py").read_text(encoding="utf-8")
    assert 'self.query.connect("activate", lambda *_: self.query_activated())' in source


def test_youth_collected_reports_ingest_outcomes_per_identity_without_leaking_rejections():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    identity = ["alice"]
    messages = []
    view.identity_id = lambda: identity[0]
    view.status = NS(set_label=messages.append)
    view.render_cached = lambda: None

    view.collected(
        {"items": [{"title": "Livre Atlas", "isbn": "9780306406157",
                     "age_label": "8+"}], "next_cursor": "2", "fresh_for_seconds": 60},
        source_id="booktrust", label="BookTrust", append=False,
        source_plugin="youth.source.booktrust", duration_ms=27,
    )
    a = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    assert a["booktrust"]["collection_state"] == "items"
    assert a["booktrust"]["cache_count"] == 1
    assert a["booktrust"]["next_cursor_present"] is True
    assert a["booktrust"]["duration_ms"] == 27
    assert view.next_cursor == "2"

    view.collected(
        {"items": [{"title": ""}], "fresh_for_seconds": 30},
        source_id="booktrust", label="BookTrust", append=False,
        source_plugin="youth.source.booktrust",
    )
    a = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    assert a["booktrust"]["collection_state"] == "invalid"
    assert a["booktrust"]["rejected"] == 1
    assert a["booktrust"]["cache_count"] == 1
    assert "Livre Atlas" not in repr(a["booktrust"])
    identity[0] = "bob"
    b = {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}
    assert b["booktrust"]["collection_state"] == "not_tested"


def test_youth_explicit_remote_exception_records_only_typed_failure_and_keeps_cache():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    tasks, dispatch, messages = Tasks(), [], []
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "milan", [{"title": "Atlas ancien", "isbn": "9780306406157"}])
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=messages.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.milan"
    view.selected_source = lambda: NS(plugin_id="youth.source.milan", service=NS(
        source_info=lambda: {"source_id": "milan", "display_name": "Milan"}
    ))
    view.filter_state = lambda: FilterState(query="Atlas", age=8, themes=())

    def broken(control):
        raise TimeoutError("secret=https://private.example/token")

    token = ("alice", "youth.source.milan", "Atlas", 8, ())
    view.run_remote(broken, lambda raw: None, token)
    tasks.handles[0].finish()
    dispatch.pop()()
    row = {x["source_id"]: x for x in view.catalog.source_diagnostics("alice")}["milan"]
    assert row["collection_state"] == "timeout"
    assert row["error_type"] == "TimeoutError"
    assert row["cache_count"] == 1
    assert "private.example" not in repr(row)
    assert messages[-1] == "Échec de la source. Le cache précédent est conservé."
    assert {x["source_id"]: x for x in view.catalog.source_diagnostics("bob")}["milan"]["collection_state"] == "not_tested"
