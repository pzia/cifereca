from dataclasses import replace
from types import SimpleNamespace

from cifereca.models import ItemDetail
from tests.test_catalog_083 import _service
from tests.test_core_p0_1354_mirrored_source_scope import notice


def test_holdings_cache_isolates_identical_records_by_collection_and_profile(tmp_path):
    service = _service(str(tmp_path))
    fetched = []

    def holdings(item, _search_terms):
        label = f"{service.current_identity.identity_id}:{item.source_collection}"
        fetched.append(label)
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase, title=item.title,
            author=None, type=None, year=None, thumbnail_url=None,
            availability_status="available",
            availability_label=label, holdings=[],
            source_provider=item.source_provider, source_credential=item.source_credential,
        )

    service._session_for_source = lambda *_: SimpleNamespace(holdings=holdings)
    # Generic cache-key regression: foreign Paris details are deliberately
    # inert under P1 #4909 and tested separately with exact owner scopes.
    first = replace(notice(collection="catalogue-A"),
                    source_provider="fixture-shared-catalogue")
    second = replace(first, source_collection="catalogue-B")
    try:
        assert service.holdings(first).availability_label == "default:catalogue-A"
        assert service.holdings(second).availability_label == "default:catalogue-B"
        assert service.holdings(first).availability_label == "default:catalogue-A"
        service.current_identity = service.identity_by_id("alex")
        assert service.holdings(first).availability_label == "alex:catalogue-A"
        assert fetched == ["default:catalogue-A", "default:catalogue-B", "alex:catalogue-A"]
    finally:
        service.close()
