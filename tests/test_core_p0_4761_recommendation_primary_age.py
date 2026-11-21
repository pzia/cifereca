"""Exact primary age in known-C Recommendations; no supplier call or new right.

One retained two-source C is built from synthetic same-call observations.
CoreUI consumes only the original, current opaque C receipt.
"""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_cover_index import persist_attested_public_cover_group
from cifereca.core.notice_c_recommendation_known import (
    known_public_c_binding_facts, known_public_c_primary_hit,
)
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_4530_public_age_c import original, project
from tests.test_core_p0_6366_public_c_sqlite_source_search import persist
from tests.test_core_p0_6366_real_search_notice_c_ingestion import public_row
from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view
from tests.test_plugin_ui_host_boundary import host_type


def test_primary_age_exact_and_provider_scoped_after_reopening_sqlite(tmp_path):
    filename = tmp_path / "recommendation-age.sqlite"
    with closing(Storage(filename)) as db:
        first = original(source_provider="aaa-fixture")
        first.record_id = "publisher-age"
        first.metadata["bibliography_evidence"]["youth_age"]["record_id"] = first.record_id
        second = public_row(
            source_provider="zzz-fixture", record_id="no-age",
            metadata={"subjects": ["Astronomie"]},
        )
        sources = (persist(db, project(first)), persist(db, project(second)))
        canonical = persist_attested_public_cover_group(
            db, canonical_edition_id="strong:recommendation-primary-age",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    with closing(Storage(filename)) as db:
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices(
            "Même titre",
        )
        assert known_public_c_binding_facts(item) == (
            canonical.document, canonical.epoch,
        )
        primary = known_public_c_primary_hit(item)
        assert primary is not None and primary.provider_id == "aaa-fixture"
        assert primary.age_observation is not None
        _, window, service = _view()
        host = host_type()(window)
        document, receipt = host.public_notice_cover_binding(item, identity_id="alice")
        assert document == canonical.document

        def fields(*, candidate=item, identity="alice", packet=receipt, sources=()):
            return host.public_notice_primary_age_fields(
                candidate, identity_id=identity, document=document,
                receipt=packet, catalogue_sources=sources,
            )

        one, = fields()
        assert one.id == "known-public-primary-age-1"
        assert one.label == "Âge éditeur"
        assert one.value == "À partir de 12 ans"
        assert one.kind == "copy"
        assert one.provenance.provider_id == "aaa-fixture"
        assert one.provenance.source_id == "publisher-age"
        assert one.provenance.source == "aaa-fixture.fixture.age"
        assert fields(sources=("aaa-fixture",)) == (one,)
        assert fields(sources=("zzz-fixture",)) == ()
        assert fields(identity="bob") == ()
        assert fields(packet=object()) == ()
        assert fields(candidate=replace(item)) == ()

        # The inherited DTO is not an authority once it diverges from its
        # native receipt; no unscoped metadata fallback is allowed.
        before = dict(item.metadata)
        item.metadata["youth_age_ranges"][0]["age_kind"] = "reading"
        assert known_public_c_primary_hit(item) is None
        assert fields() == ()
        item.metadata = before
        service._account_context_epoch += 2
        assert fields() == ()
        assert db.entitlements() == db.acquisitions() == db.placements() == []


def test_age_from_alternative_does_not_become_primary_recommendation_age(tmp_path):
    with closing(Storage(tmp_path / "secondary-age.sqlite")) as db:
        first = project(public_row(
            source_provider="aaa-fixture", record_id="unattested",
        ))
        second = original(source_provider="zzz-fixture")
        second.record_id = "only-secondary"
        second.metadata["bibliography_evidence"]["youth_age"]["record_id"] = second.record_id
        sources = (persist(db, first), persist(db, project(second)))
        persist_attested_public_cover_group(
            db, canonical_edition_id="strong:secondary-age",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices(
            "Même titre",
        )
        hit = known_public_c_primary_hit(item)
        assert hit is not None and hit.provider_id == "aaa-fixture"
        assert hit.age_observation is None
        _, window, _ = _view()
        host = host_type()(window)
        document, receipt = host.public_notice_cover_binding(item, identity_id="alice")
        assert host.public_notice_primary_age_fields(
            item, identity_id="alice", document=document, receipt=receipt,
        ) == ()
        assert db.entitlements() == db.acquisitions() == db.placements() == []
