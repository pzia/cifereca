"""Exact structured age proof from same-call public Search through C/restart.

Synthetic supplier evidence only. No positive BnF digital field is qualified
by this test, no provider request or book acquisition is performed.
"""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.attested_age_filter import attested_age_status
from cifereca.core.notice_c_cover_index import (
    persist_attested_public_cover_group, persist_exact_public_search_cover,
    read_consolidated_notice_cover,
)
from cifereca.core.notice_c_known_projection import (
    known_public_primary_facts, merge_public_notice_c_known_rows,
)
from cifereca.core.notice_c_loaded_filter import expand_filter_sources
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.storage import Storage
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row


def original(**kw):
    row = public_row(record_id="record-age", revision_id="rev1", **kw)
    row.metadata = {"bibliography_evidence": {"youth_age": {
        "schema": 1, "provider_id": row.source_provider, "record_id": row.record_id,
        "docbase": row.docbase, "medium": row.medium, "source_revision": row.revision_id,
        "source_field": row.source_provider + ".fixture.age",
        "ranges": [{"age_min": 12, "age_max": None, "age_kind": "publisher",
                    "source_label": "À partir de 12 ans"}],
    }}}
    return row


def project(row):
    out, = _public_rows_from_response(
        ProviderSearchResponse([row], public_notices=(PublicSearchNotice(row),)),
        NS(provider_id=row.source_provider, credential_id="__public__",
           credentials={}, _accounts=[]),
    )
    return out


def test_same_call_exact_age_survives_source_eviction_and_restart(tmp_path):
    filename = tmp_path / "age.sqlite"
    with closing(Storage(filename)) as db:
        a = original()
        # Same text and ISBN are not a paper/digital relationship.
        b = public_row(record_id="print-other", medium="physical",
                       revision_id="print1", format=None,
                       metadata={"youth_age_ranges": [{"age_min": 6, "age_max": 8,
                                 "age_kind": "publisher", "source_label": "6–8 ans"}],
                                 "subjects": ["Jeunesse"]})
        c = original(source_provider="fixture")
        c.record_id = "conflict"
        proof = c.metadata["bibliography_evidence"]["youth_age"]
        proof["record_id"] = c.record_id
        proof["ranges"].append({"age_min": 4, "age_max": 6, "age_kind": "publisher",
                                "source_label": "4 à 6 ans"})
        sources = [persist(db, project(row)) for row in (a, b, c)]
        documents = [persist_exact_public_search_cover(db, source).document
                     for source in sources]

        def check(storage):
            page = search_public_notice_c(storage, "Même titre",
                                          group_by_document=True, now=100)
            rows = merge_public_notice_c_known_rows([], page, identity_id="alice")
            keyed = {row["item_id"]: row for row in rows}
            assert len(keyed) == 3
            assert {row["_cover_document_ref"] for row in rows} == set(documents)
            assert attested_age_status(keyed["record-age"], 12) == "match"
            assert attested_age_status(keyed["record-age"], 17) == "match"
            assert attested_age_status(keyed["record-age"], 11) == "incompatible"
            assert attested_age_status(keyed["print-other"], 7) == "unknown"
            assert attested_age_status(keyed["conflict"], 5) == "unknown"
            assert attested_age_status(keyed["conflict"], 13) == "unknown"
            age = known_public_primary_facts(keyed["record-age"]).age_observation
            assert age.source_revision == "rev1" and age.medium == "digital"
            assert age.ranges[0].source_label == "À partir de 12 ans"
            assert age.ranges[0].age_max is None
            assert len(known_public_primary_facts(keyed["conflict"]).age_observation.ranges) == 2
            assert storage.entitlements() == storage.acquisitions() == storage.placements() == []

        check(db)
        for source in sources:
            evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(filename)) as db:
        check(db)


