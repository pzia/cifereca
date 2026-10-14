import pytest
from youth_catalog import (
    YouthCatalogIndex,
    YouthCatalogPlugin,
    bibliographic_match,
    normalize_isbn,
    normalize_record,
    parse_age_label,
    validate_source_contract,
)


def row(title, author="Auteur", **extra):
    return {"title": title, "authors": [author] if author else [], **extra}


def test_parse_starting_age_and_plus():
    ranges = parse_age_label("à partir de 8 ans", source_id="publisher")
    assert [(r.age_min, r.age_max, r.age_kind) for r in ranges] == [(8, None, "recommended")]
    assert ranges[0].contains(12)
    assert not ranges[0].contains(7)
    plus = parse_age_label("9+")
    assert (plus[0].age_min, plus[0].age_max) == (9, None)


def test_parse_closed_range_and_upper_bound():
    ranged = parse_age_label("10–12 ans")
    assert (ranged[0].age_min, ranged[0].age_max) == (10, 12)
    upper = parse_age_label("jusqu’à 6 ans")
    assert (upper[0].age_min, upper[0].age_max) == (0, 6)


def test_parse_interest_and_reading_age_kinds():
    assert parse_age_label("Interest age 8-10")[0].age_kind == "interest"
    assert parse_age_label("Reading age 7 to 9")[0].age_kind == "reading"


def test_inferred_age_is_flagged_and_lower_confidence():
    item = parse_age_label("11+", inferred=True)[0]
    assert item.age_kind == "inferred"
    assert item.confidence <= 0.55


def test_age_18_and_over_are_rejected():
    assert parse_age_label("18+") == ()
    assert parse_age_label("à partir de 21 ans") == ()


def test_structured_age_range_is_preserved_with_source_label():
    record = normalize_record(
        row("Livre", age_ranges=[{
            "age_min": 8, "age_max": 10, "age_kind": "reading",
            "source_label": "Reading age 8-10", "confidence": 0.95,
        }]),
        source_id="ecole-loisirs",
    )
    rng = record.age_ranges[0]
    assert (rng.age_min, rng.age_max, rng.age_kind, rng.source_label, rng.confidence) == (
        8, 10, "reading", "Reading age 8-10", 0.95
    )
    assert rng.source_id == "ecole-loisirs"


def test_isbn_match_is_confirmed_and_title_author_only_is_suggestion():
    a = normalize_record(row("Livre", isbn13="9780306406157"), source_id="one")
    b = normalize_record(row("Autre édition", isbn="978-0-306-40615-7"), source_id="two")
    assert bibliographic_match(a, b).score == 100
    c = normalize_record(row("Même titre", "Même auteur"), source_id="one")
    d = normalize_record(row("Même titre", "Même auteur"), source_id="two")
    match = bibliographic_match(c, d)
    assert (match.status, match.score) == ("suggested", 60)


def test_title_author_suggestion_does_not_transfer_age_between_editions():
    index = YouthCatalogIndex()
    index.ingest("alice", "one", [row("Même titre", "Même auteur", age_label="8+")])
    index.ingest("alice", "two", [row("Même titre", "Même auteur", age_label="12+")])
    entries = index.entries("alice")
    assert len(entries) == 2
    assert sorted(r.age_min for book in entries for r in book.age_ranges) == [8, 12]


def test_confirmed_isbn_merge_preserves_all_age_provenance():
    index = YouthCatalogIndex()
    index.ingest("alice", "one", [row("Livre", isbn13="9780306406157", interest_age="8-10")])
    index.ingest("alice", "two", [row("Livre bis", isbn13="9780306406157", reading_age="7-9")])
    entries = index.entries("alice")
    assert len(entries) == 1
    book = entries[0]
    assert set(book.provenance) == {"one", "two"}
    assert {(r.age_kind, r.source_id) for r in book.age_ranges} == {("interest", "one"), ("reading", "two")}


def test_age_and_kind_filter():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [
        row("A", interest_age="8-10"),
        row("B", reading_age="8-10"),
        row("C"),
    ])
    assert [b.title for b in index.search("alice", age=9)] == ["A", "B"]
    assert [b.title for b in index.search("alice", age=9, age_kind="interest")] == ["A"]
    assert [b.title for b in index.search("alice")] == ["A", "B", "C"]


def test_inferred_ranges_can_be_excluded():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [
        row("Explicit", age_label="10+"),
        row("Inferred", age_ranges=[{"age_min": 10, "kind": "recommended", "inferred": True}]),
    ])
    assert {b.title for b in index.search("alice", age=11, include_inferred=True)} == {"Explicit", "Inferred"}
    assert [b.title for b in index.search("alice", age=11)] == ["Explicit"]
    assert [b.title for b in index.search("alice", age=11, include_inferred=False)] == ["Explicit"]


def test_standard_filters_are_local_and_composable():
    index = YouthCatalogIndex()
    index.ingest("alice", "paris", [
        row("Roman mer", language="fr", format=["epub"], book_type="fiction", themes=["mer", "aventure"], age_label="10-12"),
        row("Doc mer", language="fr", format=["pdf"], book_type="documentary", themes=["mer"], age_label="10-12"),
    ])
    result = index.search(
        "alice", "mer", age=11, source_id="paris", language="fr",
        format="epub", book_type="fiction", themes=["aventure"],
    )
    assert [b.title for b in result] == ["Roman mer"]


def test_cache_is_isolated_by_identity_and_targeted_source_replace():
    index = YouthCatalogIndex()
    index.ingest("alice", "one", [row("A", age_label="8+")], fresh_for_seconds=60)
    index.ingest("bob", "one", [row("B", age_label="8+")])
    assert [b.title for b in index.entries("alice")] == ["A"]
    assert [b.title for b in index.entries("bob")] == ["B"]
    assert index.fresh_until("alice", "one")
    index.ingest("alice", "one", [row("C", age_label="9+")], replace_source=True)
    assert [b.title for b in index.entries("alice")] == ["C"]
    assert [b.title for b in index.entries("bob")] == ["B"]


def test_all_invalid_source_refresh_preserves_last_good_cache_and_freshness():
    index = YouthCatalogIndex()
    index.ingest("camille", "ecole-loisirs", [row("Notice attestée", age_label="8+")], fresh_for_seconds=180)
    index.ingest("camille", "milan", [row("Autre source", age_label="9+")])
    index.ingest("alex", "ecole-loisirs", [row("Notice Alex", age_label="10+")])
    previous_freshness = index.fresh_until("camille", "ecole-loisirs")
    previous = index.entries("camille")
    report = index.ingest(
        "camille", "ecole-loisirs", [{"title": ""}, {"title": None}],
        fresh_for_seconds=300, replace_source=True,
    )
    assert report.accepted == 0 and report.rejected == 2
    assert len(report.errors) == 2
    assert report.fresh_until == previous_freshness
    assert index.entries("camille") == previous
    assert index.fresh_until("camille", "ecole-loisirs") == previous_freshness
    assert [book.title for book in index.entries("alex")] == ["Notice Alex"]


