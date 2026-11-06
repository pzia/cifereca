"""#4432: first failed Kobo offer guard is observable, without weaker admission.

All data are synthetic. Never requests a real Kobo service or an EPUB.
"""
from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p0_3252_kdrm_canonical_runtime import kdrm_candidate
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


_SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1",
)


def qualify(candidate, *, envelope=None):
    seen = {}
    payload = envelope if envelope is not None else descriptor(candidate=candidate)
    value = exact_kobo_offer_id(
        payload, **_SCOPE, kdrm_runtime_ready=True, diagnostic=seen,
    )
    return value, seen


def test_valid_kdrm_keeps_exact_offer_with_or_without_diagnostic():
    candidate = kdrm_candidate("book-1")
    value, seen = qualify(candidate)
    baseline = exact_kobo_offer_id(
        descriptor(candidate=candidate), **_SCOPE, kdrm_runtime_ready=True,
    )
    assert value == baseline
    assert value.startswith("kobo.runtime:")
    assert "qualification_reason" not in seen
    assert seen["candidate_source"] == "library_sync"


@pytest.mark.parametrize(("change", "reason", "source", "media", "upstream"), [
    ("source", "kdrm_source_unsupported", "content_access_book", "kepub", "kepub"),
    ("source_case", "kdrm_source_unsupported", "other", "kepub", "kepub"),
    ("format", "kdrm_format_pair_unqualified", "library_sync", "kepub", "epub3"),
    ("transport", "candidate_transport_or_expiry_invalid", "library_sync", "kepub", "kepub"),
    ("family", "candidate_drm_family_mismatch", "library_sync", "kepub", "kepub"),
    ("scope", "candidate_scope_conflict", "library_sync", "kepub", "kepub"),
    ("drm", "candidate_drm_type_conflict", "library_sync", "kepub", "kepub"),
])
def test_single_kdrm_failure_reports_actual_guard(
    change, reason, source, media, upstream,
):
    candidate = kdrm_candidate("book-1")
    if change == "source":
        candidate["kobo"]["source"] = "content_access_book"
    elif change == "source_case":
        candidate["kobo"]["source"] = "LIBRARY_SYNC"
    elif change == "format":
        candidate["source_format"] = "EPUB3"
    elif change == "transport":
        candidate["url"] = "http://bad.invalid/book"
    elif change == "family":
        candidate["drm"]["family"] = "none"
    elif change == "scope":
        candidate["account_id"] = "someone-else"
    elif change == "drm":
        candidate["drm_type"] = "unrelated"

    offer, seen = qualify(candidate)
    assert offer == ""
    assert seen["qualification_reason"] == reason
    assert seen["candidate_source"] == source
    assert seen["candidate_media_format"] == media
    assert seen["candidate_source_format"] == upstream
    if change == "scope":
        # The contradictory owner aborts the whole descriptor immediately.
        assert "eligible_candidate_count" not in seen
    else:
        assert seen["eligible_candidate_count"] == 0
    assert "url" not in seen and "token" not in str(seen)


def test_multiple_valid_candidates_do_not_attrib_single_candidate_failure():
    candidate = kdrm_candidate("book-1")
    payload = descriptor(candidate=candidate)
    payload["acquisition"]["candidates"].append(dict(candidate))
    offer, seen = qualify(candidate, envelope=payload)
    assert offer == ""
    assert seen == {
        "eligible_candidate_count": 2,
        "qualification_reason": "eligible_count_not_one",
        "candidate_selection": "multiple_eligible_candidates",
        "candidate_rejection_counts": {},
    }


def test_descriptor_scope_failure_is_not_attributed_to_candidate():
    candidate = kdrm_candidate("book-1")
    payload = descriptor(candidate=candidate)
    payload["identity_id"] = "not-the-bound-identity"
    offer, seen = qualify(candidate, envelope=payload)
    assert offer == ""
    assert seen == {"qualification_reason": "descriptor_scope_conflict"}


@pytest.mark.parametrize("multiple", [False, True])
def test_real_provider_preflight_logs_rejection_without_delegated_post(caplog, multiple):
    session, vault, _broker, account, posts = setup()
    session.runtime_capabilities = SimpleNamespace(
        acquisition=lambda _provider: SimpleNamespace(
            available=True, compatible=True, operational=True,
            modes=("kobo_kdrm",),
        )
    )

    def observed_descriptor(product_id, **opts):
        posts.append((product_id, opts))
        candidate = kdrm_candidate(product_id)
        candidate["kobo"]["source"] = "content_access_book"
        payload = descriptor(product=product_id, candidate=candidate)
        if multiple:
            plain = descriptor(product=product_id)["acquisition"]["candidates"][0]
            plain["media_format"] = "unknown"
            payload["acquisition"]["candidates"].append(plain)
        return payload

    vault.acquisition = observed_descriptor
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            snapshot = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        records = [
            r.getMessage() for r in caplog.records
            if "EVENT kobo selected_offer" in r.getMessage()
        ]
        assert len(records) == 1
        record = records[0]
        assert "reason=descriptor_candidate_unqualified" in record
        assert "candidate_selection=no_eligible_candidates" in record
        if multiple:
            assert "qualification_reason=eligible_count_not_one" in record
            assert ("candidate_rejection_counts=candidate_format_pair_unqualified:1,"
                    "kdrm_source_unsupported:1") in record
        else:
            assert "qualification_reason=kdrm_source_unsupported" in record
            assert "candidate_source=content_access_book" in record
            assert "candidate_media_format=kepub" in record
            assert "candidate_source_format=kepub" in record
            assert "candidate_rejection_counts=kdrm_source_unsupported:1" in record
        assert "delegated_resolver_called=False" in record
        assert "token=" not in record and "https://" not in record
    finally:
        session.close()