@pytest.mark.parametrize("mutate", [
    lambda proof: proof.update(record_id="foreign"),
    lambda proof: proof.update(provider_id="foreign"),
    lambda proof: proof.update(docbase="print"),
    lambda proof: proof.update(medium="physical"),
    lambda proof: proof.update(source_revision=None),
    lambda proof: proof.update(source_field="another.field"),
    lambda proof: proof.update(source_field="https://not-a-field"),
    lambda proof: proof["ranges"][0].update(age_min=True),
    lambda proof: proof["ranges"][0].update(age_max=11),
    lambda proof: proof["ranges"][0].update(source_label="https://example.org/private"),
    lambda proof: proof["ranges"].append({"age_min": 12}),
    lambda proof: proof.update(schema=True),
])
def test_unqualified_structured_age_drops_only_age_not_book(tmp_path, mutate):
    with closing(Storage(tmp_path / "negative.sqlite")) as db:
        row = original()
        mutate(row.metadata["bibliography_evidence"]["youth_age"])
        projected = project(row)
        source = persist(db, projected)
        canonical = persist_exact_public_search_cover(db, source)
        assert canonical.sources[0].public_bibliography.age_observation is None
        assert canonical.sources[0].public_bibliography.title == row.title
        assert len(search_public_notice_c(db, "Même titre", now=100).hits) == 1


def test_copy_or_late_edit_does_not_mint_same_call_age(tmp_path):
    with closing(Storage(tmp_path / "copy.sqlite")) as db:
        row = project(original())
        copied = replace(row)
        # Even manually copying all ephemeral receipt fields is insufficient.
        copied.__dict__.update(row.__dict__)
        snapshot = persist(db, copied)
        assert "age_observation" not in snapshot.observation.fields_copy()["search_result"]["public_bibliography"]
        row.revision_id = "different"
        snapshot = persist(db, row)
        assert "age_observation" not in snapshot.observation.fields_copy()["search_result"]["public_bibliography"]


@pytest.mark.parametrize("invalid", ["no_evidence", "wrong_record", "bad_range", "foreign_type"])
def test_reused_public_row_cannot_replay_prior_age_after_failed_restamp(tmp_path, invalid):
    from cifereca.core.notice_c_public_age import (
        stamp_same_call_public_age, verified_same_call_public_age,
    )

    row = project(original())
    old = verified_same_call_public_age(row)
    assert old is not None and old.ranges[0].age_min == 12
    candidate = original()
    if invalid == "no_evidence":
        candidate.metadata = {}
    elif invalid == "wrong_record":
        candidate.record_id = "unrelated"
    elif invalid == "bad_range":
        candidate.metadata["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = True
    else:
        candidate = object()
    # Reused SearchResult object must never retain its previous exact-call
    # proof when the latest attempt does not qualify; the negative result
    # must survive the subsequent Core C writer, not merely the filter.
    stamp_same_call_public_age(row, candidate)
    assert verified_same_call_public_age(row) is None
    with closing(Storage(tmp_path / (invalid + ".sqlite"))) as db:
        source = persist(db, row)
        bibliography = source.observation.fields_copy()["search_result"]["public_bibliography"]
        assert "age_observation" not in bibliography
        assert db.entitlements() == db.acquisitions() == db.placements() == []

    # Fresh, independently qualified exact evidence can stamp again.
    stamp_same_call_public_age(row, original())
    renewed = verified_same_call_public_age(row)
    assert renewed is not None and renewed.ranges == old.ranges


def test_grouped_filter_keeps_age_on_its_own_source_only(tmp_path):
    with closing(Storage(tmp_path / "group.sqlite")) as db:
        a = project(original())
        b = project(public_row(source_provider="fixture-other", record_id="unproved",
                               metadata={"subjects": ["Jeunesse"]}))
        group = persist_attested_public_cover_group(
            db, canonical_edition_id="strong:controlled-age-group",
            sources=(persist(db, a), persist(db, b)),
            relation_proof_id="strong:synthetic",
        )
        page = search_public_notice_c(db, a.title, group_by_document=True, now=100)
        row, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert row["_cover_document_ref"] == group.document
        expanded, _ = expand_filter_sources([row], scope="all")
        assert {r["provider_id"]: attested_age_status(r, 12) for r in expanded} == {
            a.source_provider: "match", b.source_provider: "unknown",
        }
        assert read_consolidated_notice_cover(db, group.document) == group


def test_exact_age_predicate_before_page_and_retained_candidate_budget(tmp_path):
    filename = tmp_path / "age-cursor.sqlite"
    with closing(Storage(filename)) as db:
        rows = []
        for i in range(5):
            row = public_row(record_id="unknown-" + str(i), source_provider="fixture")
            rows.append(project(row))
        a = original()
        b = original(source_provider="fixture-other")
        b.record_id = "conflicting"
        proof = b.metadata["bibliography_evidence"]["youth_age"]
        proof["record_id"] = b.record_id
        proof["ranges"].append({"age_min": 4, "age_max": 6, "age_kind": "publisher",
                                "source_label": "4 à 6 ans"})
        rows += [project(a), project(b)]
        sources = [persist(db, row) for row in rows]
        for source in sources:
            persist_exact_public_search_cover(db, source)
        page = search_public_notice_c(db, a.title, age=12, page_size=1,
                                      group_by_document=True, now=100)
        assert [h.record_id for h in page.hits] == [a.record_id]
        assert not page.has_more
        assert not search_public_notice_c(db, a.title, age=5, now=100).hits
        assert not search_public_notice_c(db, a.title, age=12, age_kind="reading", now=100).hits
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(filename)) as db:
        page = search_public_notice_c(db, a.title, age=12, page_size=1,
                                      max_scan=1, group_by_document=True, now=100)
        assert [h.record_id for h in page.hits] == [a.record_id]
        assert not page.has_more
        assert not search_public_notice_c(db, a.title, age=5, now=100).hits