def test_empty_valid_source_refresh_clears_only_target_and_obsolete_ttl():
    index = YouthCatalogIndex()
    index.ingest("camille", "ecole-loisirs", [row("Avant", age_label="8+")], fresh_for_seconds=120)
    index.ingest("camille", "milan", [row("Milan", age_label="9+")])
    assert index.fresh_until("camille", "ecole-loisirs")
    report = index.ingest("camille", "ecole-loisirs", [], replace_source=True)
    assert report.accepted == report.rejected == 0
    assert [book.title for book in index.entries("camille")] == ["Milan"]
    assert index.fresh_until("camille", "ecole-loisirs") == ""
    assert [book.title for book in index.search("camille", age=9)] == ["Milan"]


def test_mixed_valid_and_invalid_refresh_keeps_accepted_records():
    index = YouthCatalogIndex()
    index.ingest("camille", "ecole-loisirs", [row("Avant", age_label="8+")])
    report = index.ingest("camille", "ecole-loisirs", [row("Après", age_label="9+"), {"title": ""}])
    assert (report.accepted, report.rejected) == (1, 1)
    assert [book.title for book in index.entries("camille")] == ["Après"]


class GoodSource:
    def __init__(self):
        self.calls = 0

    def source_info(self):
        return {"source_id": "youth.test", "display_name": "Youth Test"}

    def search(self, query, *, cursor=None, limit=50):
        self.calls += 1
        return {"items": [row("Remote", age_label="7+")], "next_cursor": "next", "fresh_for_seconds": 30}


class BrokenSource(GoodSource):
    def search(self, query, *, cursor=None, limit=50):
        self.calls += 1
        raise RuntimeError("offline")


def test_source_contract_and_explicit_collection():
    source = GoodSource()
    assert validate_source_contract(source).source_id == "youth.test"
    plugin = YouthCatalogPlugin()
    assert plugin.search_cached("alice") == ()
    assert source.calls == 0
    report = plugin.collect_source("alice", source, "chat")
    assert report.accepted == 1
    assert report.next_cursor == "next"
    assert source.calls == 1
    assert [b.title for b in plugin.search_cached("alice", age=8)] == ["Remote"]


def test_source_failure_is_isolated():
    plugin = YouthCatalogPlugin()
    source = BrokenSource()
    report = plugin.collect_source("alice", source, "chat")
    assert report.accepted == 0
    assert report.rejected == 1
    assert report.errors == ("source search failed",)
    assert plugin.search_cached("alice") == ()


def bibliography_row(record_id="OL1W", **changes):
    value = {
        "record_id": record_id,
        "title": "Livre",
        "authors": ["Auteur enrichi"],
        "isbns": ["9780306406157"],
        "languages": ["eng"],
        "subjects": ["Space", "Adventure"],
        "publishers": ["Open Publisher"],
        "first_publish_year": 1999,
        "notice_url": "https://openlibrary.org/works/" + record_id,
        "cover_url": "https://covers.openlibrary.org/b/id/42-M.jpg",
        "age_ranges": (),
        "metadata": {
            "bibliographic_only": True,
            "age_evidence": "none",
        },
    }
    value.update(changes)
    return value


def test_isbn10_is_canonicalized_to_isbn13_for_cross_source_matching():
    assert normalize_isbn("0-306-40615-2") == "9780306406157"
    a = normalize_record(row("Livre", isbn10="0306406152"), source_id="one")
    b = normalize_record(row("Autre édition", isbn13="9780306406157"), source_id="two")
    assert a.identifiers["isbn"] == "9780306406157"
    assert bibliographic_match(a, b).score == 100


def test_bibliography_overlay_enriches_only_exact_isbn_without_age_provenance_pollution():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Livre", isbn10="0306406152", interest_age="8-10", language=""),
    ])
    report = index.ingest_bibliography(
        "alice", "openlibrary", "9780306406157", [bibliography_row()]
    )
    assert report.accepted == 1
    (book,) = index.entries("alice")
    assert book.identifiers["isbn"] == "9780306406157"
    assert book.identifiers["openlibrary_record_id"] == "OL1W"
    assert book.language == "eng"
    assert {"space", "adventure"} <= set(book.themes)
    assert book.publishers == ("Open Publisher",)
    assert book.published_year == 1999
    assert book.cover_url.endswith("/42-M.jpg")
    assert book.bibliography_sources == ("openlibrary",)
    assert book.provenance == ("ecole-loisirs",)
    assert [(r.age_kind, r.source_id) for r in book.age_ranges] == [("interest", "ecole-loisirs")]
    assert [b.title for b in index.search("alice", source_id="ecole-loisirs")] == ["Livre"]
    assert index.search("alice", source_id="openlibrary") == ()


def test_bibliography_overlay_rejects_wrong_isbn_or_any_age_evidence():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Livre", isbn13="9780306406157", age_label="8+"),
    ])
    wrong = index.ingest_bibliography(
        "alice", "openlibrary", "9780306406157",
        [bibliography_row(isbns=["9783161484100"])],
    )
    assert wrong.accepted == 0 and wrong.rejected == 1

    contaminated = index.ingest_bibliography(
        "alice", "openlibrary", "9780306406157",
        [bibliography_row(age_ranges=[{"age_min": 5}])],
    )
    assert contaminated.accepted == 0 and contaminated.rejected == 1
    assert index.entries("alice")[0].bibliography_sources == ()


def test_ambiguous_bibliography_results_are_not_applied():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Livre", isbn13="9780306406157", age_label="8+"),
    ])
    report = index.ingest_bibliography(
        "alice", "openlibrary", "9780306406157",
        [bibliography_row("OL1W"), bibliography_row("OL2W")],
    )
    assert report.accepted == 0
    assert report.rejected == 2
    assert report.errors == ("ambiguous bibliography result",)
    assert index.entries("alice")[0].bibliography_sources == ()


def test_clear_identity_clears_bibliography_overlay_too():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Livre", isbn13="9780306406157", age_label="8+"),
    ])
    index.ingest_bibliography("alice", "openlibrary", "9780306406157", [bibliography_row()])
    assert index.entries("alice")[0].bibliography_sources == ("openlibrary",)
    index.clear_identity("alice")
    assert index.entries("alice") == ()



def test_find_cached_by_isbn_does_not_depend_on_title():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Titre source très différent", isbn10="0306406152", age_label="8+"),
    ])
    found = index.find("alice", isbn="9780306406157")
    assert len(found) == 1
    assert found[0].title == "Titre source très différent"
    assert found[0].identifiers["isbn"] == "9780306406157"


