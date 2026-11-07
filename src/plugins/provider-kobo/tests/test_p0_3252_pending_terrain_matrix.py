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
def test_multiple_plain_routes_prefer_unique_epub_but_never_two_epubs(media_format):
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
    selected = exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic)
    if media_format == "pdf":
        # The owner explicitly prefers the one independently admitted EPUB.
        assert selected == first_id
        assert diagnostic == {
            "candidate_shape_counts": {
                "content_access_book:epub:missing:http_plain": 1,
                "content_access_book:pdf:missing:http_plain": 1,
            },
            "candidate_selection": "unique_epub_preferred",
        }
    else:
        assert selected == ""
        assert diagnostic == {
            "eligible_candidate_count": 2,
            "qualification_reason": "eligible_count_not_one",
            "candidate_selection": "multiple_eligible_candidates",
            "candidate_rejection_counts": {},
            "candidate_route_comparison": "same_route_distinct_urls",
            "candidate_url_relation": "same_host_different_paths",
            "candidate_shape_counts": {
                "content_access_book:epub:missing:http_plain": 2,
            },
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
        "candidate_shape_counts": {
            "content_access_book:epub:missing:http_plain": 1,
            "library_sync:kepub:kepub:kobo_kdrm": 1,
        },
        "candidate_rejection_profile_counts": {
            "content_access_book:epub:missing:http_plain|candidate_transport_invalid": 1,
            "library_sync:kepub:kepub:kobo_kdrm|candidate_expiry_invalid": 1,
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
    ("unrecognized-format", "other"), ("FLEPUB", "flepub"),
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


def test_same_route_and_same_signed_url_remains_ambiguous():
    envelope = descriptor()
    candidate = envelope["acquisition"]["candidates"][0]
    envelope["acquisition"]["candidates"].append(dict(candidate))
    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["candidate_selection"] == "multiple_eligible_candidates"
    assert diagnostic["candidate_route_comparison"] == "same_route_same_url"
    assert diagnostic["eligible_candidate_count"] == 2
    assert "https://" not in str(diagnostic)
    assert "signature" not in str(diagnostic)


def test_k3_same_shape_can_have_distinct_rejection_reasons_without_leaking_urls():
    envelope = descriptor()
    first = envelope["acquisition"]["candidates"][0]
    first["url"] = "http://bad.invalid/?secret=DO_NOT_LEAK"
    second = dict(first)
    second["url"] = "https://cdn.kobo.com/other?token=HIDDEN"
    second["expires_at"] = "2000-01-01T00:00:00Z"
    envelope["acquisition"]["candidates"].append(second)
    diagnostics = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostics) == ""
    assert diagnostics["candidate_selection"] == "no_eligible_candidates"
    assert diagnostics["candidate_shape_counts"] == {
        "content_access_book:epub:missing:http_plain": 2,
    }
    assert diagnostics["candidate_rejection_profile_counts"] == {
        "content_access_book:epub:missing:http_plain|candidate_transport_invalid": 1,
        "content_access_book:epub:missing:http_plain|candidate_expiry_invalid": 1,
    }
    assert "DO_NOT_LEAK" not in str(diagnostics)
    assert "HIDDEN" not in str(diagnostics)
    assert "http://" not in str(diagnostics)


def test_k3_unknown_format_and_source_remain_other_not_raw_text():
    envelope = descriptor()
    first = envelope["acquisition"]["candidates"][0]
    first["media_format"] = "SECRET-PRIVATE-FORMAT"
    first["source_format"] = "PRIVATE-SOURCE-SECRET"
    second = dict(first)
    second["media_format"] = "unknown"
    second["url"] = "https://cdn.kobo.com/second?token=VERY_SECRET"
    envelope["acquisition"]["candidates"].append(second)
    diagnostic = {}
    assert exact_kobo_offer_id(envelope, **_SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["candidate_selection"] == "no_eligible_candidates"
    assert diagnostic["candidate_rejection_profile_counts"] == {
        "content_access_book:other:other:http_plain|candidate_format_pair_unqualified": 1,
        "content_access_book:unknown:other:http_plain|candidate_format_pair_unqualified": 1,
    }
    for secret in ("PRIVATE", "VERY_SECRET", "SECRET"):
        assert secret not in str(diagnostic)


@pytest.mark.parametrize(("media", "alias", "expected_media", "expected_alias"), [
    ("unknown", "FLEPUB", "media_unknown", "matching"),
    ("unknown", "EPUB3", "media_unknown", "conflicting"),
    ("unknown", None, "media_unknown", "missing"),
    ("FLEPUB", "FLEPUB", "media_other", "matching"),
    ("epub", "FLEPUB", "normalized_epub", "matching"),
    ("epub", "PRIVATE-API-TOKEN-DO-NOT-LOG", "normalized_epub", "conflicting"),
])
def test_k2_kvault_flepub_alias_diagnostic_is_closed_and_not_an_admission(
    media, alias, expected_media, expected_alias,
):
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = media
    candidate["source_format"] = "FLEPUB"
    if alias is None:
        candidate.pop("format", None)
    else:
        candidate["format"] = alias
    diagnostic = {}
    offer = exact_kobo_offer_id(
        descriptor(candidate=candidate), **_SCOPE,
        kdrm_runtime_ready=True, diagnostic=diagnostic,
    )
    assert diagnostic["candidate_kvault_flepub_contract"] == expected_media
    assert diagnostic["candidate_kvault_format_alias"] == expected_alias
    assert "PRIVATE-API-TOKEN" not in str(diagnostic)
    assert "FLEPUB" not in str(diagnostic)
    if media in {"epub", "unknown"}:
        assert offer.startswith("kobo.runtime:")
        assert diagnostic.get("candidate_kdrm_flepub_normalized_count") == (
            1 if media == "unknown" else None
        )
    else:
        assert offer == ""
        assert diagnostic["qualification_reason"] == "kdrm_format_pair_unqualified"


def test_k2_alias_diagnostic_requires_library_sync_kdrm_and_flepub():
    for change in ("source", "drm", "format"):
        candidate = kdrm_candidate("book-1")
        if change == "source":
            candidate["kobo"]["source"] = "content_access_book"
        elif change == "drm":
            candidate["drm"]["raw_type"] = "None"
            candidate["drm"]["family"] = "none"
            candidate["drm"]["protected"] = False
            candidate["drm_type"] = "None"
            candidate["protected"] = False
            candidate["handling"] = "http_plain"
        else:
            candidate["source_format"] = "KEPUB"
        diagnostic = {}
        exact_kobo_offer_id(
            descriptor(candidate=candidate), **_SCOPE,
            kdrm_runtime_ready=True, diagnostic=diagnostic,
        )
        assert "candidate_kvault_flepub_contract" not in diagnostic
        assert "candidate_kvault_format_alias" not in diagnostic
