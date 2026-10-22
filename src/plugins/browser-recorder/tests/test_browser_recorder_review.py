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


@pytest.mark.parametrize("mime,body", [
    ("application/json", json.dumps({"email": "reader@example.org", "uuid": UUID})),
    ("application/xml", f"<email>reader@example.org</email><uuid>{UUID}</uuid>"),
    ("text/html", f'<input name="email" value="reader@example.org"><p>{UUID}</p>'),
    ("application/x-www-form-urlencoded", "email=reader%40example.org&uuid=" + UUID),
])
def test_occurrences_count_source_values_once_across_overlapping_detectors(mime, body):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", ORIGIN + "/catalog",
        request_headers={"content-type": mime}, request_body=body,
    ))
    review = IdentifierReview(trace)
    rows = _by_value(review)
    assert rows["reader@example.org"]["occurrences"] == 1
    assert rows[UUID]["occurrences"] == 1
    review.destroy()
    trace.destroy()


def test_occurrences_include_repeated_json_values_but_not_keys_or_numeric_substrings():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", ORIGIN + "/catalog?ProductId=214",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({
            "ProductId": 214, "other": 9214, "214": "unrelated",
            "email": "reader@example.org",
            "description": "Contact reader@example.org twice reader@example.org",
        }),
    ))
    review = IdentifierReview(trace)
    rows = _by_value(review)
    assert rows["214"]["occurrences"] == 2
    assert rows["reader@example.org"]["occurrences"] == 3
    review.destroy()
    trace.destroy()


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


def test_html_password_hidden_token_and_bearer_text_never_survive_preview():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/login?BookId=214",
        response_body=(
            '<form><input type="password" name="password" '
            'value="PRIVATE_FORM_PASSWORD"/>'
            '<input type="hidden" name="access_token" '
            'value="PRIVATE_HIDDEN_TOKEN"/>'
            '</form>'
            '<pre>Authorization: Bearer PrivateBearerValue12345</pre>'
        ),
        response_headers={"content-type": "text/html"},
    ))
    review = IdentifierReview(trace)
    candidates = _by_value(review)
    assert "PRIVATE_FORM_PASSWORD" not in candidates
    assert "PRIVATE_HIDDEN_TOKEN" not in candidates
    assert "PrivateBearerValue12345" not in candidates
    for entry in review.candidates():
        review.decide(entry.key, "keep")
    result = json.dumps(review.preview())
    assert "PRIVATE_FORM_PASSWORD" not in result
    assert "PRIVATE_HIDDEN_TOKEN" not in result
    assert "PrivateBearerValue12345" not in result
    assert result.count("[SECRET]") >= 3
    review.destroy()
    trace.destroy()


def test_mandatory_secret_inside_alphanumeric_payload_is_never_previewed():
    """Secret values are excluded even when wrapped in unrelated characters.

    Ordinary approved identifiers still use whole-value boundaries so an
    unrelated identifier containing their digits is not rewritten.
    """
    credential = "FICTIONAL_SecretToken_ABC9"
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api?BookId=214",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({
            "access_token": credential,
            "note": "before" + credential + "after",
            "bookId": "214",
            "unrelated": "1214",
        }),
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({
            "message": "left" + credential + "right",
            "orderId": "214",
            "otherOrder": "1214",
        }),
    ))
    review = IdentifierReview(trace)
    try:
        assert credential not in {row["value"] for row in review.local_rows()}
        assert any(row["value"] == "214" for row in review.local_rows())
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        output = review.preview()
        serialized = json.dumps(output)
        assert credential not in serialized
        assert "before" + credential not in serialized
        assert "left" + credential not in serialized
        assert "before[SECRET]after" in serialized
        assert "left[SECRET]right" in serialized
        assert "1214" in serialized
        assert "PSEUDO_" in serialized
    finally:
        review.destroy()
        trace.destroy()


def test_mandatory_secret_scrubs_unbounded_substring_variants():
    from browser_recorder_review import _replace
    from urllib.parse import quote

    secret = "FICTIONAL a+b@example.org"
    encoded = quote(secret, safe="")
    output = _replace(
        "xx" + secret + "yy " + "zz" + encoded + "ww",
        {secret: "[SECRET]", "214": "PSEUDO_I0001"},
        secret_values=frozenset((secret,)),
    )
    assert secret not in output
    assert encoded not in output
    assert output == "xx[SECRET]yy zz[SECRET]ww"
    assert _replace("1214 214", {"214": "PSEUDO_I0001"}) == "1214 PSEUDO_I0001"