def test_find_cached_supports_provider_item_and_notice_url():
    index = YouthCatalogIndex()
    index.ingest("alice", "source.one", [
        row(
            "Livre",
            record_id="item-42",
            source_item_id="item-42",
            notice_url="HTTPS://EXAMPLE.ORG/book/42#fragment",
            age_label="9+",
        ),
    ])
    by_item = index.find("alice", provider="source.one", source_item_id="item-42")
    by_url = index.find("alice", notice_url="https://example.org/book/42")
    assert [book.record_id for book in by_item] == ["item-42"]
    assert [book.record_id for book in by_url] == ["item-42"]


def test_find_cached_never_uses_title_author_and_is_identity_scoped():
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [row("Même titre", "Même auteur", age_label="8+")])
    index.ingest("bob", "source", [row("Autre", isbn13="9780306406157", age_label="10+")])
    assert index.find("alice") == ()
    assert index.find("alice", isbn="9780306406157") == ()
    assert [book.title for book in index.find("bob", isbn="9780306406157")] == ["Autre"]


def test_find_cached_preserves_bibliographic_overlay():
    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [
        row("Livre", isbn13="9780306406157", age_label="8+"),
    ])
    index.ingest_bibliography("alice", "openlibrary", "9780306406157", [bibliography_row()])
    found = index.find("alice", isbn="9780306406157")
    assert len(found) == 1
    assert found[0].bibliography_sources == ("openlibrary",)
    assert found[0].publishers == ("Open Publisher",)



def test_ambiguous_partial_and_approximate_age_labels_are_not_evidence():
    for label in ("8 ans", "8", "about 11 years", "recommandé pour 8 ans",
                  "Une aventure 9+ passionnante", "Collection 10–12 ans",
                  "under 8", "Interest age 8-10 extra", "8+\n"):
        assert parse_age_label(label) == (), label
        assert parse_age_label(label, inferred=True) == (), label


def test_explicit_labels_preserve_original_and_source():
    for label, lo, hi in (("  9+  ", 9, None), ("10–12 ans", 10, 12),
                          ("Interest age 8-10", 8, 10), ("0+", 0, None),
                          ("17+", 17, None), ("up to 6 years", 0, 6)):
        (age,) = parse_age_label(label, source_id="Publisher")
        assert (age.age_min, age.age_max) == (lo, hi)
        assert age.source_label == label
        assert age.source_id == "publisher"
        assert age.provenance == ("publisher",)
    assert parse_age_label("18+") == ()
    assert parse_age_label("12–8 ans") == ()


def test_invalid_age_confidence_is_not_evidence():
    for confidence in (float("nan"), float("inf"), -0.1, 1.1, "invalid"):
        assert parse_age_label("8+", confidence=confidence) == ()


def test_cache_filter_rejects_ambiguous_labels_without_remote_collection():
    plugin = YouthCatalogPlugin()
    source = GoodSource()
    plugin.collect_source("alice", source, "")
    calls = source.calls
    for age in (0, 7, 17):
        plugin.search_cached("alice", age=age)
    assert source.calls == calls
    index = YouthCatalogIndex()
    index.ingest("alice", "source", [row("Ambiguous", age_label="8 ans"),
                                       row("Attested", age_label="8+")])
    assert [b.title for b in index.search("alice", age=8)] == ["Attested"]
    assert len(index.entries("alice")) == 2


