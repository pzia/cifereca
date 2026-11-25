"""Source-declared classification survives C retention; no inferred facts."""
import json

import pytest

from cifereca.core import notice_c_cover_index as index
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.public_search_projection import (
    declared_public_classification, decode_public_classification,
)
from cifereca.core.search import filter_loaded_results
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
from tests.test_core_p0_6366_c_first_known_search import _known


def _publish(db, labels, *, record="edition-a", at=100, provider=None):
    options = {"source_provider": provider} if provider is not None else {}
    row = verified_public(record_id=record, title="Même titre", **options)
    row.metadata = labels
    source = persist(db, row, at=at)
    return source, index.persist_exact_public_search_cover(db, source)


def test_source_classifications_survive_eviction_restart_and_local_filter(tmp_path):
    path = tmp_path / "classifications.sqlite"
    db = Storage(path)
    labels = {"subjects": ["Astronomie", "Jeunesse"], "genre": "Documentaire",
              "keywords": ["Étoiles"], "tags": ["illustré"], "themes": "Sciences"}
    try:
        source, first = _publish(db, labels)
        second_source, second = _publish(db, {"subjects": ["Cuisine"]}, record="edition-b")
        assert first.document != second.document
        assert first.sources[0].public_bibliography.classification.wire() == labels
        live, = search_public_notice_c(db, "astronomie", field="subject", now=100).hits
        assert live.document == first.document
        labels["subjects"].append("mutation du DTO")
        # No alias into a retained decoded immutable observation.
        copied = first.sources[0].public_bibliography.classification.wire()
        copied["subjects"].append("mutation de la copie")
        assert first.sources[0].public_bibliography.classification.wire()["subjects"] == ["Astronomie", "Jeunesse"]
        for saved in (source, second_source):
            assert evict_notice_cover(db, saved.document, expected_epoch=saved.epoch)
    finally:
        db.close()
    db = Storage(path)
    try:
        page = search_public_notice_c(db, "même titre", now=100000)
        assert len(page.hits) == 2 and all(hit.stale for hit in page.hits)
        rows = merge_public_notice_c_known_rows([], page, identity_id="alice")
        filtered = filter_loaded_results(rows, "astronomie", field="subject")
        assert len(filtered) == 1 and filtered[0]["item_id"] == "edition-a"
        assert filtered[0]["metadata"]["genre"] == "Documentaire"
        assert filtered[0]["metadata"]["keywords"] == ["Étoiles"]
        assert not filtered[0]["local"] and filtered[0]["local_path"] is None
        assert not any("age" in key or "rights" in key for key in filtered[0]["metadata"])
        assert db.entitlements() == [] and db.acquisitions() == []
        for query, field in (("astronomie", "all"), ("astronomie", "subject"),
                             ("étoiles", "all"), ("documentaire", "subject")):
            selected = search_public_notice_c(db, query, field=field, now=100000)
            assert len(selected.hits) == 1 and selected.hits[0].document == first.document, (query, field)
            assert selected.status == "partial"  # Known facts, never provider coverage.
            known = _known(db, query, field=field)
            assert len(known) == 1 and known[0]["source"] == "notice_c_public"
            assert known[0]["_cover_document_ref"] == first.document
    finally:
        db.close()


def test_changed_classification_advances_only_its_exact_c_revision(tmp_path):
    db = Storage(tmp_path / "changes.sqlite")
    try:
        _, before = _publish(db, {"subjects": ["Astronomie"]})
        _, other = _publish(db, {"subjects": ["Cuisine"]}, record="edition-b")
        source, changed = _publish(db, {"subjects": ["Physique"]}, at=101)
        assert changed.document == before.document and changed.epoch == before.epoch + 1
        assert changed.sources[0].public_bibliography.classification.wire() == {"subjects": ["Physique"]}
        assert index.read_consolidated_notice_cover(db, other.document) == other
        _, same = _publish(db, {"subjects": ["Physique"]}, at=102)
        assert same.epoch == changed.epoch
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        hits = search_public_notice_c(db, "même titre", now=102).hits
        hit, = (hit for hit in hits if hit.document == changed.document)
        assert hit.classification.wire() == {"subjects": ["Physique"]}
        assert search_public_notice_c(db, "astronomie", field="subject", now=102).hits == ()
        selected, = search_public_notice_c(db, "physique", field="subject", now=102).hits
        assert selected.document == changed.document
    finally:
        db.close()


@pytest.mark.parametrize("bad", [
    {"subjects": ["ok", {"token": "secret-test"}]},
    {"genres": [True]}, {"keywords": ["x"] * 33},
    {"tags": ["x" * 8193]}, {"themes": ["x" * 8192] * 5},
    {"subject": "https://private.example.test/?token=secret-test"},
])
def test_invalid_classification_is_not_partially_retained(tmp_path, bad):
    assert declared_public_classification(bad) is None
    db = Storage(tmp_path / "invalid.sqlite")
    try:
        _, c = _publish(db, bad)
        assert c.sources[0].public_bibliography.classification is None
    finally:
        db.close()


