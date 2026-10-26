"""#1886 P0: anonymous federated Numilog catalogue is not the owned store.

All HTML comes from a sanitized, existing offline public fixture. No Numilog
account, session cookies, actual public GET or Adobe fulfillment is involved.
"""
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession, VERSION as PRIVATE_VERSION
import cifereca_numilog_public_provider as public
from cifereca_numilog_public import parse_public_search

FIXTURES = Path(__file__).parent / "fixtures"
HTML = (FIXTURES / "public_search_jefferson_20261016.html").read_text()
GOLDEN = json.loads(
    (FIXTURES / "public_search_jefferson_20261016.expected.json").read_text()
)


def new_public_session(credentials=None):
    return public.PLUGIN.create_session(
        credential_id="__public__", label="Catalogue public",
        credentials=credentials or {}, options={}, config={},
        storage=None, diagnostics=None,
    )


def test_manifest_installs_independent_opt_in_singleton_without_brokers():
    manifest = json.loads(
        (Path(__file__).parents[1] / "cifereca-plugin.json").read_text()
    )
    assert manifest["package"]["version"] == PRIVATE_VERSION
    owned, catalogue = manifest["plugins"]
    assert owned["id"] == "provider.numilog"
    assert catalogue["id"] == "provider.numilog.catalog"
    assert catalogue["entrypoint"] == "cifereca_numilog_public_provider:PLUGIN"
    assert catalogue["instantiation"] == "singleton_public"
    assert catalogue["enabled_by_default"] is False
    assert catalogue["requires_brokers"] == ["httpx"]
    assert catalogue["capabilities"] == ["catalog.search", "catalog.detail"]
    assert catalogue["version"] == owned["version"] == PRIVATE_VERSION


def test_connect_is_anonymous_without_remote_probe_or_fake_account(monkeypatch):
    def forbidden(*a, **kw):
        raise AssertionError("connect should not fetch or login")

    monkeypatch.setattr(public, "fetch_public_search", forbidden)
    session = new_public_session()
    assert session.connected is False
    assert session.connect() == []
    assert session.connected is True and session.accounts == []
    assert session.provider_id == "numilog-catalog"
    assert session.credential_id == "__public__"
    assert session.capabilities.search is True
    assert session.capabilities.holdings is True
    assert session.capabilities.personal_library is False
    assert session.capabilities.direct_download is False
    assert session.descriptor.credential_fields == ()
    assert session.search_capabilities.fields == ("all",)
    assert session.search_capabilities.scopes == ("all", "digital")
    assert session.search_capabilities.page_size == 20
    session.close()
    assert session.connected is False


def test_public_federated_results_keep_distinct_provider_and_no_entitlements(monkeypatch):
    page = parse_public_search(HTML, GOLDEN["source_url"])
    calls = []

    def fake_fetch(query, *, page, field):
        calls.append((query, page, field))
        return SimpleNamespace(items=page_result.items)

    page_result = page
    monkeypatch.setattr(public, "fetch_public_search", fake_fetch)
    session = new_public_session()
    session.connect()
    try:
        rows = session.search("jefferson", field="all", scope="all")
        assert calls == [("jefferson", 0, "all")]
        assert len(rows) == 20
        assert rows[0].price == "9.99" and rows[0].currency == "EUR"
        assert rows[0].metadata["price"]["minor_units"] == 999
        assert len({r.record_id for r in rows}) == 20
        assert all(r.source_provider == "numilog-catalog"
                   and r.source_credential == "__public__"
                   and r.source_collection == "public_catalog"
                   and r.record_id.startswith("numilog:public:")
                   and r.owned is False and r.downloadable is False
                   and r.download_url is None for r in rows)
        assert page.items[0].source_provider == "numilog"  # parser input unchanged

        owned = NumilogSession(
            credential_id="fictional-credential", label="Mes achats",
            credentials={}, options={}, config={}, diagnostics=None,
        )
        try:
            with pytest.raises(ProviderError, match="autre connexion"):
                owned._canonical_item(rows[0])
        finally:
            owned.close()

        with pytest.raises(ProviderCapabilityError, match="unsupported"):
            session.download_direct(rows[0], Path("/unused"))
    finally:
        session.close()


@pytest.mark.parametrize("field,scope", [
    ("author", "all"), ("title", "all"), ("isbn", "all"),
    ("all", "physical"), ("all", "catalog"), ("all", "private"),
])
def test_unattested_fields_and_scopes_never_call_network(monkeypatch, field, scope):
    calls = []
    monkeypatch.setattr(
        public, "fetch_public_search",
        lambda *a, **kw: calls.append(True) or SimpleNamespace(items=()),
    )
    session = new_public_session()
    session.connect()
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported"):
            session.search("jefferson", scope=scope, field=field)
        assert calls == []
    finally:
        session.close()


def test_public_session_rejects_credentials_and_disconnected_search(monkeypatch):
    with pytest.raises(ProviderCapabilityError, match="credentials_forbidden"):
        new_public_session({"api_token": "PRIVATE_MARKER"})
    calls = []
    monkeypatch.setattr(public, "fetch_public_search", lambda *args, **kwargs: calls.append(True))
    session = new_public_session()
    with pytest.raises(ProviderError, match="non connecté"):
        session.search("jefferson")
    assert not calls


def test_core_manifest_parser_understands_two_provider_plugins():
    # The entrypoint and instantiation must be valid to be loadable by Core.
    from cifereca.plugins.manifest import parse_manifest
    manifest = json.loads(
        (Path(__file__).parents[1] / "cifereca-plugin.json").read_text()
    )
    parsed = parse_manifest(manifest)
    assert len(parsed.plugins) == 2
    catalogue = next(x for x in parsed.plugins if x.id == "provider.numilog.catalog")
    assert catalogue.instantiation == "singleton_public"
    assert catalogue.enabled_by_default is False
