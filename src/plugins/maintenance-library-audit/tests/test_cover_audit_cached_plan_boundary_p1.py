"""#1493: restored cover-audit previews are never trusted as public text."""
from dataclasses import replace

import pytest

from cifereca.plugin_api import MaintenanceContext, MaintenancePlan
from cifereca_library_audits import CoverAuditTool


class Catalogue:
    def catalog_entries(self, **_kwargs):
        return [
            {"title": "PRIVATE_ALPHA", "cover_url": "https://example.org/cover.jpg"},
            {"title": "PRIVATE_BETA"},
            {"title": "PRIVATE_GAMMA", "cover_url": "javascript:PRIVATE_TOKEN"},
        ]


class Context:
    def broker(self, name):
        assert name == "catalog"
        return Catalogue()


class Control:
    def __init__(self, stop_after=None):
        self.events = []
        self.checks = 0
        self.stop_after = stop_after

    def check_cancelled(self):
        self.checks += 1
        if self.checks == self.stop_after:
            raise RuntimeError("cancelled")

    def report(self, **data):
        self.events.append(data)


def plan():
    return CoverAuditTool().activate(Context()).plan(
        MaintenanceContext(scope="library", identity_id="alice")
    )


def test_real_cover_plan_keeps_safe_preview_and_no_synthetic_progress():
    prepared = plan()
    control = Control()
    result = CoverAuditTool().run(prepared, control)
    assert result.ok and result.changed == 0
    assert result.summary == prepared.summary
    assert result.warnings == prepared.warnings
    assert result.facts == dict(prepared.facts)
    assert control.events == [{"message": prepared.summary}]
    assert control.checks == 2
    assert not any("current" in event or "total" in event for event in control.events)


@pytest.mark.parametrize("poisoned", (
    {"summary": "Bearer PRIVATE_TOKEN /home/alice/book.epub"},
    {"warnings": ("Bearer PRIVATE_TOKEN /home/alice/book.epub",)},
    {"warnings": ("Sans couverture : notice n°999",)},
    {"warnings": ("Couverture non reconnue : notice n°0",)},
    {"warnings": ("Sans couverture : notice n°1",) * 13},
    {"warnings": "PRIVATE_WARNING"},
    {"tool_id": "other.tool"},
    {"facts": {"entries": 3, "missing": 4, "invalid": 0}},
    {"facts": {"entries": 3, "missing": True, "invalid": 0}},
    {"facts": {"entries": 3, "missing": "Bearer PRIVATE_TOKEN", "invalid": 0}},
    {"facts": {"entries": 3, "missing": 1, "invalid": -2}},
    {"facts": {"entries": 3, "missing": 1, "invalid": 3}},
    {"facts": {"entries": 3, "missing": 1, "invalid": 1, "token": "PRIVATE"}},
    {"facts": ["PRIVATE"]},
))
def test_forged_cached_plans_fail_closed_without_echoing_secret(poisoned):
    prepared = replace(plan(), **poisoned)
    control = Control()
    result = CoverAuditTool().run(prepared, control)
    assert not result.ok and result.changed == 0
    assert result.facts == {"invalid_plan": 1}
    assert control.events == [{"message": "Audit des couvertures impossible : plan invalide"}]
    for word in ("PRIVATE", "Bearer", "/home/"):
        assert word not in str((result.summary, result.warnings, result.facts, control.events))
    assert control.checks == 2


def test_cancellation_after_report_does_not_publish_success():
    prepared = plan()
    control = Control(stop_after=2)
    with pytest.raises(RuntimeError, match="cancelled"):
        CoverAuditTool().run(prepared, control)
    assert control.events == [{"message": prepared.summary}]


def test_ordinal_preview_warning_never_includes_private_title():
    prepared = plan()
    assert prepared.warnings == (
        "Sans couverture : notice n°2",
        "Couverture non reconnue : notice n°3",
    )
    assert "PRIVATE" not in str(prepared.warnings)
