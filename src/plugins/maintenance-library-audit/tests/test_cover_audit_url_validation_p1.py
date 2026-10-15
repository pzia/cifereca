"""#1493: audit cover references structurally without network or private URL echo."""
from __future__ import annotations

import json

import pytest

from cifereca.plugin_api import MaintenanceContext
from cifereca_library_audits import CoverAuditTool


class Catalog:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def catalog_entries(self, **options):
        self.calls.append(options)
        return self.rows


class Context:
    def __init__(self, rows):
        self.catalog = Catalog(rows)

    def broker(self, name):
        assert name == "catalog"
        return self.catalog


@pytest.mark.parametrize("cover", [
    "http://", "https://", "https:///cover.png", "https://?x=1",
    "http://user:PRIVATE_PASSWORD@covers.example/image.jpg",
    "https://covers.example:99999/cover.png",
    "https://covers.example:notaport/cover.png",
    "https://covers.example:0/cover.png",
    "file://remote.example/home/PRIVATE_USER/cover.jpg",
    "file:relative.png", "file://user:pass@localhost/home/a.png",
    "//covers.example/path.jpg",
    "https://covers.example/path.jpg\nPRIVATE_SECRET",
    "javascript:PRIVATE_TOKEN", "https://[unclosed/cover.jpg",
    "https://covers.example/cover%0D%0APRIVATE_TOKEN.jpg",
    "https://covers.example/%00cover.jpg",
    "https://covers.example/%1fcover.jpg",
    "https://covers.example/%7Fcover.jpg",
    "covers/%09PRIVATE_TOKEN.jpg",
    "file:///home/user/cover%0aPRIVATE_SECRET.jpg",
])
def test_invalid_cover_reference_is_counted_without_leaking_private_values(cover):
    ctx = Context([{"title": "PRIVATE_TITLE", "thumbnail_url": cover}])
    tool = CoverAuditTool().activate(ctx)
    plan = tool.plan(MaintenanceContext(scope="library", identity_id="owner"))
    assert ctx.catalog.calls == [{"identity_id": "owner"}]
    assert plan.facts == {"entries": 1, "missing": 0, "invalid": 1}
    assert plan.warnings == ("Couverture non reconnue : notice n°1",)
    assert "PRIVATE" not in json.dumps({
        "summary": plan.summary, "warnings": plan.warnings,
        "facts": plan.facts,
    })


@pytest.mark.parametrize("cover", [
    "https://covers.example/cover.jpg", "http://covers.example",
    "https://[::1]:443/cover.png", "https://covers.example:8443/a.png",
    "file:///home/user/cover.jpg", "file://localhost/home/user/cover.jpg",
    "covers/cover.jpg", "cover.jpg",
    "https://covers.example/cover%20image.jpg",
    "https://covers.example/cover%25discount.jpg",
    "https://covers.example/cover%GG.jpg",
])
def test_valid_remote_local_and_relative_cover_references_remain_accepted(cover):
    ctx = Context([{"cover_url": cover}])
    plan = CoverAuditTool().activate(ctx).plan(
        MaintenanceContext(scope="library", identity_id=None),
    )
    assert plan.facts == {"entries": 1, "missing": 0, "invalid": 0}
    assert plan.warnings == ()