def test_phone_and_postal_identifiers_are_decided_individually():
    """Contact coordinates are not public just because keys lack 'Id'."""
    from browser_recorder_zip import prepare_reviewed_zip

    telephone = "33612345678"
    postal = "13000"
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/books?telephone=" + telephone,
        status=200,
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({
            "phoneNumber": int(telephone),
            "postalCode": int(postal),
            "year": 2026,
            "price": 9.5,
        }),
    ))
    review = IdentifierReview(trace)
    try:
        rows = _by_value(review)
        assert set(rows) == {telephone, postal}
        assert rows[telephone]["occurrences"] == 2
        assert rows[postal]["kind"] == "identifier"
        with pytest.raises(RecorderError, match="review_pending"):
            review.preview()

        review.decide(rows[telephone]["key"], "anonymize")
        review.decide(rows[postal]["key"], "keep")
        preview = json.dumps(review.preview())
        assert telephone not in preview
        assert postal in preview
        assert "PSEUDO_" in preview

        bundle = prepare_reviewed_zip(review).preview()
        serialized = json.dumps(bundle)
        assert telephone not in serialized
        body = next(row["text"] for row in bundle["members"]
                    if row["name"] == "bodies/0001-response.txt")
        projected = json.loads(body)
        assert projected["phoneNumber"].startswith("PSEUDO_")
        assert projected["postalCode"] == int(postal)  # Explicit, warned KEEP.
        assert projected["year"] == 2026 and projected["price"] == 9.5
    finally:
        review.destroy()
        trace.destroy()


@pytest.mark.parametrize("field", [
    "phone", "phoneNumber", "telephone", "mobileNum",
    "postalCode", "codePostal", "zipCode", "code_postal",
])
def test_contact_field_spellings_are_reviewable(field):
    from browser_recorder_review import _category
    assert _category(field, "13000") == "identifier"
    assert _category("year", "2026") is None


@pytest.mark.parametrize("pairs", [
    [("alpha", "PSEUDO_I0001"), ("I0001", "PSEUDO_I0002")],
    [("I0001", "PSEUDO_I0002"), ("alpha", "PSEUDO_I0001")],
])
def test_replacements_never_rescan_generated_pseudonyms(pairs):
    from browser_recorder_review import _replace

    assert _replace("alpha I0001 alpha", dict(pairs)) == (
        "PSEUDO_I0001 PSEUDO_I0002 PSEUDO_I0001"
    )


def test_secret_markers_are_not_reinterpreted_as_ordinary_identifiers():
    from browser_recorder_review import _replace

    assert _replace(
        "fictional-password SECRET xxfictional-passwordyy",
        {"fictional-password": "[SECRET]", "SECRET": "PSEUDO_I0002"},
        secret_values=frozenset(("fictional-password",)),
    ) == "[SECRET] PSEUDO_I0002 xx[SECRET]yy"
    # Mandatory substring scrubbing still beats identifier boundaries.
    assert _replace("1214 214", {"214": "[SECRET]"},
                    secret_values=frozenset(("214",))) == "1[SECRET] [SECRET]"


def test_overlapping_encoded_identifiers_choose_longest_original_occurrence():
    from browser_recorder_review import _replace

    mapping = {"reader+alpha@example.org": "PSEUDO_I0001",
               "alpha": "PSEUDO_I0002", "I0001": "PSEUDO_I0003"}
    source = quote("reader+alpha@example.org", safe="") + " alpha I0001"
    assert _replace(source, mapping) == "PSEUDO_I0001 PSEUDO_I0002 PSEUDO_I0003"


def test_preview_and_saved_zip_preserve_non_cascading_correlations(tmp_path):
    from urllib.parse import parse_qs, urlsplit
    from zipfile import ZipFile
    from browser_recorder_zip import prepare_reviewed_zip

    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [PrivateExchange(
        1, "POST", url=ORIGIN + "/books?accountId=alpha&bookId=I0001",
        request_body=json.dumps({"accountId": "alpha", "bookId": "I0001"}),
        request_headers={"content-type": "application/json"},
        response_body=json.dumps({"accountId": "alpha", "bookId": "I0001"}),
        response_headers={"content-type": "application/json"}, status=200,
    )]
    review = IdentifierReview(trace)
    expected = {}
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
        expected[candidate.value] = "PSEUDO_" + candidate.key
    assert set(expected) == {"alpha", "I0001"}
    preview = review.preview()["exchanges"][0]
    projected = json.loads(preview["request_body"])
    assert projected == {"accountId": expected["alpha"], "bookId": expected["I0001"]}
    plan = prepare_reviewed_zip(review)
    assert not list(tmp_path.iterdir())
    plan.preview()
    archive_path = tmp_path / plan.save(tmp_path, user_confirmed=True)
    with ZipFile(archive_path) as archive:
        row = json.loads(archive.read("exchanges/0001.json"))
        query = parse_qs(urlsplit(row["url"]).query)
        for direction in ("request", "response"):
            body = json.loads(archive.read(row["bodies"][direction]["file"]))
            assert body == projected
            assert query == {key: [value] for key, value in body.items()}
        assert b"PSEUDO_PSEUDO_" not in b"\n".join(
            archive.read(name) for name in archive.namelist()
        )
    # Reversing one decision recomputes from the original trace only.
    candidate = next(c for c in review.candidates() if c.value == "alpha")
    review.decide(candidate.key, "keep")
    restored = json.loads(review.preview()["exchanges"][0]["request_body"])
    assert restored == {"accountId": "alpha", "bookId": expected["I0001"]}
    review.destroy()
    trace.destroy()
