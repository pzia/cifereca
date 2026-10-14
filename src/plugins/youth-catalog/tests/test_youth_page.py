import pytest
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
    source_diagnostic_presentation,
)


def youth_book(**changes):
    values = {
        "record_id": "42",
        "source_id": "ecole-loisirs",
        "title": "Un livre",
        "authors": ("Autrice",),
        "identifiers": {"isbn": "9780306406157", "provider": "ecole-loisirs", "source_item_id": "42"},
        "notice_url": "https://example.org/book/42",
        "friendly_url": "https://example.org/book/42",
        "age_ranges": (YouthAgeRange(8, 10, "interest", "Interest age 8-10", "ecole-loisirs", 0.95, ("ecole-loisirs",)),),
        "language": "fr",
        "formats": ("epub",),
        "book_type": "fiction",
        "themes": ("aventure",),
        "provenance": ("ecole-loisirs",),
    }
    values.update(changes)
    return YouthBook(**values)


def test_age_text_and_detail_keep_kind_source_label_and_confidence():
    row = YouthAgeRange(8, 10, "interest", "Interest age 8-10", "ecole-loisirs", 0.95, ("ecole-loisirs",))
    assert age_text(row) == "Âge d’intérêt · 8–10 ans"
    detail = age_detail(row)
    assert "source ecole-loisirs" in detail
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
    assert "ecole-loisirs" in detail.sections[0].fields[0].value


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
        YouthAgeRange(10, 12, "reading", "Reading age 10-12", "ecole-loisirs", 0.9, ("ecole-loisirs",)),
    ))
    row = list_entry(book)
    assert row.badge == "Lecture 10–12 ans"
    assert "Âge de lecture · 10–12 ans" in row.meta
    field = detail_presentation(book).sections[0].fields[0].value
    assert "Reading age 10-12" in field
    assert "source ecole-loisirs" in field
    assert "confiance 90 %" in field
    assert list_entry(youth_book(age_ranges=())).badge == "Âge inconnu"


def test_homonymous_editions_display_distinct_verified_isbns_in_list_and_detail():
    same_title = dict(title="Livre miroir", authors=("Même autrice",))
    first = youth_book(
        **same_title,
        identifiers={"isbn": "0-306-40615-2", "source_item_id": "one"},
        age_ranges=(
            YouthAgeRange(5, 7, "interest", "5–7 ans", "ecole-loisirs"),
            YouthAgeRange(12, None, "publisher", "À partir de 12 ans", "milan"),
        ),
        provenance=("ecole-loisirs", "milan"),
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
    assert [f.value for section in details[0].sections for f in section.fields
            if f.id.startswith("source-")] == ["ecole-loisirs", "milan"]
    assert [f.value for section in details[1].sections for f in section.fields
            if f.id.startswith("source-")] == ["milan"]
    assert len(details[0].sections[0].fields) == 2  # conflicting age attestations preserved
    assert len(details[1].sections[0].fields) == 1


def test_missing_or_invalid_isbn_uses_labeled_source_reference_without_inventing_edition():
    missing = youth_book(
        record_id="source-42", identifiers={"source_item_id": "public-42"},
        source_id="ecole-loisirs",
    )
    meta = list_entry(missing).meta
    assert meta.startswith("Référence source ecole-loisirs:public-42 ·")
    fields = {f.id: f.value for section in detail_presentation(missing).sections for f in section.fields}
    assert fields["source-reference"] == "ecole-loisirs:public-42"
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
    from youth_catalog import InvalidYouthSourcePage

    with pytest.raises(InvalidYouthSourcePage):
        page_parts({"items": "bad"})


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
    assert fields["publisher-0"] == "Open Publisher"
    assert fields["published-year"] == "1999"
    assert fields["bibliography-source-0"] == "openlibrary"
    assert fields["source-0"] == "ecole-loisirs"


def test_stale_bibliography_completion_is_dropped_after_selection_change():
    view = object.__new__(YouthView)
    tasks, dispatch, completed = Tasks(), [], []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=lambda value: None)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_book = youth_book(record_id="one")
    view.run_bibliography(lambda control: ("ok",), completed.append, ("alice", "ecole-loisirs:one"))
    tasks.handles[0].finish()
    view.selected_book = youth_book(record_id="two")
    dispatch.pop()()
    assert completed == []


