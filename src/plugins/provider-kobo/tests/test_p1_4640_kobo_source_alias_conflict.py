"""#4640: root/nested Kobo source divergence must fail closed.

All synthetic descriptors. Neither KVault nor live Kobo are invoked.
"""
from copy import deepcopy
import logging

import pytest

from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from cifereca_kobo_provider_kdrm import scoped_kdrm_source
from tests.test_p0_3252_kdrm_canonical_runtime import (
    delegated, kdrm_candidate,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    descriptor, setup,
)


SCOPE = dict(
    identity_id="identity-A", credential_id="synthetic-card",
    account_id="alice", product_id="book-1",
    kdrm_runtime_ready=True,
)


@pytest.mark.parametrize(("root", "nested"), [
    ("library_sync", "content_access_book"),
    ("content_access_book", "library_sync"),
    ("LIBRARY_SYNC", "library_sync"),
    (None, "library_sync"),
])
def test_conflicting_source_aliases_refuse_entire_kdrm_offer_and_recipe(
    root, nested,
):
    row = kdrm_candidate("book-1")
    row["source"] = root
    row["kobo"]["source"] = nested
    diagnosis = {}
    assert exact_kobo_offer_id(
        descriptor(candidate=row), **SCOPE, diagnostic=diagnosis,
    ) == ""
    assert diagnosis["qualification_reason"] == "candidate_source_conflict"
    assert "eligible_candidate_count" not in diagnosis
    assert "candidate_rejection_counts" not in diagnosis
    assert scoped_kdrm_source(
        delegated("book-1"), row,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
        kdrm_delegated_ready=True,
    ) is None
    for secret in ("https://", "token=", "PRIVATE"):
        assert secret not in str(diagnosis)


@pytest.mark.parametrize("reverse", [False, True])
def test_k1_conflicting_sibling_cannot_upgrade_other_valid_candidate(reverse):
    payload = descriptor()
    valid = payload["acquisition"]["candidates"][0]
    inconsistent = deepcopy(valid)
    inconsistent["source"] = "content_access_book"
    inconsistent["kobo"]["source"] = "library_sync"
    inconsistent["url"] = "https://cdn.kobo.com/k1?token=PRIVATE_K1"
    rows = [valid, inconsistent]
    payload["acquisition"]["candidates"] = rows[::-1] if reverse else rows
    diagnosis = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=diagnosis) == ""
    assert diagnosis["qualification_reason"] == "candidate_source_conflict"
    assert "eligible_candidate_count" not in diagnosis
    assert "candidate_shape_counts" not in diagnosis
    assert "PRIVATE_K1" not in str(diagnosis)


def test_matching_alias_and_absent_alias_preserve_exact_kdrm_offer_and_recipe():
    candidate = kdrm_candidate("book-1")
    baseline = exact_kobo_offer_id(descriptor(candidate=candidate), **SCOPE)
    assert baseline.startswith("kobo.runtime:")
    candidate["source"] = "library_sync"
    observed = {}
    assert exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE, diagnostic=observed,
    ) == baseline
    assert "qualification_reason" not in observed
    source = scoped_kdrm_source(
        delegated("book-1"), candidate,
        product_id="book-1", account_id="alice",
        identity_id="identity-A", credential_id="synthetic-card",
    )
    assert source is not None
    assert source["candidate"]["delivery_source"] == "library_sync"


def test_k2_unknown_flepub_stays_unqualified_even_with_matching_alias():
    candidate = kdrm_candidate("book-1")
    candidate["source"] = "library_sync"
    candidate["media_format"] = "unknown"
    candidate["source_format"] = "FLEPUB"
    diagnosis = {}
    assert exact_kobo_offer_id(
        descriptor(candidate=candidate), **SCOPE, diagnostic=diagnosis,
    ) == ""
    assert diagnosis["qualification_reason"] == "kdrm_format_pair_unqualified"
    assert diagnosis["candidate_kvault_flepub_contract"] == "media_unknown"


def test_provider_rejects_source_conflict_without_delegated_or_content_get(caplog):
    session, vault, _broker, account, posts = setup()
    candidate = kdrm_candidate("book-1")
    candidate["source"] = "library_sync"
    candidate["kobo"]["source"] = "content_access_book"
    candidate["url"] = "https://cdn.kobo.com/forbidden?token=PRIVATE_K1"
    vault.acquisition = lambda product_id, **kwargs: (
        posts.append((product_id, kwargs))
        or descriptor(product=product_id, candidate=candidate)
    )
    try:
        with caplog.at_level(logging.INFO, logger="cifereca"):
            snapshot = session.rights_delivery_offer_snapshot(
                account, "book-1", explicit_user_action=True,
            )
        assert snapshot.rights[0].delivery == "unknown"
        assert snapshot.rights[0].delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        assert vault.calls == 1
        events = [
            row.getMessage() for row in caplog.records
            if "EVENT kobo selected_offer" in row.getMessage()
        ]
        assert len(events) == 1
        assert "qualification_reason=candidate_source_conflict" in events[0]
        assert "delegated_resolver_called=False" in events[0]
        assert "PRIVATE_K1" not in events[0]
        assert "https://" not in events[0]
    finally:
        session.close()
