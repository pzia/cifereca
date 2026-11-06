"""#4530: Gallica OPDS/SRU descriptive labels are not edition-specific ages.

Synthetic XML only; no network, BnF login, loan or acquisition.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

from cifereca_gallica import GallicaSession

AGE_KEYS = frozenset((
    "recommended_age", "youth_age_ranges", "attested_age_ranges",
    "attested_age_status", "public_age_range_label",
))


def _session():
    session = object.__new__(GallicaSession)
    session.provider_id = "gallica"
    session.credential_id = "__public__"
    return session


def test_opds_ages_in_categories_do_not_become_attested_edition_ages():
    feed = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/elements/1.1/">
 <entry>
  <id>https://gallica.bnf.fr/ark:/12148/bpt6k111</id>
  <title>Meme titre</title>
  <category term="jeunesse" label="D\xc3\xa8s 8 ans"/>
  <link rel="http://opds-spec.org/acquisition/open-access"
        type="application/epub+zip"
        href="https://gallica.bnf.fr/ark:/12148/bpt6k111/epub"/>
 </entry>
 <entry>
  <id>https://gallica.bnf.fr/ark:/12148/bpt6k222</id>
  <title>Meme titre</title>
  <category term="young-adult" label="13-99"/>
  <link rel="http://opds-spec.org/acquisition/open-access"
        type="application/epub+zip"
        href="https://gallica.bnf.fr/ark:/12148/bpt6k222/epub"/>
 </entry>
</feed>"""
    result = _session()._parse_feed(feed, "https://gallica.bnf.fr/")
    assert len(result) == 2
    assert result[0].record_id != result[1].record_id
    assert result[0].metadata["subjects"] == ["Dès 8 ans"]
    assert result[1].metadata["subjects"] == ["13-99"]
    for item in result:
        assert not (AGE_KEYS & item.metadata.keys())
        assert item.metadata["format_evidence"] == "opds-acquisition"
        assert item.owned is not True


def test_sru_audio_ages_in_subjects_and_description_are_not_numeric():
    node = ET.fromstring("""<recordData xmlns:dc="http://purl.org/dc/elements/1.1/">
      <dc:identifier>ark:/12148/bpt6k333</dc:identifier>
      <dc:title>Album jeunesse</dc:title>
      <dc:type>sound recording</dc:type>
      <dc:subject>À partir de 13 ans</dc:subject>
      <dc:description>Conseillé dès 8 ans</dc:description>
      <dc:language>fr</dc:language>
    </recordData>""")
    rows = _session()._audio_rows_from_nodes([node])
    assert len(rows) == 1
    item = rows[0]
    assert item.record_id == "ark:/12148/bpt6k333"
    assert item.metadata["subjects"] == ["À partir de 13 ans"]
    assert not (AGE_KEYS & item.metadata.keys())
    assert item.metadata["metadata_evidence"] == "gallica.sru.dublin_core"
    assert item.format is None
