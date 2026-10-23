"""P1 #1917: Syracuse public catalogue HTTP denials are not loose-query retries.

MockTransport only: no login, PNB loan, signed ACSM, catalogue network or DRM.
"""
from __future__ import annotations

import httpx
import pytest

from cifereca_paris_client import ParisClient, ParisSearchUnavailable
from cifereca.search_failures import search_failure_code
from cifereca.core.known_catalog_fallback import public_service_unavailable


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, *args, **kwargs):
        self.rows.append((args, kwargs))


def client_for(status):
    events = Diagnostics()
    calls = []
    def respond(request):
        calls.append(request)
        if status == "transport":
            raise httpx.ConnectError("PRIVATE_TOKEN https://signed.invalid/?token=SECRET")
        if status == 400:
            # Retain the old documented broad-query 50 -> 20 recovery path
            # for a BUSINESS error, not a true HTTP auth denial.
            return httpx.Response(400, json={
                "success": False, "message": "PRIVATE_Syracuse search too broad",
            })
        return httpx.Response(status, json={
            "success": False, "message": "PRIVATE_TOKEN https://signed.invalid/",
        })
    client = ParisClient("https://example.test", diagnostics=events)
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False)
    return client, calls, events


@pytest.mark.parametrize("status,expected", (
    (401, "SearchAuthenticationRequired"),
    (403, "SearchAccessDenied"),
    (408, "SearchTemporarilyUnavailable"),
    (429, "SearchTemporarilyUnavailable"),
    (503, "SearchTemporarilyUnavailable"),
    ("transport", "SearchTemporarilyUnavailable"),
))
@pytest.mark.parametrize("scope,scenarios", (
    ("digital", 1), ("physical", 1), ("all", 2),
))
def test_denied_or_failed_syracuse_branch_does_not_retry_a_smaller_post(
    status, expected, scope, scenarios,
):
    client, calls, events = client_for(status)
    try:
        with pytest.raises(ParisSearchUnavailable) as caught:
            client.search("Doyle", scope=scope)
        assert len(calls) == scenarios
        assert search_failure_code(caught.value) == expected
        assert public_service_unavailable(caught.value) is (
            expected == "SearchTemporarilyUnavailable"
        )
        assert caught.value.__cause__ is None
        assert "PRIVATE" not in str(caught.value)
        assert "SECRET" not in str(caught.value)
        assert "PRIVATE" not in repr(events.rows)
        if status in {401, 403}:
            assert caught.value.retryable is False
        assert len(client.last_search_warnings) == scenarios
    finally:
        client.close()


def test_business_search_error_still_gets_existing_bounded_50_to_20_retry():
    client, calls, _ = client_for(400)
    try:
        with pytest.raises(ParisSearchUnavailable):
            client.search("Doyle", scope="digital")
        assert len(calls) == 2
        assert [request.method for request in calls] == ["POST", "POST"]
        assert "PRIVATE" not in repr(client.last_search_warnings)
    finally:
        client.close()


@pytest.mark.parametrize("status,caption", (
    (401, "authentification requise"),
    (403, "accès refusé"),
))
def test_healthy_sibling_stays_accessible_when_other_branch_is_denied(
    status, caption,
):
    client, calls, events = client_for(status)
    try:
        def branch(query, scenario, page, size):
            if scenario == "CATALOGUE":
                return []
            request = httpx.Request(
                "POST", "https://example.test/?token=PRIVATE_SECRET"
            )
            raise httpx.HTTPStatusError(
                "PRIVATE_TOKEN", request=request,
                response=httpx.Response(status, request=request),
            )
        client._search_scenario = branch
        assert client.search("Doyle", scope="all") == []
        assert client.last_search_warnings == [
            "partial_results : Catalogue numérique : " + caption
        ]
        assert "PRIVATE" not in repr(events.rows)
    finally:
        client.close()


def test_mixed_syracuse_denial_and_timeout_preserves_the_refusal():
    client, _, _ = client_for(403)
    try:
        def branch(query, scenario, page, size):
            if scenario == "CATALOGUE":
                raise httpx.ReadTimeout("PRIVATE_TOKEN")
            request = httpx.Request("POST", "https://example.test/?token=SECRET")
            raise httpx.HTTPStatusError(
                "PRIVATE_TOKEN", request=request,
                response=httpx.Response(403, request=request),
            )
        client._search_scenario = branch
        with pytest.raises(ParisSearchUnavailable) as caught:
            client.search("Doyle", scope="all")
        assert search_failure_code(caught.value) == "SearchAccessDenied"
        assert not public_service_unavailable(caught.value)
        assert caught.value.__cause__ is None
    finally:
        client.close()
