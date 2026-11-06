"""#4640 K2: case-only FLEPUB variations are not a new Kobo permission.

K2's real media_format=unknown remains pending, while an independently
attested media_format=epub + KDRM library_sync source preserves one offer ID.
No Vault HTTP, content GET, DRM runtime or account access.
"""
from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from cifereca_kobo_provider_kdrm import scoped_kdrm_source
from tests.test_p0_3252_kdrm_canonical_runtime import kdrm_candidate, delegated
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor


SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1", kdrm_runtime_ready=True,
)


def _candidate(media, source):
    row = kdrm_candidate("book-1")
    row["media_format"] = media
    row["source_format"] = source
    return row


@pytest.mark.parametrize("media", ["epub", "EPUB", "EpUb"])
@pytest.mark.parametrize("source", ["FLEPUB", "flepub", "FlEpUb"])
def test_case_only_flepub_variations_keep_exact_offer_id_and_runtime_route(
    media, source,
):
    baseline = _candidate("epub", "FLEPUB")
    expected = exact_kobo_offer_id(
        descriptor(product="book-1", candidate=baseline), **SCOPE,
    )
    candidate = _candidate(media, source)
    observed = {}
    offer = exact_kobo_offer_id(
        descriptor(product="book-1", candidate=candidate),
        **SCOPE, diagnostic=observed,
    )
    assert expected.startswith("kobo.runtime:") and offer == expected
    recipe = scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    )
    assert recipe is not None
    assert recipe["candidate"]["media_format"] == "epub"
    assert recipe["candidate"]["source_format"].casefold() == "flepub"
    assert "candidate_rejection_counts" not in observed


@pytest.mark.parametrize("media", ["unknown", "UNKNOWN", "flepub", "FLEPUB"])
@pytest.mark.parametrize("source", ["FLEPUB", "flepub"])
def test_unknown_or_unqualified_media_cannot_be_upgraded_from_source_label(
    media, source,
):
    candidate = _candidate(media, source)
    observed = {}
    offer = exact_kobo_offer_id(
        descriptor(product="book-1", candidate=candidate),
        **SCOPE, diagnostic=observed,
    )
    assert offer == ""
    assert observed["candidate_selection"] == "no_eligible_candidates"
    assert observed["candidate_rejection_counts"].get(
        "kdrm_format_pair_unqualified"
    ) == 1
    assert scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    ) is None


def test_two_case_equivalent_routes_with_distinct_urls_still_require_selection():
    first = _candidate("epub", "FLEPUB")
    second = deepcopy(first)
    second["source_format"] = "flepub"
    second["url"] = second["url"].replace(".kepub", "-different.kepub")
    payload = descriptor(product="book-1", candidate=first)
    payload["acquisition"]["candidates"].append(second)
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["candidate_selection"] == "multiple_eligible_candidates"
    assert diagnostic["candidate_route_comparison"] == "same_route_distinct_urls"
