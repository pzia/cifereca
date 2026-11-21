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
