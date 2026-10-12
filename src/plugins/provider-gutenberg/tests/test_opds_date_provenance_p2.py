"""P2 #764 — OPDS Atom chronology must not masquerade as book publication.

Fixtures are deliberately synthetic OPDS snippets; the Gutenberg public page
for ebook 11 separately shows release 2008-06-27 and last update 2025-06-26.
No XML feed bytes are claimed captured from the live upstream here.
"""
from __future__ import annotations

import pytest

from cifereca_gutenberg import (
    GutenbergSession, _attested_date, _attested_atom_timestamp,
)


def _feed(*, issued=None, atom_published=None, atom_updated=None, ebook="11"):
    issued_tag = f"<dc:issued>{issued}</dc:issued>" if issued is not None else ""
    publish_tag = f"<published>{atom_published}</published>" if atom_published else ""
    update_tag = f"<updated>{atom_updated}</updated>" if atom_updated else ""
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom"
          xmlns:dc="http://purl.org/dc/terms/">
      <entry>
        <id>https://www.gutenberg.org/ebooks/{ebook}</id>
        <title>Alice</title>
        {issued_tag}{publish_tag}{update_tag}
        <link rel="http://opds-spec.org/acquisition"
              type="application/epub+zip" href="/ebooks/{ebook}.epub"/>
      </entry>
    </feed>"""
    return xml.encode("utf-8")


def _session():
    return GutenbergSession(
        credential_id="public", label="", credentials={},
        options={"contact": "tests@example.test"},
        config={}, diagnostics=None,
    )


def _row(**kwargs):
    session = _session()
    try:
        result = session._parse_feed(
            _feed(**kwargs),
            "https://www.gutenberg.org/ebooks/search.opds/",
        )
        assert len(result) == 1
        return result[0]
    finally:
        session.close()


def test_opds_entry_update_is_never_book_publication():
    row = _row(atom_published="2008-06-27T12:00:00Z",
               atom_updated="2025-06-26T23:59:59+00:00")
    assert row.published_at is None
    assert row.metadata["opds_entry_published"] == "2008-06-27T12:00:00Z"
    assert row.metadata["opds_entry_updated"] == "2025-06-26T23:59:59+00:00"
    assert "publication_date_evidence" not in row.metadata
    assert row.record_id == "11" and row.downloadable is True
    # Never infer original publication (1865) or reading age from feed dates.
    assert "1865" not in repr(row.metadata)
    assert "age" not in row.metadata


def test_dublin_core_issued_supplies_only_attested_bibliographic_date():
    row = _row(
        issued="2008-06-27",
        atom_published="2008-06-27T12:00:00Z",
        atom_updated="2025-06-26T23:59:59Z",
    )
    assert row.published_at == "2008-06-27"
    assert row.metadata["publication_date_evidence"] == "opds.dcterms.issued"
    assert row.metadata["opds_entry_updated"] == "2025-06-26T23:59:59Z"


@pytest.mark.parametrize("issued, expected", [
    ("1865", "1865"),
    ("2008-06", "2008-06"),
    ("2008-06-27", "2008-06-27"),
    ("2008-06-99", None),
    ("2008-13-04", None),
    ("2008-00", None),
    ("2008-02-30", None),
    ("X", None),
    ("1865; reprint", None),
    ("2025-10-11T10:00:00Z", None),  # publication timestamp ≠ bare issued date
    ("", None),
    ("2" * 500, None),
])
def test_only_valid_precision_preserving_issued_dates(issued, expected):
    assert _attested_date(issued) == expected
    row = _row(issued=issued)
    assert row.published_at == expected


@pytest.mark.parametrize("value,expected", [
    ("2025-06-26T10:45:00Z", True),
    ("2025-06-26T10:45:00.123456+02:00", True),
    ("2025-06-26", True),
    ("2025-99-99T10:45:00Z", False),
    ("2025-02-30T10:45:00Z", False),
    ("2025-06-26T10:45:00", False),
    ("bad-token@secret.example", False),
    ("2025-06-26T10:45:00Z&access_token=SECRET", False),
])
def test_atom_chronology_must_be_valid_iso_with_timezone(value, expected):
    assert bool(_attested_atom_timestamp(value)) == expected


def test_synthetic_feed_missing_dates_keeps_bibliography_unclaimed():
    row = _row()
    assert row.published_at is None
    assert "publication_date_evidence" not in row.metadata
    assert "opds_entry_published" not in row.metadata
    assert "opds_entry_updated" not in row.metadata


def test_opds_bibliographic_date_is_visible_in_detail_only_when_attested():
    session = _session()
    try:
        row = session._parse_feed(
            _feed(issued="2008-06-27", atom_updated="2025-06-26T09:00:00Z"),
            "https://www.gutenberg.org/ebooks/search.opds/",
        )[0]
        assert session.holdings(row).year == "2008-06-27"
        row_no_issued = session._parse_feed(
            _feed(atom_updated="2025-06-26T09:00:00Z", ebook="12"),
            "https://www.gutenberg.org/ebooks/search.opds/",
        )[0]
        assert session.holdings(row_no_issued).year is None
    finally:
        session.close()
