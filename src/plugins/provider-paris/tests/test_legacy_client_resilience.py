from __future__ import annotations

import json

import httpx
import pytest

from cifereca_paris_client import ParisClient


class Diagnostics:
    def __init__(self):
        self.rows = []

    def add(self, category, operation, status="ok", duration_ms=None, detail=None, **kwargs):
        self.rows.append({
            "category": category,
            "operation": operation,
            "status": status,
            "duration_ms": duration_ms,
            "detail": detail or {},
        })


def _result(record_id: str, title: str, base: str = "SYRACUSE") -> dict:
    return {
        "Resource": {"RscId": record_id, "RscBase": base, "Ttl": title, "Crtr": "Auteur"},
        "CompactResult": "",
    }


def test_login_and_loans():
    def handler(request: httpx.Request):
        path = request.url.path
        if path.endswith("/numerique/"):
            return httpx.Response(200, text="ok")
        if path.endswith("/logon.svc/logon"):
            return httpx.Response(
                200,
                json={"success": True, "d": {}},
                headers={"set-cookie": "_syrSessGuid=abc; Path=/"},
            )
        if path.endswith("/ListLoans"):
            provider = request.url.params.get("serviceCode")
            loans = [
                {
                    "Id": "1",
                    "RecordId": "r",
                    "Title": provider + " title",
                    "AdditionalProperties": {"CanReturn": True},
                }
            ] if provider == "DILICOM" else []
            return httpx.Response(200, json={"success": True, "d": {"Loans": loans}})
        raise AssertionError(path)

    client = ParisClient("https://example.test", diagnostics=Diagnostics())
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        client.login("u", "p")
        rows = client.all_loans()
        assert len(rows) == 1
        assert rows[0].provider == "DILICOM"
    finally:
        client.close()


def test_all_search_keeps_physical_results_when_ebooks_fails():
    def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        scenario = body["query"]["ScenarioCode"]
        if scenario == "CATALOGUE":
            return httpx.Response(200, json={"success": True, "d": {"Results": [_result("1", "Manga papier")]}})
        return httpx.Response(
            200,
            json={"success": False, "errors": [{"id": "SearchService", "msg": "Votre recherche a échoué", "type": ""}]},
        )

    client = ParisClient("https://example.test", diagnostics=Diagnostics())
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        rows = client.search("manga", "all")
        assert [row.title for row in rows] == ["Manga papier"]
        assert client.last_search_warnings == ["partial_results : Catalogue numérique momentanément indisponible"]
    finally:
        client.close()


def test_digital_search_retries_with_smaller_result_size():
    sizes = []

    def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        size = int(body["query"]["ResultSize"])
        sizes.append(size)
        if size == 50:
            return httpx.Response(
                200,
                json={"success": False, "errors": [{"id": "SearchService", "msg": "Votre recherche a échoué", "type": ""}]},
            )
        return httpx.Response(200, json={"success": True, "d": {"Results": [_result("2", "Manga numérique", "DILICOM")]}})

    client = ParisClient("https://example.test", diagnostics=Diagnostics())
    client.client.close()
    client.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        rows = client.search("manga", "digital")
        assert sizes == [50, 20]
        assert len(rows) == 1
        assert rows[0].medium == "digital"
    finally:
        client.close()


def test_remote_protocol_error_recreates_session_and_retries_once():
    factory_calls = 0
    login_posts = 0

    def factory():
        nonlocal factory_calls
        factory_calls += 1
        generation = factory_calls

        def handler(request: httpx.Request):
            nonlocal login_posts
            if request.url.path.endswith("/numerique/"):
                return httpx.Response(200, text="ok")
            if request.url.path.endswith("/logon.svc/logon"):
                login_posts += 1
                if generation == 1:
                    raise httpx.RemoteProtocolError("truncated")
                return httpx.Response(
                    200,
                    json={"success": True, "d": {}},
                    headers={"set-cookie": "_syrSessGuid=abc; Path=/"},
                )
            raise AssertionError(request.url.path)

        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    diagnostics = Diagnostics()
    client = ParisClient("https://example.test", diagnostics=diagnostics, client_factory=factory)
    try:
        result = client.login("user", "password")
        assert result["success"]
        assert login_posts == 2
        assert factory_calls == 2
        retry = [row for row in diagnostics.rows if row["operation"] == "login retry"]
        assert len(retry) == 1
        assert retry[0]["detail"]["attempt"] == "1/2"
    finally:
        client.close()


def test_remote_protocol_error_stops_after_second_attempt():
    login_posts = 0

    def factory():
        def handler(request: httpx.Request):
            nonlocal login_posts
            if request.url.path.endswith("/numerique/"):
                return httpx.Response(200, text="ok")
            if request.url.path.endswith("/logon.svc/logon"):
                login_posts += 1
                raise httpx.RemoteProtocolError("truncated")
            raise AssertionError(request.url.path)
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)

    client = ParisClient("https://example.test", diagnostics=Diagnostics(), client_factory=factory)
    try:
        with pytest.raises(httpx.RemoteProtocolError):
            client.login("user", "password")
        assert login_posts == 2
    finally:
        client.close()
