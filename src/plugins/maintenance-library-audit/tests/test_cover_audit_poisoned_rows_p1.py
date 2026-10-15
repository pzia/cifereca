"""#1493: corrupt cover rows are isolated without exposing private metadata."""
import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool


class PoisonedMapping(dict):
    def get(self, key, default=None):
        raise RuntimeError("Bearer PRIVATE_TOKEN /home/PRIVATE_BOOK.epub")


class PoisonedCover(str):
    def __bool__(self):
        raise RuntimeError("PRIVATE_BOOK /home/PRIVATE_COVER.png")


class PoisonedStrip(str):
    def strip(self, *args, **kwargs):
        raise RuntimeError("PRIVATE_ACCOUNT Bearer PRIVATE_TOKEN")


class Context:
    def __init__(self, rows):
        self.rows = rows

    def broker(self, name):
        assert name == "catalog"
        return type("Catalog", (), {
            "catalog_entries": lambda _self, **_kw: self.rows,
        })()


class Control:
    def __init__(self):
        self.events = []

    def check_cancelled(self):
        return None

    def report(self, **kwargs):
        self.events.append(kwargs)


@pytest.mark.parametrize("malformed", (
    PoisonedMapping(cover_url="https://PRIVATE.example.org/secret.png"),
    {"metadata": PoisonedMapping(cover_url="https://PRIVATE.example.org/secret.png")},
    {"cover_url": PoisonedCover("https://PRIVATE.example.org/secret.png")},
    {"cover_url": PoisonedStrip("https://PRIVATE.example.org/secret.png")},
))
def test_broken_cover_row_does_not_abort_scan_or_leak_metadata(malformed):
    context = Context([
        {"title": "Public", "cover_url": "https://example.org/cover.jpg"},
        malformed,
        {"title": "No cover"},
        {"title": "Next", "cover_url": "https://example.org/second.png"},
    ])
    tool = CoverAuditTool().activate(context)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="alice"))
    assert plan.facts == {"entries": 4, "missing": 1, "invalid": 1}
    assert plan.warnings == (
        "Couverture non reconnue : notice n°2",
        "Sans couverture : notice n°3",
    )
    control = Control()
    result = tool.run(plan, control)
    assert result.ok and result.changed == 0
    assert control.events == [{"message": plan.summary}]
    public = json.dumps({
        "summary": result.summary, "warnings": result.warnings,
        "facts": result.facts, "progress": control.events,
    }, ensure_ascii=False)
    for secret in ("PRIVATE", "Bearer", "/home/", "RuntimeError"):
        assert secret not in public
