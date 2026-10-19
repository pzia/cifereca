from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession


class FailingDiagnostics:
    def add(self, *args, **kwargs):
        raise RuntimeError("diagnostics unavailable")


def _session(transport):
    session = BnfCatalogueSession(
        credential_id="public",
        label="",
        credentials={},
        options={"sru_url": "https://catalogue.example/api/SRU"},
        diagnostics=FailingDiagnostics(),
    )
    session.client.close()
    session.client = httpx.Client(transport=transport, follow_redirects=True)
    return session


def test_search_success_survives_diagnostic_sink_failure():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
  <numberOfRecords>1</numberOfRecords>
  <records>
    <record>
      <recordData>
        <dc xmlns="http://purl.org/dc/elements/1.1/">
          <identifier>ark:/12148/cb123456789a</identifier>
          <title>Titre test</title>
          <format>1 vol. ; 20 cm</format>
        </dc>
      </recordData>
    </record>
  </records>
</searchRetrieveResponse>
"""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=xml)

    session = _session(httpx.MockTransport(handler))
    try:
        rows = session.search("Titre test")

        assert len(rows) == 1
        assert rows[0].title == "Titre test"
    finally:
        session.close()


def test_http_error_is_not_replaced_by_diagnostic_sink_failure():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="unavailable")

    session = _session(httpx.MockTransport(handler))
    try:
        with pytest.raises(ProviderError, match="catalogue temporairement indisponible"):
            session.search("Titre test")
    finally:
        session.close()