def test_fresh_installed_package_exposes_both_contributions_without_enabling_them(tmp_path):
    """Regression P0 #859: an undeclared custom category hid catalog.youth."""
    import json
    from pathlib import Path

    from cifereca.plugins import PackageStore, PluginManager, PluginStateStore

    manifest_path = Path(__file__).resolve().parents[1] / "cifereca-plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    package_id = manifest["package"]["id"]
    version = manifest["package"]["version"]
    expected = {row["id"]: row["type"] for row in manifest["plugins"]}
    assert expected == {"catalog.youth": "catalog.youth", "ui.page.youth": "ui.page"}

    store = PackageStore(tmp_path / "packages")
    target = store.version_root(package_id, version)
    target.mkdir(parents=True)
    (target / "cifereca-plugin.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    store.set_active(package_id, version)
    manager = PluginManager(
        package_store=store,
        state_store=PluginStateStore(tmp_path / "plugins-state.json"),
    )
    try:
        manager.activate()
        rows = [row for row in manager.diagnostic() if row["package_id"] == package_id]
        assert {row["plugin_id"]: row["type"] for row in rows} == expected
        assert all(row["state"] == "disabled" and row["enabled"] is False for row in rows)
        assert manager.categories.get("catalog.youth").contract_id == "cifereca.youth-catalog"
        assert store.active_version(package_id) == version
    finally:
        manager.close()


def test_source_diagnostics_list_known_age_sources_and_bibliography_without_network():
    index = YouthCatalogIndex()
    inventory = (
        {"plugin_id": "youth.source.ecole-loisirs", "type": "youth.source",
         "state": "ready", "enabled": True, "package_id": "org.cifereca.youth-source-ecole-loisirs"},
        {"plugin_id": "youth.source.milan", "type": "youth.source",
         "state": "disabled", "enabled": False, "package_id": "org.cifereca.youth-source-milan"},
        {"plugin_id": "youth.bibliography.openlibrary", "type": "youth.bibliography",
         "state": "ready", "enabled": True, "package_id": "org.cifereca.youth-bibliography-openlibrary"},
        {"plugin_id": "other.plugin", "type": "provider", "enabled": True},
    )
    rows = {item["source_id"]: item for item in index.source_diagnostics("alice", inventory)}
    assert {"ecole-loisirs", "milan", "youth.bibliography.openlibrary"}.issubset(rows)
    assert rows["ecole-loisirs"]["enabled"] is True
    assert rows["ecole-loisirs"]["plugin_state"] == "ready"
    assert rows["ecole-loisirs"]["collection_state"] == "not_tested"
    assert rows["milan"]["plugin_state"] == "disabled"
    assert rows["milan"]["collection_state"] == "not_tested"
    assert rows["youth.bibliography.openlibrary"]["family"] == "bibliography"
    assert rows["youth.bibliography.openlibrary"]["collection_state"] == "not_tested"
    assert "other.plugin" not in rows
    assert index.source_diagnostics("") == ()


def test_source_collection_state_is_isolated_per_identity_and_old_cache_survives_error():
    index = YouthCatalogIndex()
    first = index.ingest("alice", "ecole-loisirs", [
        row("Atlas", isbn="9780306406157", age_label="8+")
    ], fresh_for_seconds=180)
    assert first.accepted == 1
    index.record_collection("alice", "ecole-loisirs", state="items",
                            accepted=1, duration_ms=43, next_cursor_present=True)
    index.record_collection("bob", "ecole-loisirs", state="http_error",
                            http_status=429, error_type="HTTPStatusError")
    rows_a = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    rows_b = {x["source_id"]: x for x in index.source_diagnostics("bob")}
    assert rows_a["ecole-loisirs"]["collection_state"] == "items"
    assert rows_a["ecole-loisirs"]["accepted"] == 1
    assert rows_a["ecole-loisirs"]["cache_count"] == 1
    assert rows_a["ecole-loisirs"]["duration_ms"] == 43
    assert rows_a["ecole-loisirs"]["next_cursor_present"] is True
    assert rows_a["ecole-loisirs"]["fresh_until"]
    assert rows_b["ecole-loisirs"]["collection_state"] == "http_error"
    assert rows_b["ecole-loisirs"]["http_status"] == 429
    assert rows_b["ecole-loisirs"]["cache_count"] == 0
    assert rows_b["ecole-loisirs"]["last_success_at_utc"] == ""

    index.record_collection("alice", "ecole-loisirs", state="timeout",
                            error_type="ReadTimeout")
    after = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    assert after["ecole-loisirs"]["collection_state"] == "timeout"
    assert after["ecole-loisirs"]["last_success_at_utc"] == rows_a["ecole-loisirs"]["last_success_at_utc"]
    assert after["ecole-loisirs"]["cache_count"] == 1
    assert len(index.search("alice", "Atlas")) == 1
    assert index.search("bob") == ()
    index.clear_identity("alice")
    clean = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    assert clean["ecole-loisirs"]["collection_state"] == "not_tested"
    assert clean["ecole-loisirs"]["cache_count"] == 0


def test_collection_diagnostics_refuses_raw_exception_text_and_non_terminal_states():
    import pytest

    index = YouthCatalogIndex()
    with pytest.raises(ValueError):
        index.record_collection("alice", "ecole-loisirs", state="success_from_activation")
    index.record_collection(
        "alice", "milan", state="invalid",
        accepted=-1, rejected=900_000,
        duration_ms=9_000_000,
        error_type="ValueError?token=TOP_SECRET",
        http_status=20,
    )
    result = {x["source_id"]: x for x in index.source_diagnostics("alice")}["milan"]
    assert result["accepted"] == 0
    assert result["rejected"] == 100_000
    assert result["duration_ms"] == 600_000
    assert result["error_type"] == ""
    assert result["http_status"] is None
    assert "TOP_SECRET" not in repr(result)


def test_public_catalog_service_exposes_cache_only_identity_diagnostics():
    plugin = YouthCatalogPlugin()
    rows = plugin.source_diagnostics("alice")
    assert any(r["source_id"] == "ecole-loisirs" for r in rows)
    plugin.record_collection("alice", "milan", state="empty_unverified")
    assert {r["source_id"]: r["collection_state"] for r in plugin.source_diagnostics("alice")}["milan"] == "empty_unverified"
    assert {r["source_id"]: r["collection_state"] for r in plugin.source_diagnostics("bob")}["milan"] == "not_tested"


def test_explicit_catalog_collect_source_updates_identity_scoped_diagnostic():
    plugin = YouthCatalogPlugin()
    source = GoodSource()
    assert source.calls == 0
    initial = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}
    assert "youth.test" not in initial
    report = plugin.collect_source("alice", source, "chat")
    assert report.accepted == 1 and report.next_cursor == "next"
    assert source.calls == 1
    collected = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert collected["collection_state"] == "items"
    assert collected["collection_stage"] == "ingest"
    assert collected["cache_count"] == 1
    assert collected["accepted"] == 1 and collected["rejected"] == 0
    assert collected["next_cursor_present"] is True
    assert collected["last_success_at_utc"]
    assert collected["attempted_at_utc"]
    assert collected["duration_ms"] >= 0
    assert source.calls == 1, "Reading diagnostics must not call the source"
    assert "youth.test" not in {r["source_id"] for r in plugin.source_diagnostics("bob")}, (
        "An unknown source collected by Alice must not appear in Bob's identity"
    )


def test_catalog_collect_source_http_failure_preserves_last_good_cache():
    plugin = YouthCatalogPlugin()
    good = GoodSource()
    plugin.collect_source("alice", good, "the")
    old = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert old["cache_count"] == 1
    assert old["collection_state"] == "items"

    class SimulatedHttpFailure(Exception):
        def __init__(self):
            super().__init__("https://secret.example/token=DO-NOT-LOG")
            self.response = type("Response", (), {"status_code": 503})()

    class HTTPSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            raise SimulatedHttpFailure()

    source = HTTPSource()
    report = plugin.collect_source("alice", source, "the")
    assert report.accepted == 0 and report.rejected == 1
    assert source.calls == 1
    state = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert state["collection_state"] == "http_error"
    assert state["collection_stage"] == "search"
    assert state["http_status"] == 503
    # Provider-defined class names are untrusted: only the public error
    # category and attested HTTP status can be exported to diagnostics.
    assert state["error_type"] == "SourceError"
    assert state["last_success_at_utc"] == old["last_success_at_utc"]
    assert state["cache_count"] == 1 and state["fresh_until"]
    assert [x.title for x in plugin.search_cached("alice")] == ["Remote"]
    assert "secret.example" not in repr(state)
    assert "DO-NOT-LOG" not in repr(state)
    assert "Remote" not in repr(state)


def test_catalog_collect_source_rejections_timeout_and_empty_are_distinct():
    plugin = YouthCatalogPlugin()

    class InvalidSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            return {"items": [{"title": ""}], "next_cursor": ""}

    invalid = InvalidSource()
    report = plugin.collect_source("alice", invalid, "")
    assert (report.accepted, report.rejected) == (0, 1)
    assert {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]["collection_state"] == "invalid"

    class TimeoutSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            raise TimeoutError("https://private.invalid/session")

    report = plugin.collect_source("bob", TimeoutSource(), "")
    assert report.rejected == 1
    bob = {r["source_id"]: r for r in plugin.source_diagnostics("bob")}["youth.test"]
    assert bob["collection_state"] == "timeout"
    assert bob["collection_stage"] == "search"
    assert bob["error_type"] == "TimeoutError"
    assert bob["cache_count"] == 0
    assert "private.invalid" not in repr(bob)

    class EmptySource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            return {"items": (), "next_cursor": ""}

    assert plugin.collect_source("carol", EmptySource(), "").accepted == 0
    carol = {r["source_id"]: r for r in plugin.source_diagnostics("carol")}["youth.test"]
    assert carol["collection_state"] == "empty_unverified"
    assert carol["collection_stage"] == "ingest"
    assert carol["last_success_at_utc"]



