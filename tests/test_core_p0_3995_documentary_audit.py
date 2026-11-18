"""Real LibraryService/CatalogBroker, no remote call, no global catalogue fallback."""
from types import SimpleNamespace as NS
from unittest.mock import Mock

from cifereca.brokers import CatalogBrokerImpl
from tests.test_core043_progress_cache_stats import _service


def test_documentary_audit_real_scoped_library_and_empty_unknown_owner(tmp_path):
    service = _service(tmp_path)
    try:
        broker = CatalogBrokerImpl(service)
        assert broker.library_documentary_audit(identity_id="alice", kind="covers") == {
            "schema": 1, "status": "partial", "entries": 0,
            "embedded": 0, "origin": 0, "catalog": 0,
            "ambiguous": 0, "missing": 0, "unknown": 0,
        }
        for owner, key, language in (("alice", "a", "FR"), ("bob", "b", "???")):
            service.storage.catalog_entry_put({
                "catalog_entry_id": key, "item_id": key,
                "identity_id": owner, "provider_id": "local",
                "local_state": "local", "title": "Private title " + owner,
                "media_format": "pdf",
                "metadata": {"language": language, "cover_url": "https://example.org/image.png"},
            })
        service.catalog_scan = Mock(side_effect=AssertionError("scan"))
        covers = broker.library_documentary_audit(identity_id="alice", kind="covers")
        assert covers["status"] == "partial" and covers["entries"] == covers["unknown"] == 1
        assert covers["missing"] == covers["catalog"] == 0
        languages = broker.library_documentary_audit(identity_id="alice", kind="languages")
        assert languages["entries"] == languages["noncanonical"] == 1
        assert languages["invalid"] == 0
        assert broker.library_documentary_audit(identity_id="bob", kind="languages")["status"] == "unavailable"
        assert broker.library_documentary_audit(identity_id=None, kind="covers")["status"] == "unavailable"
        assert "Private title" not in repr(covers)
        service.catalog_scan.assert_not_called()
        assert service.storage.entitlements() == [] and service.storage.acquisitions() == []
    finally:
        service.close()


def test_audit_unavailable_on_mixed_or_rebound_projection():
    from cifereca.core.library_documentary_audit import library_documentary_audit
    service = NS(identity_by_id=lambda x: object(), _account_context_epoch=2,
                 _source_context_epoch=7)
    service.library_entries = lambda **kw: [{"identity_id": "bob"}]
    assert library_documentary_audit(service, identity_id="alice", kind="covers")["status"] == "unavailable"
    def rebound(**kw):
        service._source_context_epoch += 1
        return []
    service.library_entries = rebound
    assert library_documentary_audit(service, identity_id="alice", kind="languages")["status"] == "unavailable"


def test_cover_audit_v2_headless_preserves_v1_and_explicit_unknown(tmp_path):
    service = _service(tmp_path)
    try:
        broker = CatalogBrokerImpl(service)
        service.storage.catalog_entry_put({
            "catalog_entry_id": "a", "item_id": "a", "identity_id": "alice",
            "provider_id": "local", "local_state": "local", "title": "A",
            "media_format": "pdf", "metadata": {},
        })
        v1 = broker.library_documentary_audit(identity_id="alice", kind="covers")
        v2 = broker.library_cover_audit(identity_id="alice")
        assert v2["schema"] == 2 and v2["status"] == "partial"
        assert v2["entries"] == v2["unknown"] == 1
        for level in ("n2", "n3"):
            assert v2[level]["unknown"] == sum(v2[level].values()) == 1
        assert {k: v for k, v in v2.items() if k not in ("n2", "n3", "schema")} == {
            k: v for k, v in v1.items() if k != "schema"
        }
        assert broker.library_cover_audit(identity_id="bob")["status"] == "unavailable"
    finally:
        service.close()
