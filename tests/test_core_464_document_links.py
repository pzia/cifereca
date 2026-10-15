from __future__ import annotations
from tests.gui_source import gui_source_text

from pathlib import Path

from cifereca.plugin_api import DocumentLink, UiBookDetailPresentation, document_links


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / 'src/cifereca/gui/main_window.py'


def test_document_link_is_part_of_public_detail_contract():
    link = DocumentLink('notice', 'Ouvrir la notice', 'https://catalogue.example/book')
    detail = UiBookDetailPresentation('Livre', links=(link,))
    assert detail.links == (link,)
    assert link.relation == 'notice'


def test_friendly_url_becomes_notice_link():
    links = document_links({'friendly_url': 'https://catalogue.bnf.fr/ark:/12148/cb123'})
    assert len(links) == 1
    assert links[0].id == 'notice'
    assert links[0].label == 'Ouvrir la notice'
    assert links[0].url == 'https://catalogue.bnf.fr/ark:/12148/cb123'


def test_explicit_gallica_document_link_gets_standard_label():
    links = document_links({
        'friendly_url': 'https://catalogue.bnf.fr/ark:/12148/cb123',
        'metadata': {'document_links': [
            {'relation': 'gallica', 'url': 'https://gallica.bnf.fr/ark:/12148/bpt6k123'},
        ]},
    })
    assert [link.label for link in links] == ['Ouvrir la notice', 'Ouvrir dans Gallica']
    assert links[1].relation == 'gallica'


def test_document_links_are_deduplicated_by_safe_url():
    url = 'https://catalogue.bnf.fr/ark:/12148/cb123'
    links = document_links({
        'friendly_url': url,
        'metadata': {'document_links': [
            {'relation': 'notice', 'label': 'Autre libellé', 'url': url},
        ]},
    })
    assert len(links) == 1


def test_delivery_signed_and_file_urls_are_never_document_links():
    links = document_links({
        'download_url': 'https://cdn.example/book.epub',
        'metadata': {'document_links': [
            {'relation': 'related', 'label': 'EPUB', 'url': 'https://cdn.example/book.epub'},
            {'relation': 'related', 'label': 'Signé', 'url': 'https://example.test/page?token=secret'},
            {'relation': 'related', 'label': 'ACSM', 'url': 'https://example.test/fulfillment/book.acsm'},
        ]},
    })
    assert links == ()


def test_generic_relation_requires_explicit_label():
    links = document_links({
        'metadata': {'document_links': [
            {'relation': 'related', 'url': 'https://example.test/info'},
            {'relation': 'related', 'label': 'Documentation', 'url': 'https://example.test/docs'},
        ]},
    })
    assert len(links) == 1
    assert links[0].label == 'Documentation'


def test_shared_detail_renderer_uses_external_link_buttons_and_dedupes_catalogue_action():
    source = gui_source_text(MAIN)
    assert 'for link in presentation.links:' in source
    assert 'Gtk.LinkButton(uri=uri, label=str(link.label))' in source
    assert 'self._rendered_document_urls.add(uri)' in source
    action_source = gui_source_text(MAIN.with_name("public_catalogue_action_controller.py"))
    assert 'url in getattr(detail, "_rendered_document_urls", set())' in action_source
    assert 'links=document_links(result_row(item))' in source