def test_collection_failure_diagnostic_uses_explicit_http_status_not_exception_text():
    from types import SimpleNamespace
    from youth_catalog import collection_failure_diagnostic

    error = RuntimeError("private=https://session.example/ HTTP 503")
    # Never classify a response by scraping potentially sensitive error text.
    assert collection_failure_diagnostic(error) == {
        "state": "error", "error_type": "RuntimeError", "http_status": None,
        "request_count": None,
    }
    error.http_status = 503
    assert collection_failure_diagnostic(error) == {
        "state": "http_error", "error_type": "RuntimeError", "http_status": 503,
        "request_count": None,
    }
    error.http_status = True  # bool is not a valid HTTP status
    assert collection_failure_diagnostic(error)["state"] == "error"
    error.http_status = 900
    assert collection_failure_diagnostic(error)["state"] == "error"

    class ResponseError(RuntimeError):
        def __init__(self):
            super().__init__("sensitive URL")
            self.http_status = 401
            self.response = SimpleNamespace(status_code=429)

    assert collection_failure_diagnostic(ResponseError()) == {
        "state": "http_error", "error_type": "ResponseError", "http_status": 429,
        "request_count": None,
    }
    timeout = TimeoutError("private location")
    timeout.http_status = 503
    assert collection_failure_diagnostic(timeout)["state"] == "timeout"


def test_collect_source_runtime_http_failure_is_typed_and_keeps_identity_isolation():
    from youth_catalog import YouthCatalogPlugin
    plugin = YouthCatalogPlugin()
    source = GoodSource()
    assert plugin.collect_source("alice", source, "some").accepted == 1
    old = {row["source_id"]: row for row in plugin.source_diagnostics("alice")}["youth.test"]
    assert old["cache_count"] == 1

    class PublisherHTTP(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            failure = RuntimeError("Publisher HTTP 503")
            failure.http_status = 503
            raise failure

    report = plugin.collect_source("alice", PublisherHTTP(), "query")
    assert report.rejected == 1
    now = {row["source_id"]: row for row in plugin.source_diagnostics("alice")}["youth.test"]
    assert now["collection_state"] == "http_error"
    assert now["http_status"] == 503
    assert now["error_type"] == "RuntimeError"
    assert now["cache_count"] == 1
    assert now["last_success_at_utc"] == old["last_success_at_utc"]
    bob = {row["source_id"]: row for row in plugin.source_diagnostics("bob")}
    assert "youth.test" not in bob  # a private new source must not leak across identities
    assert bob["ecole-loisirs"]["collection_state"] == "not_tested"



def test_rejection_categories_are_bounded_and_never_reveal_invalid_input():
    from youth_catalog import safe_ingest_rejection_categories
    raw = (
        "item[0]: title is required",
        "item[1]: invalid publisher https://private.example/?token=secret",
        "item[2]: title is required",
    )
    summary = safe_ingest_rejection_categories(raw)
    assert summary == {"missing_title": 2, "other": 1}
    assert "private.example" not in repr(summary)
    assert safe_ingest_rejection_categories(()) == {
        "missing_title": 0, "other": 0,
    }


def test_youth_collection_metrics_are_local_and_identity_isolated():
    from youth_catalog import YouthCatalogPlugin

    plugin = YouthCatalogPlugin()
    assert plugin.source_diagnostics("alice")[0]["received_count"] is None
    assert plugin.source_diagnostics("alice")[0]["requested_limit"] is None
    assert plugin.source_diagnostics("bob")[0]["received_count"] is None
    source = GoodSource()
    report = plugin.collect_source("alice", source, "chat", limit=7)
    assert report.accepted == 1 and source.calls == 1
    a = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]
    assert a["collection_state"] == "items"
    assert a["received_count"] == 1
    assert a["requested_limit"] == 7
    assert a["rejection_categories"] == {}
    assert source.calls == 1  # diagnostic read is local

    class MalformedSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            return {"items": [{"title": ""}, {"title": "valid"}]}

    malformed = MalformedSource()
    report = plugin.collect_source("alice", malformed, "chat", limit=3)
    assert (report.accepted, report.rejected) == (1, 1)
    current = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]
    assert current["received_count"] == 2
    assert current["requested_limit"] == 3
    assert current["rejection_categories"] == {"missing_title": 1}
    assert current["cache_count"] == 1
    assert "title is required" not in repr(current)
    assert "youth.test" not in {x["source_id"] for x in plugin.source_diagnostics("bob")}


def test_failed_youth_fetch_marks_received_count_unknown_and_preserves_old_cache():
    from youth_catalog import YouthCatalogPlugin

    plugin = YouthCatalogPlugin()
    plugin.collect_source("alice", GoodSource(), "Atlas", limit=5)
    old = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]

    class FailedSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            raise RuntimeError("private=https://example.org/token")

    failed = FailedSource()
    report = plugin.collect_source("alice", failed, "Atlas", limit=2)
    assert report.rejected == 1
    after = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]
    assert after["collection_state"] == "error"
    assert after["received_count"] is None
    assert after["requested_limit"] == 2
    assert after["cache_count"] == old["cache_count"] == 1
    assert after["last_success_at_utc"] == old["last_success_at_utc"]
    assert after["rejection_categories"] == {}
    assert "token" not in repr(after)
    assert failed.calls == 1


def test_youth_collection_metrics_whitelist_rejection_types_and_values():
    index = YouthCatalogIndex()
    index.record_collection(
        "alice", "ecole-loisirs", state="invalid",
        received_count=True, requested_limit="100",
        rejection_categories={
            "missing_title": 200_000, "other": -1,
            "URL=https://private.example/token": 3,
        },
    )
    result = {x["source_id"]: x for x in index.source_diagnostics("alice")}["ecole-loisirs"]
    assert result["received_count"] is None
    assert result["requested_limit"] is None
    assert result["rejection_categories"] == {"missing_title": 100_000}
    assert "private.example" not in repr(result)
    index.record_collection(
        "alice", "ecole-loisirs", state="items",
        received_count=1, requested_limit=2,
        rejection_categories={"missing_title": True, "other": 1},
    )
    result = {x["source_id"]: x for x in index.source_diagnostics("alice")}["ecole-loisirs"]
    assert result["received_count"] == 1
    assert result["requested_limit"] == 2
    assert result["rejection_categories"] == {"other": 1}


