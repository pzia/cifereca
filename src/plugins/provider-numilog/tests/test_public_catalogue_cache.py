"""Deterministic time/concurrency contracts, no HTTP or account session."""
from concurrent.futures import ThreadPoolExecutor
import threading
import pytest
from cifereca.plugin_api import ProviderError
from cifereca_numilog_public_cache import PublicCatalogueCache


def rig():
    now = [0.0]
    return PublicCatalogueCache(clock=lambda:now[0]), now


def test_hit_is_defensive_and_expiry_fetches_fresh_instead_of_stale():
    cache, now = rig();calls=[]
    original={'fields':{'title':'Public'}}
    def load():calls.append(True);return original
    first=cache.get('page',load);first['fields']['title']='changed'
    second=cache.get('page',load)
    assert second=={'fields':{'title':'Public'}} and len(calls)==1
    second['fields']['title']='changed-again'
    assert cache.get('page',load)['fields']['title']=='Public'
    now[0]=60
    assert cache.get('page',lambda:{'fields':{'title':'Fresh'}})['fields']['title']=='Fresh'


def test_two_request_burst_then_budget_error_never_calls_loader_or_returns_zero():
    cache, now=rig();calls=[]
    for key in ('search','detail'):cache.get(key,lambda:calls.append(True) or {'public':True})
    with pytest.raises(ProviderError,match='budget_exhausted'):
        cache.get('another',lambda:calls.append(True))
    assert len(calls)==2
    now[0]=1
    cache.get('another',lambda:calls.append(True) or {})
    assert len(calls)==3


@pytest.mark.parametrize('code',['public_catalog_rate_limited','public_detail_rate_limited'])
def test_429_pauses_new_gets_across_clear_but_not_valid_cached_reads(code):
    cache, now=rig()
    cache.get('existing',lambda:{'public':True})
    def limited():raise ProviderError('Numilog : '+code)
    with pytest.raises(ProviderError):cache.get('limited',limited)
    assert cache.get('existing',lambda:pytest.fail('valid cache uses no HTTP'))=={'public':True}
    cache.clear();now[0]=59
    with pytest.raises(ProviderError,match='rate_limited'):
        cache.get('new',lambda:pytest.fail('pause must not issue GET'))
    now[0]=60
    assert cache.get('new',lambda:{'fresh':True})=={'fresh':True}


def test_other_errors_are_not_cached_as_success_or_empty():
    cache, now=rig()
    def forbidden():raise ProviderError('Numilog : public_catalog_forbidden')
    with pytest.raises(ProviderError,match='forbidden'):cache.get('x',forbidden)
    assert cache.get('x',lambda:{'fresh':True})=={'fresh':True}


def test_lru_is_bounded_and_does_not_cross_sessions():
    cache, now=rig()
    for i in range(17):
        now[0]+=1;cache.get(str(i),lambda:{'public':True})
    assert len(cache._entries)==16 and '0' not in cache._entries
    other=PublicCatalogueCache(clock=lambda:now[0])
    assert other.get('16',lambda:{'separate':True})=={'separate':True}


def test_clear_during_inflight_is_nonblocking_and_late_response_never_repopulates():
    cache, now=rig();entered=threading.Event();finish=threading.Event()
    def load():
        entered.set();assert finish.wait(2);return {'late':True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task=pool.submit(cache.get,'page',load)
        assert entered.wait(2)
        cache.clear()  # Must not wait for load or hold the request mutex.
        with pytest.raises(ProviderError,match='in_progress'):
            cache.get('other',lambda:pytest.fail('no duplicate in-flight GET'))
        finish.set()
        with pytest.raises(ProviderError, match='public_catalog_request_invalidated'):
            task.result(timeout=2)
    assert not cache._entries
    assert cache.get('page',lambda:{'fresh':True})=={'fresh':True}


def test_public_provider_reuses_only_own_cache_and_clear_invalidates(monkeypatch):
    from pathlib import Path
    import json
    from cifereca_numilog_public import parse_public_search
    import cifereca_numilog_public_provider as provider
    root=Path(__file__).parent/'fixtures'
    expected=json.loads((root/'public_search_jefferson_20261016.expected.json').read_text())
    page=parse_public_search((root/'public_search_jefferson_20261016.html').read_text(),expected['source_url'])
    calls=[]
    monkeypatch.setattr(provider,'fetch_public_search',lambda *_a,**kw:calls.append(kw) or page)
    def session():
        s=provider.NumilogPublicSession(credential_id='__public__',label='Public',credentials={},options={})
        s.connect();return s
    s=session();first=s.search('jefferson');first[0].metadata['altered']='PRIVATE'
    second=s.search('jefferson')
    assert len(calls)==1 and 'altered' not in second[0].metadata
    assert all(x.owned is False and x.downloadable is False and x.download_url is None for x in second)
    s.close();s.connect();s.search('jefferson')
    assert len(calls)==2
    other=session();other.search('jefferson');assert len(calls)==3



@pytest.mark.parametrize("transition", ("close", "reconnect"))
def test_public_search_started_before_session_transition_cannot_return_old_rows(
    monkeypatch, transition,
):
    """The response from an old session epoch must not reach Search/Core UI."""
    import json
    from pathlib import Path
    from cifereca_numilog_public import parse_public_search
    import cifereca_numilog_public_provider as provider

    root = Path(__file__).parent / "fixtures"
    expected = json.loads(
        (root / "public_search_jefferson_20261016.expected.json").read_text()
    )
    page = parse_public_search(
        (root / "public_search_jefferson_20261016.html").read_text(),
        expected["source_url"],
    )
    entered, release = threading.Event(), threading.Event()
    calls = []

    def fetch(*_args, **_kwargs):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        return page

    monkeypatch.setattr(provider, "fetch_public_search", fetch)
    session = provider.NumilogPublicSession(
        credential_id="__public__", label="Public", credentials={}, options={},
    )
    session.connect()
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            stale = pool.submit(session.search, "jefferson")
            assert entered.wait(3)
            if transition == "close":
                session.close()
            else:
                session.connect()
            release.set()
            with pytest.raises(ProviderError, match="public_catalog_request_invalidated"):
                stale.result(timeout=3)

        assert not session._public_cache._entries
        if transition == "close":
            with pytest.raises(ProviderError, match="non connecté"):
                session.search("jefferson")
            session.connect()
        fresh = session.search("jefferson")
        assert len(fresh) == 20
        assert all(r.owned is False and r.downloadable is False for r in fresh)
        assert len(calls) == 2  # new session, new explicit search
    finally:
        release.set()
        session.close()
