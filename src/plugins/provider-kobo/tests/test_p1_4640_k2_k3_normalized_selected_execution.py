"""P1 #4640 K2/K3: local alias, exact offer, runtime consistency and ambiguity.

Only synthetic POSTs, never a real Kobo/Cloud GET or a KVault change.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p0_3252_kdrm_canonical_runtime import (
    kdrm_candidate, ready, selected, deliver,
)
from tests.test_p1_3252_kobo_selected_candidate import epub_bytes
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup


SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1", kdrm_runtime_ready=True,
)


def _unknown_flepub():
    candidate = kdrm_candidate("book-1")
    candidate["media_format"] = " unknown "
    candidate["source_format"] = " fLePuB "
    candidate["format"] = "PRIVATE_FORMAT_ALIAS"
    return candidate


@pytest.mark.parametrize("fresh_media", ["unknown", "epub"])
def test_k2_end_to_end_exact_selection_and_epub_runtime_without_plain_get(
    monkeypatch, tmp_path, fresh_media,
):
    session, vault, _broker, account, _posts = setup()
    calls = ready(session, vault)
    original = vault.acquisition
    candidate = _unknown_flepub()
    descriptor_count = 0
    baseline = deepcopy(candidate)
    baseline["media_format"] = "epub"
    expected = exact_kobo_offer_id(descriptor(candidate=baseline), **SCOPE)
    assert expected.startswith("kobo.runtime:")

    def upstream(product, **options):
        nonlocal descriptor_count
        response = original(product, **options)
        if options.get("mode") == "descriptor":
            row = deepcopy(candidate)
            if descriptor_count:
                # The refreshed POST may already be fixed upstream; the same
                # opaque offer must remain executable without a second route.
                row["media_format"] = fresh_media
            descriptor_count += 1
            response["acquisition"]["candidates"] = [row]
        return response

    vault.acquisition = upstream
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **_kw: pytest.fail("Never GET Kobo KDRM as plaintext"),
    )
    execution = []

    def runtime(request):
        execution.append(request)
        pinned = request["source"]["candidate"]
        assert pinned["media_format"] == "epub"
        assert pinned["source_format"] == "FLEPUB"
        assert pinned["delivery_source"] == "library_sync"
        assert request["format"] == "epub"
        path = Path(request["destination"])
        path.write_bytes(epub_bytes())
        return {"ok": True, "artifact": {"path": str(path), "format": "epub"}}

    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available"
        assert offer.delivery_offer_id == expected
        staged = deliver(
            session, account, "book-1", offer.delivery_offer_id,
            tmp_path, runtime,
        )
        assert staged.path.is_file()
        assert len(execution) == 1
        assert descriptor_count == 2
        assert calls == [
            ("book-1", "descriptor"), ("book-1", "delegated"),
            ("book-1", "descriptor"), ("book-1", "delegated"),
        ]
    finally:
        session.close()


@pytest.mark.parametrize("reverse", [False, True])
def test_k3_two_normalized_kdrm_offers_remain_pending_no_delegation(reverse):
    first = _unknown_flepub()
    second = deepcopy(first)
    second["url"] = "https://cdn.kobo.com/second?token=PRIVATE_K3"
    payload = descriptor(candidate=first)
    rows = [first, second]
    payload["acquisition"]["candidates"] = rows[::-1] if reverse else rows
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["eligible_candidate_count"] == 2
    assert diagnostic["candidate_selection"] == "multiple_eligible_candidates"
    assert diagnostic["candidate_kdrm_flepub_normalized_count"] == 2
    assert diagnostic["candidate_shape_counts"] == {
        "library_sync:unknown:flepub:kobo_kdrm": 2,
    }
    assert "PRIVATE" not in str(diagnostic)
    session, vault, _broker, account, posts = setup()
    ready(session, vault)
    vault.acquisition = lambda product, **options: (
        posts.append((product, options)) or deepcopy(payload)
    )
    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "unknown"
        assert offer.delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
    finally:
        session.close()


@pytest.mark.parametrize("change", [
    "missing_media", "empty_media", "other_media", "other_source",
    "sample", "other_drm", "other_handling", "other_origin", "other_kind",
    "foreign_scope", "invalid_url",
])
def test_k2_alias_never_bypasses_other_admission_guards(change):
    row = _unknown_flepub()
    if change == "missing_media":
        del row["media_format"]
    elif change == "empty_media":
        row["media_format"] = ""
    elif change == "other_media":
        row["media_format"] = "other"
    elif change == "other_source":
        row["source_format"] = "EPUB3FL"
    elif change == "sample":
        row["source_format"] = "FLEPUB_SAMPLE"
    elif change == "other_drm":
        row["drm"]["raw_type"] = "UnknownDRM"
    elif change == "other_handling":
        row["handling"] = "http_plain"
    elif change == "other_origin":
        row["kobo"]["source"] = "content_access_book"
    elif change == "other_kind":
        row["kind"] = "acsm"
    elif change == "foreign_scope":
        row["account_id"] = "another-account"
    elif change == "invalid_url":
        row["url"] = "http://cdn.kobo.com/forbidden?token=PRIVATE"
    assert exact_kobo_offer_id(descriptor(candidate=row), **SCOPE) == ""


def test_unknown_flepub_without_runtime_is_not_an_executable_offer():
    candidate = _unknown_flepub()
    diagnostic = {}
    assert exact_kobo_offer_id(
        descriptor(candidate=candidate),
        **{**SCOPE, "kdrm_runtime_ready": False}, diagnostic=diagnostic,
    ) == ""
    assert diagnostic["candidate_kdrm_flepub_normalized_count"] == 1
    assert diagnostic["candidate_selection"] == "no_eligible_candidates"
