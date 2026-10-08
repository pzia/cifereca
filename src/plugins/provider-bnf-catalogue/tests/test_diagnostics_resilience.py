from __future__ import annotations

import httpx
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_bnf_catalogue import BnfCatalogueSession


class FailingDiagnostics:
    def add(self, *_args, **_kwargs):
        raise RuntimeError("diagnostics unavailable")


class FakeClient:
    def __init__(self, *, fail=False):
        self.fail = fail

    def get(self, url, params=None):
        if self.fail:
            raise httpx.ConnectError(
                "network unavailable",
                request=httpx.Request("GET", url),
            )
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            content=b'''<searchRetrieveResponse xmlns="http://www.loc.gov/zing/srw/">
                <numberOfRecords>0</numberOfRecords>
                <records/>
            </searchRetrieveResponse>''',
        )

    def close(self):
        return None


def _session(*, fail=False):
    session = BnfCatalogueSession(
        credential_id="public",
        label="",
        credentials={},
        options={},
        diagnostics=FailingDiagnostics(),
    )
    session.client.close()
    session.client = FakeClient(fail=fail)
    return session


def test_bnf_search_succeeds_when_diagnostics_sink_fails():
    session = _session()
    try:
        assert session.search("Germinal") == []
    finally:
        session.close()


def test_bnf_http_error_is_not_replaced_by_diagnostics_failure():
    session = _session(fail=True)
    try:
        with pytest.raises(ProviderError, match="catalogue temporairement indisponible"):
            session.search("Germinal")
    finally:
        session.close()