def test_enter_sequence_is_cache_first_and_remote_only_after_matching_explicit_selection():
    seq = YouthEnterSequence()
    token = ("alice", "youth.source.ecole-loisirs", FilterState(query="Forest", age=8))
    assert seq.advance(token=token, query="Forest", source_selected=True) == "local"
    assert seq.advance(token=token, query="Forest", source_selected=True) == "remote"
    assert seq.advance(token=token, query="Forest", source_selected=True) == "already_requested"

    changed_age = ("alice", "youth.source.ecole-loisirs", FilterState(query="Forest", age=9))
    assert seq.advance(token=changed_age, query="Forest", source_selected=True) == "local"
    changed_profile = ("bob", "youth.source.ecole-loisirs", FilterState(query="Forest", age=9))
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

    source[0] = "youth.source.ecole-loisirs"
    view.query_activated()
    assert len(renders) == 2 and fetches == []
    view.query_activated()
    assert fetches == [("alice", "youth.source.ecole-loisirs", "")]
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
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.filter_state = lambda: FilterState(query="Forest", age=8, themes=())
    token = ("alice", "youth.source.ecole-loisirs", "Forest", 8, ())
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
        source_id="ecole-loisirs", label="École des loisirs", append=False,
        source_plugin="youth.source.ecole-loisirs", duration_ms=27, requested_limit=5,
    )
    a = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    assert a["ecole-loisirs"]["collection_state"] == "items"
    assert a["ecole-loisirs"]["cache_count"] == 1
    assert a["ecole-loisirs"]["next_cursor_present"] is True
    assert a["ecole-loisirs"]["duration_ms"] == 27
    assert a["ecole-loisirs"]["received_count"] == 1
    assert a["ecole-loisirs"]["requested_limit"] == 5
    assert view.next_cursor == "2"

    view.collected(
        {"items": [{"title": ""}], "fresh_for_seconds": 30},
        source_id="ecole-loisirs", label="École des loisirs", append=False,
        source_plugin="youth.source.ecole-loisirs",
    )
    a = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    assert a["ecole-loisirs"]["collection_state"] == "invalid"
    assert a["ecole-loisirs"]["rejected"] == 1
    assert a["ecole-loisirs"]["received_count"] == 1
    assert a["ecole-loisirs"]["requested_limit"] is None
    assert a["ecole-loisirs"]["rejection_categories"] == {"missing_title": 1}
    assert a["ecole-loisirs"]["cache_count"] == 1
    assert "Livre Atlas" not in repr(a["ecole-loisirs"])
    identity[0] = "bob"
    b = {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}
    assert b["ecole-loisirs"]["collection_state"] == "not_tested"


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



def test_youth_remote_typed_http_error_preserves_cache_and_renders_http_status():
    """Publisher RuntimeError.http_status is honored, never parsed as raw text."""
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    tasks, dispatch, messages = Tasks(), [], []
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{
        "title": "Private title", "isbn": "9780306406157",
        "age_label": "8+",
    }])
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=messages.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.selected_source = lambda: NS(
        plugin_id="youth.source.ecole-loisirs",
        service=NS(source_info=lambda: {
            "source_id": "ecole-loisirs", "display_name": "École des loisirs",
        }),
    )
    view.filter_state = lambda: FilterState(query="private", age=8, themes=())

    def blocked(control):
        error = RuntimeError("École des loisirs HTTP 503")
        error.http_status = 503
        raise error

    token = ("alice", "youth.source.ecole-loisirs", "private", 8, ())
    view.run_remote(blocked, lambda raw: None, token)
    tasks.handles[0].finish()
    dispatch.pop()()
    rows = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    current = rows["ecole-loisirs"]
    assert current["collection_state"] == "http_error"
    assert current["http_status"] == 503
    assert current["error_type"] == "RuntimeError"
    assert current["cache_count"] == 1
    summary, lines = source_diagnostic_presentation(tuple(rows.values()))
    assert any("erreur HTTP 503" in line for line in lines)
    assert "Private title" not in repr(current)
    assert messages[-1] == "Échec de la source. Le cache précédent est conservé."
    bob = {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}
    assert bob["ecole-loisirs"]["collection_state"] == "not_tested"


def test_source_diagnostic_presentation_distinguishes_plugin_state_from_last_collection():
    rows = (
        {"source_id": "milan", "label": "Éditions Milan", "family": "age",
         "plugin_state": "ready", "enabled": True, "collection_state": "not_tested",
         "cache_count": 0},
        {"source_id": "ecole-loisirs", "label": "École des loisirs", "family": "age",
         "plugin_state": "ready", "enabled": True, "collection_state": "http_error",
         "http_status": 503, "cache_count": 2, "rejected": 1,
         "collection_stage": "search", "error_type": "HTTPStatusError",
         "attempted_at_utc": "2026-10-10T03:20:00+00:00",
         "fresh_until": "2026-10-11T03:00:00+00:00"},
        {"source_id": "youth.bibliography.openlibrary", "label": "Open Library",
         "family": "bibliography", "plugin_state": "ready", "enabled": True,
         "collection_state": "not_tested"},
    )
    summary, lines = source_diagnostic_presentation(rows)
    assert "2 source(s) d’âge · 1 bibliographie" in summary
    assert "1 dernier(s) essai(s) en échec" in summary
    assert len(lines) == 3
    assert "non testée" in lines[0] and "activée" in lines[0]
    assert "erreur HTTP 503" in lines[1]
    assert "étape : search" in lines[1]
    assert "exception : HTTPStatusError" in lines[1]
    assert "cache : 2 notice(s)" in lines[1]
    assert "(conservé)" in lines[1]
    assert "essai UTC : 2026-10-10 03:20" in lines[1]
    assert "fraîcheur annoncée" in lines[1] and "1 rejet(s)" in lines[1]
    assert "bibliographie uniquement" in lines[2] and "pas de preuve d’âge" in lines[2]
    assert "aucun" not in summary.casefold()
    assert source_diagnostic_presentation(())[0] == "Aucun profil actif"



def test_youth_diagnostic_presentation_shows_bounded_receipts_and_safe_rejection_reasons():
    row = {
        "source_id": "ecole-loisirs", "label": "École des loisirs",
        "family": "age", "plugin_state": "ready", "enabled": True,
        "collection_state": "invalid", "cache_count": 2,
        "received_count": 4, "requested_limit": 10,
        "accepted": 1, "rejected": 3,
        "rejection_categories": {
            "missing_title": 2, "other": 1,
            "raw_secret?https://private.example/token": 42,
        },
    }
    headline, lines = source_diagnostic_presentation((row,))
    assert "1 dernier(s) essai(s) en échec" in headline
    assert "reçues : 4 · limite demandée : 10 (pas un total de notices disponibles)" in lines[0]
    assert "3 rejet(s) (2 sans titre, 1 autres)" in lines[0]
    assert "cache : 2 notice(s)" in lines[0]
    assert "private.example" not in repr(lines)
    row["received_count"] = None
    row["requested_limit"] = None
    row["rejection_categories"] = {}
    _, unknown = source_diagnostic_presentation((row,))
    assert "reçues :" not in unknown[0]
    assert "demandées" not in unknown[0]


