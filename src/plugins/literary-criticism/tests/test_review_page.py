from types import SimpleNamespace as NS
import httpx
import pytest
from cifereca.plugin_api import BookRef
from literary_criticism import normalize_article, normalize_isbn, match_article, canonical_url
from criticism_feeds import FeedSource, parse_feed, discover_url, fetch_public
from criticism_page import ReviewStore, CriticismPage, collect_snapshot, filtered_matches

RSS = b'''<rss version="2.0" xmlns:b="https://cifereca.org/ns/book/1" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><language>fr</language><item><title>Critique</title><link>/article</link><dc:creator>Critiqueur</dc:creator><description>&lt;p&gt;Court extrait&lt;/p&gt;</description><pubDate>Thu, 08 Oct 2026 12:00:00 GMT</pubDate><b:title>Livre</b:title><b:author>Autrice</b:author><dc:identifier>urn:isbn:9780306406157</dc:identifier></item></channel></rss>'''


def test_real_rss_source_contract_and_distinct_authors():
    source=FeedSource('https://reviews.test/rss',fetch=lambda url:(url,RSS))
    index,reports=collect_snapshot([source],NS(cancelled=lambda:False))
    article=index.articles()[0]
    assert article.critic_author == 'Critiqueur'
    assert article.book_authors == ('Autrice',)
    assert article.url == 'https://reviews.test/article'
    assert article.excerpt == 'Court extrait'
    assert reports[0][2]
    assert index.matches_for(BookRef('b','Livre',identifiers={'isbn':'9780306406157'}))[0].status == 'confirmed'


def test_atom_and_jsonfeed_do_not_copy_full_body():
    atom=b'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Article</title><link href="/article"/><author><name>Critic</name></author><content>FULL</content><summary>Excerpt</summary></entry></feed>'
    row=parse_feed(atom,'https://r.test/feed')[0]
    assert row['critic_author']=='Critic' and row['excerpt']=='Excerpt'
    assert not row['book']['authors']
    body=b'{"version":"https://jsonfeed.org/version/1.1","items":[{"title":"A","url":"https://r.test/a","content_html":"FULL","summary":"Short"}]}'
    assert parse_feed(body,'https://r.test/feed')[0]['excerpt']=='Short'


def test_discovery_previews_without_subscribing_and_network_is_explicit(tmp_path):
    fetch=lambda url:(url,b'<html><link rel="alternate" type="application/atom+xml" href="/feed"/></html>')
    candidates=discover_url('https://r.test',fetch=fetch)
    store=ReviewStore(tmp_path)
    assert not store.subscriptions
    store.subscribe(candidates[0].url)
    assert store.subscriptions == {'https://r.test/feed':False}
    assert not store.sources(NS(services=lambda *a,**k:()))


def test_cache_round_trip_and_filters(tmp_path):
    store=ReviewStore(tmp_path)
    store.index.ingest('feed.test',[{'title':'Article','url':'https://r.test/a','book_title':'Livre','book_author':'Autrice','language':'fr','published_at':'2026-10-08'}])
    store.save(); restored=ReviewStore(tmp_path)
    book=BookRef('b','Livre',('Autrice',))
    assert len(filtered_matches(restored.index,book,language='fr',since='2026-10-07'))==1
    assert not filtered_matches(restored.index,book,language='en')
    assert not filtered_matches(restored.index,book,since='bad date')
    assert not filtered_matches(restored.index,book,since='2026-10-09')


def test_book_action_only_reads_cache(tmp_path):
    context=NS(data_directory=tmp_path)
    plugin=CriticismPage().activate(context)
    book=BookRef('b','Livre');calls=[]
    plugin.pages()[0].actions[0].callback(NS(action_context=lambda:NS(book=book),navigate=calls.append))
    assert plugin.pending is book and calls==['literary-criticism']


def test_sources_fail_independently_and_pagination_is_bounded():
    class Failing:
        def source_info(self):return {'source_id':'failure','display_name':'Bad'}
        def discover(self,*a,**k):return ()
        def collect(self,**kw):raise RuntimeError('private transport message')
    good=FeedSource('https://r.test/rss',fetch=lambda url:(url,RSS))
    index,reports=collect_snapshot([Failing(),good],NS(cancelled=lambda:False))
    assert len(index.articles())==1
    assert reports[0][1]=='Collecte incomplète'
    assert 'private' not in str(reports)


def test_isbn_equivalence_and_conflicting_editions():
    assert normalize_isbn('0-306-40615-2') == '9780306406157'
    assert not normalize_isbn('garbage9780306406157')
    article=normalize_article({'title':'Review','url':'https://r.test/a','book_title':'Livre','book_author':'A','isbn':'9780306406157'},source_id='feed')
    book=BookRef('b','Livre',('A',),identifiers={'isbn':'9780140328721'})
    assert match_article(article,book).status=='suggested'
    assert 'different-edition' in match_article(article,book).reasons
    equivalent=BookRef('b','Livre',identifiers={'isbn':'0306406152'})
    assert match_article(article,equivalent).score==100


def test_excerpt_bounded_plain_and_url_safe():
    article=normalize_article({'title':'A','url':'https://r.test/a','excerpt':'<script>bad()</script><b>'+('a'*2000)+'</b>'},source_id='feed')
    assert len(article.excerpt)==600 and 'bad' not in article.excerpt
    assert canonical_url('https://[::1]:443/a#x')=='https://[::1]/a'
    assert not canonical_url('https://user:secret@r.test/a')


def test_http_redirect_and_size_limit(monkeypatch):
    def handler(request):
        if request.url.path=='/start':return httpx.Response(302,headers={'Location':'/feed'})
        return httpx.Response(200,content=RSS)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        final,body=fetch_public('https://r.test/start',client=client)
        assert final=='https://r.test/feed' and body==RSS
        monkeypatch.setattr('criticism_feeds.MAX_BYTES',10)
        with pytest.raises(ValueError):fetch_public('https://r.test/feed',client=client)


def test_xml_entities_rejected():
    with pytest.raises(ValueError):
        parse_feed(b'<!DOCTYPE rss [<!ENTITY x "huge">]><rss/>','https://r.test/rss')
