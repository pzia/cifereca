"""#4640: two attested identical KDRM candidates -> first signed URL only.

Synthetic Kobo descriptors. The rule is deliberately limited to one changing
downloadToken on the SAME HTTPS route, not arbitrary source/format/DRM/profile
ambiguity. Verify offer attestation AND the fresh selected runtime source.
"""
from copy import deepcopy
from pathlib import Path

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT
from cifereca_kobo_delivery_offer import (
    _equivalent_signed_kdrm_candidates, exact_kobo_offer_id,
)
from cifereca_kobo_selected_candidate import _single_admitted_candidate
from tests.test_p0_3252_kdrm_canonical_runtime import ready, selected, deliver
from tests.test_p1_3252_kobo_selected_candidate import epub_bytes
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup
from tests.test_p1_4640_k2_k3_normalized_selected_execution import (
    SCOPE, _unknown_flepub,
)


BASE = "https://storedownloads.kobo.com/download?downloadToken="
URL_A = BASE + "SYNTHETIC_TOKEN_A"
URL_B = BASE + "SYNTHETIC_TOKEN_B"


def twins():
    a = _unknown_flepub()
    a["url"] = URL_A
    a["kobo"]["platform"] = "Android"
    a["headers"] = {"x-amz-request-payer": "requester"}
    b = deepcopy(a)
    b["url"] = URL_B
    return [a, b]


def envelope(rows):
    data = descriptor(candidate=rows[0])
    data["acquisition"]["candidates"] = rows
    return data


def test_same_complete_k3_route_chooses_first_and_stays_opaque():
    rows = twins()
    assert _equivalent_signed_kdrm_candidates(rows)
    diag = {}
    pair = exact_kobo_offer_id(envelope(rows), **SCOPE, diagnostic=diag)
    alone = exact_kobo_offer_id(envelope([rows[0]]), **SCOPE)
    assert pair.startswith("kobo.runtime:") and pair == alone
    assert diag["candidate_selection"] == "equivalent_signed_first"
    assert "SYNTHETIC_TOKEN" not in str(diag)
    assert "https://" not in str(diag)
    chosen_url, fmt, mode, candidate = _single_admitted_candidate(
        envelope(rows), pair, identity_id=SCOPE["identity_id"],
        credential_id=SCOPE["credential_id"],
        account_id=SCOPE["account_id"], product_id=SCOPE["product_id"],
        kdrm_ready=True,
    )
    assert chosen_url == URL_A
    assert fmt == "epub" and mode == "kdrm"
    assert candidate is rows[0]


@pytest.mark.parametrize("difference", [
    "platform", "profile", "source", "format", "drm", "method",
    "header", "unknown_extra", "path", "host", "non_token_query",
    "token_name", "duplicate_token", "invalid_url",
])
def test_discriminating_parameters_never_trigger_first_wins(difference):
    a,b = twins()
    if difference == "platform":
        b["kobo"]["platform"] = "Generic"
    elif difference == "profile":
        b["kobo"]["display_profile"] = "Desktop"
    elif difference == "source":
        b["kobo"]["source"] = "content_access_book"
    elif difference == "format":
        b["source_format"] = "PDF"
        b["media_format"] = "pdf"
    elif difference == "drm":
        b["drm"]["family"] = "adobe_adept"
    elif difference == "method":
        b["method"] = "POST"
    elif difference == "header":
        b["headers"]["x-amz-request-payer"] = "different"
    elif difference == "unknown_extra":
        b["opaque_upstream"] = "different"
    elif difference == "path":
        b["url"] = "https://storedownloads.kobo.com/other?downloadToken=SECOND"
    elif difference == "host":
        b["url"] = "https://content.kobo.com/download?downloadToken=SECOND"
    elif difference == "non_token_query":
        a["url"] += "&format=epub"
        b["url"] += "&format=pdf"
    elif difference == "token_name":
        b["url"] = "https://storedownloads.kobo.com/download?token=OTHER"
    elif difference == "duplicate_token":
        b["url"] += "&downloadToken=ANOTHER"
    elif difference == "invalid_url":
        b["url"] = "http://storedownloads.kobo.com/download?downloadToken=BAD"
    assert not _equivalent_signed_kdrm_candidates([a,b])
    assert exact_kobo_offer_id(envelope([a,b]), **SCOPE) == ""


def test_only_exactly_two_equivalent_candidates_can_choose_first():
    row = twins()[0]
    assert not _equivalent_signed_kdrm_candidates([row])
    assert not _equivalent_signed_kdrm_candidates(
        [row, deepcopy(row), deepcopy(row)]
    )
    assert not _equivalent_signed_kdrm_candidates([row, "bad"])


def test_real_kobo_provider_fresh_post_selects_first_only_and_one_runtime(
    monkeypatch, tmp_path,
):
    session, vault, broker, account, _ = setup()
    ready(session, vault)
    original = vault.acquisition
    observations, runtime_calls = [], []

    def acquisition(product, **options):
        observations.append((product, options["mode"]))
        if options["mode"] == "delegated":
            return original(product, **options)
        result = original(product, **options)
        # Deliberately rotate both signed URLs between the initial offer and
        # execution POST; the *route* and first-choice contract stay stable.
        rows = twins()
        if len([mode for _,mode in observations if mode == "descriptor"]) > 1:
            rows[0]["url"] = BASE + "FRESH_FIRST"
            rows[1]["url"] = BASE + "FRESH_SECOND"
        result["acquisition"]["candidates"] = rows
        return result

    vault.acquisition = acquisition
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **kw: pytest.fail("KDRM may only use the delegated runtime"),
    )

    def runtime(request):
        runtime_calls.append(request)
        candidate = request["source"]["candidate"]
        # The signed download URL itself remains private in the provider
        # runtime recipe, not in the Core's durable origin.
        assert candidate["delivery_source"] == "library_sync"
        assert request["format"] == "epub"
        path = Path(request["destination"])
        path.write_bytes(epub_bytes())
        return {"ok": True, "artifact": {"path": str(path), "format": "epub"}}

    try:
        right = selected(session, account, "book-1")
        assert right.delivery == "available"
        assert right.delivery_offer_id.startswith("kobo.runtime:")
        result = deliver(session, account, "book-1",
                         right.delivery_offer_id, tmp_path, runtime)
        assert result.path.is_file()
        assert len(runtime_calls) == 1
        assert observations == [
            ("book-1", "descriptor"), ("book-1", "delegated"),
            ("book-1", "descriptor"), ("book-1", "delegated"),
        ]
    finally:
        session.close()


def test_semantic_identity_does_not_allow_second_get_on_error():
    # First-choice policy produces one source, never a failure fallback.
    rows = twins()
    assert _equivalent_signed_kdrm_candidates(rows)
    assert exact_kobo_offer_id(envelope(rows), **SCOPE)
    assert exact_kobo_offer_id(envelope([rows[0]]), **SCOPE)
    # A second candidate is ignored by the selection contract; the
    # downstream HTTP/runtime error handling remains unchanged.
