"""Observed product labels and conservative commercial-edition provenance."""
from pathlib import Path
import pytest
from cifereca_numilog_public_metadata import (
    extract_public_metadata, validated_public_metadata, PublicMetadataError,
)
from cifereca_numilog_public_detail import parse_public_detail_jsonld, PublicDetailError

ROOT = Path(__file__).parent / 'fixtures'
URL = 'https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook'
TITLE = 'Blackbone - Coltan song- Tome 1 - Dès 15 ans'
PREFIX = 'ctl02_CentrePage_FicheDetaille1_'


def fixture(name='blackbone'):
    return (ROOT / ('observed_metadata_' + name + '.html')).read_text()


def test_observed_labels_have_exact_selectors_and_no_digital_or_rights_inference():
    data = parse_public_detail_jsonld(fixture(), URL)
    meta = data['public_html_metadata']
    assert meta['fields'] == {
        'brand': 'NATHAN', 'publication_date': '2020-04-16',
        'pagination_label': '228 pages', 'language_label': 'Français',
        'selected_format_label': 'epub sans DRM',
    }
    assert 'collection' not in meta['fields']  # Upstream says n.c, never a collection.
    assert all(p['edition_proven'] is False and p['rights_proven'] is False
               and p['source_url'] == URL for p in meta['field_evidence'].values())
    assert validated_public_metadata(data, URL) == meta
    assert 'publisher' not in data['fields']  # Marque is retained as brand.
    assert data['public_identifiers'] == {'paper':'9782092591086', 'epub':'9782092591093'}
    assert not data['owned'] and not data['downloadable'] and data['download_url'] is None


def test_second_observed_page_retains_distinct_values_but_conflicting_eans_still_fail():
    url = 'https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook'
    # Read the exact observed h1 rather than assuming typographic normalization.
    from cifereca_numilog_html import Parser
    title = next(n.text() for n in Parser(fixture('sainte-soline')).root.walk() if n.tag == 'h1')
    data = extract_public_metadata(fixture('sainte-soline'), url, title)
    assert data['fields']['brand'] == 'Massot éditions'
    assert data['fields']['pagination_label'] == '146 pages'
    assert data['fields']['publication_date'] == '2026-10-01'
    with pytest.raises(PublicDetailError, match='public_identifier_evidence_unverified'):
        parse_public_detail_jsonld(fixture('sainte-soline'), url)


@pytest.mark.parametrize('change', ['title', 'form', 'hidden', 'foreign_form', 'footer'])
def test_other_product_or_nonvisible_context_does_not_supply_labels(change):
    html = fixture()
    if change == 'title':
        data = extract_public_metadata(html, URL, 'Another product')
        assert data['fields'] == {}
        return
    if change == 'form':
        html = html.replace('action="/1171334/', 'action="/999/')
    else:
        start = '<div id="ctl02_CentrePage_dvFicheDetail"'
        if change == 'hidden':html = html.replace(start, '<div hidden id="ctl02_CentrePage_dvFicheDetail"')
        if change == 'foreign_form':html = html.replace(start, '<form action="/login">'+start).replace('</form></body>', '</form></form></body>')
        if change == 'footer':html = html.replace(start, '<footer>'+start).replace('</form></body>', '</footer></form></body>')
    data = extract_public_metadata(html, URL, TITLE)
    assert 'brand' not in data['fields'] and 'language_label' not in data['fields']


@pytest.mark.parametrize('fragment', [
    '<span id="'+PREFIX+'lblLanguage">Foreign language</span>',
    '<span id="'+PREFIX+'lblLanguage" id="another">PRIVATE</span>',
])
def test_duplicate_evidence_does_not_choose_a_label(fragment):
    html = fixture().replace('<div class="titledetailDRM"', fragment+'<div class="titledetailDRM"')
    with pytest.raises(PublicMetadataError):extract_public_metadata(html, URL, TITLE)


@pytest.mark.parametrize('value', ['2020-02-30', 'tomorrow', '2020-04-16T00:00:00'])
def test_invalid_publication_date_is_not_projected(value):
    html = fixture().replace('>2020-04-16<', '>'+value+'<')
    with pytest.raises(PublicMetadataError):extract_public_metadata(html, URL, TITLE)


def test_missing_optional_layout_does_not_invent_fields():
    assert extract_public_metadata('<h1>Product</h1>', URL, TITLE)['fields'] == {}


