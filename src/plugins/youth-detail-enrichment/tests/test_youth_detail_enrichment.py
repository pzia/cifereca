from types import SimpleNamespace as NS

from cifereca.plugin_api import BookRef, Provenance
from youth_detail_enrichment import (
    YouthDetailEnrichment,
    age_label,
    canonical_url,
    normalize_isbn,
    select_candidate,
)


def age(lo, hi=None, kind="recommended", source="ecole-loisirs", label="", confidence=1.0):
    return NS(
        age_min=lo,
        age_max=hi,
        age_kind=kind,
        source_id=source,
        source_label=label,
        confidence=confidence,
    )


def candidate(
    *,
    record_id="42",
    source_id="ecole-loisirs",
    title="Titre",
    isbn="",
    provider="ecole-loisirs",
    source_item_id="42",
    notice_url="https://example.org/book/42",
    ranges=None,
):
    identifiers = {"provider": provider, "source_item_id": source_item_id}
    if isbn:
        identifiers["isbn"] = isbn
    return NS(
        record_id=record_id,
        source_id=source_id,
        title=title,
        identifiers=identifiers,
        notice_url=notice_url,
        friendly_url=notice_url,
        age_ranges=tuple(ranges if ranges is not None else (
            age(8, 10, "interest", label="Interest age 8-10", confidence=0.95),
        )),
        provenance=(source_id,),
    )


class Catalog:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.calls = []

    def search_cached(self, identity_id, query):
        self.calls.append((identity_id, query))
        q = query.casefold()
        return tuple(row for row in self.rows if q in row.title.casefold())


class Context:
    def __init__(self, catalog=None):
        self.catalog = catalog
        self.calls = []

    def service(self, plugin_id):
        self.calls.append(plugin_id)
        return self.catalog if plugin_id == "catalog.youth" else None


def test_isbn10_and_13_normalize_to_same_value():
    assert normalize_isbn("0-306-40615-2") == "9780306406157"
    assert normalize_isbn("9780306406157") == "9780306406157"
    assert normalize_isbn("9780000000000") == ""


def test_age_labels_keep_kind_and_open_bounds():
    assert age_label(age(8, 10, "interest")) == "Âge d’intérêt · 8–10 ans"
    assert age_label(age(9, None, "reading")) == "Âge de lecture · à partir de 9 ans"
    assert age_label(age(None, 6, "publisher")) == "Âge éditeur · jusqu’à 6 ans"


def test_select_candidate_prefers_exact_isbn():
    book = BookRef("local", "Titre", identifiers={"isbn": "0306406152"})
    strong = candidate(isbn="9780306406157")
    other = candidate(record_id="43", source_item_id="43", isbn="9783161484100")
    match = select_candidate(book, (other, strong))
    assert match is not None
    assert match.candidate is strong
    assert match.score == 100
    assert match.reason == "isbn:9780306406157"


def test_provider_and_source_item_match_without_provenance_object():
    book = BookRef(
        "local",
        "Titre",
        identifiers={"provider": "ecole-loisirs", "source_item_id": "42"},
    )
    match = select_candidate(book, (candidate(),))
    assert match is not None
    assert match.reason == "source-item"


def test_notice_url_match_is_strong_when_exposed_in_book_metadata():
    book = BookRef(
        "local",
        "Titre",
        metadata={"notice_url": "HTTPS://EXAMPLE.ORG/book/42#fragment"},
    )
    match = select_candidate(book, (candidate(provider="other", source_item_id="other"),))
    assert match is not None
    assert match.reason == "notice-url"


def test_title_author_alone_never_enriches():
    book = BookRef("local", "Titre", ("Autrice",))
    assert select_candidate(book, (candidate(),)) is None


def test_ambiguous_best_matches_return_none():
    book = BookRef("local", "Titre", identifiers={"isbn": "9780306406157"})
    one = candidate(record_id="one", source_item_id="one", isbn="9780306406157")
    two = candidate(record_id="two", source_item_id="two", isbn="9780306406157")
    assert select_candidate(book, (one, two)) is None


def test_cached_detail_reads_only_catalog_cache_and_preserves_provenance():
    rows = [candidate(isbn="9780306406157", ranges=(
        age(8, 10, "interest", source="ecole-loisirs", label="Interest age 8-10", confidence=0.95),
        age(7, 9, "reading", source="ecole-loisirs", label="Reading age 7-9", confidence=1.0),
    ))]
    catalog = Catalog(rows)
    plugin = YouthDetailEnrichment().activate(Context(catalog))
    book = BookRef("book-42", "Titre", identifiers={"isbn": "9780306406157"})

    result = plugin.cached_detail(book, identity_id="alice")

    assert catalog.calls == [("alice", "Titre")]
    assert result is not None
    assert result.book_id == "book-42"
    assert result.identity_id == "alice"
    assert result.sections[0].title == "Jeunesse"
    assert [field.label for field in result.sections[0].fields[:2]] == ["Âge d’intérêt", "Âge de lecture"]
    assert "Interest age 8-10" in result.sections[0].fields[0].value
    assert result.sections[0].fields[0].provenance.source_id == "ecole-loisirs"
    assert result.source.metadata["match"].startswith("isbn:")
    assert result.freshness_token
    assert result.actions[0].uri == "https://example.org/book/42"


