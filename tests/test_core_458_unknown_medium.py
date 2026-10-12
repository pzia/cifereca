from __future__ import annotations

from tests.gui_source import gui_source_text

from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace as NS
from typing import get_args

from cifereca.acquisition import AcquisitionManager
from cifereca.core.search import filter_loaded_results, support_kind
from cifereca.gui.book_facets import facets_for_book
from cifereca.gui.query_model import QueryState, apply_facet, result_row
from cifereca.models import Medium, SearchResult
from cifereca.plugin_api import Medium as PublicMedium
from cifereca.services import LibraryService


def item(*, record_id: str, medium: str, fmt: str | None = None, friendly_url: str | None = None, content_type: str | None = None):
    return SearchResult(
        id=record_id,
        record_id=record_id,
        docbase="TEST",
        medium=medium,
        title="Titre",
        author=None,
        type=None,
        format=fmt,
        publisher=None,
        published_at=None,
        description=None,
        thumbnail_url=None,
        friendly_url=friendly_url,
        scenario="TEST",
        source_provider="test",
        source_credential="cred",
        content_type=content_type,
    )


def test_public_medium_contract_includes_unknown():
    assert PublicMedium is Medium
    assert set(get_args(Medium)) == {"digital", "physical", "unknown"}


def test_unknown_support_is_not_digital_without_evidence():
    assert support_kind({"medium": "unknown"}) == "unknown"
    assert support_kind({"friendly_url": "https://gallica.bnf.fr/ark:/12148/example"}) == "unknown"
    assert support_kind({"medium": "unknown", "friendly_url": "https://gallica.bnf.fr/ark:/12148/example"}) == "unknown"


def test_explicit_file_evidence_can_refine_unknown_to_digital():
    assert support_kind({"medium": "unknown", "format": "epub"}) == "digital"
    assert support_kind({"medium": "unknown", "media_format": "application/pdf"}) == "digital"
    assert support_kind({"medium": "unknown", "content_type": "audiobook"}) == "digital"


def test_explicit_physical_support_remains_authoritative():
    assert support_kind({"medium": "physical", "format": "epub"}) == "physical"


def test_scope_filters_are_exact_and_all_keeps_unknown():
    rows = [
        {"key": "p", "medium": "physical"},
        {"key": "d", "medium": "digital"},
        {"key": "u", "medium": "unknown"},
        {"key": "proved", "medium": "unknown", "format": "epub"},
        {"key": "missing"},
    ]

    assert [row["key"] for row in filter_loaded_results(rows, scope="all")] == ["p", "d", "u", "proved", "missing"]
    assert [row["key"] for row in filter_loaded_results(rows, scope="physical")] == ["p"]
    assert [row["key"] for row in filter_loaded_results(rows, scope="digital")] == ["d", "proved"]
    assert [row["key"] for row in filter_loaded_results(rows, scope="unknown")] == ["u", "missing"]


def test_unknown_support_is_a_visible_filterable_facet():
    facets = facets_for_book({"title": "Notice", "medium": "unknown", "metadata": {}})
    support = next(row for row in facets if row.field == "support")

    assert support.label == "Support indéterminé"
    assert support.value == "unknown"
    assert support.group == "form"

    state = apply_facet(QueryState(scope="all"), "support", "unknown")
    assert state.scope == "unknown"


def test_search_result_projection_preserves_unknown_medium():
    row = result_row(item(record_id="u", medium="unknown", friendly_url="https://gallica.bnf.fr/ark:/x"))

    assert row["medium"] == "unknown"
    assert row["media_format"] == ""
    assert support_kind(row) == "unknown"


def test_content_type_does_not_invent_ebook_from_documentary_link():
    unknown = item(record_id="u", medium="unknown", friendly_url="https://gallica.bnf.fr/ark:/x")
    proved = item(record_id="e", medium="unknown", fmt="epub")

    assert AcquisitionManager.content_type_for(unknown) == "unknown"
    assert AcquisitionManager.content_type_for(proved) == "ebook"


class FakeSession:
    provider_id = "test"
    descriptor = NS(display_name="Test")
    client = None

    def __init__(self):
        self.scopes = []
        self.last_search_warnings = []

    def search(self, query, scope="all", field="all"):
        self.scopes.append(scope)
        return [
            item(record_id="unknown", medium="unknown", friendly_url="https://gallica.bnf.fr/ark:/unknown"),
            item(record_id="proved", medium="unknown", fmt="epub"),
            item(record_id="digital", medium="digital", fmt="pdf"),
        ]


class SearchHost:
    diagnostics = NS(add=lambda *args, **kwargs: None)

    @staticmethod
    def _search_query_variants(query):
        return [query]

    @staticmethod
    def _search_relevance(query, row):
        return 100

    @staticmethod
    def _field_match(field, query, row):
        return True


def test_unknown_remote_scope_queries_provider_all_then_filters_by_core_evidence():
    session = FakeSession()

    rows, warnings, code, message = LibraryService._search_provider_session(
        SearchHost(), session, "notice", "unknown"
    )

    assert session.scopes == ["all"]
    assert [row.record_id for row in rows] == ["unknown"]
    assert warnings == []
    assert code == ""
    assert message == ""


def test_query_controls_source_exposes_unknown_scope_without_importing_gtk():
    source = Path("src/cifereca/gui/query_controls.py").read_text(encoding="utf-8")

    assert "Support indéterminé" in source
    assert "('all','physical','digital','unknown')" in source


def test_search_list_labels_unknown_support_instead_of_digital():
    source = gui_source_text(Path("src/cifereca/gui/main_window.py"))

    assert 'medium = "Support indéterminé"' in source
    assert 'item.medium == "unknown"' in source