@pytest.mark.parametrize("kw", [{"age": True}, {"age": -1}, {"age": 18},
                                {"age_kind": "unqualified"}])
def test_invalid_age_cursor_parameters(tmp_path, kw):
    with closing(Storage(tmp_path / "invalid-age.sqlite")) as db:
        with pytest.raises(ValueError, match="invalid_notice_c_search_request"):
            search_public_notice_c(db, "Même titre", **kw)


def test_known_search_age_filter_reuses_exact_c_across_readers_and_restart(tmp_path):
    from cifereca.core.search_orchestration import search_known_catalog
    filename = tmp_path / "known-age.sqlite"

    def known(db, reader, **kw):
        return search_known_catalog(
            NS(storage=db, catalog_entries=db.catalog_entries,
               library_entries=lambda **opts: []),
            "Même titre", identity_id=reader, logger=NS(info=lambda *a: None),
            **kw,
        )

    def check(db):
        for reader in ("alice", "bob"):
            rows = known(db, reader, age=12, age_kind="publisher")
            assert len(rows) == 1
            row, = rows
            assert row["source"] == "notice_c_public"
            assert row["identity_id"] == reader
            assert row["_cover_document_ref"] == canonical.document
            assert row["metadata"]["notice_c_query_status"] == "partial"
            assert attested_age_status(row, 12) == "match"
            assert known(db, reader, age=11) == []
            assert known(db, reader, age=12, age_kind="reading") == []
            assert known(db, reader, age=12, provider_ids=("other",)) == []
            assert len(known(db, reader)) == 2
        assert db.entitlements() == db.acquisitions() == db.placements() == []

    with closing(Storage(filename)) as db:
        source = persist(db, project(original()))
        canonical = persist_exact_public_search_cover(db, source)
        unknown = persist(db, project(public_row(record_id="unknown-age")))
        persist_exact_public_search_cover(db, unknown)
        check(db)
        for entry in (source, unknown):
            assert evict_notice_cover(db, entry.document, expected_epoch=entry.epoch)
    with closing(Storage(filename)) as db:
        check(db)


