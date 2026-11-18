"""#6366/#6590: OPDS2 coverage and conflicting search declarations, offline."""
import importlib.util
import sys
from pathlib import Path

import pytest

module = Path(__file__).resolve().parents[1] / "cifereca_bnf_pret_opds2.py"
spec = importlib.util.spec_from_file_location("opds2_coverage_6366", module)
opds = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = opds
spec.loader.exec_module(opds)


def page(links=(), **kwargs):
    return {"metadata": {"title": "Test"}, "links": list(links),
            "publications": [{"metadata": {"title": "Book"}}], **kwargs}


def search(href, **kwargs):
    return {"rel": "search", "href": href, "type": "application/opds+json",
            "templated": True, **kwargs}


@pytest.mark.parametrize("links,state", [
    ([], "absent"),
    ([search("/v1/a{?query}")], "valid"),
    ([search("/v1/a{?query}"), search("https://pret.bnf.fr/v1/a{?query}")], "valid"),
    ([search("/v1/a{?query}"), search("/v1/b{?query}")], "ambiguous"),
    ([search("/v1/a{?query}"), search("/v1/a{?query,title}")], "ambiguous"),
    ([search("/v1/a{?query}"), search("https://outside.test/v1/a{?query}")], "invalid"),
    ([search("/v1/a{?query}"), search("/v1/a", templated=False)], "invalid"),
])
def test_search_declarations_never_last_wins(links, state):
    for ordered in (links, list(reversed(links))):
        parsed = opds.parse_catalog(page(ordered))
        assert parsed.search_status == state
        assert (parsed.search_template is not None) == (state == "valid")
        assert parsed.entries_partial is False


def test_invalid_entries_mark_partial_without_losing_positive_rows():
    result = opds.parse_catalog(page(groups=[
        {"navigation": [{"title": "Outside", "href": "https://outside.test/v1/a",
                         "type": "application/opds+json"}]},
        {"publications": [{"metadata": {"title": ""}},
                          {"metadata": {"title": "Another"}}]},
    ]))
    assert [p.title for p in result.publications] == ["Book", "Another"]
    assert result.navigation == ()
    assert result.entries_partial is True
    assert all(not p.owned and not p.borrowable for p in result.publications)


def test_raw_group_entries_share_aggregate_budget(monkeypatch):
    monkeypatch.setattr(opds, "MAX_ENTRIES", 3)
    ignored = {"metadata": {"title": ""}}
    with pytest.raises(opds.OPDS2ContractError, match="too_many_entries"):
        opds.parse_catalog(page(groups=[
            {"publications": [ignored, ignored]},
            {"publications": [ignored]},
        ]))


def test_empty_unqualified_feed_still_refused():
    raw = page()
    raw["publications"] = [{"metadata": {"title": ""}}]
    with pytest.raises(opds.OPDS2ContractError, match="empty_catalog"):
        opds.parse_catalog(raw)
