from contextlib import closing

import pytest

from cifereca.storage import Storage
from cifereca.core.notice_c_cover_index import persist_origin_linked_public_cover_group
from cifereca.core.notice_c_public_age_inheritance import capture_origin_linked_age_sources_txn
from cifereca.core.notice_consolidation_inputs import admit_descriptive_facts
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_origin_attested_public_c import _origins, A_URL, B_URL
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist


def setup(db, tmp_path):
    _origins(db, tmp_path)
    sources = []
    for provider, record, url in (
        ("seller-a", "edition-a", A_URL), ("seller-b", "edition-b", B_URL),
    ):
        row = original(source_provider=provider, title="L'ouvrage commun", thumbnail_url=url)
        row.record_id = record
        row.metadata["bibliography_evidence"]["youth_age"]["record_id"] = record
        sources.append(persist(db, project(row), at=200))
    group = persist_origin_linked_public_cover_group(db, sources=tuple(sources))
    return group, sources


def test_live_origin_group_supplies_only_directed_inherited_facts(tmp_path):
    with closing(Storage(tmp_path / "origin-age.sqlite")) as db:
        group, sources = setup(db, tmp_path)
        target = sources[0].observation.key.edition_id
        other = sources[1].observation.key.edition_id
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            facts, links = capture_origin_linked_age_sources_txn(
                sql, group_document=group.document, target_edition_id=target,
            )
            assert links == {other: group.sources[0].relation_proof_id}
            admitted = admit_descriptive_facts(
                target_edition_id=target, canonical_type="age",
                consolidator_id="plugin.age.alpha", consolidator_version="1.0.0",
                source_facts=facts, attested_strong_links=links,
            )
            assert len(admitted.exact) == len(admitted.inherited) == 1
            assert admitted.inherited[0].edition_id == other
            assert admitted.inherited[0].inherited is True
            with pytest.raises(ValueError, match="relation_changed"):
                capture_origin_linked_age_sources_txn(
                    sql, group_document=group.document, target_edition_id="edition:foreign",
                )


@pytest.mark.parametrize("damage", ["origin", "placement", "eviction"])
def test_inherited_facts_refuse_stale_group(tmp_path, damage):
    from cifereca.core.notice_cover_repository import evict_notice_cover

    with closing(Storage(tmp_path / "revoked.sqlite")) as db:
        group, sources = setup(db, tmp_path)
        if damage == "eviction":
            assert evict_notice_cover(db, sources[1].document, expected_epoch=sources[1].epoch)
        else:
            with db._lock, db._connect() as sql:
                if damage == "origin":
                    sql.execute("DELETE FROM origin_links")
                else:
                    sql.execute("UPDATE placements SET state='missing'")
        with db._lock, db._connect() as sql:
            sql.execute("BEGIN")
            with pytest.raises(ValueError, match="relation_changed|origin_group_required"):
                capture_origin_linked_age_sources_txn(
                    sql, group_document=group.document,
                    target_edition_id=sources[0].observation.key.edition_id,
                )
