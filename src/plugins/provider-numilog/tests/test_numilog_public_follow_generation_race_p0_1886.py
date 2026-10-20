"""#1886: reject a stale attested facet before any anonymous cache/GET.

A close/reconnect between the verified navigation token and HTTP lookup
cannot grant a prior session a fresh public navigation capability.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderError
from cifereca_numilog_public import parse_public_search
import cifereca_numilog_public_provider as provider


FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
PAGE = parse_public_search(FIXTURE.read_text(), SOURCE)


def _session(monkeypatch):
    monkeypatch.setattr(provider, "fetch_public_search", lambda *a, **k: PAGE)
    s = provider.PLUGIN.create_session(
        credential_id="__public__", label="Public", credentials={},
        options={}, config={}, storage=None, diagnostics=None,
    )
    s.connect()
    return s


class _InvalidateAfterTokenRead:
    """Inject a deterministic disconnect *after* the navigation lock exits."""

    def __init__(self, lock, session, reconnect):
        self.lock = lock
        self.session = session
        self.reconnect = reconnect
        self.fired = False

    def __enter__(self):
        return self.lock.__enter__()

    def __exit__(self, *args):
        result = self.lock.__exit__(*args)
        if not self.fired:
            self.fired = True
            self.session.close()
            if self.reconnect:
                self.session.connect()
        return result


@pytest.mark.parametrize("reconnect", [False, True])
def test_follow_refuses_invalidated_token_before_cache_or_http(
    monkeypatch, reconnect,
):
    s = _session(monkeypatch)
    attempted = []
    try:
        verified = s.browse_public_search("jefferson")
        assert verified.next_url is not None
        monkeypatch.setattr(
            s._public_cache, "get",
            lambda *_args, **_kwargs: attempted.append("stale-get"),
        )
        s._navigation_lock = _InvalidateAfterTokenRead(
            s._navigation_lock, s, reconnect,
        )
        with pytest.raises(ProviderError, match="navigation_invalidated") as exc:
            s.follow_public_link(verified.next_url)
        assert attempted == []
        assert "jefferson" not in str(exc.value)
        assert "https://" not in str(exc.value)
        assert not s._navigation_urls
    finally:
        s.close()


def test_follow_still_uses_verified_link_and_public_cache(monkeypatch):
    calls = []
    s = _session(monkeypatch)
    monkeypatch.setattr(
        provider, "fetch_public_search_url",
        lambda url: calls.append(url) or PAGE,
    )
    try:
        url = s.browse_public_search("jefferson").next_url
        assert s.follow_public_link(url).items
        assert s.follow_public_link(url).items
        assert calls == [url]
    finally:
        s.close()