def test_youth_view_local_diagnostics_uses_public_inventory_and_keeps_identities_separate():
    from youth_catalog import YouthCatalogPlugin

    class FakeLabel:
        def __init__(self, *, label="", wrap=False, xalign=0):
            self.text = label
            self.owner = None
        def get_next_sibling(self):
            siblings = self.owner.children
            pos = siblings.index(self)
            return siblings[pos + 1] if pos + 1 < len(siblings) else None

    class FakeBox:
        def __init__(self):
            self.children = []
        def get_first_child(self):
            return self.children[0] if self.children else None
        def append(self, child):
            child.owner = self
            self.children.append(child)
        def remove(self, child):
            self.children.remove(child)

    class FakeExpander:
        def __init__(self):
            self.label = ""
        def set_label(self, label):
            self.label = label

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{"title": "Secret book", "age_label": "8+"}])
    view.catalog.record_collection("alice", "ecole-loisirs", state="items", accepted=1)
    view.catalog.record_collection("bob", "ecole-loisirs", state="timeout", error_type="ReadTimeout")
    selected_identity = ["alice"]
    view.identity_id = lambda: selected_identity[0]
    view.Gtk = NS(Label=FakeLabel)
    view.diagnostic_rows_box = FakeBox()
    view.diagnostic_expander = FakeExpander()
    broker_reads = []
    def broker(name):
        broker_reads.append(name)
        assert name == "diagnostics"
        return NS(inventory=lambda: (
            {"type": "youth.source", "plugin_id": "youth.source.ecole-loisirs",
             "state": "ready", "enabled": True},
        ))
    view.plugin = NS(context=NS(broker=broker))
    assert view.refresh_source_diagnostics() is False
    alice_text = " ".join(r.text for r in view.diagnostic_rows_box.children)
    assert "École des loisirs · activée · dernier essai : collecte avec notices" in alice_text
    assert "cache : 1 notice(s)" in alice_text
    assert "Secret book" not in alice_text
    assert len(view.diagnostic_rows_box.children) == 3
    assert "2 source(s) d’âge" in view.diagnostic_expander.label

    selected_identity[0] = "bob"
    assert view.refresh_source_diagnostics() is False
    bob_text = " ".join(r.text for r in view.diagnostic_rows_box.children)
    assert "délai dépassé" in bob_text and "cache : 0 notice(s)" in bob_text
    assert "cache : 1 notice(s)" not in bob_text
    assert "exception : ReadTimeout" in bob_text
    assert "private." not in bob_text
    assert len(view.diagnostic_rows_box.children) == 3
    assert broker_reads == ["diagnostics", "diagnostics"]


def test_source_diagnostics_renderer_handles_missing_optional_broker_without_network():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.identity_id = lambda: "alice"
    view.Gtk = NS(Label=lambda **kwargs: NS(**kwargs))
    view.diagnostic_expander = NS(set_label=lambda x: None)
    view.diagnostic_rows_box = NS(get_first_child=lambda: None, append=lambda x: None)
    calls = []
    view.plugin = NS(context=NS(broker=lambda name: (calls.append(name), None)[1]))
    assert view.refresh_source_diagnostics() is False
    assert calls == ["diagnostics"]


def test_youth_ui_records_per_call_http_count_without_consulting_shared_snapshot():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.identity_id = lambda: "alice"
    view.status = NS(set_label=lambda *_: None)
    view.render_cached = lambda: None
    view.refresh_source_diagnostics = lambda: None
    view.collected(
        {"items": [{"title": "Private book"}], "collection_request_count": 4},
        source_id="milan", label="Milan", append=False,
        source_plugin="youth.source.milan", requested_limit=8,
    )
    alice = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}
    assert alice["milan"]["request_count"] == 4
    assert alice["milan"]["requested_limit"] == 8
    summary, lines = source_diagnostic_presentation(tuple(alice.values()))
    assert "requêtes HTTP : 4" in " ".join(lines)
    assert "Private book" not in repr(alice)
    assert {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}["milan"]["request_count"] is None


def test_youth_explicit_pre_request_cancel_has_zero_http_and_keeps_cache():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{"title": "Previous private title"}])
    tasks, dispatch, messages, public_requests = Tasks(), [], [], []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=messages.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.selected_source = lambda: NS(plugin_id="youth.source.ecole-loisirs", service=NS(
        source_info=lambda: {"source_id": "ecole-loisirs", "display_name": "École des loisirs"},
        browse=lambda **kwargs: public_requests.append(kwargs),
    ))
    view.filter_state = lambda: FilterState(query="Atlas", age=8, themes=())
    view.refresh_source_diagnostics = lambda: None

    view._remote_request("")
    assert len(tasks.handles) == 1
    tasks.handles[0].control = NS(cancelled=lambda: True)
    tasks.handles[0].finish()
    dispatch.pop()()

    assert public_requests == []
    row = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert row["collection_state"] == "cancelled"
    assert row["request_count"] == 0
    assert row["received_count"] is None
    assert row["cache_count"] == 1
    assert "Previous private title" not in repr(row)
    assert "youth.source.ecole-loisirs" not in str(public_requests)


