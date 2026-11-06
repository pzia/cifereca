"""#4640: closed shape histogram for K1/K3, no signed URL or content access."""
from cifereca_kobo_delivery_offer import exact_kobo_offer_id
from tests.test_p1_3252_kobo_selected_delivery_offer import descriptor

SCOPE = dict(identity_id="identity-A", credential_id="synthetic-card",
             account_id="alice", product_id="book-1")


def test_multi_candidate_unknown_values_do_not_expose_secrets_or_pick_offer():
    payload = descriptor()
    first = payload["acquisition"]["candidates"][0]
    second = dict(first)
    second.update(
        media_format="epub", source_format="epub",
        source="other-account-private", handling="http_plain",
        url="https://cdn.kobo.com/book-2?token=PRIVATE-SECRET",
        private_metadata="private-token-VERY-SECRET",
        future_format_label="https://signed.example.org/bearer-secret",
    )
    payload["acquisition"]["candidates"].append(second)
    seen = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=seen) == ""
    assert seen["candidate_selection"] == "multiple_eligible_candidates"
    assert seen["candidate_shape_counts"] == {
        "content_access_book:epub:missing:http_plain": 1,
        "other:epub:epub:http_plain": 1,
    }
    trace = str(seen)
    for secret in ("VERY-SECRET", "PRIVATE-SECRET", "bearer-secret",
                   "other-account-private", "cdn.kobo.com"):
        assert secret not in trace


def test_foreign_candidate_scope_conflict_does_not_publish_partial_shapes():
    payload = descriptor()
    payload["acquisition"]["candidates"].append({
        **payload["acquisition"]["candidates"][0], "identity_id": "other-account",
    })
    seen = {}
    assert exact_kobo_offer_id(payload, **SCOPE, diagnostic=seen) == ""
    assert seen == {"qualification_reason": "candidate_scope_conflict"}
