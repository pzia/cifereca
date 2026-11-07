"""#4640 K2/K3: tightly scoped unknown/FLEPUB aliases match EPUB KDRM.

Synthetic descriptor and delegated-envelope fixtures only; no external I/O.
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


@pytest.mark.parametrize("media", ["unknown", "UNKNOWN", " Unknown "])
@pytest.mark.parametrize("source", ["FLEPUB", "flepub", " FlEpUb "])
def test_unknown_flepub_is_normalized_to_same_opaque_offer_and_recipe(media, source):
    baseline = _candidate("epub", "FLEPUB")
    expected = exact_kobo_offer_id(descriptor(candidate=baseline), **SCOPE)
    candidate = _candidate(media, source)
    observed = {}
    offer = exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE, diagnostic=observed,
    )
    assert offer == expected and offer.startswith("kobo.runtime:")
    assert observed["candidate_media_format"] == "unknown"
    assert observed["candidate_kdrm_flepub_normalized_count"] == 1
    assert observed["candidate_kvault_flepub_contract"] == "media_unknown"
    recipe = scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    )
    assert recipe["candidate"]["media_format"] == "epub"
    assert recipe["candidate"]["source_format"] == "FLEPUB"


@pytest.mark.parametrize("media", ["flepub", "FLEPUB", "other", "", None])
@pytest.mark.parametrize("source", ["FLEPUB", "flepub"])
def test_other_or_missing_media_is_not_inferred_from_flepub(media, source):
    candidate = _candidate(media, source)
    observed = {}
    assert exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE, diagnostic=observed,
    ) == ""
    assert scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    ) is None
    assert "candidate_kdrm_flepub_normalized_count" not in observed


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
