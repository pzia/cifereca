"""#1072: owned/public provenance must be checked at the merge boundary.

No account HTTP, purchases, downloads or ACSM. Uses the public HTML fixture
already in the repository and a synthetic purchased-library projection.
"""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_numilog import NumilogSession
from cifereca_numilog_enrichment import merge_attested_public_listing
from cifereca_numilog_public import parse_public_search

SOURCE = (
    "https://www.numilog.com/Pages/Recherche/ResultatRecherche.aspx"
    "?mode=brSimple&query=jefferson"
)
FIXTURE = Path(__file__).parent / "fixtures" / "public_search_jefferson_20261016.html"
PAGE = parse_public_search(FIXTURE.read_text(), SOURCE)


def private_row():
    session = NumilogSession(
        credential_id="account-one", label="Owned", credentials={},
        options={}, config={}, diagnostics=None,
    )
    try:
        return session._normalize({
            "lid": "42", "variant": "()", "title": PAGE.items[0].title,
            "cover": None, "public_url": PAGE.items[0].friendly_url,
        })
    finally:
        session.close()


@pytest.mark.parametrize("changes", [
    {"owned": False},
    {"owned": None},
    {"source_collection": "public_catalog"},
    {"source_provider": "other-provider"},
    {"source_credential": "__public__"},
    {"source_credential": ""},
    {"downloadable": False},
    {"download_url": "https://example.invalid/private?token=PRIVATE"},
    {"metadata": {}},
    {"metadata": {"public_url_evidence": "caller_guessed"}},
])
def test_unverified_purchased_origin_is_rejected_before_copy(changes):
    owned = replace(private_row(), **changes)
    initial = deepcopy(owned.metadata)
    with pytest.raises(ProviderCapabilityError, match="owned_edition_evidence_unverified") as exc:
        merge_attested_public_listing(owned, PAGE.items)
    assert "PRIVATE" not in str(exc.value)
    assert owned.metadata == initial


@pytest.mark.parametrize("changes", [
    {"scenario": "NUMILOG_PRIVATE"},
    {"docbase": "OTHER"},
])
def test_public_rows_with_forged_scope_cannot_enrich_a_purchase(changes):
    owned = private_row()
    public = replace(PAGE.items[0], **changes)
    with pytest.raises(ProviderCapabilityError, match="public_edition_evidence_conflict"):
        merge_attested_public_listing(owned, (public,))
    assert "public_enrichment" not in owned.metadata


def test_authentic_private_and_public_proofs_still_enrich_only_metadata():
    owned = private_row()
    result, state = merge_attested_public_listing(owned, PAGE.items)
    assert state == "attested_listing_match"
    assert result.owned is True
    assert result.download_url is None
    assert result.source_credential == "account-one"
    assert result.metadata["public_enrichment"]["scope"] == "public_listing_only"
    assert result.format is None
