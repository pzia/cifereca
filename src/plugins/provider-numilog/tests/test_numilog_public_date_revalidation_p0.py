"""#1072: cached/publicly supplied Numilog labels are revalidated at merge.

Fixtures describe commercial product metadata, not proof of owned variant.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from cifereca_numilog_public_detail import parse_public_detail_jsonld
from cifereca_numilog_public_metadata import (
    PublicMetadataError, validated_public_metadata,
)

URL = (
    "https://www.numilog.com/1171334/"
    "blackbone--coltan-song-tome-1--des-15-ans.ebook"
)
HTML = (
    Path(__file__).with_name("fixtures")
    / "observed_metadata_blackbone.html"
).read_text()


@pytest.mark.parametrize("forged", [
    "2026-02-30", "2026-13-01", "2026-00-01",
    "2026-10-20T01:00:00", "20261020", "9999-99-99",
])
def test_mutated_public_date_cannot_survive_final_merge_boundary(forged):
    detail = parse_public_detail_jsonld(HTML, URL)
    detail["public_html_metadata"]["fields"]["publication_date"] = forged
    with pytest.raises(PublicMetadataError, match="evidence_unverified"):
        validated_public_metadata(detail, URL)


@pytest.mark.parametrize("forged", [
    "", " ", "\t", "n.c", "n.c.", "non communiqué", "non communiquée",
])
def test_missing_or_placeholder_labels_are_not_promoted_as_public_evidence(forged):
    detail = parse_public_detail_jsonld(HTML, URL)
    detail["public_html_metadata"]["fields"]["brand"] = forged
    with pytest.raises(PublicMetadataError, match="evidence_unverified"):
        validated_public_metadata(detail, URL)


def test_original_attested_date_and_publisher_remain_usable():
    detail = parse_public_detail_jsonld(HTML, URL)
    before = deepcopy(detail["public_html_metadata"])
    result = validated_public_metadata(detail, URL)
    assert result == before
    assert result["fields"]["publication_date"] == "2020-04-16"
    assert result["fields"]["brand"] == "NATHAN"
    assert result["field_evidence"]["publication_date"]["edition_proven"] is False
    assert result["field_evidence"]["publication_date"]["rights_proven"] is False
