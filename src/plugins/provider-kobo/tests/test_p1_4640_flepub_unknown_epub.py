"""#4640 K2/K3: owner-approved unknown/FLEPUB KDRM is EPUB only in Cifereca.

All sources and Kobo Vault descriptors are synthetic. No real Kobo GET,
credentials, ADEPT or canonical publication; Core still owns final receipt.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from cifereca.plugin_api import HTTPX_TRANSPORT, ProviderCapabilityError
from cifereca_kobo_delivery_offer import (
    exact_kobo_offer_id, effective_kobo_media_format,
)
from cifereca_kobo_provider_kdrm import scoped_kdrm_source
from cifereca_kobo_selected_candidate import _single_admitted_candidate
from tests.test_p0_3252_kdrm_canonical_runtime import (
    kdrm_candidate, delegated, ready, selected, deliver,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor, setup
from tests.test_p1_3252_kobo_selected_candidate import epub_bytes


SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1", kdrm_runtime_ready=True,
)
RUNTIME_SCOPE = dict(
    product_id="book-1", account_id="alice",
    identity_id="identity-A", credential_id="synthetic-card",
)


def k2(media="unknown", source="FLEPUB"):
    row = kdrm_candidate("book-1")
    row["media_format"] = media
    row["source_format"] = source
    return row


@pytest.mark.parametrize("media", ["unknown", "UNKNOWN", " Unknown "])
@pytest.mark.parametrize("source", ["flepub", "FLEPUB", " FlePub "])
def test_k2_raw_unknown_flepub_has_exact_epub_offer_and_runtime_recipe(media, source):
    observed = k2(media, source)
    before = deepcopy(observed)
    normal = k2("epub", "FLEPUB")
    canonical = exact_kobo_offer_id(
        descriptor(candidate=normal), **SCOPE,
    )
    diagnostic = {}
    offer = exact_kobo_offer_id(
        descriptor(candidate=observed), **SCOPE, diagnostic=diagnostic,
    )
    assert canonical.startswith("kobo.runtime:") and offer == canonical
    assert effective_kobo_media_format(observed) == "epub"
    assert diagnostic["candidate_media_format"] == "unknown"
    assert diagnostic["candidate_source_format"] == "flepub"
    url, file_format, delivery_mode, chosen = _single_admitted_candidate(
        descriptor(candidate=observed), offer,
        identity_id="identity-A", credential_id="synthetic-card",
        account_id="alice", product_id="book-1", kdrm_ready=True,
    )
    assert (file_format, delivery_mode) == ("epub", "kdrm")
    assert url == observed["url"] and chosen is observed or (
        chosen == observed and url == observed["url"]
    )
    source_request = scoped_kdrm_source(
        delegated("book-1"), observed, **RUNTIME_SCOPE,
    )
    assert source_request is not None
    assert source_request["candidate"]["media_format"] == "epub"
    assert source_request["candidate"]["source_format"].casefold() == "flepub"
    assert "unknown" not in str(source_request["candidate"])
    assert observed == before  # descriptor is never mutated


@pytest.mark.parametrize(("media", "source", "handling", "raw"), [
    (None, "FLEPUB", "kobo_kdrm", "KDRM"),
    ("", "FLEPUB", "kobo_kdrm", "KDRM"),
    ("other", "FLEPUB", "kobo_kdrm", "KDRM"),
    ("FLEPUB", "FLEPUB", "kobo_kdrm", "KDRM"),
    ("kepub", "FLEPUB", "kobo_kdrm", "KDRM"),
    ("unknown", "EPUB", "kobo_kdrm", "KDRM"),
    ("unknown", "FLEPUB_SAMPLE", "kobo_kdrm", "KDRM"),
    ("unknown", None, "kobo_kdrm", "KDRM"),
    ("unknown", "FLEPUB", "http_plain", "None"),
    ("unknown", "FLEPUB", "kobo_kdrm", "UnsupportedDRM"),
])
def test_k2_no_inference_from_incomplete_media_drm_or_non_flepub(
    media, source, handling, raw,
):
    row = k2(media, source)
    row["handling"] = handling
    row["drm"] = {
        "raw_type": raw,
        "family": "none" if raw == "None" else "kobo_kdrm",
        "protected": False if raw == "None" else True,
    }
    diagnostic = {}
    assert effective_kobo_media_format(row) != "epub"
    assert exact_kobo_offer_id(
        descriptor(candidate=row), **SCOPE, diagnostic=diagnostic,
    ) == ""
    assert scoped_kdrm_source(delegated("book-1"), row, **RUNTIME_SCOPE) is None


@pytest.mark.parametrize("reverse", [False, True])
def test_k3_two_explicit_unknown_flepub_candidates_still_ambiguous(reverse):
    first = k2()
    second = deepcopy(first)
    second["url"] = second["url"].replace(".kepub", "-second.kepub")
    candidates = [first, second]
    payload = descriptor(candidate=first)
    payload["acquisition"]["candidates"] = candidates[::-1] if reverse else candidates
    diagnostic = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnostic) == ""
    assert diagnostic["eligible_candidate_count"] == 2
    assert diagnostic["candidate_selection"] == "multiple_eligible_candidates"
    assert diagnostic["candidate_route_comparison"] == "same_route_distinct_urls"
    assert diagnostic["candidate_shape_counts"] == {
        "library_sync:unknown:flepub:kobo_kdrm": 2,
    }
    assert "token=" not in str(diagnostic)
    assert "https://" not in str(diagnostic)


def test_k3_valid_and_transport_rejected_still_yields_unique_revalidated_offer():
    first = k2()
    second = deepcopy(first)
    second["url"] = "http://untrusted.invalid/?token=PRIVATE"
    payload = descriptor(candidate=first)
    payload["acquisition"]["candidates"].append(second)
    expected = exact_kobo_offer_id(descriptor(candidate=first), **SCOPE)
    assert exact_kobo_offer_id(payload, **SCOPE) == expected
    selected_tuple = _single_admitted_candidate(
        payload, expected, identity_id="identity-A",
        credential_id="synthetic-card", account_id="alice",
        product_id="book-1", kdrm_ready=True,
    )
    assert selected_tuple[1:3] == ("epub", "kdrm")


def test_k3_foreign_sibling_refuses_even_if_another_kdrm_candidate_is_valid():
    first = k2()
    second = deepcopy(first)
    second["identity_id"] = "foreign-identity"
    payload = descriptor(candidate=first)
    payload["acquisition"]["candidates"].append(second)
    diag = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diag) == ""
    assert diag == {"qualification_reason": "candidate_scope_conflict"}


def test_k2_session_real_selected_kdrm_runtime_uses_normalized_epub(tmp_path, monkeypatch):
    session, vault, broker, account, _ = setup()
    modes = ready(session, vault)
    original = vault.acquisition
    descriptor_count = [0]
    observed = []

    def fake_acquisition(product, **kwargs):
        if kwargs.get("mode") == "descriptor":
            descriptor_count[0] += 1
            row = k2("unknown" if descriptor_count[0] == 1 else "epub",
                     "FLEPUB")
            return descriptor(product=product, candidate=row)
        return original(product, **kwargs)

    vault.acquisition = fake_acquisition
    monkeypatch.setattr(
        HTTPX_TRANSPORT, "attested_https_download",
        lambda **_: pytest.fail("KDRM is never a plain HTTP file GET"),
    )
    bytes_ = epub_bytes()

    def runtime(request):
        assert request["format"] == "epub"
        pin = request["source"]["candidate"]
        assert pin["delivery_source"] == "library_sync"
        assert pin["media_format"] == "epub"
        assert pin["source_format"].casefold() == "flepub"
        target = Path(request["destination"])
        target.write_bytes(bytes_)
        observed.append(request["format"])
        return {"ok": True, "artifact": {"path": str(target), "format": "epub"}}

    try:
        offer = selected(session, account, "book-1")
        assert offer.delivery == "available" and offer.delivery_offer_id.startswith(
            "kobo.runtime:"
        )
        assert descriptor_count[0] == 1
        result = deliver(
            session, account, "book-1", offer.delivery_offer_id,
            tmp_path, runtime,
        )
        assert result.path.is_file() and result.path.read_bytes() == bytes_
        assert result.path.suffix == ".epub" and result.selected_offer_id == offer.delivery_offer_id
        assert not result.authorizes_publication
        assert descriptor_count[0] == 2
        assert observed == ["epub"]
        # The only additional provider mode is the existing in-memory delegated
        # resolver; no actual Kobo Vault HTTP, account or network is exercised.
        assert ("book-1", "delegated") in modes
    finally:
        session.close()