def test_closed_classification_shape_rejects_rights_and_untyped_wire_values():
    assert decode_public_classification({"rights": ["owned"]}) is None
    assert decode_public_classification({"subjects": None}) is None
    assert decode_public_classification({"subjects": ("x",)}) is None
    assert decode_public_classification({}) is None
    proof = declared_public_classification({"subjects": ["Roman"], "owned": True, "age_min": 8})
    assert proof.wire() == {"subjects": ["Roman"]}


@pytest.mark.parametrize("value,accepted", [
    ("x" * 7000, True), ("x" * 7000 + "@", True),
    ("x" * 7000 + "@domain", True),
    ("x" * 7000 + "@example.test", False),
    ("Sujet author@example.test suite", False),
    ("Sujet (author@example.test)", False),
    ("other@author@example.test", False),
    ("@domain.test", True), ("author@domain.", True),
    ("Roman documentaire", True),
])
def test_long_scalar_admission_keeps_existing_token_boundary_policy(value, accepted):
    from cifereca.core.public_search_projection import _public_bibliographic_text
    assert _public_bibliographic_text(value) is accepted


def test_corrupt_retained_classification_is_refused_not_relabelled(tmp_path):
    db = Storage(tmp_path / "corrupt.sqlite")
    try:
        source, c = _publish(db, {"subjects": ["Roman"]})
        key = index._db_key(c.document)
        raw = json.loads(db.metadata_get(key))
        raw["sources"][source.document.source_key]["public_bibliography"]["classification"] = {"rights": ["owned"]}
        with db._lock, db._connect() as sql:
            sql.execute("UPDATE metadata SET value=? WHERE key=?", (json.dumps(raw), key))
        with pytest.raises(ValueError, match="corrupt_canonical_cover"):
            index.read_consolidated_notice_cover(db, c.document)
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        assert search_public_notice_c(db, "roman", field="subject", now=100).hits == ()
    finally:
        db.close()


def test_proven_group_retains_source_alternatives_without_union(tmp_path):
    from tests.test_core_p0_6366_library_recent_public_c import _real_origins
    db = Storage(tmp_path / "group.sqlite")
    try:
        _real_origins(db, tmp_path)
        a, _ = _publish(db, {"subjects": ["Astronomie"]}, provider="seller-a")
        b, _ = _publish(db, {"subjects": ["Cuisine"]}, record="edition-b", provider="seller-b")
        group = index.persist_origin_linked_public_cover_group(db, sources=(a, b))
        for source in (a, b):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        page = search_public_notice_c(db, "même titre", now=100000, group_by_document=True)
        assert len(page.hits) == 1 and page.hits[0].document == group.document
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert row["metadata"]["subjects"] == ["Astronomie"]
        other, = row["metadata"]["notice_c_source_alternatives"]
        assert other["provider_id"] == "seller-b" and other["subjects"] == ["Cuisine"]
        assert db.entitlements() == [] and db.acquisitions() == []
    finally:
        db.close()


@pytest.mark.parametrize("field", ("awards", "prizes", "literary_prizes"))
def test_exact_public_award_remains_searchable_after_eviction_restart(tmp_path, field):
    """A same-source literary award is not a subject or a right."""
    from cifereca.core.search import local_match_explanation

    path = tmp_path / "awards.sqlite"
    label = "Prix du Quai des Orfèvres"
    with_award = {field: [label]}
    db = Storage(path)
    try:
        source, canonical = _publish(db, with_award, provider="kobo")
        other_source, other = _publish(
            db, {"subjects": ["Cuisine"]}, record="edition-b", provider="kobo",
        )
        assert canonical.document != other.document
        assert canonical.sources[0].public_bibliography.classification.wire() == with_award
        for phase in ("live", "retained"):
            page = search_public_notice_c(db, "prix quai", now=100, page_size=1)
            assert page.status == "partial" and not page.has_more
            hit, = page.hits
            assert hit.document == canonical.document and page.known_matches == 1
            row, = _known(db, "prix du quai")
            assert row["_cover_document_ref"] == canonical.document
            assert row["metadata"][field] == [label]
            assert local_match_explanation(row, "prix quai") == "Correspondance : Prix littéraire"
            assert not search_public_notice_c(db, "prix quai", field="subject", now=100).hits
            assert not search_public_notice_c(db, "prix quai", field="title", now=100).hits
            assert db.entitlements() == [] and db.acquisitions() == []
            if phase == "live":
                for saved in (source, other_source):
                    assert evict_notice_cover(db, saved.document, expected_epoch=saved.epoch)
                db.close()
                db = Storage(path)
    finally:
        db.close()


def test_public_award_retention_uses_existing_label_bounds():
    assert declared_public_classification({"awards": ["x" * 241]}) is None
    assert declared_public_classification({"prizes": ["https://private.invalid/?token=secret"]}) is None
    assert decode_public_classification({"literary_prizes": [{"name": "Prix"}]}) is None
    proof = declared_public_classification({"awards": ["Prix attesté"], "owned": True})
    assert proof.wire() == {"awards": ["Prix attesté"]}
