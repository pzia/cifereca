"""#1072: hidden WebForms product labels cannot attest edition metadata.

Only saved offline product-page markup; never a personal account or download.
"""
from pathlib import Path

import pytest

from cifereca_numilog_public_metadata import extract_public_metadata

ROOT = Path(__file__).with_name("fixtures")
HTML = (ROOT / "observed_metadata_blackbone.html").read_text()
URL = (
    "https://www.numilog.com/1171334/"
    "blackbone--coltan-song-tome-1--des-15-ans.ebook"
)
TITLE = "Blackbone - Coltan song- Tome 1 - Dès 15 ans"
DETAIL = '<div id="ctl02_CentrePage_dvFicheDetail"'


@pytest.mark.parametrize("marker", [
    'aria-hidden="TRUE"',
    'aria-hidden=" True "',
    'style="DISPLAY:\tNONE"',
    'style="visibility:\n hidden"',
])
def test_hidden_detail_container_never_publishes_publisher_or_language(marker):
    assert DETAIL in HTML
    altered = HTML.replace(DETAIL, '<div ' + marker
                           + ' id="ctl02_CentrePage_dvFicheDetail"', 1)
    fields = extract_public_metadata(altered, URL, TITLE)["fields"]
    assert "brand" not in fields
    assert "language_label" not in fields
    assert "pagination_label" not in fields


def test_hidden_uppercase_canonical_form_cannot_attest_any_fields():
    assert '<form id="aspnetForm"' in HTML
    modified = HTML.replace(
        '<form id="aspnetForm"',
        '<form aria-hidden="TRUE" id="aspnetForm"', 1,
    )
    assert extract_public_metadata(modified, URL, TITLE)["fields"] == {}


def test_original_public_metadata_still_extracted_without_fictitious_rights():
    fields = extract_public_metadata(HTML, URL, TITLE)["fields"]
    assert fields["brand"] == "NATHAN"
    assert fields["language_label"] == "Français"
    assert fields["selected_format_label"] == "epub sans DRM"
    assert "isbn_digital" not in fields
