"""#773: real generated publications, indexed once, read-only documentary audits."""
from hashlib import sha256
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from cifereca.brokers import CatalogBrokerImpl
from cifereca.plugin_api import MaintenanceContext
from tests.test_catalog_083 import _service


class Control:
    def __init__(self):
        self.checks = 0

    def check_cancelled(self):
        self.checks += 1

    def report(self, **_data):
        pass


def test_generated_epub_and_metadata_free_pdf_real_documentary_audit(tmp_path, monkeypatch):
    repo = Path(__file__).resolve().parents[1]
    monkeypatch.syspath_prepend(str(repo / "simulation"))
    from plugin_scenarios import create

    dataset = tmp_path / "dataset"
    manifest = create(dataset)
    source = repo / "src/plugins/maintenance-library-audit/cifereca_library_audits.py"
    spec = importlib.util.spec_from_file_location("_generated_documentary_audit", source)
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)

    service = _service(str(tmp_path))
    db = service.storage
    witnesses = {}
    try:
        for identity, directory, filename in (
            ("default", "hugues", "optimizer-valid.epub"),
            ("default", "hugues", "publication-text.pdf"),
            ("alex", "alex", "optimizer-valid.epub"),
        ):
            fixture = dataset / filename
            raw = fixture.read_bytes()
            assert sha256(raw).hexdigest() == manifest["files"][filename]
            path = tmp_path / "EBooks" / directory / "local" / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            witnesses[path] = witnesses[fixture] = raw
            result = service.catalog_index_path(path, identity_id=identity, provider_id="local")
            assert result["status"] == "indexed"
            assert result["artifact_id"] == "sha256:" + manifest["files"][filename]

        entries = service.library_entries(identity_id="default", scan=False)
        assert len(entries) == 2
        epub = next(row for row in entries if row["artifact_id"] ==
                    "sha256:" + manifest["files"]["optimizer-valid.epub"])
        assert epub.get("_exact_local_cover_evidence") is not None
        other = service.library_entries(identity_id="alex", scan=False)
        assert len(other) == 1 and other[0]["artifact_id"] == epub["artifact_id"]
        assert other[0]["placement_id"] != epub["placement_id"]
        placements = db.placements()
        catalogue = db.catalog_entries()
        service.catalog_scan = Mock(side_effect=AssertionError("no scan"))
        legacy = Mock(side_effect=AssertionError("no global catalogue"))
        monkeypatch.setattr(CatalogBrokerImpl, "catalog_entries", legacy)
        broker = CatalogBrokerImpl(service)

        def resolve(name):
            assert name == "catalog"
            return broker

        for owner, count, unknown in (("default", 2, 1), ("alex", 1, 0)):
            request = MaintenanceContext(scope="library", identity_id=owner)
            for cls, kind in ((plugin.CoverAuditTool, "covers"),
                              (plugin.LanguageAuditTool, "languages")):
                tool = cls().activate(SimpleNamespace(broker=resolve))
                control = Control()
                plan = tool.plan_with_progress(request, control)
                actual = (broker.library_cover_audit(identity_id=owner) if kind == "covers"
                          else broker.library_language_audit(identity_id=owner))
                assert plan.facts == actual
                assert actual["entries"] == count and actual["status"] == "partial"
                assert actual["unknown"] == unknown
                if kind == "covers":
                    assert actual["embedded"] == 1
                    # No GUI observer: N1 evidence cannot invent N2/N3.
                    assert actual["n2"]["unknown"] == actual["n3"]["unknown"] == count
                else:
                    assert actual["canonical"] == 1
                    assert actual["invalid"] == 0
                    assert actual["provenance"]["unknown"] == count
                result = tool.run(plan, control)
                assert result.ok and result.changed == 0 and result.facts == actual
                assert control.checks > 0
                assert str(tmp_path) not in repr((plan, result))

        assert db.placements() == placements and db.catalog_entries() == catalogue
        assert db.entitlements() == db.acquisitions() == []
        service.catalog_scan.assert_not_called()
        legacy.assert_not_called()
        assert all(path.read_bytes() == raw for path, raw in witnesses.items())
    finally:
        service.close()
