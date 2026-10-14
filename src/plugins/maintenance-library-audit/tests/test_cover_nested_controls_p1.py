"""#1493: repeated percent-encoding must not hide controls in cover audit.

Pure synthetic catalogue; no HTTP, filesystem writes or UI.
"""
import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool, _encoded_cover_has_control


class Catalog:
    def __init__(self, cover):
        self.cover = cover

    def catalog_entries(self, **kwargs):
        assert kwargs == {"identity_id": "profile"}
        return ({"title": "PRIVATE_BOOK_TITLE", "cover_url": self.cover},)


class Context:
    def __init__(self, cover):
        self.catalog = Catalog(cover)

    def broker(self, name):
        assert name == "catalog"
        return self.catalog


@pytest.mark.parametrize("cover", (
    "https://example.org/covers/book%250Asecret.jpg",
    "https://example.org/covers/book%25250Dsecret.jpg",
    "https://example.org/covers/book%2500secret.jpg",
    "https://example.org/covers/book%257Fsecret.jpg",
    "file:///tmp/PRIVATE_COVER%250Afile.png",
    "covers/PRIVATE_COVER%25250Afile.jpg",
))
def test_nested_encoded_controls_are_counted_without_exposing_references(cover):
    plan = CoverAuditTool().activate(Context(cover)).plan(
        MaintenanceContext(scope="library", identity_id="profile"),
    )
    assert plan.facts == {"entries": 1, "missing": 0, "invalid": 1}
    assert plan.warnings == ("Couverture non reconnue : notice n°1",)
    exported = json.dumps({
        "summary": plan.summary,
        "warnings": plan.warnings,
        "facts": plan.facts,
    })
    assert "PRIVATE" not in exported
    assert "https://" not in exported


@pytest.mark.parametrize("cover", (
    "https://example.org/covers/book%20title.jpg",
    "https://example.org/covers/book%25discount.jpg",
    "https://example.org/covers/book%2520title.jpg",
    "https://example.org/covers/book%GGtitle.jpg",
    "file:///tmp/covers/ordinary.jpg",
    "covers/cover.jpg",
))
def test_benign_encoded_or_local_cover_references_stay_usable(cover):
    assert not _encoded_cover_has_control(cover)
    plan = CoverAuditTool().activate(Context(cover)).plan(
        MaintenanceContext(scope="library", identity_id="profile"),
    )
    assert plan.facts == {"entries": 1, "missing": 0, "invalid": 0}
    assert plan.warnings == ()


def test_oversized_cover_value_is_rejected_without_public_string_echo():
    plan = CoverAuditTool().activate(
        Context("https://example.org/" + "PRIVATE" * 2000)
    ).plan(MaintenanceContext(scope="library", identity_id="profile"))
    assert plan.facts["invalid"] == 1
    assert "PRIVATE" not in repr(plan.warnings)
