"""#3252: three observed pending Kobo shapes remain non-executable.

Synthetic descriptors only: no HTTP, credential, file read, or DRM runtime.
These tests encode the *observed shape*, not a retroactive diagnosis of
the three rejected final artifacts or permission to select a fallback.
"""
from __future__ import annotations

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


@pytest.mark.parametrize("media_format", ["epub", "pdf"])
def test_multiple_plain_routes_remain_pending_even_if_opaque_ids_match(media_format):
    envelope = descriptor()
    first = envelope["acquisition"]["candidates"][0]
    second = {**first, "media_format": media_format,
              "url": "https://cdn.kobo.com/second/signed-book?signature=synthetic"}
    first_id = exact_kobo_offer_id(envelope, **_SCOPE)
    second_id = exact_kobo_offer_id(descriptor(candidate=second), **_SCOPE)
    assert first_id and second_id
    assert (first_id == second_id) is (media_format == "epub")
    envelope["acquisition"]["candidates"].append(second)

    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic == {
        "eligible_candidate_count": 2,
        "qualification_reason": "eligible_count_not_one",
        "candidate_selection": "multiple_eligible_candidates",
        "candidate_rejection_counts": {},
    }


@pytest.mark.parametrize("reverse", [False, True])
def test_no_eligible_route_counts_distinct_first_rejections(reverse):
    envelope = descriptor()
    first = envelope["acquisition"]["candidates"][0]
    first.update(url="http://cdn.kobo.com/book", expires_at="2000-01-01T00:00:00Z")
    second = kdrm_candidate("book-1")
    second["expires_at"] = "2000-01-01T00:00:00Z"
    candidates = [first, second]
    envelope["acquisition"]["candidates"] = candidates[::-1] if reverse else candidates

    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic == {
        "eligible_candidate_count": 0,
        "qualification_reason": "eligible_count_not_one",
        "candidate_selection": "no_eligible_candidates",
        "candidate_rejection_counts": {
            "candidate_transport_invalid": 1,
            "candidate_expiry_invalid": 1,
        },
    }


def test_candidate_rejection_counts_stop_at_descriptor_limit():
    envelope = descriptor()
    envelope["acquisition"]["candidates"] = [None] * 50
    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["candidate_rejection_counts"] == {"candidate_not_mapping": 50}
    envelope["acquisition"]["candidates"].append(None)
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic == {"qualification_reason": "candidate_list_invalid"}


def test_candidate_scope_conflict_does_not_report_partial_counts():
    envelope = descriptor()
    foreign = {**envelope["acquisition"]["candidates"][0], "account_id": "bob"}
    envelope["acquisition"]["candidates"] = [None, foreign]
    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic == {"qualification_reason": "candidate_scope_conflict"}


@pytest.mark.parametrize(("media_format", "reported"), [
    ("unrecognized-format", "other"), ("unknown", "unknown"), ("FLEPUB", "flepub"),
])
def test_observed_non_epub_flepub_pair_is_not_inferred_as_epub(media_format, reported):
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = media_format
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
    assert diagnostic["candidate_media_format"] == reported
    assert diagnostic["candidate_source_format"] == "flepub"
    assert diagnostic["candidate_rejection_counts"] == {"kdrm_format_pair_unqualified": 1}
    assert "url" not in str(diagnostic).lower()

    # This is a separate, explicitly attested contract pair. It does not
    # justify coercing any other upstream media format.
    candidate["media_format"] = "epub"
    accepted = exact_kobo_offer_id(
        descriptor(candidate=candidate), **_SCOPE, kdrm_runtime_ready=True,
    )
    assert accepted.startswith("kobo.runtime:")
