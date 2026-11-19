"""Selected Gallica OPDS languages carry complete entry-local provenance.

Controlled XML, no claim about current server responses or acquisition.
"""
import pytest

from cifereca_gallica import GallicaSession


def _entry(tags="", ark="bpt6k111"):
    return f"""<entry><id>https://gallica.bnf.fr/ark:/12148/{ark}</id>
      <title>Même titre</title>{tags}
      <link rel="http://opds-spec.org/acquisition/open-access"
            type="application/epub+zip"
            href="https://gallica.bnf.fr/ark:/12148/{ark}/epub"/>
    </entry>"""


def _parse(*entries):
    feed = ('<feed xmlns="http://www.w3.org/2005/Atom" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/">'
            + "".join(entries) + '</feed>').encode()
    session = object.__new__(GallicaSession)
    session.provider_id = "gallica"
    session.credential_id = "__public__"
    return session._parse_feed(feed, "https://gallica.bnf.fr/")


@pytest.mark.parametrize("namespace", ["dc", "dcterms"])
def test_language_set_and_original_tokens_belong_to_exact_opds_entry(namespace):
    row, = _parse(_entry(
        f'<{namespace}:language>fre</{namespace}:language>'
        f'<{namespace}:language>eng</{namespace}:language>'
        f'<{namespace}:language>fra</{namespace}:language>',
    ))
    assert row.metadata["languages"] == ["fr", "en"]
    assert row.metadata["bibliography_evidence"]["languages"] == {
        "raw_tags": ["fre", "eng", "fra"], "source": "gallica.opds",
        "source_field": namespace + ".language", "scope": "OPDS",
        "confidence": "explicit_record_field", "freshness": "search_response",
    }
    assert row.record_id == "ark:/12148/bpt6k111"
    assert row.downloadable and not row.owned


def test_both_namespaces_are_one_complete_declaration_not_a_subset():
    row, = _parse(_entry('<dc:language>fre</dc:language>'
                         '<dcterms:language>eng</dcterms:language>'))
    proof = row.metadata["bibliography_evidence"]["languages"]
    assert proof["source_field"] == "dcterms.language_and_dc.language"
    assert proof["raw_tags"] == ["eng", "fre"]
    assert row.metadata["languages"] == ["en", "fr"]


@pytest.mark.parametrize("tags", [
    "", '<dc:language/>',
    '<dc:language>fr</dc:language><dcterms:language>???</dcterms:language>',
    '<dcterms:language>fr</dcterms:language><dc:language/>',
    '<dc:language><x>fr</x></dc:language>',
    '<dc:language xml:lang="fr">fr</dc:language>',
    '<dc:language>fr</dc:language>' * 17,
    '<dc:language>' + 'a' * 129 + '</dc:language>',
])
def test_absent_invalid_or_over_budget_declaration_has_no_proof(tags):
    row, = _parse(_entry(tags))
    assert "bibliography_evidence" not in row.metadata


def test_same_title_entries_do_not_share_language_provenance():
    first, second = _parse(_entry(), _entry('<dc:language>eng</dc:language>', 'bpt6k222'))
    assert first.record_id != second.record_id
    assert first.metadata["languages"] == []
    assert "bibliography_evidence" not in first.metadata
    assert second.metadata["languages"] == ["en"]
    assert second.metadata["bibliography_evidence"]["languages"]["raw_tags"] == ["eng"]