@pytest.mark.parametrize("change", ["unknown", "incompatible", "other_kind", "match"])
def test_exact_live_lookup_outside_budget_reapplies_age_predicate(tmp_path, change):
    from cifereca.core.notice_cover_repository import _opaque_key
    filename = tmp_path / "exact-live-age.sqlite"
    with closing(Storage(filename)) as db:
        # Pick the key order deterministically; only the second source has
        # retained C and is reached through the exact secondary lookup.
        pairs = []
        for record in ("first-age", "second-age"):
            row = original()
            row.record_id = record
            row.metadata["bibliography_evidence"]["youth_age"]["record_id"] = record
            pairs.append((persist(db, project(row)), row))
        pairs.sort(key=lambda pair: _opaque_key(pair[0].document))
        first, _ = pairs[0]
        source, row = pairs[1]
        canonical = persist_exact_public_search_cover(db, source)
        if change == "unknown":
            row.metadata = {}
        elif change == "incompatible":
            row.metadata["bibliography_evidence"]["youth_age"]["ranges"][0].update(
                age_min=14, source_label="À partir de 14 ans")
        elif change == "other_kind":
            row.metadata["bibliography_evidence"]["youth_age"]["ranges"][0].update(
                age_kind="reading")
        else:
            row.metadata["bibliography_evidence"]["youth_age"]["ranges"][0].update(
                age_min=11, source_label="À partir de 11 ans")
        current = persist(db, project(row), at=101)
        assert current.document == source.document and current.epoch > source.epoch
        assert read_consolidated_notice_cover(db, canonical.document) == canonical

    with closing(Storage(filename)) as db:
        page = search_public_notice_c(
            db, row.title, age=12, age_kind="publisher", max_scan=1,
            now=101, group_by_document=True,
        )
        assert page.status == "budget_exceeded" and page.scanned_sources == 1
        expected = {first.document}
        if change == "match":
            expected.add(current.document)
        assert {hit.document for hit in page.hits} == expected
        assert page.known_matches == len(expected)
        assert all(hit.age_observation is not None for hit in page.hits)
        assert db.entitlements() == db.acquisitions() == db.placements() == []



class _HostileAgeMember:
    def __hash__(self):
        return hash("age_min")

    def __eq__(self, other):
        raise AssertionError("provider age proof equality executed")


def test_structured_age_proof_rejects_foreign_keys_before_set_or_get():
    from cifereca.core.notice_c_public_age import decode_public_age_observation

    source = original()
    proof = source.metadata["bibliography_evidence"]["youth_age"]
    params = dict(provider_id=source.source_provider, record_id=source.record_id,
                  docbase=source.docbase, medium=source.medium,
                  source_revision=source.revision_id)
    assert decode_public_age_observation(proof, **params) is not None
    malformed = dict(proof)
    malformed["ranges"] = [{_HostileAgeMember(): 12}]
    assert decode_public_age_observation(malformed, **params) is None
    assert decode_public_age_observation({
        _HostileAgeMember(): 12,
    }, **params) is None


def test_reused_age_row_refuses_hostile_source_metadata_and_coordinates():
    from cifereca.core.notice_c_public_age import (
        stamp_same_call_public_age, verified_same_call_public_age,
    )

    row = project(original())
    assert verified_same_call_public_age(row) is not None
    candidate = original()
    candidate.metadata = {_HostileAgeMember(): "untrusted"}
    stamp_same_call_public_age(row, candidate)
    assert verified_same_call_public_age(row) is None
    candidate = original()
    candidate.source_provider = _HostileAgeMember()
    stamp_same_call_public_age(row, candidate)
    assert verified_same_call_public_age(row) is None
    stamp_same_call_public_age(row, original())
    assert verified_same_call_public_age(row) is not None


@pytest.mark.parametrize("change", ("bound", "range_container", "proof_source"))
def test_mutated_native_age_receipt_refuses_unsafe_replay_before_wire(change):
    from cifereca.core.notice_c_public_age import (
        stamp_same_call_public_age, verified_same_call_public_age,
    )

    row = project(original())
    valid = row._core_same_call_public_age
    age = valid[2]
    if change == "bound":
        damaged = replace(age, ranges=(
            replace(age.ranges[0], age_min=_HostileAgeMember()),
        ))
    elif change == "range_container":
        damaged = replace(age, ranges=_HostileAgeMember())
    else:
        damaged = replace(age, source_field=_HostileAgeMember())
    row._core_same_call_public_age = (valid[0], valid[1], damaged)
    assert verified_same_call_public_age(row) is None
    row._core_same_call_public_age = valid
    assert verified_same_call_public_age(row) is not None
