"""#1886: author/publisher/year/format local filters NEVER claim server coverage.

Observed anonymous Numilog search fixture, no live HTTP, no credentials.
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog_public import parse_public_search
from cifereca_numilog_public_local_filter import refine_loaded_public_page
import cifereca_numilog_public_provider as provider

FIX = Path(__file__).parent / "fixtures"
HTML = (FIX / "public_search_jefferson_20261016.html").read_text()
SOURCE = "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=jefferson"


def session(monkeypatch):
    calls = []
    def load(q, *, page, field):
        calls.append((q, page, field))
        return parse_public_search(HTML, SOURCE)
    monkeypatch.setattr(provider, "fetch_public_search", load)
    s = provider.PLUGIN.create_session(
        credential_id="__public__", label="Public", credentials={},
        options={}, config={}, storage=None, diagnostics=None,
    )
    s.connect()
    return s, calls


def test_author_title_and_publisher_local_filter_is_page_only(monkeypatch):
    s, calls = session(monkeypatch)
    try:
        page = s.browse_public_search("jefferson")
        filtered = s.refine_loaded_page(
            page, author="JEAN CLAUDE MOURLEVAT", title="prend des risques",
            publisher="Gallimard Jeunesse",
        )
        assert filtered.scope == "loaded_public_page_only"
        assert filtered.applied_fields == ("author", "publisher", "title")
        assert filtered.loaded_count == 20
        assert filtered.matching_count == 1
        assert filtered.items[0].title == "Jefferson prend des risques"
        assert filtered.public_unfiltered_total == 242
        assert filtered.public_page_index == 0
        assert filtered.public_page_complete is False
        assert filtered.has_uninspected_or_unverified_matches is True
        assert filtered.missing_evidence_count == 0
        assert calls == [("jefferson", 0, "all")]
        assert all(not item.owned and not item.downloadable
                   and item.download_url is None for item in filtered.items)
    finally:
        s.close()


def test_coauthor_and_explicit_commercial_format_with_year(monkeypatch):
    s, calls = session(monkeypatch)
    try:
        p = s.browse_public_search("jefferson")
        pdf = s.refine_loaded_page(
            p, author="Antoine Ronzon", year=2026, format_label="PDF",
        )
        epub = s.refine_loaded_page(
            p, author="Antoine Ronzon", year=2026, format_label="ePub",
        )
        # Two commercially distinct product IDs with the same title and
        # authors must never be collapsed into a fabricated owned edition.
        assert pdf.matching_count == epub.matching_count == 1
        assert pdf.items[0].record_id != epub.items[0].record_id
        assert all("Antoine Ronzon" in row.metadata["authors"]
                   for row in pdf.items + epub.items)
        assert pdf.items[0].metadata["format_labels"] == ["PDF"]
        assert epub.items[0].metadata["format_labels"] == ["ePub"]
        assert all(row.published_at.startswith("2026-")
                   for row in pdf.items + epub.items)
        assert pdf.public_unfiltered_total == epub.public_unfiltered_total == p.total_results
        assert calls == [("jefferson", 0, "all")]
    finally:
        s.close()


def test_local_result_does_not_modify_original_page_or_cached_result(monkeypatch):
    s, calls = session(monkeypatch)
    try:
        p = s.browse_public_search("jefferson")
        r = s.refine_loaded_page(p, title="prend des risques")
        r.items[0].metadata["authors"].append("forged author")
        r.items[0].title = "Forged"
        check = s.refine_loaded_page(p, title="prend des risques")
        assert check.items[0].title == "Jefferson prend des risques"
        assert "forged author" not in check.items[0].metadata["authors"]
        assert s.browse_public_search("jefferson").items[0].title == "Jefferson prend des risques"
        assert calls == [("jefferson", 0, "all")]
    finally:
        s.close()


def test_absent_field_is_unknown_not_an_exhaustive_negative(monkeypatch):
    s, _ = session(monkeypatch)
    try:
        page = s.browse_public_search("jefferson")
        row = page.items[0]
        metadata = dict(row.metadata)
        proof = dict(metadata["field_evidence"])
        proof.pop("publisher")
        metadata["field_evidence"] = proof
        unknown = replace(page, items=(replace(row, metadata=metadata),),
                          complete=True, total_results=1, next_url=None)
        result = s.refine_loaded_page(unknown, publisher="Gallimard")
        assert result.matching_count == 0
        assert result.missing_evidence_count == 1
        assert result.has_uninspected_or_unverified_matches is True
        assert result.public_page_complete is True
        assert result.public_unfiltered_total == 1
        assert result.loaded_count == 1
    finally:
        s.close()


def test_complete_observed_page_positive_and_negative_are_local_only(monkeypatch):
    s, _ = session(monkeypatch)
    try:
        p = s.browse_public_search("jefferson")
        one = replace(p, items=p.items[:1], total_results=1, complete=True, next_url=None)
        found = s.refine_loaded_page(one, author="Jean-Claude")
        missing = s.refine_loaded_page(one, author="unrelated-name")
        assert found.matching_count == 1
        assert missing.matching_count == 0
        assert missing.has_uninspected_or_unverified_matches is False
        assert missing.scope == "loaded_public_page_only"
    finally:
        s.close()


@pytest.mark.parametrize("filters", [
    {}, {"isbn": "9782075241939"}, {"language": "fr"},
    {"author": ""}, {"title": "  "}, {"publisher": "x"*121},
    {"author": "token=PRIVATE\n"}, {"year": "2026"},
    {"year": True}, {"year": 10000}, {"format_label": 14},
    {"title": {"secret": "PRIVATE"}},
])
def test_unqualified_or_malformed_filters_refused_before_search(monkeypatch, filters):
    s, calls = session(monkeypatch)
    try:
        with pytest.raises(ProviderCapabilityError):
            s.refine_loaded_page(None, **filters)
        assert calls == []
    finally:
        s.close()


@pytest.mark.parametrize("field", [
    "owned", "downloadable", "download_url", "source_provider", "source_collection",
    "source_credential", "scenario", "docbase",
])
def test_cannot_refine_foreign_owned_or_signed_items(monkeypatch, field):
    s, _ = session(monkeypatch)
    try:
        page = s.browse_public_search("jefferson")
        row = page.items[0]
        changes = {
            "owned": True, "downloadable": True,
            "download_url": "https://evil.invalid/?token=PRIVATE",
            "source_provider": "numilog", "source_collection": "library",
            "source_credential": "alice", "scenario": "NUMILOG_LIBRARY",
            "docbase": "KOBO",
        }
        fake = replace(page, items=(replace(row, **{field: changes[field]}),))
        with pytest.raises(ProviderCapabilityError, match="unverified_local_page") as exc:
            s.refine_loaded_page(fake, title="jefferson")
        assert "PRIVATE" not in str(exc.value)
    finally:
        s.close()


def test_missing_provenance_cannot_turn_absent_author_into_false_exhaustiveness(monkeypatch):
    s, _ = session(monkeypatch)
    try:
        page = s.browse_public_search("jefferson")
        row = page.items[0]
        metadata = dict(row.metadata)
        evidence = dict(metadata["field_evidence"])
        evidence["authors"] = {
            "source_url": metadata["source_url"], "source": "footer",
            "kind": "observed_public_html",
        }
        metadata["field_evidence"] = evidence
        fake = replace(page, items=(replace(row, metadata=metadata),),
                       total_results=1, complete=True, next_url=None)
        result = s.refine_loaded_page(fake, author="Mourlevat")
        assert not result.items
        assert result.missing_evidence_count == 1
        assert result.has_uninspected_or_unverified_matches
    finally:
        s.close()


def test_closed_session_refuses_local_refinement_without_network(monkeypatch):
    s, calls = session(monkeypatch)
    p = s.browse_public_search("jefferson")
    s.close()
    with pytest.raises(ProviderError, match="non connecté"):
        s.refine_loaded_page(p, author="Mourlevat")
    assert calls == [("jefferson", 0, "all")]


def test_direct_refinement_requires_attested_public_rows():
    p = parse_public_search(HTML, SOURCE)
    with pytest.raises(ProviderCapabilityError, match="unverified_local_page"):
        refine_loaded_public_page(p, author="Mourlevat")
