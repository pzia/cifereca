import json
from pathlib import Path

from cifereca_library_audits import CoverAuditTool, LanguageAuditTool, NamingAuditTool


class Catalog:
    def catalog_entries(self, **_options):
        return [
            {"title": "A", "thumbnail_url": "https://example.test/a.jpg", "metadata": {"language": "FR"}},
            {"title": "B", "metadata": {"language": "fre"}},
            {"title": "C", "thumbnail_url": "javascript:bad", "metadata": {"language": "???"}},
        ]


class Metadata:
    def library_entries(self, **_options):
        return [{"title": "Titre", "author": "Auteur", "filename": "bad.epub", "format": "EPUB"}]


class Naming:
    def preview(self, **_kwargs):
        return "Auteur - Titre.epub"


class Context:
    def broker(self, name):
        return {"catalog": Catalog(), "metadata": Metadata(), "library.naming": Naming()}[name]


def test_cover_audit_is_read_only():
    tool = CoverAuditTool().activate(Context())
    plan = tool.plan(type("MC", (), {"identity_id": None})())
    assert plan.facts["missing"] == 1
    assert plan.facts["invalid"] == 1
    assert tool.describe().danger_level == "read-only"


def test_language_audit_uses_canonicalizer():
    tool = LanguageAuditTool().activate(Context())
    plan = tool.plan(type("MC", (), {"identity_id": None})())
    assert plan.facts["noncanonical"] >= 2
    assert plan.facts["invalid"] == 1


def test_naming_audit_never_renames():
    tool = NamingAuditTool().activate(Context())
    plan = tool.plan(type("MC", (), {"identity_id": None})())
    assert plan.facts["mismatch"] == 1
    assert "aucune mutation" in " ".join(plan.warnings).lower()


def test_manifest_requires_plugin_api_14_for_maintenance_contracts():
    manifest = json.loads((Path(__file__).resolve().parents[1] / "cifereca-plugin.json").read_text(encoding="utf-8"))
    assert manifest["package"]["plugin_api"] == ">=1.4,<2"
    assert manifest["package"]["version"] == "0.2.10"
    assert {row["version"] for row in manifest["plugins"]} == {"0.2.10"}


def test_retired_reconciliation_tool_not_exposed_but_maintenance_remains():
    manifest = json.loads((Path(__file__).resolve().parents[1] / "cifereca-plugin.json").read_text())
    ids = {row["id"] for row in manifest["plugins"]}
    assert "maintenance.audit.reconciliation" not in ids
    assert {"maintenance.audit.integrity", "maintenance.cleanup.duplicates",
            "maintenance.repair.naming"} <= ids
    assert not any("library.reconciliation.read" in row.get("requires_brokers", ())
                   for row in manifest["plugins"])
