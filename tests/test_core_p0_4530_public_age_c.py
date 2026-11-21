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
