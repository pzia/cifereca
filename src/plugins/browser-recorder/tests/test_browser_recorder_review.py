"""#2529: fictional values only, no network or export of unreviewed traffic."""
from __future__ import annotations

import json
from urllib.parse import quote
import pytest

from browser_recorder import RecorderError
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review import IdentifierReview


ORIGIN = "https://books.example.org"
ACCOUNT = "acct-ABC7"
PRODUCT = "214"
UUID = "550e8400-e29b-41d4-a716-446655440000"
PASSWORD = "FICTIONAL-PASSWORD-SHOULD-NEVER-EXPORT"
TOKEN = "FICTIONAL-ACCESS-TOKEN-SHOULD-NEVER-EXPORT"


def fixture():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [
        PrivateExchange(
            1, "POST",
            url=ORIGIN + "/account?AccountId=" + ACCOUNT + "&ProductId=" + PRODUCT,
            response_body=json.dumps({
                "accountId": ACCOUNT, "ProductId": PRODUCT,
                "access_token": TOKEN, "password": PASSWORD,
            }),
            response_headers={"content-type": "application/json"},
            response_body_state="captured",
        ),
        PrivateExchange(
            2, "GET",
            url=ORIGIN + "/book?ProductId=214&AccountId=" + ACCOUNT,
            response_body=json.dumps({"ProductId": PRODUCT, "user_id": ACCOUNT}),
            response_headers={"content-type": "application/json"},
            response_body_state="captured",
        ),
        PrivateExchange(
            3, "GET",
            url=ORIGIN + "/book?ProductId=214&uuid=" + UUID,
            response_body=json.dumps({"uuid": UUID, "ProductId": PRODUCT}),
            response_headers={"content-type": "application/json"},
            response_body_state="captured",
        ),
    ]
    return trace


def _by_value(review):
    return {row["value"]: row for row in review.local_rows()}


def test_independent_decisions_keep_product_anonymize_account_exclude_uuid():
    trace = fixture()
    review = IdentifierReview(trace)
    rows = _by_value(review)
    assert {ACCOUNT, PRODUCT, UUID}.issubset(rows)
    assert TOKEN not in rows and PASSWORD not in rows
    assert rows[ACCOUNT]["occurrences"] >= 2
    with pytest.raises(RecorderError, match="review_pending"):
        review.preview()
    for candidate in review.candidates():
        value = candidate.value
        action = ("anonymize" if value == ACCOUNT else
                  "exclude" if value == UUID else "keep")
        review.decide(candidate.key, action)
    projection = review.preview()
    assert projection["approved_for_export"] is False
    assert projection["dropped_exchanges"] == 1
    assert [e["exchange_id"] for e in projection["exchanges"]] == [1, 2]
    encoded = json.dumps(projection)
    assert PRODUCT in encoded
    assert ACCOUNT not in encoded
    assert UUID not in encoded
    assert PASSWORD not in encoded and TOKEN not in encoded
    assert "PSEUDO_" in encoded
    assert encoded.count("PSEUDO_") >= 4
    assert projection["secrets_replaced"] >= 2
    assert "FICTIONAL-ACCESS" not in repr(review)
    review.destroy()
    assert trace.exchanges  # trace owner manages raw data separately
    with pytest.raises(RecorderError, match="review_closed"):
        review.preview()
    trace.destroy()


def test_encoded_occurrences_share_one_decision_and_preview_recomputes():
    trace = PrivateHttpTrace((ORIGIN,))
    value = "reader+test@example.org"
    trace.exchanges.append(PrivateExchange(
        1, "POST",
        url=ORIGIN + "/search?email=" + quote(value, safe=""),
        request_body=json.dumps({"email": value}),
        request_headers={"content-type": "application/json"},
        response_body="<email>" + value + "</email>",
        response_headers={"content-type": "application/xml"},
    ))
    review = IdentifierReview(trace)
    row = _by_value(review)[value]
    with pytest.raises(RecorderError, match="invalid_decision"):
        review.decide(row["key"], "keep-everything")
    review.decide(row["key"], "anonymize")
    anonymized = json.dumps(review.preview())
    assert value not in anonymized and quote(value, safe="") not in anonymized
    assert "PSEUDO_" in anonymized
    review.decide(row["key"], "keep")
    restored = json.dumps(review.preview())
    assert value in restored
    # Export warning is mandatory when the user explicitly retains an ID.
    assert review.preview()["approved_for_export"] is False
    review.destroy()
    trace.destroy()


def test_mandatory_secrets_cannot_be_kept_and_are_scrubbed_globally():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST",
        url=ORIGIN + "/api?access_token=" + TOKEN + "&book_id=214",
        request_body=json.dumps({"book_id": "214", "password": PASSWORD,
                                 "note": "password=" + PASSWORD,
                                 "token_repeat": TOKEN}),
        request_headers={"content-type": "application/json"},
        response_body=json.dumps({"book_id": 214, "message": "token=" + TOKEN}),
        response_headers={"content-type": "application/json"},
    ))
    review = IdentifierReview(trace)
    assert TOKEN not in _by_value(review) and PASSWORD not in _by_value(review)
    for candidate in review.candidates():
        review.decide(candidate.key, "keep")
    projection = review.preview()
    serialized = json.dumps(projection)
    assert TOKEN not in serialized and PASSWORD not in serialized
    assert "[SECRET]" in serialized
    assert "book_id" in serialized
    review.destroy()
    trace.destroy()


def test_excluding_one_value_removes_all_matching_exchanges():
    trace = fixture()
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "exclude" if candidate.value == PRODUCT else "keep")
    result = review.preview()
    assert result["dropped_exchanges"] == 3
    assert result["exchanges"] == []
    review.destroy()
    trace.destroy()


def test_explicit_budgets_stop_excessive_value_cardinality():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/search?" +
        "&".join("ProductId=" + str(i) for i in range(260)),
    ))
    with pytest.raises(RecorderError, match="review_limit"):
        IdentifierReview(trace)
    trace.destroy()


def test_no_mutation_or_disk_export_during_review(tmp_path):
    trace = fixture()
    review = IdentifierReview(trace)
    assert list(tmp_path.iterdir()) == []
    assert "FICTIONAL" not in repr(review)
    assert not any("PRIVATE" in repr(x) for x in review.candidates())
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    review.preview()
    assert list(tmp_path.iterdir()) == []
    review.destroy()
    trace.destroy()
