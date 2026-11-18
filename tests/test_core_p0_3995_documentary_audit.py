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


def test_real_plugin_plan_preview_run_on_scoped_core_broker(tmp_path):
    """No mock aggregate: actual suspended plugin consumes the real broker."""
    import importlib.util
    from pathlib import Path
    from cifereca.plugin_api import MaintenanceContext

    source = (Path(__file__).resolve().parents[1] / "src" / "plugins"
              / "maintenance-library-audit" / "cifereca_library_audits.py")
    spec = importlib.util.spec_from_file_location("_documentary_audit_receipt", source)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    class Control:
        def __init__(self):
            self.messages = []
            self.checks = 0

        def check_cancelled(self):
            self.checks += 1

        def report(self, **data):
            self.messages.append(data)

    service = _service(tmp_path)
    try:
        broker = CatalogBrokerImpl(service)
        service.catalog_scan = Mock(side_effect=AssertionError("unexpected scan"))
        broker.catalog_entries = Mock(side_effect=AssertionError("legacy or global catalogue"))

        def resolve(name):
            assert name == "catalog"
            return broker

        context = NS(broker=resolve)
        for owner, key, language in (("alice", "a", "FR"), ("bob", "b", "???")):
            service.storage.catalog_entry_put({
                "catalog_entry_id": key, "item_id": key,
                "identity_id": owner, "provider_id": "local",
                "local_state": "local", "title": "PRIVATE_TITLE_" + key,
                "media_format": "pdf",
                "metadata": {"language": language,
                             "cover_url": "https://example.org/private.png"},
            })

        for cls, kind in ((plugin.CoverAuditTool, "covers"),
                          (plugin.LanguageAuditTool, "languages")):
            tool = cls().activate(context)
            control = Control()
            prepared = tool.plan_with_progress(
                MaintenanceContext(scope="library", identity_id="alice"), control,
            )
            expected = (broker.library_cover_audit(identity_id="alice")
                        if kind == "covers" else
                        broker.library_documentary_audit(identity_id="alice", kind=kind))
            assert prepared.facts == expected
            assert prepared.facts["entries"] == 1
            assert prepared.facts["status"] == "partial"
            if kind == "covers":
                assert expected["schema"] == 2
                assert expected["unknown"] == expected["n2"]["unknown"] == expected["n3"]["unknown"] == 1
                assert "N1 attesté" in prepared.summary
                assert "N2 :" in prepared.summary and "N3 :" in prepared.summary
            else:
                assert expected["noncanonical"] == 1 and expected["invalid"] == 0
                assert any("provenance" in warning for warning in prepared.warnings)
            result = tool.run(prepared, control)
            assert result.ok and result.changed == 0
            assert result.facts == expected and result.summary == prepared.summary
            assert control.checks > 0 and control.messages
            assert "PRIVATE_TITLE" not in repr((prepared, result, control.messages))
            assert "private.png" not in repr((prepared, result, control.messages))

            unavailable = tool.plan(MaintenanceContext(scope="library", identity_id="bob"))
            assert unavailable.facts["status"] == "unavailable"
            assert not tool.run(unavailable, Control()).ok

        service.catalog_scan.assert_not_called()
        broker.catalog_entries.assert_not_called()
        assert service.storage.entitlements() == [] and service.storage.acquisitions() == []
    finally:
        service.close()
