"""#4530: COPS OPDS categories and genres never attest exact-edition age.

All Atom payloads are synthetic, parsed offline. No network, fetch or borrow.
"""
from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from cifereca_ebooksgratuits import _opds_items

ATOM = "{http://www.w3.org/2005/Atom}"
FEED_URL = "https://opds2.ebooksgratuits.org/index.php/feed"
CANONICAL_AGE_FIELDS = frozenset((
    "recommended_age", "youth_age_ranges", "attested_age_ranges",
    "attested_age_status", "public_age_range_label",
))


class FakeSession:
    opds_feed_url = FEED_URL
    credential_id = "__public__"


def _feed():
    # Identical titles and contradictory age-like labels: neither an Atom
    # category nor its scheme certifies reader-age for a specific edition.
    xml = """<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry>
        <id>urn:uuid:child-edition</id>
        <title>Le même titre</title>
        <category term="Jeunesse" label="Dès 8 ans" />
        <category scheme="https://example.test/genres"
                  term="12-15" label="12-15" />
        <ageRange>8-12</ageRange>
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="application/epub+zip"
              href="/index.php/fetch/42.epub" />
      </entry>
      <entry>
        <id>urn:uuid:adult-edition</id>
        <title>Le même titre</title>
        <category term="Adultes" label="13-99" />
        <category scheme="https://example.test/genres"
                  term="Roman" label="Roman" />
        <link rel="http://opds-spec.org/acquisition/open-access"
              type="application/epub+zip"
              href="/index.php/fetch/43.epub" />
      </entry>
    </feed>"""
    return ET.fromstring(xml)


def test_same_title_opds_categories_are_scoped_to_their_own_entry():
    rows = _opds_items(FakeSession(), _feed(), FEED_URL)
    assert len(rows) == 2
    assert rows[0].title == rows[1].title == "Le même titre"
    assert rows[0].record_id != rows[1].record_id
    assert rows[0].metadata["categories"] == ["Dès 8 ans"]
    assert rows[0].metadata["genres"] == ["12-15"]
    assert rows[1].metadata["categories"] == ["13-99"]
    assert rows[1].metadata["genres"] == ["Roman"]
    assert all(not (CANONICAL_AGE_FIELDS & row.metadata.keys())
               for row in rows)
    assert all(row.owned is False for row in rows)


@pytest.mark.parametrize("value", ["3+", "À partir de 13 ans", "Jeunesse", "13-99"])
def test_age_looking_category_is_not_a_proven_numeric_age(value):
    feed = _feed()
    first = feed.findall(ATOM + "entry")[0]
    category = first.findall(ATOM + "category")[0]
    category.set("label", value)
    rows = _opds_items(FakeSession(), feed, FEED_URL)
    assert rows[0].metadata["categories"] == [value]
    assert not (CANONICAL_AGE_FIELDS & rows[0].metadata.keys())


def test_unknown_age_elements_are_not_promoted_or_inherited():
    feed = _feed()
    first = feed.findall(ATOM + "entry")[0]
    ET.SubElement(first, ATOM + "recommended_age").text = "13"
    ET.SubElement(first, ATOM + "attested_age_ranges").text = "13-15"
    rows = _opds_items(FakeSession(), feed, FEED_URL)
    assert len(rows) == 2
    assert all(not (CANONICAL_AGE_FIELDS & row.metadata.keys())
               for row in rows)
