"""#4030: public C search is descriptive and never evidence of acquisition."""
import pytest

from cifereca.core.local_origin_documentary_suggestions import suggest_known_public_documents
from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
from cifereca.core.notice_cover_repository import evict_notice_cover
from tests.test_core_p0_3171_atomic_publication_v14 import store, rows
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public


def _seed(store, **fields):
    return persist(store, verified_public(**fields), at=100)


def test_same_title_distinct_editions_and_source_comparisons(store):
    for record, author in (("first", "Autrice"), ("second", "Autrice"),
                           ("third", "Autre")):
        _seed(store, record_id=record, title="Roman commun", author=author)
    before = rows(store)
    result = suggest_known_public_documents(store, title="Roman commun",
                                           author="Autrice", now=101)
    assert result.status == "partial" and not result.coverage_complete
    assert result.known_matches == 3
    comparison = {item.hit.record_id: item.comparison for item in result.suggestions}
    assert comparison == {
        "first": "title_author_concordant", "second": "title_author_concordant",
        "third": "title_concordant_author_divergent",
    }
    assert len({item.hit.edition_id for item in result.suggestions}) == 3
    assert all(not item.provider_origin_attested and not item.association_authorized
               for item in result.suggestions)
    assert not result.provider_access_performed and not result.may_write_origin_or_right
    assert rows(store) == before and store.entitlements() == []


def test_retained_c_after_eviction_preserves_staleness(store):
    source = _seed(store, record_id="retained", title="Roman commun", author="Autrice")
    canonical = persist_exact_public_search_cover(store, source)
    assert evict_notice_cover(store, source.document, expected_epoch=source.epoch)
    page = suggest_known_public_documents(store, title="Roman commun", now=100 + 90000)
    hit, = page.suggestions
    assert hit.hit.document == canonical.document
    assert hit.hit.stale is True and hit.hit.last_seen_at == 100
    assert hit.comparison == "title_concordant_author_unknown"
    assert not hit.provider_origin_attested


def test_pages_and_empty_remain_partial(store):
    for number in range(3):
        _seed(store, record_id=f"rec-{number}", title="Roman commun")
    first = suggest_known_public_documents(store, title="Roman commun", page_size=1, now=100)
    second = suggest_known_public_documents(store, title="Roman commun", page_size=1, page=1, now=100)
    assert first.has_more and second.has_more
    assert first.suggestions[0].hit.edition_id != second.suggestions[0].hit.edition_id
    empty = suggest_known_public_documents(store, title="Absent", now=100)
    assert empty.status == "partial" and empty.known_matches == 0
    assert not empty.coverage_complete


@pytest.mark.parametrize("title", ["x", "/private/book.epub", "https://example.org/?token=secret", "a"*161])
def test_invalid_local_text_refused(store, title):
    with pytest.raises(ValueError, match="invalid_documentary_suggestion_request"):
        suggest_known_public_documents(store, title=title)


def test_known_c_page_orders_documentary_strength_without_changing_c_window(store):
    """Evidence ranks within the returned page; identical ranks keep C order."""
    from cifereca.core.local_origin_documentary_suggestions import _comparison
    from cifereca.core.notice_c_public_search import search_public_notice_c

    for record, author in (
        ("weak-first", "Different author"),
        ("exact-a", "Autrice"),
        ("unknown", None),
        ("exact-b", "Autrice"),
    ):
        _seed(store, record_id=record, title="Roman commun", author=author)
    before = rows(store)
    ranks = {
        "title_author_concordant": 0,
        "title_concordant_author_unknown": 1,
        "title_concordant_author_divergent": 2,
        "title_related": 3,
    }
    for page, page_size in ((0, 10), (0, 2), (1, 2)):
        source = search_public_notice_c(
            store, "Roman commun", field="title", scope="all",
            page=page, page_size=page_size, max_scan=2048, now=101,
            group_by_document=False,
        )
        expected = sorted(
            source.hits,
            key=lambda hit: ranks[_comparison(hit, "Roman commun", "Autrice")],
        )
        result = suggest_known_public_documents(
            store, title="Roman commun", author="Autrice",
            page=page, page_size=page_size, now=101,
        )
        assert [entry.hit for entry in result.suggestions] == expected
        assert [ranks[entry.comparison] for entry in result.suggestions] == sorted(
            ranks[entry.comparison] for entry in result.suggestions
        )
        assert result.known_matches == source.known_matches
        assert result.has_more == source.has_more
        assert result.page == page and result.page_size == page_size
        assert result.status == "partial" and not result.coverage_complete
        assert all(not item.association_authorized and not item.provider_origin_attested
                   for item in result.suggestions)
    assert rows(store) == before and store.entitlements() == []


@pytest.mark.parametrize("field", ["title", "author"])
@pytest.mark.parametrize("control", [
    "\n", "\r", "\t", "\x85", "\u202e", "\u2028", "\u200d",
    "\u2066", "\x1b",
])
def test_unprintable_local_documentary_labels_refuse_known_c_read(
    store, monkeypatch, field, control,
):
    from cifereca.core import local_origin_documentary_suggestions as documentary

    original = rows(store)
    def forbidden(*_args, **_kwargs):
        pytest.fail("An unprintable local label cannot dispatch a C query")
    monkeypatch.setattr(documentary, "search_public_notice_c", forbidden)
    values = {"title": "Roman commun", "author": "Autrice"}
    values[field] = "before" + control + "FORGED"
    with pytest.raises(ValueError, match="invalid_documentary_suggestion_request"):
        suggest_known_public_documents(store, **values)
    assert rows(store) == original
    assert store.entitlements() == []
