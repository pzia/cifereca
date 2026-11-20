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
