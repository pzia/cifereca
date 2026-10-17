"""Anonymous upstream excerpts fetched 2026-10-17, no ASP.NET state or tracking."""
from pathlib import Path
import json
import re
import pytest
from cifereca_numilog_public_detail import parse_public_detail_jsonld, PublicDetailError
from cifereca_numilog_public_identifiers import extract_public_identifiers, PublicIdentifierError

ROOT=Path(__file__).parent/'fixtures'
URL='https://www.numilog.com/1171334/blackbone--coltan-song-tome-1--des-15-ans.ebook'
OTHER='https://www.numilog.com/1956345/le-piege-de-sainte-soline--megabassines--la-lutte-pour-l-eau.ebook'

def fixture(name='blackbone'):
    return (ROOT/('observed_public_'+name+'.html')).read_text()

def mutate_node(html, **updates):
    match=re.search(r'<script type="application/ld\+json">(.*?)</script>',html,re.S)
    node=json.loads(match[1]);node.update(updates)
    return html[:match.start(1)]+json.dumps(node)+html[match.end(1):]

def test_observed_product_book_and_webform_preserve_typed_public_evidence():
    data=parse_public_detail_jsonld(fixture(),URL)
    assert data['fields']['title']=='Blackbone - Coltan song- Tome 1 - Dès 15 ans'
    assert len(data['fields']['authors'])==4
    assert data['public_identifiers']=={'paper':'9782092591086','epub':'9782092591093'}
    assert all(e['source']=='public_product_jsonld_Product_Book' for e in data['field_evidence'].values())
    assert all(e['owned_format_proven'] is False for e in data['identifier_evidence'].values())
    assert data['owned'] is False and data['downloadable'] is False and data['download_url'] is None
    assert 'isbn_digital' not in data['fields'] and 'format' not in data['fields']
    assert '__VIEWSTATE' not in fixture() and '__EVENTVALIDATION' not in fixture()

@pytest.mark.parametrize('updates',[
    {'additionalType':None},{'additionalType':'MusicAlbum'},{'additionalType':['Book']},
    {'additionalType':{'private':'PRIVATE'}},{'sku':'1171335'},{'sku':1171334},
    {'url':OTHER},{'@type':'ProductGroup'},
])
def test_other_product_type_sku_or_canonical_never_supplies_book_evidence(updates):
    with pytest.raises(PublicDetailError) as exc:
        parse_public_detail_jsonld(mutate_node(fixture(),**updates),URL)
    assert exc.value.code=='missing_or_ambiguous_book' and 'PRIVATE' not in str(exc.value)

def test_duplicate_matching_product_nodes_remain_ambiguous():
    html=fixture();script=re.search(r'<script.*?</script>',html,re.S)[0]
    with pytest.raises(PublicDetailError) as exc:
        parse_public_detail_jsonld(html.replace('</head>',script+'</head>'),URL)
    assert exc.value.code=='missing_or_ambiguous_book'

def test_real_second_page_conflicts_do_not_choose_a_preferred_edition():
    with pytest.raises(PublicIdentifierError) as exc:
        extract_public_identifiers(fixture('sainte-soline'),OTHER)
    assert exc.value.code=='conflicting_identifier'
    with pytest.raises(PublicDetailError) as exc:
        parse_public_detail_jsonld(fixture('sainte-soline'),OTHER)
    assert exc.value.code=='public_identifier_evidence_unverified'

@pytest.mark.parametrize('old,new',[
    ('id="aspnetForm"','id="loginForm"'),('name="aspnetForm"','name="anotherForm"'),
    ('method="post"','method="get"'),('action="/1171334/','action="/1171335/'),
    ('id="aspnetForm"','id="aspnetForm" id="other"'),
])
def test_other_form_wrappers_never_supply_product_identifiers(old,new):
    assert extract_public_identifiers(fixture().replace(old,new),URL)['identifiers']=={}

def test_nested_login_form_and_foreign_identifiers_are_ignored():
    html=fixture().replace('<h2>Détails</h2>',
        '<form action="/Login.aspx"><p>EAN papier</p><p>9782380354621</p></form><h2>Détails</h2>')
    assert extract_public_identifiers(html,URL)['identifiers']['paper']=='9782092591086'

def test_duplicate_canonical_webform_is_rejected_instead_of_splicing_products():
    html=fixture();form=re.search(r'<form[^>]*>',html)[0]
    with pytest.raises(PublicIdentifierError) as exc:
        extract_public_identifiers(html.replace('</body>',form+'</form></body>'),URL)
    assert exc.value.code=='ambiguous_product_form'
