from __future__ import annotations

import json

import httpx

from cifereca.diagnostics import Diagnostics
from cifereca.paris.client import ParisClient


def _result(record_id: str, title: str, base: str = "SYRACUSE") -> dict:
    return {
        "Resource": {"RscId": record_id, "RscBase": base, "Ttl": title, "Crtr": "Auteur"},
        "CompactResult": "",
    }


def test_all_search_keeps_physical_results_when_ebooks_fails():
    def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        scenario = body["query"]["ScenarioCode"]
        if scenario == "CATALOGUE":
            return httpx.Response(200, json={"success": True, "d": {"Results": [_result("1", "Manga papier")]}})
        return httpx.Response(200, json={"success": False, "errors": [{"id": "SearchService", "msg": "Votre recherche a échoué", "type": ""}]})

    c = ParisClient("https://example.test", diagnostics=Diagnostics())
    c.client.close()
    c.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        rows = c.search("manga", "all")
        assert [x.title for x in rows] == ["Manga papier"]
        assert c.last_search_warnings == ["Catalogue numérique momentanément indisponible"]
    finally:
        c.close()


def test_digital_search_retries_with_smaller_result_size():
    sizes: list[int] = []

    def handler(request: httpx.Request):
        body = json.loads(request.content.decode())
        size = int(body["query"]["ResultSize"])
        sizes.append(size)
        if size == 50:
            return httpx.Response(200, json={"success": False, "errors": [{"id": "SearchService", "msg": "Votre recherche a échoué", "type": ""}]})
        return httpx.Response(200, json={"success": True, "d": {"Results": [_result("2", "Manga numérique", "DILICOM")]}})

    c = ParisClient("https://example.test", diagnostics=Diagnostics())
    c.client.close()
    c.client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    try:
        rows = c.search("manga", "digital")
        assert sizes == [50, 20]
        assert len(rows) == 1
        assert rows[0].medium == "digital"
        assert c.last_search_warnings == []
    finally:
        c.close()
