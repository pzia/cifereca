"""SearchService must not turn missing or malformed pages into false zero."""
from __future__ import annotations

import json

import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisSearchUnavailable
from cifereca.search_failures import search_failure_code
from cifereca.core.known_catalog_fallback import public_service_unavailable


def _session(responses):
    calls = []

    def respond(request):
        query = json.loads(request.content)["query"]
        scenario = query["ScenarioCode"]
        calls.append(scenario)
        return httpx.Response(200, json=responses[scenario])

    client = ParisClient("https://example.test")
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    return client, calls


def _page(rows):
    return {"success": True, "d": {"Results": rows}}


def _record(identifier="positive"):
    return {"Resource": {"RscId": identifier, "RscBase": "SYRACUSE",
                         "Type": "Livre", "Frmt": "MONO", "Ttl": "Positive"}}


@pytest.mark.parametrize("payload", [
    {"success": True},
    {"success": True, "d": None},
    {"success": True, "d": []},
    {"success": True, "d": {}},
    _page(None), _page({}), _page(""), _page(False), _page(0),
    _page([None, 0, {}, {"Resource": {"RscId": {}, "RscBase": "SYRACUSE"}}]),
    _page([{"Resource": {"RscId": "x", "RscBase": {"bad": 1}}}]),
])
def test_invalid_envelope_or_all_rejected_has_no_zero_and_no_retry(payload):
    client, calls = _session({"CATALOGUE": payload})
    try:
        with pytest.raises(ParisSearchUnavailable) as caught:
            client.search_response("test", "physical")
        assert caught.value.code == "search_response_invalid"
        assert caught.value.retryable is False
        assert search_failure_code(caught.value) is None
        assert not public_service_unavailable(caught.value)
        assert calls == ["CATALOGUE"]
        assert client.last_search_warnings == ["Catalogue physique : réponse invalide"]
    finally:
        client.close()


def test_explicit_empty_list_is_a_successful_page_not_a_global_completion():
    client, calls = _session({"CATALOGUE": _page([])})
    try:
        assert client.search_response("test", "physical") == ([], [], [])
        assert calls == ["CATALOGUE"]
    finally:
        client.close()


def test_invalid_branch_and_healthy_empty_sibling_is_partial_zero():
    client, calls = _session({"CATALOGUE": _page([]), "EBOOKS": _page(None)})
    try:
        assert client.search_response("test", "all") == (
            [], [], ["partial_results : Catalogue numérique : réponse invalide"],
        )
        assert calls == ["CATALOGUE", "EBOOKS"]
    finally:
        client.close()


def test_partial_decoding_retains_positive_rows_and_marks_page_partial():
    client, calls = _session({"CATALOGUE": _page([_record(), None, {}])})
    try:
        rows, facets, warnings = client.search_response("test", "physical")
        assert [row.id for row in rows] == ["SYRACUSE:positive"]
        assert facets == []
        assert warnings == ["partial_results : Catalogue physique : résultats partiellement décodés"]
        assert calls == ["CATALOGUE"]
    finally:
        client.close()


def test_both_invalid_branches_are_unavailable_without_smaller_queries():
    client, calls = _session({"CATALOGUE": _page(None), "EBOOKS": _page([{}])})
    try:
        with pytest.raises(ParisSearchUnavailable) as caught:
            client.search_response("test", "all")
        assert caught.value.code == "search_response_invalid"
        assert calls == ["CATALOGUE", "EBOOKS"]
    finally:
        client.close()


def test_local_medium_filter_is_not_a_parse_failure():
    client, calls = _session({"EBOOKS": _page([_record()])})
    try:
        assert client.search_response("test", "digital") == ([], [], [])
        assert calls == ["EBOOKS"]
    finally:
        client.close()
