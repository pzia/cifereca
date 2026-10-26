"""429 delay propagation through anonymous HTTP and the monotonic budget."""
from email.utils import formatdate
import httpx
import pytest
from cifereca.plugin_api import ProviderError
import cifereca_numilog_public_client as public
from cifereca_numilog_public_cache import PublicCatalogueCache

URL = 'https://www.numilog.com/1958476/jefferson-prend-des-risques.ebook'


@pytest.mark.parametrize('value,expected', [('120',120),(' 180 ',180),('0',0),
    ('-1',0),('1.5',0),('nan',0),('inf',0),('١٢٠',0),('',0),(None,0),
    ('PRIVATE ?token=PRIVATE',0),('1'*129,0)])
def test_retry_after_parser_is_bounded_and_rejects_non_http_values(value, expected):
    assert public._retry_after_seconds(value, now=1000) == expected


def test_retry_after_http_dates_use_wall_clock_once_then_monotonic_deadline():
    assert public._retry_after_seconds(formatdate(1180,usegmt=True), now=1000) == 180
    assert public._retry_after_seconds(formatdate(900,usegmt=True), now=1000) == 0
    assert public._retry_after_seconds('Thu, 01 Jan 1970 00:19:40', now=1000) == 0


@pytest.mark.parametrize('surface', ['search','detail'])
@pytest.mark.parametrize('header', ['180','http-date','PRIVATE ?auth=PRIVATE',None])
def test_429_blocks_new_http_until_requested_deadline_even_after_clear(monkeypatch, surface, header):
    now=[0.0];cache=PublicCatalogueCache(clock=lambda:now[0]);requests=[]
    if header == 'http-date':
        header=formatdate(1180,usegmt=True)
    monkeypatch.setattr(public.time,'time',lambda:1000)
    original_client=public.HTTPX_TRANSPORT.client
    def respond(request):
        requests.append(request)
        assert 'cookie' not in request.headers and 'authorization' not in request.headers
        return httpx.Response(429, headers={} if header is None else {'Retry-After':header},
                              content=b'PRIVATE upstream body')
    def client(**kwargs):return original_client(**kwargs,transport=httpx.MockTransport(respond))
    monkeypatch.setattr(public.HTTPX_TRANSPORT,'client',client)
    fetch=(lambda:public.fetch_public_search('jefferson')) if surface == 'search' else (lambda:public.fetch_public_detail(URL))
    cache.get('existing',lambda:{'public':True})
    with pytest.raises(public.PublicRateLimitError) as caught:cache.get('limited',fetch)
    assert 'PRIVATE' not in str(caught.value) and 'PRIVATE' not in repr(caught.value.__dict__)
    assert len(requests)==1
    assert cache.get('existing',lambda:pytest.fail('valid cache must avoid GET'))=={'public':True}
    cache.clear();now[0]=60
    delay=180 if header in ('180',formatdate(1180,usegmt=True)) else 60
    if delay>60:
        with pytest.raises(ProviderError,match='rate_limited'):cache.get('again',fetch)
        assert len(requests)==1
    now[0]=delay
    assert cache.get('fresh',lambda:{'fresh':True})=={'fresh':True}
    assert len(requests)==1


@pytest.mark.parametrize('delay',[float('nan'),float('inf'),-3,'900',True,None])
def test_untrusted_exception_delay_keeps_minimum_pause(delay):
    now=[0.0];cache=PublicCatalogueCache(clock=lambda:now[0])
    def fail():
        error=ProviderError('Numilog : public_catalog_rate_limited')
        error.retry_after_seconds=delay
        raise error
    with pytest.raises(ProviderError):cache.get('limited',fail)
    now[0]=59
    with pytest.raises(ProviderError):cache.get('blocked',lambda:pytest.fail('must pause'))
    now[0]=60
    assert cache.get('fresh',lambda:True) is True
