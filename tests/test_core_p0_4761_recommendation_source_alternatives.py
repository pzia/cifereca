"""Known Recommendations keeps the very same matching C alternatives as Search."""
from contextlib import closing
from copy import deepcopy
from types import SimpleNamespace as NS

from cifereca.brokers import CatalogBrokerImpl
from cifereca.core.notice_c_known_projection import merge_public_notice_c_known_rows
from cifereca.core.notice_c_public_search import search_public_notice_c
from cifereca.core.notice_c_recommendation_known import known_public_c_binding_facts
from cifereca.core.notice_cover_repository import evict_notice_cover
from cifereca.storage import Storage
from tests.test_core_p0_6366_multisource_c_search import sources, shared


def test_recommendation_retains_matching_alternatives_across_restart(tmp_path):
    filename = tmp_path / "multi.sqlite"
    with closing(Storage(filename)) as db:
        first, second = sources(db)
        canonical = shared(db, first, second)
        for source in (first, second):
            assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    with closing(Storage(filename)) as db:
        catalogue = CatalogBrokerImpl(NS(storage=db))
        page = search_public_notice_c(db, "deux sources", group_by_document=True)
        search, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        result, = catalogue.known_public_notices("deux sources")
        variants = search["metadata"]["notice_c_source_alternatives"]
        assert len(variants) == 1
        assert result.metadata["notice_c_source_alternatives"] == variants
        assert result.description == search["metadata"]["description"]
        assert known_public_c_binding_facts(result) == (canonical.document, canonical.epoch)
        assert result.source_credential == "" and result.source_account_id is None
        assert result.owned is result.downloadable is result.download_url is None
        assert result.friendly_url is result.thumbnail_url is None

        original = deepcopy(result.metadata)
        result.metadata["notice_c_source_alternatives"][0]["description"] = "Fait modifié"
        assert known_public_c_binding_facts(result) is None
        result.metadata = original
        assert known_public_c_binding_facts(result) == (canonical.document, canonical.epoch)
        result.metadata["notice_c_source_alternatives"] *= 32
        assert known_public_c_binding_facts(result) is None
        assert db.entitlements() == [] and db.acquisitions() == []


def test_recommendation_alternatives_obey_exact_provider_filter(tmp_path):
    with closing(Storage(tmp_path / "filter.sqlite")) as db:
        first, second = sources(db)
        canonical = shared(db, first, second)
        catalogue = CatalogBrokerImpl(NS(storage=db))
        item, = catalogue.known_public_notices("deux sources", provider_ids=("numilog-catalog",))
        assert item.source_provider == "numilog-catalog"
        assert "notice_c_source_alternatives" not in item.metadata
        assert known_public_c_binding_facts(item) == (canonical.document, canonical.epoch)
        assert catalogue.known_public_notices("deux sources", provider_ids=("unrelated",)) == []


def test_budget_lookup_never_invokes_hostile_dict_key(tmp_path):
    class CollidingKey:
        def __hash__(self):
            return hash("notice_c_source_alternatives")

        def __eq__(self, other):
            raise AssertionError("untrusted equality")

    with closing(Storage(tmp_path / "hostile-key.sqlite")) as db:
        first, second = sources(db)
        shared(db, first, second)
        item, = CatalogBrokerImpl(NS(storage=db)).known_public_notices("deux sources")
        item.metadata = {CollidingKey(): []}
        assert known_public_c_binding_facts(item) is None


def test_alternative_provenance_shared_with_search_after_restart(tmp_path):
    from dataclasses import replace
    from cifereca.gui.search_known_public_c_bibliography import known_public_alternative_section
    from tests.test_core_p0_6366_public_c_sqlite_source_search import persist, verified_public
    from tests.test_core_p0_6366_plugin_public_c_n3_revocation import _view
    from tests.test_plugin_ui_host_boundary import host_type

    filename = tmp_path / "alternate-provenance.sqlite"
    with closing(Storage(filename)) as db:
        originals = []
        for provider, record, series, language in (
            ("bnf", "a", "Cycle Orion", "ja"),
            ("numilog-catalog", "b", "Cycle Vega", "fr"),
        ):
            row = verified_public(source_provider=provider, record_id=record,
                                  title="Deux séries")
            common = {"source": provider + ".fixture", "scope": row.scenario,
                      "freshness": "search_response"}
            row.metadata = {
                "series": [series], "languages": [language],
                "bibliography_evidence": {
                    "series": {**common, "source_field": "Search.series",
                               "confidence": "linked_search_relation"},
                    "languages": {**common, "raw_tags": [language],
                                  "source_field": "dc.language",
                                  "confidence": "explicit_record_field"},
                },
            }
            originals.append(persist(db, row))
        canonical = shared(db, *originals)
        for original in originals:
            assert evict_notice_cover(db, original.document, expected_epoch=original.epoch)
    with closing(Storage(filename)) as db:
        catalogue = CatalogBrokerImpl(NS(storage=db))
        item, = catalogue.known_public_notices("Deux séries")
        page = search_public_notice_c(db, "Deux séries", group_by_document=True)
        search, = merge_public_notice_c_known_rows([], page, identity_id="alice")
        section = known_public_alternative_section(search["metadata"], item=search)
        _, window, service = _view()
        host = host_type()(window)
        document, packet = host.public_notice_cover_binding(item, identity_id="alice")
        assert document == canonical.document

        def fields(*, candidate=item, owner="alice", receipt=packet,
                   ref=document, providers=()):
            return host.public_notice_alternative_fields(
                candidate, identity_id=owner, document=ref, receipt=receipt,
                catalogue_sources=providers,
            )

        expected = fields()
        assert expected == section.fields
        proven = [field for field in expected if field.provenance is not None]
        assert len(proven) == 2
        alternative, = item.metadata["notice_c_source_alternatives"]
        assert all(f.provenance.provider_id == alternative["provider_id"]
                   and f.provenance.source_id == alternative["record_id"] for f in proven)
        assert fields(providers=(alternative["provider_id"],)) == expected
        assert fields(providers=("unrelated",)) == ()
        assert fields(candidate=replace(item)) == ()
        assert fields(owner="bob") == ()
        assert fields(receipt=object()) == ()
        assert fields(ref=object()) == ()

        original = deepcopy(item.metadata)
        item.metadata["notice_c_source_alternatives"][0]["series"] = ["Changed"]
        assert fields() == ()
        item.metadata = original
        service._account_context_epoch += 2
        assert fields() == ()
        assert db.entitlements() == [] and db.acquisitions() == []
