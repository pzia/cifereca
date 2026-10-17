"""Reduced public DOM excerpts from anonymous HTTP 200 responses on 2026-10-17.

One observed card per positive page; exact pager/total preserved. No summary,
ASP.NET state, reader links, scripts, credentials or private acquisitions.
"""
from pathlib import Path
import pytest
from cifereca_numilog_public import parse_public_search, PublicSearchParseError, GRID_ID
from cifereca_numilog_public_client import public_search_url

ROOT=Path(__file__).parent/'fixtures'
EMPTY_QUERY='zzciferecaxxyyqqnobook20261017'

def fixture(name):return (ROOT/('observed_search-'+name+'.html')).read_text()

def test_two_observed_page_excerpts_accept_implicit_zero_and_keep_next_page():
    first=parse_public_search(fixture('loup-0'),public_search_url('loup'))
    second=parse_public_search(fixture('loup-1'),public_search_url('loup',1))
    assert first.total_results==second.total_results==973
    assert first.page_index==0 and second.page_index==1
    assert len(first.items)==len(second.items)==1  # deliberate reduced excerpts
    assert first.items[0].record_id!=second.items[0].record_id
    assert first.next_url==public_search_url('loup',1)
    assert second.next_url==public_search_url('loup',2)
    back=[p for p in second.pagination_links if p['GRID_PAGE']=='0']
    assert back and all('GRID_PAGE' not in p['url'] for p in back)
    assert not first.complete and not second.complete
    for row in (*first.items,*second.items):
        assert row.owned is False and row.downloadable is False and row.download_url is None
        assert row.source_credential=='__public__'


def test_observed_empty_marker_proves_zero_without_numeric_counter():
    page=parse_public_search(fixture('empty'),public_search_url(EMPTY_QUERY))
    assert page.total_results==0 and not page.items and page.complete
    assert page.next_url is None and not page.pagination_links


@pytest.mark.parametrize('transform',[
    lambda s:s.replace('Aucun livre trouvé avec ces critères.','Service indisponible'),
    lambda s:s.replace('_ctl01_lblAucunLivre','_unknown'),
    lambda s:s.replace('alert-warning','hidden'),
    lambda s:s.replace('<span','<div').replace('</span>','</div>'),
    lambda s:s+s,
    lambda s:s.replace('</table>',s+'</table>'),
    lambda s:s.replace('</table>','</table><div class="pagination"></div>'),
])
def test_unknown_or_ambiguous_empty_pages_never_become_successful_zero(transform):
    with pytest.raises(PublicSearchParseError):
        parse_public_search(transform(fixture('empty')),public_search_url(EMPTY_QUERY))


def test_empty_marker_cannot_hide_results_or_contradict_nonzero_total():
    marker=fixture('empty').split('>',1)[1].rsplit('</table>',1)[0]
    for html in [fixture('loup-0').replace('</table>',marker+'</table>'),
                 '<span class="ResultatDetail">1</span>'+fixture('empty')]:
        with pytest.raises(PublicSearchParseError):
            parse_public_search(html,public_search_url('loup'))


@pytest.mark.parametrize('old,new',[
    ('query=loup','query=other'),
    ('query=loup','query=loup&amp;auth=PRIVATE'),
    ('mode=brSimple','mode=advanced'),
    ('href="/Pages/Recherche/','href="https://evil.invalid/Pages/Recherche/'),
])
def test_implicit_first_page_still_rejects_changed_query_or_origin(old,new):
    with pytest.raises(PublicSearchParseError) as exc:
        parse_public_search(fixture('loup-1').replace(old,new),public_search_url('loup',1))
    assert 'PRIVATE' not in str(exc.value)