@pytest.mark.parametrize('mutation', ['url', 'selector', 'rights', 'unknown', 'value'])
def test_forged_or_unbounded_provenance_is_refused(mutation):
    detail = {'public_html_metadata': extract_public_metadata(fixture(), URL, TITLE)}
    data = detail['public_html_metadata']
    if mutation == 'url':data['product_url'] = URL+'?auth=PRIVATE'
    if mutation == 'selector':data['field_evidence']['brand']['selector_id'] = 'footer'
    if mutation == 'rights':data['field_evidence']['brand']['rights_proven'] = True
    if mutation == 'unknown':data['fields']['download_url'] = 'PRIVATE'
    if mutation == 'value':data['fields']['brand'] = 'x'*257
    with pytest.raises(PublicMetadataError) as exc:validated_public_metadata(detail, URL)
    assert 'PRIVATE' not in str(exc.value)


@pytest.mark.parametrize('ancestor', ['<footer>', '<div hidden>', '<form action="/login">'])
def test_canonical_wrapper_inside_ignored_ancestor_is_not_product_evidence(ancestor):
    html = fixture().replace('<body>', '<body>'+ancestor)
    assert extract_public_metadata(html, URL, TITLE)['fields'] == {}


def test_format_label_inside_product_button_is_text_evidence_only():
    html = fixture().replace('<span id="ctl02_CentrePage_MiseEnAvantLivre_BuyWithFormatSelect',
                             '<button><span id="ctl02_CentrePage_MiseEnAvantLivre_BuyWithFormatSelect')
    html = html.replace('>epub sans DRM</span>', '>epub sans DRM</span></button>')
    assert extract_public_metadata(html, URL, TITLE)['fields']['selected_format_label'] == 'epub sans DRM'


def test_irrelevant_malformed_navigation_does_not_reject_otherwise_verified_product():
    html = fixture().replace('<body>', '<body><a style="x" style="y">Navigation</a>')
    assert extract_public_metadata(html, URL, TITLE) == extract_public_metadata(fixture(), URL, TITLE)


def test_public_and_owned_merges_preserve_labels_without_changing_artifact_or_rights():
    from dataclasses import replace
    from cifereca_numilog_public import parse_public_search
    from cifereca_numilog_public_detail import merge_public_detail
    from cifereca_numilog_enrichment import merge_attested_owned_public_detail
    source = 'https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx?mode=brSimple&query=loup'
    card = parse_public_search((ROOT/'observed_search-loup-0.html').read_text(), source).items[0]
    public = replace(card, title=TITLE, record_id='numilog:public:1171334',
                     friendly_url=URL, source_provider='numilog-catalog',
                     metadata={'source_url':source, 'source_item_id':'1171334'})
    detail = parse_public_detail_jsonld(fixture(), URL)
    merged = merge_public_detail(public, detail)
    assert merged.metadata['public_detail']['public_html_metadata'] == detail['public_html_metadata']
    assert merged.format == public.format and merged.owned is False and merged.download_url is None
    owned = replace(public, record_id='private-purchase', source_provider='numilog',
                    source_credential='alice', source_collection='library', owned=True, downloadable=True,
                    metadata={'public_url_evidence':'numilog.library_title_link',
                              'public_enrichment':{'status':'attested_listing_match',
                               'scope':'public_listing_only', 'product_url':URL,
                               'source_url':source, 'field_evidence':{}}})
    result = merge_attested_owned_public_detail(owned, detail)
    assert result.metadata['public_detail_enrichment']['public_html_metadata'] == detail['public_html_metadata']
    assert merged.metadata['public_product_metadata'] == detail['public_html_metadata']['fields']
    assert result.metadata['public_product_metadata'] == detail['public_html_metadata']['fields']
    assert 'public_product_metadata' not in owned.metadata
    assert result.source_credential == 'alice' and result.record_id == 'private-purchase'
    assert result.owned is True and result.downloadable is True and result.download_url is None
    assert result.format == owned.format
    detail['public_html_metadata']['field_evidence']['brand']['rights_proven'] = True
    from cifereca.plugin_api import ProviderCapabilityError
    with pytest.raises(ProviderCapabilityError):merge_public_detail(public, detail)
    with pytest.raises(ProviderCapabilityError):merge_attested_owned_public_detail(owned, detail)
