"""#3688/#4761: source-specific age detail, never merged with the primary C.

Core SQLite receipt and UI host are real; supplier values are synthetic.
"""
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace as NS

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_cover_index import persist_attested_public_cover_group
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.gui.search_known_public_c_bibliography import known_public_alternative_section
from cifereca.storage import Storage
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view
from tests.test_plugin_ui_host_boundary import host_type


def test_age_of_second_source_has_its_own_provenance_in_both_details(tmp_path):
    filename = tmp_path / "source-age.sqlite"
    with closing(Storage(filename)) as db:
        primary = project(public_row(
            source_provider="aaa-fixture", record_id="without-age",
            metadata={"subjects": ["Jeunesse"]},
        ))
        aged = original(source_provider="zzz-fixture")
        aged.record_id = "age-12"
        aged.metadata["bibliography_evidence"]["youth_age"]["record_id"] = aged.record_id
        sources = (persist(db, primary), persist(db, project(aged)))
        canonical = persist_attested_public_cover_group(
            db, canonical_edition_id="strong:age-source-detail",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as db:
        page = search_public_notice_c(db, "Même titre", group_by_document=True)
        search, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        assert search["_cover_document_ref"] == canonical.document
        assert search["provider_id"] == "aaa-fixture"
        assert "youth_age_ranges" not in search["metadata"]
        search_section = known_public_alternative_section(search["metadata"], item=search)
        search_age, = [
            field for field in search_section.fields if " · Âge (" in field.label
        ]
        assert search_age.value == "À partir de 12 ans"
        assert search_age.label == "Source 2 · zzz-fixture · Âge (éditeur)"
        assert search_age.provenance.source == "zzz-fixture.fixture.age"
        assert search_age.provenance.provider_id == "zzz-fixture"
        assert search_age.provenance.source_id == "age-12"

        candidate, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("Même titre")
        _, window, _service = _view()
        host = host_type()(window)
        document, receipt = host.public_notice_cover_binding(candidate, identity_id="alice")
        assert document == canonical.document
        fields = host.public_notice_alternative_fields(
            candidate, identity_id="alice", document=document, receipt=receipt,
        )
        assert fields == search_section.fields
        assert host.public_notice_alternative_fields(
            candidate, identity_id="bob", document=document, receipt=receipt,
        ) == ()
        assert all(not f.label.endswith(" · Âge (éditeur)")
                   for f in host.public_notice_alternative_fields(
                       candidate, identity_id="alice", document=document,
                       receipt=receipt, catalogue_sources=("aaa-fixture",),
                   ))

        # The same copy of metadata is not an exact source proof: the value
        # must agree with the immutable hit AND the original age wire.
        for changed in ("age", "wire", "kind"):
            meta = deepcopy(search["metadata"])
            alternative = meta["notice_c_source_alternatives"][0]
            if changed == "age":
                alternative["youth_age_ranges"][0]["source_label"] = "Dès 6 ans"
            elif changed == "wire":
                alternative["bibliography_evidence"]["youth_age"]["source_field"] = (
                    "other.fixture.age"
                )
            else:
                alternative["youth_age_ranges"][0]["age_kind"] = "reading"
            forged = {**search, "metadata": meta}
            part = known_public_alternative_section(meta, item=forged)
            assert part is not None
            assert not [f for f in part.fields if " · Âge (" in f.label]

        assert db.entitlements() == db.acquisitions() == db.placements() == []