def test_unexpected_empty_adapter_response_is_invalid_not_cancelled():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    tasks, dispatch = Tasks(), []
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda callback: dispatch.append(callback))
    view.status = NS(set_label=lambda *_: None)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.selected_source = lambda: NS(plugin_id="youth.source.ecole-loisirs", service=NS(
        source_info=lambda: {"source_id": "ecole-loisirs"},
    ))
    view.filter_state = lambda: FilterState(query="Atlas", age=8, themes=())
    view.refresh_source_diagnostics = lambda: None
    token = ("alice", "youth.source.ecole-loisirs", "Atlas", 8, ())
    view.run_remote(lambda control: None, lambda raw: 1 / 0, token)
    tasks.handles[0].finish()
    dispatch.pop()()
    row = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert row["collection_state"] == "invalid"
    assert row["request_count"] is None


def test_youth_ui_malformed_page_preserves_cached_records_and_pagination():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{"title": "Previous private book"}])
    view.identity_id = lambda: "alice"
    notices, status, refreshed = [], [], []
    view.render_cached = lambda: notices.append("render")
    view.refresh_source_diagnostics = lambda: refreshed.append("refresh")
    view.status = NS(set_label=status.append)
    view.next_cursor, view.next_source_plugin = "2", "youth.source.ecole-loisirs"

    view.collected(
        {"items": "https://private.example/token", "collection_request_count": 1},
        source_id="ecole-loisirs", label="École des loisirs", append=False,
        source_plugin="youth.source.ecole-loisirs", duration_ms=23, requested_limit=8,
    )
    diag = {x["source_id"]: x for x in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert diag["collection_state"] == "invalid"
    assert diag["collection_stage"] == "parsing"
    assert diag["request_count"] == 1
    assert diag["cache_count"] == 1
    assert diag["received_count"] is None
    assert view.next_cursor == "2"
    assert view.next_source_plugin == "youth.source.ecole-loisirs"
    assert len(notices) == 0
    assert refreshed == ["refresh"]
    assert "cache précédent est conservé" in status[-1]
    assert "private.example" not in repr(diag)
    assert "Previous private book" not in repr(diag)
    assert {x["source_id"]: x for x in view.catalog.source_diagnostics("bob")}["ecole-loisirs"]["collection_state"] == "not_tested"


def test_youth_ui_empty_page_is_not_mistaken_for_invalid_structure():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.identity_id = lambda: "alice"
    statuses = []
    view.status = NS(set_label=statuses.append)
    view.render_cached = lambda: None
    view.refresh_source_diagnostics = lambda: None
    view.next_cursor = "1"
    view.next_source_plugin = "youth.source.ecole-loisirs"
    view.collected(
        {"items": [], "next_cursor": "", "collection_request_count": 1},
        source_id="ecole-loisirs", label="École des loisirs", append=False,
        source_plugin="youth.source.ecole-loisirs",
    )
    current = {x["source_id"]: x for x in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert current["collection_state"] == "empty_unverified"
    assert current["received_count"] == 0
    assert current["request_count"] == 1
    assert view.next_cursor == ""


def test_fetch_source_cancellation_after_http_disallows_ingest_and_keeps_exact_count():
    """A running synchronous request may finish: its result must not be ingested."""
    from youth_page import YouthSourceCancelled
    from youth_catalog import collection_failure_diagnostic

    state, calls = {"cancelled": False}, []
    class Source:
        def browse(self, **kwargs):
            calls.append(kwargs)
            state["cancelled"] = True  # cancellation while source is executing
            return {
                "items": [{"title": "private book", "isbn13": "9780306406157"}],
                "collection_request_count": 2,
            }

    with pytest.raises(YouthSourceCancelled, match="collection cancelled") as exc:
        fetch_source_page(
            Source(), "private-query", age=8, themes=(), cursor=None,
            cancelled=lambda: state["cancelled"],
        )
    assert len(calls) == 1
    assert exc.value.collection_request_count == 2
    assert collection_failure_diagnostic(exc.value) == {
        "state": "cancelled", "error_type": "YouthSourceCancelled",
        "http_status": None, "request_count": 2,
    }
    assert "private-query" not in str(exc.value)
    assert "private book" not in str(exc.value)


def test_fetch_source_cancellation_before_request_has_proven_zero_http():
    from youth_page import YouthSourceCancelled
    calls = []
    class Source:
        def search(self, query, *, cursor, limit):
            calls.append(query)
            return {"items": ()}

    with pytest.raises(YouthSourceCancelled) as exc:
        fetch_source_page(
            Source(), "secret query", age=None, themes=(), cursor=None,
            cancelled=lambda: True,
        )
    assert exc.value.collection_request_count == 0
    assert calls == []


def test_fetch_source_cancellation_after_search_without_count_remains_unknown():
    from youth_page import YouthSourceCancelled

    state, calls = {"cancelled": False}, []
    class SearchOnly:
        def search(self, query, *, cursor, limit):
            calls.append(query)
            state["cancelled"] = True
            return {"items": [{"title": "hidden"}]}

    with pytest.raises(YouthSourceCancelled) as exc:
        fetch_source_page(
            SearchOnly(), "secret", age=None, themes=(), cursor="2",
            cancelled=lambda: state["cancelled"],
        )
    assert calls == ["secret"]
    assert exc.value.collection_request_count is None


def test_fetch_source_cancellation_during_transport_error_keeps_attested_count():
    from youth_page import YouthSourceCancelled
    state = {"cancelled": False}
    class FailedBrowse:
        def browse(self, **kwargs):
            state["cancelled"] = True
            failure = RuntimeError("private signed URL token")
            failure.collection_request_count = 1
            raise failure

    with pytest.raises(YouthSourceCancelled) as exc:
        fetch_source_page(
            FailedBrowse(), "query", age=8, themes=(), cursor=None,
            cancelled=lambda: state["cancelled"],
        )
    assert exc.value.collection_request_count == 1
    assert "signed URL" not in str(exc.value)


def test_fetch_source_without_cancellation_preserves_existing_public_contract():
    class Source:
        def browse(self, **kwargs):
            return {"items": [{"title": "Book"}], "collection_request_count": 1}

    answer = fetch_source_page(Source(), "Book", age=8, themes=(), cursor=None)
    assert answer["collection_request_count"] == 1
    assert answer["items"][0]["title"] == "Book"


def test_youth_ui_cancellation_while_source_is_running_preserves_prior_cache():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{"title": "Previous private title"}])
    state, requests, status, dispatch, refreshed = (
        {"cancelled": False}, [], [], [], [],
    )
    tasks = Tasks()
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda cb: dispatch.append(cb))
    view.status = NS(set_label=status.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.filter_state = lambda: FilterState(query="Atlas", age=8, themes=())
    view.next_cursor = "3"
    view.next_source_plugin = "youth.source.ecole-loisirs"
    view.refresh_source_diagnostics = lambda: refreshed.append("refresh")

    def browse(**kwargs):
        requests.append(kwargs)
        state["cancelled"] = True
        return {
            "items": [{"title": "New book must not replace cache"}],
            "next_cursor": "4",
            "collection_request_count": 1,
        }
    view.selected_source = lambda: NS(
        plugin_id="youth.source.ecole-loisirs",
        service=NS(
            source_info=lambda: {
                "source_id": "ecole-loisirs", "display_name": "École des loisirs",
            },
            browse=browse,
        ),
    )
    view._remote_request("")
    handle = tasks.handles[0]
    handle.control = NS(cancelled=lambda: state["cancelled"])
    handle.finish()
    assert len(dispatch) == 1
    dispatch.pop()()

    row = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert len(requests) == 1
    assert row["collection_state"] == "cancelled"
    assert row["error_type"] == "YouthSourceCancelled"
    assert row["request_count"] == 1
    assert row["received_count"] is None
    assert row["cache_count"] == 1
    assert view.next_cursor == "3"
    assert view.next_source_plugin == "youth.source.ecole-loisirs"
    assert status[-1] == "Collecte annulée. Le cache précédent est conservé."
    assert refreshed == ["refresh"]
    assert "New book must not replace cache" not in repr(row)
    assert "Previous private title" not in repr(row)
    assert {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}["ecole-loisirs"]["collection_state"] == "not_tested"


def test_fetch_source_page_prefers_opt_in_cooperative_milan_extension():
    from youth_page import YouthSourceCancelled

    state, calls = {"cancelled": False}, []
    class Milan:
        def browse(self, **kwargs):
            raise AssertionError("must select cancellable browse extension")

        def browse_with_cancellation(self, *, cancelled, **kwargs):
            calls.append(kwargs)
            assert not cancelled()
            state["cancelled"] = True
            raise YouthSourceCancelled(2)

    with pytest.raises(YouthSourceCancelled) as exc:
        fetch_source_page(
            Milan(), "private query", age=8, themes=(), cursor="2",
            cancelled=lambda: state["cancelled"],
        )
    assert exc.value.collection_request_count == 2
    assert len(calls) == 1
    assert calls[0]["cursor"] == "2"


def test_fetch_source_page_preserves_non_cooperative_browse_contract():
    calls = []
    class LegacySource:
        def browse(self, **kwargs):
            calls.append(kwargs)
            return {"items": [{"title": "Legitimate"}], "collection_request_count": 1}

    result = fetch_source_page(
        LegacySource(), "test", age=9, themes=("mer",), cursor=None,
        cancelled=lambda: False,
    )
    assert len(calls) == 1
    assert result["collection_request_count"] == 1


def test_youth_gtk_idle_cancellation_discards_already_completed_source_success():
    """Cancel after worker done, before GTK idle_add, must not ingest a stale page."""
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    view.catalog.ingest("alice", "ecole-loisirs", [{"title": "Previous private title"}])
    tasks, dispatch, statuses, refreshed = Tasks(), [], [], []
    state = {"cancelled": False}
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda cb: dispatch.append(cb))
    view.status = NS(set_label=statuses.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.filter_state = lambda: FilterState(query="book", age=8, themes=())
    view.selected_source = lambda: NS(
        plugin_id="youth.source.ecole-loisirs", service=NS(
            source_info=lambda: {"source_id": "ecole-loisirs", "display_name": "École des loisirs"}
        ),
    )
    view.refresh_source_diagnostics = lambda: refreshed.append("refresh")
    view.next_cursor, view.next_source_plugin = "2", "youth.source.ecole-loisirs"
    received = []
    token = ("alice", "youth.source.ecole-loisirs", "book", 8, ())
    view.run_remote(
        lambda control: {"items": [{"title": "Unwanted"}], "collection_request_count": 2},
        received.append, token,
    )
    handle = tasks.handles[0]
    handle.control = NS(cancelled=lambda: state["cancelled"])
    handle.finish()
    assert len(dispatch) == 1
    state["cancelled"] = True  # cancellation in the GTK idle queue
    dispatch.pop()()

    row = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert received == []
    assert row["collection_state"] == "cancelled"
    assert row["request_count"] == 2
    assert row["cache_count"] == 1
    assert row["received_count"] is None
    assert view.next_cursor == "2"
    assert view.next_source_plugin == "youth.source.ecole-loisirs"
    assert statuses[-1] == "Collecte annulée. Le cache précédent est conservé."
    assert refreshed == ["refresh"]
    assert "Unwanted" not in repr(row)
    assert {r["source_id"]: r for r in view.catalog.source_diagnostics("bob")}["ecole-loisirs"]["collection_state"] == "not_tested"


def test_youth_gtk_idle_cancellation_overrides_transport_failure_without_secrets():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    tasks, dispatch, statuses = Tasks(), [], []
    cancelled = {"value": False}
    view.plugin = NS(context=NS(broker=lambda name: tasks))
    view.GLib = NS(idle_add=lambda cb: dispatch.append(cb))
    view.status = NS(set_label=statuses.append)
    view.generation, view.task, view.disposed = 0, None, False
    view.identity_id = lambda: "alice"
    view.selected_source_plugin = lambda: "youth.source.ecole-loisirs"
    view.filter_state = lambda: FilterState(query="book", age=8, themes=())
    view.selected_source = lambda: NS(
        plugin_id="youth.source.ecole-loisirs", service=NS(
            source_info=lambda: {"source_id": "ecole-loisirs", "display_name": "École des loisirs"}
        ),
    )
    view.refresh_source_diagnostics = lambda: None
    token = ("alice", "youth.source.ecole-loisirs", "book", 8, ())
    def fail(control):
        error = RuntimeError("private https://example.invalid/token")
        error.collection_request_count = 1
        raise error
    view.run_remote(fail, lambda result: 1 / 0, token)
    handle = tasks.handles[0]
    handle.control = NS(cancelled=lambda: cancelled["value"])
    handle.finish()
    cancelled["value"] = True
    dispatch.pop()()
    row = {r["source_id"]: r for r in view.catalog.source_diagnostics("alice")}["ecole-loisirs"]
    assert row["collection_state"] == "cancelled"
    assert row["request_count"] == 1
    assert row["http_status"] is None
    assert "example.invalid" not in repr(row)
    assert statuses[-1] == "Collecte annulée. Le cache précédent est conservé."


def test_youth_funnel_presentation_explains_pipeline_without_claiming_100_analyzed():
    from youth_catalog import YouthCatalogPlugin

    plugin = YouthCatalogPlugin()
    plugin.record_collection(
        "alice", "ecole-loisirs", state="items", accepted=20,
        received_count=20, requested_limit=100, request_count=1,
        pipeline={
            "query_mode": "remote", "effective_limit": 40,
            "http_status": 200, "html_bytes": 5020,
            "html_sha256_prefix": "0123456789abcdef",
            "anchor_count": 22, "product_link_count": 20,
            "candidate_count": 20, "analyzed_count": 20,
            "isbn_valid_count": 20, "age_qualified_count": 18,
            "age_unknown_count": 2, "returned_items": 20,
            "http_ms": 12, "parse_ms": 4,
            "rejection_reasons": {"age_unattested": 1, "isbn_invalid": 2},
            "signed_url": "https://private.example/token",
        },
    )
    rows = plugin.source_diagnostics("alice")
    summary, lines = source_diagnostic_presentation(rows)
    line = next(x for x in lines if "École des loisirs" in x)
    assert "requêtes HTTP : 1" in line
    assert "reçues : 20" in line
    assert "limite demandée : 100" in line
    assert "borne source : 40" in line
    assert "candidats : 20" in line
    assert "examinés : 20" in line
    assert "ISBN valides : 20" in line
    assert "âges attestés : 18" in line
    assert "recherche distante (une page)" in line
    assert "HTTP : 200" in line and "HTTP ms : 12" in line
    assert "parsing ms : 4" in line
    assert "ISBN non valide : 2" in line
    assert "0/100 analysées" not in line
    assert "private.example" not in line
    assert "20" not in repr(plugin.source_diagnostics("bob")[-1].get("pipeline", {}))


def test_youth_ui_collected_attaches_only_explicit_ecole_source_funnel():
    from youth_catalog import YouthCatalogPlugin

    view = object.__new__(YouthView)
    view.catalog = YouthCatalogPlugin()
    calls, messages, refresh = [], [], []
    view.identity_id = lambda: "alice"
    view.status = NS(set_label=messages.append)
    view.render_cached = lambda: calls.append("render")
    view.refresh_source_diagnostics = lambda: refresh.append("local")
    books = [
        {"title": f"Book {i}", "record_id": f"book-{i}"}
        for i in range(20)
    ]
    view.collected(
        {
            "items": books,
            "collection_request_count": 1,
            "collection_pipeline": {
                "query_mode": "remote", "effective_limit": 40,
                "candidate_count": 20, "analyzed_count": 20,
                "returned_items": 20, "http_status": 200,
                "raw_html": "<p>private</p>",
            },
        },
        source_id="ecole-loisirs", label="École des loisirs", append=False,
        source_plugin="youth.source.ecole-loisirs", requested_limit=100,
    )
    result = {
        x["source_id"]: x for x in view.catalog.source_diagnostics("alice")
    }["ecole-loisirs"]
    assert result["cache_count"] == 20
    assert result["request_count"] == 1
    assert result["pipeline"]["candidate_count"] == 20
    assert result["pipeline"]["effective_limit"] == 40
    assert "raw_html" not in repr(result)
    assert calls == ["render"] and refresh == ["local"]
    assert "20 notice(s) reçue(s)" in messages[-1]
    bob = {
        x["source_id"]: x for x in view.catalog.source_diagnostics("bob")
    }["ecole-loisirs"]
    assert bob["collection_state"] == "not_tested"
    assert bob["pipeline"] == {}


def test_906_selected_book_searches_known_catalog_once_with_original_title():
    calls, shown, toasts = [], [], []
    view = object.__new__(YouthView)
    view.rows = {"a": youth_book(
        record_id="a", title="C'est quoi, l'écologie ?",
        identifiers={"isbn": "9780306406157"},
    )}
    view.master = NS(show_detail=shown.append, empty_detail=lambda: None)
    view.host = NS(
        search_books=lambda *args, **kwargs: calls.append((args, kwargs)) or True,
        toast=toasts.append,
    )
    view.identity_id = lambda: "alice"
    YouthView.selected(view, NS(key="a"))
    assert calls == []  # No implicit remote or local search on selection
    assert len(shown) == 1
    assert len(shown[0].actions) == 2
    action = shown[0].actions[0]
    assert action.id == "search-library"
    assert action.title == "Rechercher en bibliothèque"
    assert action.icon_name == "system-search-symbolic"
    action.callback()
    assert calls == [(("C'est quoi, l'écologie ?",), {
        "field": "title", "online": False, "identity_id": "alice",
    })]
    isbn_action = shown[0].actions[1]
    assert isbn_action.id == "search-library-isbn"
    assert "ISBN" in isbn_action.title
    isbn_action.callback()
    assert calls[1] == (("9780306406157",), {
        "field": "isbn", "online": False, "identity_id": "alice",
    })
    assert toasts == []


def test_906_identity_rejection_is_visible_without_fallback_search():
    calls, toasts = [], []
    view = object.__new__(YouthView)
    view.rows = {"book": youth_book(title="Même titre")}
    view.master = NS(show_detail=lambda presentation: captured.append(presentation),
                     empty_detail=lambda: None)
    captured = []
    view.host = NS(
        search_books=lambda *a, **kw: calls.append((a, kw)) or False,
        toast=toasts.append,
    )
    view.identity_id = lambda: "bob"
    YouthView.selected(view, NS(key="book"))
    captured[0].actions[0].callback()
    assert len(calls) == 1 and calls[0][1]["identity_id"] == "bob"
    assert len(toasts) == 1
    assert "profil" in toasts[0]


def test_906_absent_selection_and_legacy_host_do_not_create_buttons():
    empty, shown = [], []
    view = object.__new__(YouthView)
    view.rows = {"book": youth_book()}
    view.master = NS(show_detail=shown.append, empty_detail=lambda: empty.append(True))
    view.host = NS()
    view.identity_id = lambda: "alice"
    YouthView.selected(view, NS(key="book"))
    assert shown[0].actions == ()
    YouthView.selected(view, None)
    assert empty == [True] and view.selected_book is None


def test_906_same_title_different_editions_do_not_assert_possession():
    calls, shown = [], []
    first = youth_book(record_id="a", title="Deux éditions",
                       identifiers={"isbn": "9780306406157"})
    second = youth_book(record_id="b", title="Deux éditions",
                        identifiers={"isbn": "9780140328721"})
    view = object.__new__(YouthView)
    view.rows = {"a": first, "b": second}
    view.master = NS(show_detail=shown.append, empty_detail=lambda: None)
    view.host = NS(search_books=lambda *a, **kw: calls.append((a, kw)) or True)
    view.identity_id = lambda: "alice"
    YouthView.selected(view, NS(key="a"))
    YouthView.selected(view, NS(key="b"))
    assert calls == []
    # The previous edition's action is now stale, despite the same title.
    # Only the currently selected edition can start an explicit lookup.
    assert shown[0].actions[0].callback() is False
    assert shown[1].actions[0].callback() is True
    assert calls == [
        (("Deux éditions",), {"field": "title", "online": False, "identity_id": "alice"}),
    ]


def test_detail_does_not_silently_drop_known_multivalued_metadata():
    ages = tuple(YouthAgeRange(age, age + 1) for age in range(1, 11))
    book = youth_book(
        authors=("Autrice A", "Auteur B"),
        age_ranges=ages,
        themes=tuple(f"thème {i}" for i in range(15)),
        publishers=tuple(f"éditeur {i}" for i in range(8)),
    )
    detail = detail_presentation(book)
    assert len(detail.sections[0].fields) == 10
    assert detail.sections[1].title == "Détail"
    fields = [field for section in detail.sections for field in section.fields]
    assert [field.value for field in fields if field.label == "Auteur"] == [
        "Autrice A", "Auteur B",
    ]
    assert "thème 14" in [field.value for field in fields if field.kind == "subject"]
    assert "éditeur 7" in [field.value for field in fields if field.id.startswith("publisher-")]


def test_youth_detail_typed_fields_do_not_merge_authors_and_subjects():
    detail = detail_presentation(youth_book(
        authors=("Auteur A", "Auteur B"), themes=("nature", "écologie"),
        formats=("EPUB", "PDF"), provenance=("milan", "openlibrary"),
    ))
    fields = detail.sections[1].fields
    assert len([f for f in fields if f.kind == "author"]) == 2
    assert [f.value for f in fields if f.kind == "subject"] == ["nature", "écologie"]
    assert len([f for f in fields if f.id.startswith("format-")]) == 2
    assert len([f for f in fields if f.id.startswith("source-")]) == 2
    assert next(f for f in fields if f.id == "isbn").kind == "isbn"


def test_923_core_bridge_partial_host_has_no_action_updater():
    # Core #906 extracts selected() against a light host, not a real GTK View.
    # This must not break the previously supported callback contract.
    seen, emptied = [], []
    partial = NS(
        rows={"a": youth_book(record_id="a", title="Un livre")},
        host=NS(),
        master=NS(show_detail=seen.append, empty_detail=lambda: emptied.append(True)),
    )
    YouthView.selected(partial, NS(key="a"))
    assert len(seen) == 1 and partial.selected_book is partial.rows["a"]
    YouthView.selected(partial, None)
    assert emptied == [True] and partial.selected_book is None


def test_923_real_view_selected_refreshes_action_availability():
    calls = []
    view = object.__new__(YouthView)
    view.rows = {"a": youth_book(record_id="a", title="Une histoire")}
    view.host = NS()
    view.master = NS(show_detail=lambda _: None, empty_detail=lambda: None)
    view.update_action_availability = lambda: calls.append("refresh")
    view.selected(NS(key="a"))
    view.selected(None)
    assert calls == ["refresh", "refresh"]



def test_959_invalid_or_absent_isbn_exposes_only_legacy_title_lookup():
    seen = []
    view = object.__new__(YouthView)
    view.master = NS(show_detail=seen.append, empty_detail=lambda: None)
    view.host = NS(search_books=lambda *a, **k: True)
    view.identity_id = lambda: "alice"
    for raw in ("3127020507597", "9780306406158", "", None):
        view.rows = {"book": youth_book(identifiers={"isbn": raw})}
        YouthView.selected(view, NS(key="book"))
        assert [action.id for action in seen[-1].actions] == ["search-library"]


def test_959_isbn_lookup_is_explicit_identity_scoped_and_offline():
    calls, shown, toasts = [], [], []
    view = object.__new__(YouthView)
    view.rows = {
        "book": youth_book(title="Deux éditions", identifiers={"isbn": "9780306406157"}),
    }
    view.master = NS(show_detail=shown.append, empty_detail=lambda: None)
    view.host = NS(
        search_books=lambda *a, **kw: calls.append((a, kw)) or False,
        toast=toasts.append,
    )
    view.identity_id = lambda: "bob"
    YouthView.selected(view, NS(key="book"))
    assert calls == []
    shown[0].actions[1].callback()
    assert calls == [(("9780306406157",), {
        "field": "isbn", "online": False, "identity_id": "bob",
    })]
    assert len(toasts) == 1
    assert "profil" in toasts[0]


@pytest.mark.parametrize("unsafe_url", [
    "http://127.0.0.1:8080/private",
    "https://localhost/book",
    "https://library.local/notice",
    "https://192.168.1.7/book",
    "http://169.254.1.1/link",
    "http://[::1]/book",
    "https://example.org:8443/book",
    "https://example.org/book?access_token=SECRET",
    "https://example.org/book?%61ccess_token=SECRET",
    "https://example.org/book?api_key=SECRET",
    "https://example.org/book?X-Amz-Signature=SECRET",
    "https://example.org/book?session_id=SECRET",
    "https://example.org/book?next=person%40example.org",
    "https://example.org/book#private-session",
    "https://example.org/book\nX-Private: value",
    "https://example.org/book with spaces",
    "https://example.org/book<unsafe>",
    "https://user:secret@example.org/book",
    "ftp://example.org/book",
    "https://example.org/book?" + "&".join(f"key{i}=ok" for i in range(40)),
    "https://example.org/" + "a" * 2049,
])
def test_959_public_notice_urls_reject_local_private_signed_or_malformed(unsafe_url):
    assert public_url(unsafe_url) == ""


@pytest.mark.parametrize("safe_url", [
    "https://example.org/book/42",
    "https://example.org/book/42?utm_source=publisher",
    "https://covers.openlibrary.org/b/id/42-M.jpg?width=180",
    "http://example.org/catalogue/42",
])
def test_959_safe_public_links_are_not_needlessly_removed(safe_url):
    assert public_url(safe_url) == safe_url


def test_959_detail_and_list_do_not_expose_private_links_or_cover_urls():
    unsafe = "https://example.org/notice?access_token=private-key"
    book = youth_book(
        friendly_url=unsafe,
        notice_url="https://catalogue.example.org/notice/42",
        cover_url="http://127.0.0.1:8000/signed-cover",
    )
    detail = detail_presentation(book)
    assert len(detail.links) == 1
    assert detail.links[0].url == "https://catalogue.example.org/notice/42"
    assert detail.thumbnail_url is None
    assert list_entry(book).thumbnail_url is None
    assert "private-key" not in str(detail.links)
    blocked = youth_book(
        friendly_url=unsafe, notice_url="https://example.org/notice?api_key=hidden",
        cover_url="https://example.org/cover?X-Amz-Signature=hidden",
    )
    assert detail_presentation(blocked).links == ()
    assert detail_presentation(blocked).thumbnail_url is None
    assert list_entry(blocked).thumbnail_url is None


def test_959_links_are_only_navigation_not_edition_holdings_evidence():
    book = youth_book(
        notice_url="https://example.org/notice/42",
        friendly_url="https://example.org/notice/42",
    )
    detail = detail_presentation(book)
    assert [(link.label, link.url) for link in detail.links] == [
        ("Voir la notice publique", "https://example.org/notice/42"),
    ]
    assert all("disponib" not in link.label.casefold() for link in detail.links)
