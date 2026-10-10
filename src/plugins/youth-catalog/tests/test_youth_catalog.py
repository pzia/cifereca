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
        source_id="booktrust",
    )
    rng = record.age_ranges[0]
    assert (rng.age_min, rng.age_max, rng.age_kind, rng.source_label, rng.confidence) == (
        8, 10, "reading", "Reading age 8-10", 0.95
    )
    assert rng.source_id == "booktrust"


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
    index.ingest("camille", "booktrust", [row("Notice attestée", age_label="8+")], fresh_for_seconds=180)
    index.ingest("camille", "milan", [row("Autre source", age_label="9+")])
    index.ingest("alex", "booktrust", [row("Notice Alex", age_label="10+")])
    previous_freshness = index.fresh_until("camille", "booktrust")
    previous = index.entries("camille")
    report = index.ingest(
        "camille", "booktrust", [{"title": ""}, {"title": None}],
        fresh_for_seconds=300, replace_source=True,
    )
    assert report.accepted == 0 and report.rejected == 2
    assert len(report.errors) == 2
    assert report.fresh_until == previous_freshness
    assert index.entries("camille") == previous
    assert index.fresh_until("camille", "booktrust") == previous_freshness
    assert [book.title for book in index.entries("alex")] == ["Notice Alex"]


def test_empty_valid_source_refresh_clears_only_target_and_obsolete_ttl():
    index = YouthCatalogIndex()
    index.ingest("camille", "booktrust", [row("Avant", age_label="8+")], fresh_for_seconds=120)
    index.ingest("camille", "milan", [row("Milan", age_label="9+")])
    assert index.fresh_until("camille", "booktrust")
    report = index.ingest("camille", "booktrust", [], replace_source=True)
    assert report.accepted == report.rejected == 0
    assert [book.title for book in index.entries("camille")] == ["Milan"]
    assert index.fresh_until("camille", "booktrust") == ""
    assert [book.title for book in index.search("camille", age=9)] == ["Milan"]


def test_mixed_valid_and_invalid_refresh_keeps_accepted_records():
    index = YouthCatalogIndex()
    index.ingest("camille", "booktrust", [row("Avant", age_label="8+")])
    report = index.ingest("camille", "booktrust", [row("Après", age_label="9+"), {"title": ""}])
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
    assert report.errors == ("search: offline",)
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
    index.ingest("alice", "booktrust", [
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
    assert book.provenance == ("booktrust",)
    assert [(r.age_kind, r.source_id) for r in book.age_ranges] == [("interest", "booktrust")]
    assert [b.title for b in index.search("alice", source_id="booktrust")] == ["Livre"]
    assert index.search("alice", source_id="openlibrary") == ()


def test_bibliography_overlay_rejects_wrong_isbn_or_any_age_evidence():
    index = YouthCatalogIndex()
    index.ingest("alice", "booktrust", [
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
    index.ingest("alice", "booktrust", [
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
    index.ingest("alice", "booktrust", [
        row("Livre", isbn13="9780306406157", age_label="8+"),
    ])
    index.ingest_bibliography("alice", "openlibrary", "9780306406157", [bibliography_row()])
    assert index.entries("alice")[0].bibliography_sources == ("openlibrary",)
    index.clear_identity("alice")
    assert index.entries("alice") == ()



def test_find_cached_by_isbn_does_not_depend_on_title():
    index = YouthCatalogIndex()
    index.ingest("alice", "booktrust", [
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
    index.ingest("alice", "booktrust", [
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
        {"plugin_id": "youth.source.booktrust", "type": "youth.source",
         "state": "ready", "enabled": True, "package_id": "org.cifereca.youth-source-booktrust"},
        {"plugin_id": "youth.source.milan", "type": "youth.source",
         "state": "disabled", "enabled": False, "package_id": "org.cifereca.youth-source-milan"},
        {"plugin_id": "youth.bibliography.openlibrary", "type": "youth.bibliography",
         "state": "ready", "enabled": True, "package_id": "org.cifereca.youth-bibliography-openlibrary"},
        {"plugin_id": "other.plugin", "type": "provider", "enabled": True},
    )
    rows = {item["source_id"]: item for item in index.source_diagnostics("alice", inventory)}
    assert {"booktrust", "milan", "usborne", "ecole-loisirs",
            "youth.bibliography.openlibrary"}.issubset(rows)
    assert rows["booktrust"]["enabled"] is True
    assert rows["booktrust"]["plugin_state"] == "ready"
    assert rows["booktrust"]["collection_state"] == "not_tested"
    assert rows["milan"]["plugin_state"] == "disabled"
    assert rows["milan"]["collection_state"] == "not_tested"
    assert rows["usborne"]["plugin_state"] == "not_installed"
    assert rows["youth.bibliography.openlibrary"]["family"] == "bibliography"
    assert rows["youth.bibliography.openlibrary"]["collection_state"] == "not_tested"
    assert "other.plugin" not in rows
    assert index.source_diagnostics("") == ()


def test_source_collection_state_is_isolated_per_identity_and_old_cache_survives_error():
    index = YouthCatalogIndex()
    first = index.ingest("alice", "booktrust", [
        row("Atlas", isbn="9780306406157", age_label="8+")
    ], fresh_for_seconds=180)
    assert first.accepted == 1
    index.record_collection("alice", "booktrust", state="items",
                            accepted=1, duration_ms=43, next_cursor_present=True)
    index.record_collection("bob", "booktrust", state="http_error",
                            http_status=429, error_type="HTTPStatusError")
    rows_a = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    rows_b = {x["source_id"]: x for x in index.source_diagnostics("bob")}
    assert rows_a["booktrust"]["collection_state"] == "items"
    assert rows_a["booktrust"]["accepted"] == 1
    assert rows_a["booktrust"]["cache_count"] == 1
    assert rows_a["booktrust"]["duration_ms"] == 43
    assert rows_a["booktrust"]["next_cursor_present"] is True
    assert rows_a["booktrust"]["fresh_until"]
    assert rows_b["booktrust"]["collection_state"] == "http_error"
    assert rows_b["booktrust"]["http_status"] == 429
    assert rows_b["booktrust"]["cache_count"] == 0
    assert rows_b["booktrust"]["last_success_at_utc"] == ""

    index.record_collection("alice", "booktrust", state="timeout",
                            error_type="ReadTimeout")
    after = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    assert after["booktrust"]["collection_state"] == "timeout"
    assert after["booktrust"]["last_success_at_utc"] == rows_a["booktrust"]["last_success_at_utc"]
    assert after["booktrust"]["cache_count"] == 1
    assert len(index.search("alice", "Atlas")) == 1
    assert index.search("bob") == ()
    index.clear_identity("alice")
    clean = {x["source_id"]: x for x in index.source_diagnostics("alice")}
    assert clean["booktrust"]["collection_state"] == "not_tested"
    assert clean["booktrust"]["cache_count"] == 0


def test_collection_diagnostics_refuses_raw_exception_text_and_non_terminal_states():
    import pytest

    index = YouthCatalogIndex()
    with pytest.raises(ValueError):
        index.record_collection("alice", "booktrust", state="success_from_activation")
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
    assert state["error_type"] == "SimulatedHttpFailure"
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
