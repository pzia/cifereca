"""A cache hit preserves real public observation time and generation."""
import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_public_cache import PublicCatalogueCache
from cifereca_numilog_public_provider import NumilogPublicSession
import cifereca_numilog_public_client as client
from tests.test_public_detail_transport_p0 import URL, AnonymousClient


def session(monkeypatch):
    mono = [100]
    wall = [1000]
    AnonymousClient.requests = []
    monkeypatch.setattr(client.HTTPX_TRANSPORT, "client", AnonymousClient)
    s = NumilogPublicSession(credential_id="__public__", label="",
                             credentials={}, options={})
    s._public_cache = PublicCatalogueCache(clock=lambda: mono[0],
                                           wall_clock=lambda: wall[0])
    s.connect()
    return s, mono, wall


def count():
    return sum(item[0] == "request" for item in AnonymousClient.requests)


def test_ordinary_detail_then_guarded_hit_keeps_actual_load_timestamp(monkeypatch):
    s, mono, wall = session(monkeypatch)
    try:
        raw = s._public_cache.get(("detail", URL), lambda: client.fetch_public_detail(URL))
        mono[0] += 20
        wall[0] += 20
        detail, observed, guard = s._guarded_purchase_detail(
            URL, "Fixture", check_authority=lambda: True,
        )
        assert detail == raw and observed == 1000 and count() == 1
        with guard():
            pass
        mono[0] += 41
        wall[0] += 41
        detail, observed, _ = s._guarded_purchase_detail(
            URL, "Fixture", check_authority=lambda: True,
        )
        assert observed == 1061 and count() == 2
    finally:
        s.close()


def test_guarded_load_is_shared_between_accounts_without_refreshing_timestamp(monkeypatch):
    s, mono, wall = session(monkeypatch)
    try:
        first, when, _ = s._guarded_purchase_detail(
            URL, "Fixture", check_authority=lambda: True,
        )
        first["fields"]["title"] = "caller mutation"
        mono[0] += 1
        wall[0] += 1
        second, later, _ = s._guarded_purchase_detail(
            URL, "fixture", check_authority=lambda: True,
        )
        assert second["fields"]["title"] == "Fixture"
        assert when == later == 1000 and count() == 1
    finally:
        s.close()


def test_reconnect_rejects_prior_publication_guard_even_on_cache_hit(monkeypatch):
    s, _, _ = session(monkeypatch)
    try:
        _, _, guard = s._guarded_purchase_detail(URL, "Fixture", check_authority=lambda: True)
        s.connect()
        with pytest.raises(ProviderError, match="purchase_publication_revoked"):
            with guard():
                pytest.fail("obsolete publication")
        assert count() == 1
    finally:
        s.close()


def test_guard_revocation_stops_transport_even_after_client_construction(monkeypatch):
    s, _, _ = session(monkeypatch)
    checks = []
    try:
        def guard():
            checks.append(1)
            return len(checks) < 3
        with pytest.raises(ProviderError, match="authority_revoked"):
            s._guarded_purchase_detail(URL, "Fixture", check_authority=guard)
        assert len(checks) == 3 and count() == 0
    finally:
        s.close()