def test_inferred_age_is_explicitly_labelled_and_sorted_after_attested():
    row = candidate(isbn="9780306406157", ranges=(
        age(11, 13, "inferred", source="model", label="inferred", confidence=0.5),
        age(10, 12, "recommended", source="publisher", label="10-12", confidence=1.0),
    ))
    plugin = YouthDetailEnrichment().activate(Context(Catalog([row])))
    result = plugin.cached_detail(
        BookRef("book", "Titre", identifiers={"isbn": "9780306406157"}),
        identity_id="alice",
    )
    assert [field.label for field in result.sections[0].fields[:2]] == ["Âge recommandé", "Âge inféré"]


def test_missing_catalog_age_or_identity_returns_none():
    book = BookRef("book", "Titre", identifiers={"isbn": "9780306406157"})
    assert YouthDetailEnrichment().activate(Context()).cached_detail(book, identity_id="alice") is None
    empty = candidate(isbn="9780306406157", ranges=())
    plugin = YouthDetailEnrichment().activate(Context(Catalog([empty])))
    assert plugin.cached_detail(book, identity_id="alice") is None
    assert plugin.cached_detail(book, identity_id="") is None


def test_catalog_failure_is_isolated():
    class Broken:
        def search_cached(self, identity_id, query):
            raise RuntimeError("broken cache")

    plugin = YouthDetailEnrichment().activate(Context(Broken()))
    book = BookRef("book", "Titre", identifiers={"isbn": "9780306406157"})
    assert plugin.cached_detail(book, identity_id="alice") is None


def test_non_public_or_credential_url_never_becomes_action():
    row = candidate(
        isbn="9780306406157",
        notice_url="https://user:secret@example.org/book/42",
    )
    plugin = YouthDetailEnrichment().activate(Context(Catalog([row])))
    result = plugin.cached_detail(
        BookRef("book", "Titre", identifiers={"isbn": "9780306406157"}),
        identity_id="alice",
    )
    assert result is not None
    assert result.actions == ()
    assert canonical_url("file:///tmp/book") == ""



class LookupCatalog:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.find_calls = []
        self.search_calls = []

    def find_cached(self, identity_id, **kwargs):
        self.find_calls.append((identity_id, kwargs))
        isbn = normalize_isbn(kwargs.get("isbn"))
        if isbn:
            return tuple(
                row for row in self.rows
                if normalize_isbn(getattr(row, "identifiers", {}).get("isbn")) == isbn
            )
        provider = str(kwargs.get("provider") or "").casefold()
        item = str(kwargs.get("source_item_id") or "")
        if provider and item:
            return tuple(
                row for row in self.rows
                if str(getattr(row, "identifiers", {}).get("provider") or "").casefold() == provider
                and str(getattr(row, "identifiers", {}).get("source_item_id") or "") == item
            )
        return ()

    def search_cached(self, identity_id, query):
        self.search_calls.append((identity_id, query))
        raise AssertionError("title search must not be used when find_cached is available")


def test_cached_detail_prefers_strong_lookup_even_when_title_differs():
    row = candidate(
        title="Titre de la source",
        isbn="9780306406157",
        ranges=(age(8, 10, "interest", label="Interest age 8-10"),),
    )
    catalog = LookupCatalog([row])
    plugin = YouthDetailEnrichment().activate(Context(catalog))
    book = BookRef("book", "Titre local complètement différent", identifiers={"isbn": "0306406152"})

    result = plugin.cached_detail(book, identity_id="alice")

    assert result is not None
    assert len(catalog.find_calls) == 1
    assert catalog.find_calls[0][0] == "alice"
    assert catalog.find_calls[0][1]["isbn"] == "9780306406157"
    assert catalog.find_calls[0][1]["limit"] == 20
    assert catalog.search_calls == []
    assert result.source.metadata["match"] == "isbn:9780306406157"


def test_cached_detail_with_modern_catalog_never_falls_back_to_title_author():
    catalog = LookupCatalog([candidate()])
    plugin = YouthDetailEnrichment().activate(Context(catalog))
    book = BookRef("book", "Titre", ("Autrice",))

    assert plugin.cached_detail(book, identity_id="alice") is None
    assert catalog.find_calls == []
    assert catalog.search_calls == []


def test_cached_detail_strong_lookup_failure_is_isolated():
    class BrokenLookup:
        def find_cached(self, identity_id, **kwargs):
            raise RuntimeError("broken local index")

        def search_cached(self, identity_id, query):
            raise AssertionError("must not fallback after modern lookup failure")

    plugin = YouthDetailEnrichment().activate(Context(BrokenLookup()))
    book = BookRef("book", "Titre", identifiers={"isbn": "9780306406157"})
    assert plugin.cached_detail(book, identity_id="alice") is None