def test_source_diagnostic_stage_is_strictly_whitelisted():
    index = YouthCatalogIndex()
    index.record_collection("alice", "milan", state="error", stage="fetch?token=SECRET")
    row = {r["source_id"]: r for r in index.source_diagnostics("alice")}["milan"]
    assert row["collection_stage"] == ""
    assert "SECRET" not in repr(row)
    index.record_collection("alice", "milan", state="http_error", stage="listing", http_status=429)
    row = {r["source_id"]: r for r in index.source_diagnostics("alice")}["milan"]
    assert row["collection_stage"] == "listing"
    assert row["http_status"] == 429


def test_youth_per_call_http_counts_are_strict_and_isolated_by_identity():
    from youth_catalog import YouthCatalogPlugin, collection_failure_diagnostic

    plugin = YouthCatalogPlugin()
    index = plugin.index
    for value in (True, -1, "8", 1.5):
        index.record_collection("alice", "ecole-loisirs", state="items", request_count=value)
        assert {r["source_id"]: r for r in index.source_diagnostics("alice")}["ecole-loisirs"]["request_count"] is None
    index.record_collection("alice", "ecole-loisirs", state="items", request_count=900_000)
    assert {r["source_id"]: r for r in index.source_diagnostics("alice")}["ecole-loisirs"]["request_count"] == 100_000

    class CountedSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            return {"items": [{"title": "One"}], "collection_request_count": 3}

    counted = CountedSource()
    assert plugin.collect_source("alice", counted, "One").accepted == 1
    alice = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert alice["request_count"] == 3
    assert counted.calls == 1
    assert "youth.test" not in {r["source_id"] for r in plugin.source_diagnostics("bob")}

    class FailedCountedSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            error = RuntimeError("private url https://internal.invalid/session")
            error.collection_request_count = 2
            raise error

    failed = FailedCountedSource()
    assert plugin.collect_source("alice", failed, "One").rejected == 1
    latest = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert latest["collection_state"] == "error"
    assert latest["request_count"] == 2
    assert latest["cache_count"] == 1
    assert "session" not in repr(latest)
    assert failed.calls == 1
    assert collection_failure_diagnostic(RuntimeError("HTTP 503"))["request_count"] is None


def test_youth_collection_cancelled_exception_has_no_fabricated_http_count():
    from youth_catalog import YouthCatalogPlugin, collection_failure_diagnostic

    assert collection_failure_diagnostic(Exception("no evidence"))["request_count"] is None
    assert collection_failure_diagnostic(TimeoutError("local"))["state"] == "timeout"
    assert collection_failure_diagnostic(__import__("concurrent.futures", fromlist=["CancelledError"]).CancelledError())["state"] == "cancelled"
    plugin = YouthCatalogPlugin()

    class CancelledSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            raise __import__("concurrent.futures", fromlist=["CancelledError"]).CancelledError()

    assert plugin.collect_source("alice", CancelledSource(), "").rejected == 1
    row = {r["source_id"]: r for r in plugin.source_diagnostics("alice")}["youth.test"]
    assert row["collection_state"] == "cancelled"
    assert row["request_count"] is None


def test_nonbook_ean_never_becomes_isbn_or_confirmed_edition_match():
    for ean in ("3575678940134", "3127020507597"):
        assert normalize_isbn(ean) == ""
        a = normalize_record(row("Peluche", isbn13=ean), source_id="ecole-loisirs")
        b = normalize_record(row("Autre produit", isbn13=ean), source_id="other")
        assert "isbn" not in a.identifiers
        assert "isbn" not in b.identifiers
        assert bibliographic_match(a, b).status != "confirmed"

    index = YouthCatalogIndex()
    index.ingest("alice", "ecole-loisirs", [row("Produit A", isbn13="3575678940134")])
    index.ingest("alice", "other", [row("Produit B", isbn13="3575678940134")])
    assert len(index.entries("alice")) == 2


def test_isbn13_978_and_979_and_isbn10_are_unchanged():
    assert normalize_isbn("9782211309684") == "9782211309684"
    assert normalize_isbn("9791234567896") == "9791234567896"
    assert normalize_isbn("0306406152") == "9780306406157"
    assert normalize_isbn("9780000000000") == ""


def test_malformed_page_is_invalid_and_preserves_previous_identity_cache():
    """Malformed transport structure is not an authoritative empty result."""
    plugin = YouthCatalogPlugin()
    seed = GoodSource()
    assert plugin.collect_source("alice", seed, "Atlas").accepted == 1
    previous = {
        row["source_id"]: row
        for row in plugin.source_diagnostics("alice")
    }["youth.test"]
    assert previous["cache_count"] == 1

    malformed = (
        None,
        "private=https://example.org/session/token",
        {"unexpected": []},
        {"items": None},
        {"items": "private records"},
        {"items": {"title": "secret"}},
        {"items": 3},
        {"items": [], "next_cursor": {"private": "token"}},
        {"items": [], "fresh_for_seconds": "private"},
    )
    for raw in malformed:
        class MalformedSource(GoodSource):
            def search(self, query, *, cursor=None, limit=50):
                self.calls += 1
                return raw

        source = MalformedSource()
        report = plugin.collect_source("alice", source, "Atlas")
        assert (report.accepted, report.rejected) == (0, 1)
        assert source.calls == 1
        current = {
            row["source_id"]: row for row in plugin.source_diagnostics("alice")
        }["youth.test"]
        assert current["collection_state"] == "invalid"
        assert current["collection_stage"] == "parsing"
        assert current["cache_count"] == 1
        assert current["last_success_at_utc"] == previous["last_success_at_utc"]
        assert current["request_count"] is None
        assert "private" not in repr(current)
        assert "token" not in repr(current)
    assert {row["source_id"]: row for row in plugin.source_diagnostics("bob")}["ecole-loisirs"]["collection_state"] == "not_tested"


def test_structural_invalidity_retains_only_attested_call_count():
    plugin = YouthCatalogPlugin()

    class BrokenSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            return {"items": "secret",
                    "collection_request_count": 2,
                    "next_cursor": "sensitive"}

    src = BrokenSource()
    assert plugin.collect_source("alice", src, "x").rejected == 1
    actual = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]
    assert actual["collection_state"] == "invalid"
    assert actual["request_count"] == 2
    assert actual["received_count"] is None
    assert actual["requested_limit"] == 50
    assert actual["cache_count"] == 0
    assert "sensitive" not in repr(actual)


