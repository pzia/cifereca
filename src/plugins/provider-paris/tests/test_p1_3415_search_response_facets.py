"""P1 #3415: response-page Syracuse facets are not record metadata or filters.

MockTransport only. No account, scraping, loan, acquisition or rights.
The nested FacetList *item* schema has not been observed and is NOT parsed.
"""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_paris_bibliography import project_search_response_facets
from cifereca_paris_client import ParisClient, ParisSearchUnavailable


def _facet(field="Subj", label="Sujets", *, values=None, **extra):
    return {
        "FacetField": field, "FacetLabel": label,
        "FacetList": [] if values is None else values,
        "IsSearchable": True, "IsMultiselectable": False,
        **extra,
    }


def _response(scenario, *facets):
    return {
        "success": True,
        "d": {
            "Results": [{
                "Resource": {
                    "RscId": "synthetic-" + scenario,
                    "RscBase": "DILICOM" if scenario == "EBOOKS" else "SYRACUSE",
                    "Ttl": "Synthetic book",
                },
            }],
            "FacetCollectionList": list(facets),
        },
    }


def _client(responder):
    client = ParisClient("https://example.test")
    client.client.close()
    client.client = httpx.Client(
        transport=httpx.MockTransport(responder), trust_env=False,
    )
    return client


@pytest.mark.parametrize("scenario", ("CATALOGUE", "EBOOKS"))
def test_observed_response_descriptor_is_page_scoped_not_per_book(scenario):
    captures = []
    def respond(request):
        captures.append(json.loads(request.content))
        return httpx.Response(200, json=_response(scenario, _facet(
            values=[{"shape": "not_attested"}], IsMultiselectable=True,
        )))
    client = _client(respond)
    try:
        rows = client.search("science", scope="digital" if scenario == "EBOOKS" else "physical", page=2)
        assert len(rows) == 1
        assert "search_response_facets" not in rows[0].metadata
        assert "search_facets" not in rows[0].metadata
        assert "filter_capabilities" not in rows[0].metadata
        assert captures[0]["query"]["Page"] == 2
        assert client.last_search_response_facets == [{
            "scenario": scenario, "page": 2,
            "field": "Subj", "label": "Sujets",
            "upstream_is_searchable": True,
            "upstream_is_multiselectable": True,
            "observed_entry_count": 1,
            "source": "SearchService.d.FacetCollectionList",
            "scope": "response_page_only",
            "filter_capability": "unverified",
            "values_schema": "unverified",
        }]
    finally:
        client.close()


def test_all_scopes_keep_facets_in_their_own_branch_and_reset_between_queries():
    calls = []
    def respond(request):
        body = json.loads(request.content)
        scenario = body["query"]["ScenarioCode"]
        calls.append(scenario)
        facets = [_facet(label="Physique" if scenario == "CATALOGUE" else "Numérique")]
        return httpx.Response(200, json=_response(scenario, *facets))
    client = _client(respond)
    try:
        assert len(client.search("first", scope="all")) == 2
        assert [x["scenario"] for x in client.last_search_response_facets] == ["CATALOGUE", "EBOOKS"]
        assert [x["label"] for x in client.last_search_response_facets] == ["Physique", "Numérique"]
        before = list(client.last_search_response_facets)
        assert len(client.search_catalog("cover", scenario="CATALOGUE")) == 1
        assert client.last_search_response_facets == before
        assert len(client.search("second", scope="digital")) == 1
        assert [x["scenario"] for x in client.last_search_response_facets] == ["EBOOKS"]
        assert calls == ["CATALOGUE", "EBOOKS", "CATALOGUE", "EBOOKS"]
    finally:
        client.close()


@pytest.mark.parametrize("failed", ("CATALOGUE", "EBOOKS"))
def test_failed_branch_does_not_contribute_stale_facets(failed):
    def respond(request):
        scenario = json.loads(request.content)["query"]["ScenarioCode"]
        if scenario == failed:
            return httpx.Response(403, json={"success": False, "message": "denied"})
        return httpx.Response(200, json=_response(scenario, _facet()))
    client = _client(respond)
    try:
        rows = client.search("mixed", scope="all")
        assert len(rows) == 1
        other = "CATALOGUE" if failed == "EBOOKS" else "EBOOKS"
        assert [x["scenario"] for x in client.last_search_response_facets] == [other]
        assert client.last_search_warnings[0].startswith("partial_results")
        with pytest.raises(ParisSearchUnavailable):
            client.search("denied", scope="digital" if failed == "EBOOKS" else "physical")
        assert client.last_search_response_facets == []
    finally:
        client.close()


def test_broad_query_retry_only_admits_facets_from_successful_smaller_page():
    sent = []
    def respond(request):
        body = json.loads(request.content)
        size = body["query"]["ResultSize"]
        sent.append(size)
        if size == 50:
            return httpx.Response(400, json={"success": False, "message": "too broad"})
        return httpx.Response(200, json=_response("EBOOKS", _facet(label="After retry")))
    client = _client(respond)
    try:
        client.search("broad", scope="digital", page=1)
        assert sent == [50, 20]
        assert [x["label"] for x in client.last_search_response_facets] == ["After retry"]
        assert client.last_search_response_facets[0]["page"] == 1
    finally:
        client.close()


def test_untrusted_nested_shapes_do_not_imply_facet_values_or_server_filtering():
    response = {"d": {"FacetCollectionList": [
        _facet(values=[{"FacetValue": "Unproven server format"}]),
        _facet(field="Subj", label="duplicate"),
        _facet(field="<script>", label="bad", values=[]),
        _facet(field="Subject", label="&#60;img onerror=alert(1)&#62;", values=[]),
        _facet(field="Audience", label="Audience", values="not-a-list"),
        _facet(field="Type", label="Type", IsSearchable="true"),
        _facet(field="Numerique", label="Numérique", IsMultiselectable=1),
    ]}}
    projected = project_search_response_facets(response, "CATALOGUE", 0)
    assert [x["field"] for x in projected] == ["Subj", "Type", "Numerique"]
    assert projected[0]["observed_entry_count"] == 1
    assert "Unproven server format" not in str(projected)
    assert projected[1]["upstream_is_searchable"] is None
    assert projected[2]["upstream_is_multiselectable"] is None
    assert all(row["filter_capability"] == "unverified" for row in projected)
    assert project_search_response_facets(response, "UNKNOWN", 0) == []
    assert project_search_response_facets(response, "CATALOGUE", -1) == []
    assert project_search_response_facets({"d": {"FacetCollectionList": _facet()}}, "CATALOGUE", 0) == []
    assert project_search_response_facets({"d": {"FacetCollectionList": [None] * 65}}, "CATALOGUE", 0) == []


def test_response_with_no_facets_is_not_false_zero_server_facet_claim():
    client = _client(lambda request: httpx.Response(200, json=_response("CATALOGUE")))
    try:
        rows = client.search("q", scope="physical")
        assert len(rows) == 1
        assert client.last_search_warnings == []
        assert client.last_search_response_facets == []
    finally:
        client.close()
