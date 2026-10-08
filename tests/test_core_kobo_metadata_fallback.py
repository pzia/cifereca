from __future__ import annotations

import pytest

from cifereca.kobo.acquisition import KoboOfferAdapter


@pytest.mark.parametrize("nested_value", [None, "", "   "])
def test_kobo_candidate_metadata_falls_back_when_nested_value_is_blank(nested_value):
    candidate = {
        "kind": "acsm",
        "transport": "https",
        "method": "GET",
        "url": "https://cdn.example.org/book.acsm",
        "media_format": "acsm",
        "handling": "adobe_adept",
        "source": "library_download_link",
        "display_profile": "Desktop",
        "kobo": {
            "source": nested_value,
            "display_profile": nested_value,
        },
    }

    assert KoboOfferAdapter.candidate_source(candidate) == "library_download_link"
    assert KoboOfferAdapter.candidate_profile(candidate) == "Desktop"

    normalized = KoboOfferAdapter.normalize_candidate(candidate)
    assert normalized.source == "library_download_link"
    assert normalized.display_profile == "Desktop"


def test_kobo_candidate_nested_metadata_takes_precedence():
    candidate = {
        "source": "library_download_link",
        "display_profile": "Desktop",
        "kobo": {"source": "content_access_book", "display_profile": "Android"},
    }

    assert KoboOfferAdapter.candidate_source(candidate) == "content_access_book"
    assert KoboOfferAdapter.candidate_profile(candidate) == "Android"


def test_acsm_priority_uses_top_level_source_when_nested_source_is_empty():
    candidate = {
        "kind": "acsm",
        "transport": "https",
        "method": "GET",
        "url": "https://cdn.example.org/preferred.acsm",
        "media_format": "acsm",
        "handling": "adobe_adept",
        "source": "library_download_link",
        "kobo": {"source": ""},
    }
    alternative = {
        **candidate,
        "url": "https://cdn.example.org/alternative.acsm",
        "source": "content_access_book",
        "kobo": {"source": "content_access_book"},
    }

    assert KoboOfferAdapter.acsm_candidate(
        {"acquisition": {"candidates": [alternative, candidate]}}
    ) == candidate
