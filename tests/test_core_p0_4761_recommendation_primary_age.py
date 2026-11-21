"""Exact primary age in known-C Recommendations; no supplier call or new right.

One retained two-source C is built from synthetic same-call observations.
CoreUI consumes only the original, current opaque C receipt.
"""
from contextlib import closing
from copy import deepcopy
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
        # Recommendations uses the same partial/stale/cover-state
        # descriptive contract as known public Search, never a rights hint.
        assert item.metadata["notice_c_query_status"] == "partial"
        assert item.metadata["notice_c_cover_state"] in (
            "available", "missing", "pending",
        )
        assert item.metadata["notice_c_epoch"] == canonical.epoch
        assert type(item.metadata["notice_c_stale"]) is bool
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
        before = deepcopy(item.metadata)
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



def test_recommendation_opaque_packet_rejects_hostile_nested_values_after_restart(tmp_path):
    from cifereca.core.notice_c_recommendation_known import (
        known_public_c_alternative_hits,
    )

    filename = tmp_path / "recommendation-hostile-packet.sqlite"
    with closing(Storage(filename)) as db:
        first = original(source_provider="aaa-fixture")
        first.record_id = "age-first"
        first.metadata["bibliography_evidence"]["youth_age"]["record_id"] = first.record_id
        second = public_row(
            source_provider="zzz-fixture", record_id="alternative",
            metadata={"subjects": ["Astronomie"]},
        )
        sources = (persist(db, project(first)), persist(db, project(second)))
        canonical = persist_attested_public_cover_group(
            db, canonical_edition_id="strong:hostile-recommendation",
            sources=sources, relation_proof_id="strong:synthetic",
        )
        for source in sources:
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)

    class Hostile:
        def __eq__(self, other):
            raise AssertionError("foreign equality in recommendation C")

        def __hash__(self):
            raise AssertionError("foreign hash in recommendation C")

    with closing(Storage(filename)) as db:
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("Même titre")
        assert known_public_c_binding_facts(item) == (
            canonical.document, canonical.epoch,
        )
        assert known_public_c_primary_hit(item) is not None
        alternative, = known_public_c_alternative_hits(item)
        packet = item._core_known_public_c_projection

        def try_packet(index, value):
            candidate = list(packet)
            candidate[index] = value
            item._core_known_public_c_projection = tuple(candidate)

        try:
            try_packet(4, (Hostile(), *packet[4][1:]))
            assert known_public_c_binding_facts(item) is None
            assert known_public_c_primary_hit(item) is None

            wrong_doc = replace(packet[2])
            object.__setattr__(wrong_doc, "kind", Hostile())
            try_packet(2, wrong_doc)
            assert known_public_c_binding_facts(item) is None

            for forged in (
                replace(packet[6], record_id=Hostile()),
                replace(packet[6], observed_at=Hostile()),
                replace(packet[6], last_seen_at=Hostile()),
                replace(packet[6], stale=Hostile()),
                replace(packet[6], cover_state=Hostile()),
                replace(packet[6], edition_id=Hostile()),
                replace(packet[6], edition_id="x" * 513),
                replace(packet[6], title="x" * 8193),
                replace(packet[6], last_seen_at=-1),
                replace(packet[6], last_seen_at=packet[6].observed_at - 1),
                replace(packet[6], age_observation=Hostile()),
                replace(packet[6], age_observation=replace(
                    packet[6].age_observation,
                    ranges=(replace(packet[6].age_observation.ranges[0],
                                    age_min=Hostile()),),
                )),
            ):
                try_packet(6, forged)
                assert known_public_c_primary_hit(item) is None

            for forged in (
                replace(alternative, provider_id=Hostile()),
                replace(alternative, classification=Hostile()),
            ):
                try_packet(5, (forged,))
                assert known_public_c_alternative_hits(item) is None
        finally:
            item._core_known_public_c_projection = packet

        assert known_public_c_binding_facts(item) == (
            canonical.document, canonical.epoch,
        )
        assert known_public_c_primary_hit(item) is not None
        assert known_public_c_alternative_hits(item) == (alternative,)
        assert db.entitlements() == db.acquisitions() == db.placements() == []



def test_recommendation_source_proof_decoder_accepts_native_series_language_classification(tmp_path):
    from cifereca.core.notice_c_recommendation_known import _native_hit
    from cifereca.core.public_search_projection import (
        decode_public_classification, decode_public_language_observation,
        decode_public_series_relation,
    )

    with closing(Storage(tmp_path / "typed-proofs.sqlite")) as db:
        first = original(source_provider="aaa-fixture")
        first.record_id = "typed-proof"
        first.metadata["bibliography_evidence"]["youth_age"]["record_id"] = first.record_id
        source = persist(db, project(first))
        from cifereca.core.notice_c_cover_index import persist_exact_public_search_cover
        canonical = persist_exact_public_search_cover(db, source)
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("Même titre")
        hit = known_public_c_primary_hit(item)
        assert hit is not None
        binding = (canonical.document, canonical.epoch)
        series = decode_public_series_relation({
            "label": "Cycle du futur",
            "source": "aaa-fixture.fixture",
            "source_field": "series",
            "scope": "notice_c_public",
            "confidence": "explicit_record_field",
            "freshness": "search_response",
        }, provider_id="aaa-fixture")
        language = decode_public_language_observation({
            "raw_tags": ["fr"], "languages": ["fr"],
            "source": "aaa-fixture.fixture",
            "source_field": "language",
            "scope": "notice_c_public",
            "confidence": "explicit_record_field",
            "freshness": "search_response",
        }, provider_id="aaa-fixture")
        classification = decode_public_classification({
            "subjects": ["Science", "Astronomie"],
        })
        assert series is not None and language is not None and classification is not None
        native = replace(
            hit, series_relation=series, language_observation=language,
            classification=classification,
        )
        assert _native_hit(native, binding)
        assert not _native_hit(replace(
            native, language_observation=replace(language, raw_tags=("fr", "xx_???")),
        ), binding)
        assert not _native_hit(replace(
            native, series_relation=replace(series, source="other.fixture"),
        ), binding)
        assert not _native_hit(replace(
            native, classification=replace(classification, entries=(
                ("subjects", ("https://private.example.org/token",)),
            )),
        ), binding)
        assert db.entitlements() == db.acquisitions() == db.placements() == []
