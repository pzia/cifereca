"""#3252: three observed pending Kobo shapes remain non-executable.

Synthetic descriptors only: no HTTP, credential, file read, or DRM runtime.
These tests encode the *observed shape*, not a retroactive diagnosis of
the three rejected final artifacts or permission to select a fallback.
"""
from __future__ import annotations

from copy import deepcopy

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p0_3252_kdrm_canonical_runtime import kdrm_candidate
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor


_SCOPE = {
    "identity_id": "identity-A",
    "credential_id": "synthetic-card",
    "account_id": "alice",
    "product_id": "book-1",
}


@pytest.mark.parametrize(("eligible_count", "candidate_count"), [(2, 2), (0, 2)])
def test_observed_ambiguous_descriptors_remain_pending(
    eligible_count, candidate_count,
):
    valid = kdrm_candidate("book-1")
    candidates = [deepcopy(valid) for _ in range(candidate_count)]
    if eligible_count == 0:
        for candidate in candidates:
            candidate["url"] = "http://invalid.example/book"
    envelope = descriptor(candidate=valid)
    envelope["acquisition"]["candidates"] = candidates

    diagnostic = {}
    offered = exact_kobo_offer_id(
        envelope, **_SCOPE, kdrm_runtime_ready=True,
        diagnostic=diagnostic,
    )
    assert offered == ""
    assert diagnostic == {
        "eligible_candidate_count": eligible_count,
        "qualification_reason": "eligible_count_not_one",
    }
    assert "url" not in str(diagnostic).lower()


def test_observed_other_flepub_pair_is_not_inferred_as_epub():
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = "other"
    candidate["source_format"] = "flepub"
    envelope = descriptor(candidate=candidate)
    diagnostic = {}
    offered = exact_kobo_offer_id(
        envelope, **_SCOPE, kdrm_runtime_ready=True,
        diagnostic=diagnostic,
    )
    assert offered == ""
    assert diagnostic["qualification_reason"] == "kdrm_format_pair_unqualified"
    assert diagnostic["eligible_candidate_count"] == 0
    assert diagnostic["candidate_source"] == "library_sync"
    assert diagnostic["candidate_media_format"] == "other"
    assert diagnostic["candidate_source_format"] == "flepub"
    assert "url" not in str(diagnostic).lower()

    # This is a separate, explicitly attested contract pair. It does not
    # justify coercing the unknown upstream 'other' format above.
    candidate["media_format"] = "epub"
    accepted = exact_kobo_offer_id(
        descriptor(candidate=candidate), **_SCOPE, kdrm_runtime_ready=True,
    )
    assert accepted.startswith("kobo.runtime:")
