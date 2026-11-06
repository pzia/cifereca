"""#4530 — audited public Numilog sample, not a catalogue coverage estimate.

The fixture records four publicly visible product pages verified on
2026-11-06. It contains only selected bibliographic facts, not downloaded
HTML, credentials, purchases, API/JSON-LD responses or private account data.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit

from cifereca_numilog_public_detail import _isbn

FIXTURE = Path(__file__).parent / "fixtures" / "public_age_sample_20261106.json"

CANONICAL_AGE_KEYS = {
    "recommended_age", "youth_age_ranges", "attested_age_ranges",
    "attested_age_status", "age_min", "age_max",
}


def _sample():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_real_public_sample_has_exact_epub_ean_distinct_from_paper_ean():
    sample = _sample()
    assert sample["record_count"] == len(sample["records"]) == 4
    seen_urls = set()
    seen_epub = set()
    for row in sample["records"]:
        url = row["product_url"]
        parsed = urlsplit(url)
        assert parsed.scheme == "https" and parsed.netloc == "www.numilog.com"
        assert parsed.path.startswith("/" + row["product_id"] + "/")
        assert url not in seen_urls
        seen_urls.add(url)
        assert _isbn(row["isbn_epub"]) == row["isbn_epub"]
        assert _isbn(row["isbn_paper"]) == row["isbn_paper"]
        assert row["isbn_epub"] != row["isbn_paper"]
        assert row["isbn_epub"] not in seen_epub
        seen_epub.add(row["isbn_epub"])
        assert row["isbn_epub_field"] == "EAN EPUB SANS DRM"
        assert row["isbn_paper_field"] == "EAN papier"
        assert row["language"] == "fr"
    assert len(seen_epub) == 4


def test_age_like_marketing_labels_are_not_attested_structured_age():
    rows = _sample()["records"]
    assert [row["age_source_field"] for row in rows].count("collection") == 2
    assert [row["age_source_field"] for row in rows].count("description") == 2
    for row in rows:
        assert row["age_label"] in row["age_source_text"]
        assert row["age_source_scope"] == "product_page_editorial_text"
        assert row["age_normalized_attested"] is False
        assert row["structured_typical_age_range_status"] == "not_checked_in_raw_jsonld"
        assert not (CANONICAL_AGE_KEYS & row.keys())
    assert {row["age_label"] for row in rows} == {
        "12-15 ans", "À partir de 8 ans",
    }


def test_recorded_sample_never_claims_provider_coverage():
    sample = _sample()
    assert sample["selection"].startswith("Purposefully selected")
    assert sample["observed_date"] == "2026-11-06"
    assert "coverage" in sample["selection"]
