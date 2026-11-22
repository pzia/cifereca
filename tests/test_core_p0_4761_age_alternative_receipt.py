"""#4761/#4530: age attested by an alternative must not erase C in Recommendations.

Controlled exact same-call producer evidence, SQLite/restart. No provider GET.
"""
from contextlib import closing
from dataclasses import replace

from cifereca.core.notice_c_cover_index import persist_attested_public_cover_group
from cifereca.core.notice_c_recommendation_known import (
    known_public_notice_candidates, known_public_c_binding_facts,
    known_public_c_alternative_hits,
)
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def test_age_alternative_remains_in_exact_c_recommendation_receipt_after_restart(tmp_path):
    filename = tmp_path / "age-alternative.sqlite"
    with closing(Storage(filename)) as storage:
        # Deliberately make the unqualified source the primary (stable
        # provider order), with a distinct edition-scoped qualified source.
        primary = project(public_row(
            source_provider="aaa-fixture", record_id="no-age",
            metadata={"subjects": ["Jeunesse"]},
        ))
        aged = original(source_provider="zzz-fixture")
        aged.record_id = "has-age"
        proof = aged.metadata["bibliography_evidence"]["youth_age"]
        proof["record_id"] = aged.record_id
        qualified = project(aged)
        sources = (persist(storage, primary), persist(storage, qualified))
        canonical = persist_attested_public_cover_group(
            storage, canonical_edition_id="strong:age-alternative-c",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        for source in sources:
            assert evict_notice_cover(storage, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as storage:
        candidates = known_public_notice_candidates(storage, "Même titre")
        assert len(candidates) == 1
        candidate, = candidates
        assert candidate.source_provider == "aaa-fixture"
        assert known_public_c_binding_facts(candidate) == (
            canonical.document, canonical.epoch,
        )
        alternatives = known_public_c_alternative_hits(candidate)
        assert alternatives is not None and len(alternatives) == 1
        hit, = alternatives
        assert hit.provider_id == "zzz-fixture"
        assert hit.age_observation is not None
        assert hit.age_observation.filter_ranges() == [
            {"age_min": 12, "age_max": None, "age_kind": "publisher",
             "source_label": "À partir de 12 ans"},
        ]
        raw = candidate.metadata["notice_c_source_alternatives"][0]
        assert raw["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] == 12
        assert "youth_age_ranges" in raw
        # A post-publication mutation may not preserve this native receipt.
        raw["bibliography_evidence"]["youth_age"]["ranges"][0]["age_min"] = 6
        assert known_public_c_binding_facts(candidate) is None
        assert known_public_c_alternative_hits(candidate) is None
        assert storage.entitlements() == storage.acquisitions() == storage.placements() == []



def test_known_recommendation_filters_use_same_attested_source_as_search(tmp_path):
    from copy import copy
    from cifereca.core.notice_c_loaded_filter import matches_known_public_c_filters
    from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
    from cifereca.core.notice_c_public_search import search_public_notice_c
    from cifereca.core.document_filter import filter_loaded_with_documentary_proofs

    filename = tmp_path / "source-filter.sqlite"
    with closing(Storage(filename)) as storage:
        sources = tuple(
            persist(storage, project(public_row(
                source_provider=provider, record_id=record,
                metadata={"subjects": [subject]},
            )))
            for provider, record, subject in (
                ("aaa-fixture", "a-subject", "Astronomie"),
                ("zzz-fixture", "b-subject", "Cuisine"),
            )
        )
        canonical = persist_attested_public_cover_group(
            storage, canonical_edition_id="strong:filter-source-c",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        for source in sources:
            assert evict_notice_cover(storage, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as storage:
        candidate, = known_public_notice_candidates(storage, "Même titre")
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        rows = merge_public_notice_c_known_rows(
            [], search_public_notice_c(storage, "Même titre", group_by_document=True),
            identity_id="alice",
        )
        assert len(rows) == 1
        for text, providers, expected in (
            ("", (), True),
            ("Cuisine", (), True),
            ("Astronomie", ("aaa-fixture",), True),
            ("Cuisine", ("zzz-fixture",), True),
            ("Cuisine", ("aaa-fixture",), False),
            ("Cuisine Astronomie", (), False),
            ("Absent", (), False),
        ):
            kwargs = dict(field="subject", scope="all", providers=providers,
                          content_types=(), languages=())
            assert matches_known_public_c_filters(candidate, query=text, **kwargs) is expected
            assert bool(filter_loaded_with_documentary_proofs(rows, text, **kwargs)) is expected
        kwargs = dict(query="Cuisine", field="subject", scope="all", providers=(),
                      content_types=(), languages=())
        assert matches_known_public_c_filters(copy(candidate), **kwargs) is False
        assert matches_known_public_c_filters(public_row(), **kwargs) is None

        # A malformed internal group receipt does not change the original
        # DTO or its separately attested primary. Neither view may use the
        # stale alternative; both retain the valid primary filter match.
        from cifereca.core.notice_c_recommendation_known import known_public_c_primary_hit
        proof = candidate._core_known_public_c_projection
        alternative, = proof[5]
        candidate._core_known_public_c_projection = (
            *proof[:5], (replace(alternative, epoch=alternative.epoch + 1),), proof[6],
        )
        assert known_public_c_binding_facts(candidate) == (canonical.document, canonical.epoch)
        assert known_public_c_primary_hit(candidate) is proof[6]
        assert known_public_c_alternative_hits(candidate) is None
        group = rows[0]["_core_notice_c_filter_sources"]
        primary, alternative = group[4]
        rows[0]["_core_notice_c_filter_sources"] = (
            *group[:4], (primary, replace(alternative, epoch=alternative.epoch + 1)),
        )
        for text, provider, expected in (
            ("Astronomie", "aaa-fixture", True),
            ("Cuisine", "zzz-fixture", False),
            ("Cuisine", "aaa-fixture", False),
        ):
            options = dict(field="subject", scope="all", providers=(provider,),
                           content_types=(), languages=())
            assert matches_known_public_c_filters(candidate, query=text, **options) is expected
            assert bool(filter_loaded_with_documentary_proofs(rows, text, **options)) is expected
        # A changed DTO still invalidates the entire primary proof.
        candidate.title += " changé"
        assert known_public_c_primary_hit(candidate) is None
        assert matches_known_public_c_filters(candidate, query="Astronomie",
                                              field="subject", scope="all",
                                              providers=(), content_types=(), languages=()) is False
        assert storage.entitlements() == storage.acquisitions() == storage.placements() == []
