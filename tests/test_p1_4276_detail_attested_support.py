"""P1 #4276 — search/detail uses the same positively attested medium.

No Paris connection, provider GET, external data, rights or UI action.
Tests also inspect the real GTK presenter source without requiring a display.
"""
from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.document_classification import DocumentClassification
from cifereca.gui.detail_model import search_detail_support_label
from tests.test_p1_4276_search_document_filter import classified, notice


@pytest.mark.parametrize(("kind", "actual", "label"), [
    ("book", "digital", "Numérique"),
    ("book", "physical", "Physique"),
    ("comics", "physical", "Physique"),
    ("music", "physical", "Physique"),
    ("video", "physical", "Physique"),
    ("periodical", "physical", "Physique"),
    ("audiobook", "digital", "Numérique"),
])
def test_all_document_natures_keep_independent_attested_medium(kind, actual, label):
    historical = "physical" if actual == "digital" else "digital"
    item = notice(kind, medium=historical, old_kind="physical",
                  doc=classified(actual, kind))
    assert search_detail_support_label(item) == label


def test_unknown_support_does_not_become_physical_or_audio_from_legacy_type():
    legacy_unknown = notice("unknown", medium="unknown", old_kind="audiobook")
    assert search_detail_support_label(legacy_unknown) == "Support indéterminé"
    # Present but unverified documentary classification forbids old fallback,
    # even when the old medium field claims certainty.
    for historical in ("digital", "physical"):
        item = notice("unverified", medium=historical, old_kind="audiobook",
                      doc=DocumentClassification())
        assert search_detail_support_label(item) == "Support indéterminé"
        malformed = replace(item, document_classification={"medium": "physical"})
        assert search_detail_support_label(malformed) == "Support indéterminé"


@pytest.mark.parametrize(("medium", "expected"), [
    ("digital", "Numérique"),
    ("physical", "Physique"),
    ("unknown", "Support indéterminé"),
    (None, "Support indéterminé"),
    (True, "Support indéterminé"),
])
def test_legacy_notices_remain_readable_without_invented_support(medium, expected):
    item = notice("legacy", doc=None)
    item.medium = medium
    assert search_detail_support_label(item) == expected


def test_actual_gtk_search_and_detail_views_use_same_support_projection():
    path = Path(__file__).resolve().parents[1] / "src/cifereca/gui/detail_panel.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    panel = next(node for node in tree.body
                 if isinstance(node, ast.ClassDef) and node.name == "DetailPanel")
    for method_name in ("show_search", "show_item_detail"):
        method = next(node for node in panel.body
                      if isinstance(node, ast.FunctionDef)
                      and node.name == method_name)
        source = ast.unparse(method)
        assert "medium = search_detail_support_label(item)" in source
        assert "('Support', medium if fmt else '')" in source
        assert "self.window.service.content_type_for(item)" not in source
        assert "item.medium == 'digital'" not in source