def test_valid_empty_page_and_legacy_iterable_remain_compatible():
    from youth_catalog import _extract_page, InvalidYouthSourcePage

    assert _extract_page({"items": [], "next_cursor": "", "fresh_for_seconds": "60"}) == ([], "", 60)
    assert _extract_page({"results": [{"title": "X"}]})[0] == [{"title": "X"}]
    assert _extract_page([{"title": "X"}])[0] == [{"title": "X"}]
    for value in ({"items": [], "fresh_for_seconds": True},
                  {"items": [], "next_cursor": ["2"]},
                  {"items": [], "fresh_for_seconds": -1}):
        with pytest.raises(InvalidYouthSourcePage):
            _extract_page(value)

    plugin = YouthCatalogPlugin()

    class ValidEmpty(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            return {"items": [], "next_cursor": ""}

    assert plugin.collect_source("alice", ValidEmpty(), "none").accepted == 0
    result = {x["source_id"]: x for x in plugin.source_diagnostics("alice")}["youth.test"]
    assert result["collection_state"] == "empty_unverified"
    assert result["received_count"] == 0



def test_typed_paginated_publisher_layout_failure_preserves_identity_cache():
    from youth_catalog import YouthCatalogPlugin, collection_failure_diagnostic

    class InvalidCatalogueStructureError(RuntimeError):
        pass

    error = InvalidCatalogueStructureError(
        "private title https://private.example/?token=secret"
    )
    error.collection_request_count = 1
    assert collection_failure_diagnostic(error) == {
        "state": "invalid", "error_type": "InvalidCatalogueStructureError",
        "http_status": None, "request_count": 1,
    }

    catalog = YouthCatalogPlugin()
    assert catalog.collect_source("alice", GoodSource(), "test").accepted == 1
    previous = {d["source_id"]: d for d in catalog.source_diagnostics("alice")}["youth.test"]

    class ChangedHtmlSource(GoodSource):
        def search(self, query, *, cursor=None, limit=50):
            self.calls += 1
            raise error

    source = ChangedHtmlSource()
    failure = catalog.collect_source("alice", source, "test")
    assert failure.rejected == 1
    current = {d["source_id"]: d for d in catalog.source_diagnostics("alice")}["youth.test"]
    assert current["collection_state"] == "invalid"
    assert current["collection_stage"] == "search"
    assert current["request_count"] == 1
    assert current["cache_count"] == previous["cache_count"] == 1
    assert current["last_success_at_utc"] == previous["last_success_at_utc"]
    assert "private.example" not in repr(current)
    assert "token=secret" not in repr(current)
    assert source.calls == 1
    assert "youth.test" not in {
        d["source_id"] for d in catalog.source_diagnostics("bob")
    }


def test_youth_pipeline_projection_is_strictly_sanitized_and_per_identity():
    from youth_catalog import YouthCatalogPlugin, sanitize_collection_pipeline

    data = {
        "query_mode": "remote", "effective_limit": 40,
        "anchor_count": 22, "candidate_count": 20, "analyzed_count": 20,
        "isbn_valid_count": 20, "age_qualified_count": 18,
        "age_unknown_count": 2, "returned_items": 20,
        "html_bytes": 8322, "http_status": 200,
        "html_sha256_prefix": "abcdef0123456789",
        "http_ms": 12, "parse_ms": 3,
        "rejection_reasons": {"isbn_invalid": 2, "private": "secret"},
        "raw_html": "<p>Secret cookie</p>",
        "signed_url": "https://private.example/token",
        "isbn13": "9782211348171",
        "title": "Never stored",
    }
    sanitized = sanitize_collection_pipeline(data)
    assert sanitized["query_mode"] == "remote"
    assert sanitized["candidate_count"] == 20
    assert sanitized["rejection_reasons"] == {"isbn_invalid": 2}
    for secret in ("Secret cookie", "private.example", "9782211348171", "Never stored"):
        assert secret not in repr(sanitized)

    plugin = YouthCatalogPlugin()
    plugin.record_collection("alice", "ecole-loisirs", state="items", pipeline=data)
    alice = {
        row["source_id"]: row for row in plugin.source_diagnostics("alice")
    }["ecole-loisirs"]
    assert alice["pipeline"]["candidate_count"] == 20
    assert alice["pipeline"]["rejection_reasons"] == {"isbn_invalid": 2}
    assert {row["source_id"]: row for row in plugin.source_diagnostics("bob")}["ecole-loisirs"]["pipeline"] == {}
    # Returned values are detached; callers cannot overwrite a later snapshot.
    alice["pipeline"]["rejection_reasons"]["isbn_invalid"] = 999
    again = {
        row["source_id"]: row for row in plugin.source_diagnostics("alice")
    }["ecole-loisirs"]
    assert again["pipeline"]["rejection_reasons"]["isbn_invalid"] == 2
    for secret in ("Secret cookie", "private.example", "9782211348171", "Never stored"):
        assert secret not in repr(again)


def test_youth_pipeline_filters_invalid_types_out_before_snapshot():
    from youth_catalog import sanitize_collection_pipeline

    result = sanitize_collection_pipeline({
        "query_mode": "private_query",
        "effective_limit": "40", "anchor_count": True,
        "candidate_count": -1, "analyzed_count": 100001,
        "http_status": 999, "html_bytes": 2000001,
        "html_sha256_prefix": "z" * 16,
        "http_ms": False, "parse_ms": -1,
        "truncated_by_limit": "false",
        "rejection_reasons": {
            "isbn_invalid": 100001, "duplicate": True,
            "age_unattested": 2,
            "raw_html": "<script>private</script>",
        },
    })
    assert result == {"rejection_reasons": {"age_unattested": 2}}
    assert sanitize_collection_pipeline("private string") == {}


def test_explicit_catalog_collect_source_keeps_funnel_from_one_call_only():
    from youth_catalog import YouthCatalogPlugin

    class Publisher:
        def source_info(self):
            return {"source_id": "ecole-loisirs", "display_name": "École"}
        def search(self, query, *, cursor=None, limit=50):
            return {
                "items": [{"title": "One book", "isbn13": "9780306406157"}],
                "collection_request_count": 1,
                "collection_pipeline": {
                    "query_mode": "remote", "effective_limit": 40,
                    "candidate_count": 3, "analyzed_count": 2,
                    "isbn_valid_count": 1, "age_qualified_count": 1,
                    "returned_items": 1,
                    "rejection_reasons": {"isbn_invalid": 1},
                },
            }

    plugin = YouthCatalogPlugin()
    assert plugin.collect_source("alice", Publisher(), "loup").accepted == 1
    a = {row["source_id"]: row for row in plugin.source_diagnostics("alice")}["ecole-loisirs"]
    assert a["request_count"] == 1
    assert a["pipeline"]["candidate_count"] == 3
    assert a["pipeline"]["rejection_reasons"] == {"isbn_invalid": 1}
    assert a["requested_limit"] == 50
    assert a["pipeline"]["effective_limit"] == 40
    assert {row["source_id"]: row for row in plugin.source_diagnostics("bob")}["ecole-loisirs"]["collection_state"] == "not_tested"

def test_source_publisher_year_and_cover_survive_normalization_and_cache():
    # Publisher facts from publisher and a publisher's cover are already
    # available on source records. They must not disappear before GTK.
    raw = row(
        "C'est quoi, l'écologie ?", isbn13="9780306406157",
        publishers=("Éditions Milan", "Bayard Jeunesse"),
        published_at="2025-08-26",
        cover_url="https://imagine.bayard.io/edition/couvertures/9780306406157.jpg",
        age_label="9+",
    )
    index = YouthCatalogIndex()
    report = index.ingest("alice", "milan", (raw,))
    assert report.accepted == 1
    book = index.entries("alice")[0]
    assert book.publishers == ("Éditions Milan", "Bayard Jeunesse")
    assert book.published_year == 2025
    assert book.cover_url == raw["cover_url"]
    assert book.identifiers["isbn"] == "9780306406157"
    assert index.entries("bob") == ()


def test_attested_fixture_youth_fields_survive_without_openlibrary_overlay():
    record = normalize_record(
        row("The Moon", publisher="Test Publisher", published_year=2018),
        source_id="fixture_youth",
    )
    assert record.publishers == ("Test Publisher",)
    assert record.published_year == 2018
    assert record.bibliography_sources == ()


def test_youth_public_cover_and_publication_require_safe_evidence():
    bad = normalize_record(
        row(
            "Un livre",
            cover_url="https://images.test/cover.jpg?token=secret",
            publication_date="2024-02-30",
        ), source_id="milan",
    )
    assert bad.cover_url == ""
    assert bad.published_year is None
    for value in ("file:///home/user/private.png", "http://images.test/a.jpg",
                  "https://user:password@images.test/a.jpg"):
        assert normalize_record(
            row("Un livre", thumbnail_url=value), source_id="milan",
        ).cover_url == ""
    assert normalize_record(
        row("Un livre", publication_year="2024"), source_id="milan",
    ).published_year == 2024
    assert normalize_record(
        row("Un livre", published_year=True), source_id="milan",
    ).published_year is None


def test_confirmed_same_isbn_merge_retains_rich_facts_from_second_source():
    index = YouthCatalogIndex()
    index.ingest("alice", "fixture_youth", [
        row("The Moon", isbn="9780306406157", authors=["A"]),
    ])
    index.ingest("alice", "milan", [
        row(
            "The Moon", isbn="9780306406157", authors=["B"],
            publishers=["Milan"], published_year=2025,
            cover_url="https://example.org/cover.jpg",
        ),
    ])
    entries = index.entries("alice")
    assert len(entries) == 1
    book = entries[0]
    assert book.authors == ("A", "B")
    assert book.publishers == ("Milan",)
    assert book.published_year == 2025
    assert book.cover_url == "https://example.org/cover.jpg"
    assert book.provenance == ("fixture_youth", "milan")


def test_confirmed_merge_retains_two_distinct_publishers_without_double_counting():
    index = YouthCatalogIndex()
    index.ingest("alice", "milan", [
        row("Livre", isbn="9780306406157", publishers=["Milan", "Bayard"]),
    ])
    index.ingest("alice", "second", [
        row("Livre", isbn="9780306406157", publishers=["Milan", "Autre"]),
    ])
    assert index.entries("alice")[0].publishers == ("Milan", "Bayard", "Autre")


def test_youth_publisher_collection_and_synopsis_survive_normalization_and_merge():
    from youth_catalog import _merge
    first = normalize_record({
        "title": "Un loup à ma porte",
        "record_id": "loup-a-ma-porte",
        "source_item_id": "loup-a-ma-porte",
        "notice_url": "https://www.ecoledesloisirs.fr/livre/loup-a-ma-porte",
        "age_ranges": [{
            "age_min": 13, "age_max": None, "age_kind": "publisher",
            "source_label": "13 ans et +", "source_id": "ecole-loisirs",
        }],
        "collection": "Medium 14+",
        "description": "Résumé public de l'éditeur",
        "cover_url": "https://www.ecoledesloisirs.fr/images/cover.webp",
    }, source_id="ecole-loisirs")
    assert first.collection == "Medium 14+"
    assert first.description == "Résumé public de l'éditeur"
    assert first.identifiers["source_item_id"] == "loup-a-ma-porte"
    assert "isbn" not in first.identifiers
    assert first.age_ranges[0].age_min == 13
    assert first.age_ranges[0].age_max is None
    assert first.cover_url.endswith("/images/cover.webp")
    second = normalize_record({
        "title": "Un loup à ma porte",
        "record_id": "loup-a-ma-porte",
        "source_item_id": "loup-a-ma-porte",
        "notice_url": "https://www.ecoledesloisirs.fr/livre/loup-a-ma-porte",
    }, source_id="ecole-loisirs")
    assert _merge(second, first).description == first.description
    assert _merge(second, first).collection == first.collection



def test_youth_ingest_does_not_expose_untrusted_record_errors():
    from youth_catalog import YouthCatalogIndex, safe_ingest_rejection_categories

    class SecretRow:
        @property
        def title(self):
            raise RuntimeError("https://private.invalid/?token=SECRET")

    index = YouthCatalogIndex()
    previous = index.ingest("alice", "milan", [{"title": "Before"}])
    assert previous.accepted == 1
    report = index.ingest("alice", "milan", [
        {"title": ""},
        {"title": None},
        SecretRow(),
    ])
    assert report.accepted == 0 and report.rejected == 3
    assert report.errors == (
        "item[0]: title is required",
        "item[1]: title is required",
        "item[2]: invalid record",
    )
    assert safe_ingest_rejection_categories(report.errors) == {
        "missing_title": 2, "other": 1,
    }
    assert "SECRET" not in repr(report)
    assert [b.title for b in index.entries("alice")] == ["Before"]


def test_bibliography_ingest_does_not_expose_private_errors():
    from youth_catalog import YouthCatalogIndex

    index = YouthCatalogIndex()
    report = index.ingest_bibliography("alice", "openlibrary", "9780306406157", [
        {"metadata": {"age_evidence": "https://signed.invalid/SECRET"}},
    ])
    assert report.accepted == 0 and report.rejected == 1
    assert report.errors == ("item[0]: invalid bibliography record",)
    assert "SECRET" not in repr(report)


def test_youth_collect_search_error_reports_generic_code():
    from youth_catalog import YouthCatalogPlugin

    class SecretSearch:
        def source_info(self):
            return {"source_id": "test", "display_name": "Test"}
        def search(self, *args, **kwargs):
            raise RuntimeError("Authorization: Bearer SECRET https://private.invalid/?token=SECRET")

    plugin = YouthCatalogPlugin()
    report = plugin.collect_source("alice", SecretSearch(), "chat")
    assert report.accepted == 0 and report.rejected == 1
    assert report.errors == ("source search failed",)
    assert "SECRET" not in repr(report)
