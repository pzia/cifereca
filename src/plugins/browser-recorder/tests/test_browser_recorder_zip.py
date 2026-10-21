"""P1 #2530: only fictional HTTP observations and explicit local ZIP writing."""
from __future__ import annotations

from hashlib import sha256
import json
import os
import stat
from urllib.parse import urlencode
from zipfile import ZipFile

import pytest

from browser_recorder import RecorderError
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review import IdentifierReview
from browser_recorder_zip import prepare_reviewed_zip, ZIP_SCHEMA


ORIGIN = "https://books.example.org"
TOKEN = "FICTIONAL-NOT-A-REAL-ACCESS-TOKEN"
ACCOUNT = "acct-ABC7"
UUID = "550e8400-e29b-41d4-a716-446655440000"


def setup():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [
        PrivateExchange(
            1, "POST", url=ORIGIN +
            "/api/v3/books?BookId=214&AccountId=" + ACCOUNT +
            "&access_token=" + TOKEN + "&opaque=PRIVATE_POSSIBLE_SECRET",
            request_body=json.dumps({
                "bookId": 214, "AccountId": ACCOUNT, "access_token": TOKEN,
                "query": "free text not individually reviewed",
                "nested": {"sku": 214, "price": 9.5},
            }),
            request_headers={"content-type": "application/json"},
            response_body=(
                '<html><div class="book card unusual-user-ABC7">'
                '<input type="password" value="FORM_PRIVATE"/>'
                '<p>PRIVATE_USER_NAME</p></div></html>'
            ),
            response_headers={"content-type": "text/html"},
            status=302, redirect_to="/library?BookId=214&session=" + TOKEN,
        ),
        PrivateExchange(
            2, "GET", url=ORIGIN + "/library/books?BookId=214&AccountId=" + ACCOUNT,
            response_body=json.dumps({
                "bookId": 214, "AccountId": ACCOUNT, "title": "PRIVATE_BOOK_TITLE",
                "result": ["ok", False, 200],
            }),
            response_headers={"content-type": "application/json"},
            status=200,
        ),
        PrivateExchange(
            3, "GET", url=ORIGIN + "/book?uuid=" + UUID,
            response_body=json.dumps({"uuid": UUID}),
            response_headers={"content-type": "application/json"},
            status=200,
        ),
    ]
    review = IdentifierReview(trace)
    assert {"214", ACCOUNT, UUID}.issubset(
        set(row["value"] for row in review.local_rows()),
    )
    for candidate in review.candidates():
        review.decide(candidate.key,
                      "keep" if candidate.value == "214" else
                      "exclude" if candidate.value == UUID else "anonymize")
    return trace, review


def test_exact_reviewed_zip_is_bounded_without_auto_write(tmp_path):
    trace, review = setup()
    plan = prepare_reviewed_zip(review)
    assert not list(tmp_path.iterdir())
    with pytest.raises(RecorderError, match="export_unconfirmed"):
        plan.save(tmp_path, user_confirmed=True)
    summary = plan.preview()
    assert summary["schema"] == ZIP_SCHEMA
    assert summary["bytes"] < 2_000_000
    assert "exchanges/0001.json" in [m["name"] for m in summary["members"]]
    assert "exchanges/0003.json" not in [m["name"] for m in summary["members"]]
    assert "LLM_INSTRUCTIONS.md" in [m["name"] for m in summary["members"]]
    assert summary["sha256"] and len(summary["sha256"]) == 64
    preview_text = json.dumps(summary, sort_keys=True)
    for private in (TOKEN, ACCOUNT, UUID, "PRIVATE_POSSIBLE_SECRET",
                    "FORM_PRIVATE", "PRIVATE_USER_NAME", "PRIVATE_BOOK_TITLE"):
        assert private not in preview_text
    assert "PSEUDO_" in preview_text
    assert "BookId" in preview_text
    assert "214" in preview_text
    name = plan.save(tmp_path, user_confirmed=True)
    dest = tmp_path / name
    assert name.endswith(".zip")
    assert dest.is_file()
    assert not stat.S_IMODE(dest.stat().st_mode) & 0o077
    blob = dest.read_bytes()
    assert sha256(blob).hexdigest() == summary["sha256"]
    with ZipFile(dest) as archive:
        members = archive.namelist()
        assert sorted(members) == [m["name"] for m in summary["members"]]
        assert len(members) == len(set(members))
        assert not any(".." in name or name.startswith("/") for name in members)
        for item in summary["members"]:
            data = archive.read(item["name"])
            assert data.decode("utf-8") == item["text"]
            assert sha256(data).hexdigest() == item["sha256"]
        json_body = json.loads(archive.read("bodies/0001-request.txt"))
        assert json_body["bookId"] == 214
        assert json_body["AccountId"].startswith("PSEUDO_")
        assert json_body["access_token"] == "[SECRET]"
        assert json_body["query"] == "[TEXT]"
        html_body = archive.read("bodies/0001-response.txt").decode()
        assert '<div class="book card">' in html_body
        assert "PRIVATE" not in html_body
        descriptor = json.loads(archive.read("exchanges/0001.json"))
        assert "PRIVATE" not in json.dumps(descriptor)
        assert "[UNREVIEWED]" in descriptor["url"] or "UNREVIEWED" in json.dumps(descriptor)
    review.destroy()
    trace.destroy()


def test_unreviewed_candidates_and_duplicate_exchange_ids_fail_closed():
    trace, review = setup()
    review.decide(next(iter(review.candidates())).key, "anonymize")
    one = next(iter(review.candidates()))
    one.decision = None
    with pytest.raises(RecorderError, match="review_pending"):
        prepare_reviewed_zip(review)
    one.decision = "keep"
    dup = trace.exchanges[0]
    trace.exchanges.insert(1, dup)
    with pytest.raises(RecorderError, match="bundle_duplicate_exchange"):
        prepare_reviewed_zip(review)
    review.destroy()
    trace.destroy()


def test_existing_directory_symlink_refused_even_after_preview(tmp_path):
    trace, review = setup()
    plan = prepare_reviewed_zip(review)
    plan.preview()
    real = tmp_path / "real"
    real.mkdir()
    fake = tmp_path / "symlink"
    fake.symlink_to(real, target_is_directory=True)
    with pytest.raises(RecorderError, match="unsafe_directory"):
        plan.save(fake, user_confirmed=True)
    assert list(real.iterdir()) == []
    review.destroy()
    trace.destroy()


def test_plain_text_binary_data_and_large_fragments_are_not_in_zip(tmp_path):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/library/books#OAUTH_FRAGMENT",
        response_body="PRIVATE_UNREVIEWED_NOTE",
        response_headers={"content-type": "text/plain"},
        status=200,
    ))
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    plan = prepare_reviewed_zip(review)
    visible = json.dumps(plan.preview())
    assert "OAUTH_FRAGMENT" not in visible
    assert "PRIVATE_UNREVIEWED_NOTE" not in visible
    assert "body_omitted" in visible
    assert "bodies/0001-response.txt" not in visible
    assert list(tmp_path.iterdir()) == []
    review.destroy()
    trace.destroy()


@pytest.mark.parametrize(("redirect", "marker"), [
    ("https://thirdparty.example.org/login?code=PRIVATE_OAUTH", "[OUT_OF_SCOPE]"),
    ("http://localhost/secret?code=PRIVATE_OAUTH", "[UNSAFE_TARGET]"),
])
def test_foreign_or_unsafe_redirect_does_not_enter_export(redirect, marker):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/library",
        status=302, redirect_to=redirect,
    ))
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    plan = prepare_reviewed_zip(review)
    preview = json.dumps(plan.preview())
    assert "PRIVATE_OAUTH" not in preview
    assert "thirdparty.example.org" not in preview
    assert marker in preview
    review.destroy()
    trace.destroy()



def test_unapproved_fake_pseudonyms_never_pass_only_by_prefix():
    # An attacker can put a value whose bytes *look* like our pseudonyms
    # into any endpoint. No syntactic prefix can grant export approval.
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/PSEUDO_INOT_APPROVED?opaque=PSEUDO_INOT_APPROVED",
        request_body=json.dumps({"notes": "PSEUDO_INOT_APPROVED"}),
        request_headers={"content-type": "application/json"},
    ))
    review = IdentifierReview(trace)
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize")
    plan = prepare_reviewed_zip(review)
    text = json.dumps(plan.preview())
    assert "PSEUDO_INOT_APPROVED" not in text
    assert "[UNREVIEWED]" in text or "~redacted~" in text
    review.destroy()
    trace.destroy()



def test_write_or_fsync_failure_cleans_incomplete_archive(tmp_path, monkeypatch):
    import browser_recorder_zip as zipmod
    trace, review = setup()
    plan = prepare_reviewed_zip(review)
    plan.preview()

    def disk_error(_fd):
        raise OSError("fictional disk-full scenario")

    monkeypatch.setattr(zipmod.os, "fsync", disk_error)
    target = tmp_path / "exports"
    with pytest.raises(RecorderError, match="save_failed"):
        plan.save(target, user_confirmed=True)
    assert target.is_dir()
    assert list(target.iterdir()) == []  # neither 0-byte nor incomplete ZIP
    review.destroy()
    trace.destroy()


def test_unreviewed_long_numeric_fields_do_not_escape_json_review():
    """An arbitrary JSON field name is not proof a long integer is public."""
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books",
        response_body=json.dumps({
            "opaqueMetric": 33612345678, "unknownCounter": 13000,
            "year": 2026, "price": 9.5, "bookId": 214,
        }),
        response_headers={"content-type": "application/json"}, status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for item in review.candidates():
            review.decide(item.key, "keep" if item.value == "214" else "anonymize")
        plan = prepare_reviewed_zip(review)
        summary = plan.preview()
        text = next(m["text"] for m in summary["members"]
                    if m["name"] == "bodies/0001-response.txt")
        values = json.loads(text)
        assert values["opaqueMetric"] == "[NUMBER]"
        assert values["unknownCounter"] == "[NUMBER]"
        assert values["year"] == 2026
        assert values["price"] == 9.5
        assert values["bookId"] == 214  # expressly reviewed and kept
        for private in ("33612345678", "13000"):
            assert private not in json.dumps(summary)
    finally:
        review.destroy()
        trace.destroy()


def test_non_finite_numeric_json_shape_fails_closed():
    from browser_recorder_zip import _json_shape
    assert _json_shape(float("nan"), name="number") == "[NUMBER]"
    assert _json_shape(float("inf"), name="number") == "[NUMBER]"
    assert _json_shape(10000, name="anonymousCode") == "[NUMBER]"
    assert _json_shape(9999, name="year") == 9999
    assert _json_shape(123456789, name="bookId", approved=frozenset({"123456789"})) == 123456789



def test_zip_technical_headers_never_publish_unreviewed_string_values():
    """A nominally safe header name is not proof its VALUE is public."""
    from browser_recorder_zip import _safe_headers

    headers = {
        "content-type": "application/json; boundary=PRIVATE_MIME_BOUNDARY",
        "accept": "application/json; profile=PRIVATE_PROFILE, */*",
        "retry-after": "42",
        "x-ratelimit-limit": "PRIVATE_ACCOUNT_000123",
        "x-ratelimit-remaining": "7",
        "x-ratelimit-reset": "PRIVATE_RESET",
        "authorization": "Bearer PRIVATE_BEARER",
    }
    assert _safe_headers(headers) == {
        "content-type": "application/json",
        "accept": "application/json, */*",
        "retry-after": "[NUMBER]",
        "x-ratelimit-remaining": "[NUMBER]",
    }
    assert _safe_headers({
        "content-type": "application/vnd.fictional+json",
        "accept": "application/xml; q=0.5, text/html",
        "retry-after": "Wed, 21 Oct 2026 07:28:00 GMT",
        "x-ratelimit-limit": "1000",
    }) == {
        "content-type": "application/*+json",
        "accept": "application/xml, text/html",
        "x-ratelimit-limit": "[NUMBER]",
    }
    assert _safe_headers({"content-type": "text/html; charset=utf-8"}) == {
        "content-type": "text/html",
    }
    assert _safe_headers({"content-type": "application/pdf", "accept": "PRIVATE"}) == {}


def test_actual_zip_member_excludes_private_technical_header_values():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books", status=429,
        response_headers={
            "content-type": "application/json; profile=FICTIONAL_PRIVATE_MIME",
            "retry-after": "123",
            "x-ratelimit-limit": "FICTIONAL_PRIVATE_LIMIT",
            "x-ratelimit-remaining": "3",
        },
        response_body='{"books":[]}',
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        summary = plan.preview()
        member = next(v for v in summary["members"]
                      if v["name"] == "exchanges/0001.json")
        doc = json.loads(member["text"])
        assert doc["response_headers"] == {
            "content-type": "application/json",
            "retry-after": "[NUMBER]",
            "x-ratelimit-remaining": "[NUMBER]",
        }
        combined = json.dumps(summary)
        assert "FICTIONAL_PRIVATE_MIME" not in combined
        assert "FICTIONAL_PRIVATE_LIMIT" not in combined
    finally:
        review.destroy()
        trace.destroy()



def test_manifest_reports_bounded_capture_omissions_without_private_values():
    trace, review = setup()
    trace.omitted["out_of_scope"] = 3
    trace.omitted["binary_skipped"] = 2
    trace.omitted["paused"] = 5
    trace.omitted["unknown_size"] = 1
    try:
        plan = prepare_reviewed_zip(review)
        members = plan.preview()["members"]
        manifest = json.loads(next(member["text"] for member in members
                                   if member["name"] == "manifest.json"))
        assert manifest["capture_omissions"] == {
            "out_of_scope": 3, "limit": 0, "binary_skipped": 2,
            "oversize": 0, "unknown_size": 1, "network_error": 0,
            "paused": 5,
        }
        assert manifest["excluded_exchanges"] == 1
        assert all(type(v) is int for v in manifest["capture_omissions"].values())
        assert ACCOUNT not in json.dumps(manifest)
        assert TOKEN not in json.dumps(manifest)
        # The frozen preview and final ZIP must have identical count bytes.
        preview = plan.preview()
        assert json.loads(next(member["text"] for member in preview["members"]
                               if member["name"] == "manifest.json")) == manifest
    finally:
        review.destroy()
        trace.destroy()


@pytest.mark.parametrize("bad", [-1, "7", True, 10**12])
def test_invalid_capture_omission_counter_fails_closed(bad):
    trace, review = setup()
    try:
        trace.omitted["paused"] = bad
        with pytest.raises(RecorderError, match="bundle_invalid_omissions"):
            prepare_reviewed_zip(review)
    finally:
        review.destroy()
        trace.destroy()



def test_vendor_json_mime_is_coarsened_not_published_in_reviewed_zip():
    """A MIME subtype from an untrusted provider is not public by default."""
    from browser_recorder_zip import _safe_headers

    private = "private-account-123456789"
    headers = {
        "content-type": f"application/vnd.{private}+json; profile=SECRET_PROFILE",
        "accept": f"application/vnd.{private}+xml, application/json",
    }
    assert _safe_headers(headers) == {
        "content-type": "application/*+json",
        "accept": "application/*+xml, application/json",
    }
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books", status=200,
        response_headers=headers,
        mime=f"application/vnd.{private}+json",
        response_body='{"result":"fictional"}',
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        preview = plan.preview()
        descriptor = json.loads(next(row["text"] for row in preview["members"]
                                     if row["name"] == "exchanges/0001.json"))
        assert descriptor["response_headers"]["content-type"] == "application/*+json"
        assert descriptor["mime"] == "application/*+json"
        assert private not in json.dumps(preview)
        assert "SECRET_PROFILE" not in json.dumps(preview)
    finally:
        review.destroy()
        trace.destroy()



@pytest.mark.parametrize(("key", "value"), [
    ("page", "1"), ("count", "12"), ("hint", "123"), ("pin", "007"),
    ("opaque", "99"), ("limit", "42"),
])
def test_short_numeric_query_values_need_explicit_review(key, value):
    """A short decimal may be a private code; size cannot grant consent."""
    from browser_recorder_zip import _safe_url
    url = ORIGIN + "/api/books?" + key + "=" + value
    assert value not in _safe_url(url, frozenset(), frozenset((ORIGIN,)))
    if key == "pin":
        # Mandatory secrets override even an explicit attempt to keep them.
        assert "%5BSECRET%5D" in _safe_url(url, frozenset(), frozenset((ORIGIN,)))
        assert value not in _safe_url(url, frozenset((value,)), frozenset((ORIGIN,)))
    else:
        assert "%5BUNREVIEWED%5D" in _safe_url(url, frozenset(), frozenset((ORIGIN,)))
        assert value in _safe_url(url, frozenset((value,)), frozenset((ORIGIN,)))


def test_short_numeric_query_in_actual_approved_zip_is_not_auto_public():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books?page=7&opaque=42",
        status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "anonymize")
        plan = prepare_reviewed_zip(review)
        descriptor = next(x["text"] for x in plan.preview()["members"]
                          if x["name"] == "exchanges/0001.json")
        assert "page=7" not in descriptor
        assert "opaque=42" not in descriptor
        assert "[UNREVIEWED]" in descriptor or "%5BUNREVIEWED%5D" in descriptor
    finally:
        review.destroy()
        trace.destroy()


@pytest.mark.parametrize("field", [
    "pin", "pinCode", "payment_pin", "passcode", "pass_code",
    "verificationCode", "security_code", "accessCode", "totp",
])
def test_short_authentication_codes_always_have_secret_classification(field):
    from browser_recorder_review import _is_secret
    from browser_recorder_zip import _json_shape

    assert _is_secret(field, "1234")
    assert _json_shape(1234, name=field, approved=frozenset({"1234"})) == "[SECRET]"
    assert _json_shape("1234", name=field, approved=frozenset({"1234"})) == "[SECRET]"


def test_short_pin_never_reaches_any_zip_member_even_with_approved_book_id():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api/books?pinCode=0007&BookId=214",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({
            "pin": 1234, "passcode": "4321",
            "security_code": 5678, "BookId": 214, "count": 2,
        }),
        response_headers={"content-type": "application/json"},
        response_body='{"BookId":214,"verificationCode":"6543","success":true}',
        status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            # Only the public fictional BookId is an exportable decision;
            # credentials never appear as choices that can be kept.
            assert candidate.value not in {"0007", "1234", "4321", "5678", "6543"}
            review.decide(candidate.key, "keep" if candidate.value == "214"
                          else "anonymize")
        plan = prepare_reviewed_zip(review)
        serialized = json.dumps(plan.preview(), sort_keys=True)
        for secret in ("0007", "1234", "4321", "5678", "6543"):
            assert secret not in serialized
        body = next(item["text"] for item in plan.preview()["members"]
                    if item["name"] == "bodies/0001-request.txt")
        shaped = json.loads(body)
        assert shaped["pin"] == "[SECRET]"
        assert shaped["passcode"] == "[SECRET]"
        assert shaped["security_code"] == "[SECRET]"
        assert shaped["BookId"] == 214
        assert shaped["count"] == 2
    finally:
        review.destroy()
        trace.destroy()



@pytest.mark.parametrize(("header", "value"), [
    ("retry-after", "007"),
    ("retry-after", "123456789"),
    ("x-ratelimit-limit", "42"),
    ("x-ratelimit-remaining", "1234"),
])
def test_zip_numeric_header_values_are_structural_only(header, value):
    """A numeric HTTP header can contain a private code regardless of length."""
    from browser_recorder_zip import _safe_headers

    assert _safe_headers({header: value}) == {header: "[NUMBER]"}
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET", url=ORIGIN + "/api/books", status=429,
        response_headers={"content-type": "application/json", header: value},
        response_body='{"books":[]}',
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "keep")
        plan = prepare_reviewed_zip(review)
        descriptor = json.loads(next(row["text"] for row in plan.preview()["members"]
                                     if row["name"] == "exchanges/0001.json"))
        assert descriptor["response_headers"][header] == "[NUMBER]"
        assert value not in descriptor["response_headers"][header]
    finally:
        review.destroy()
        trace.destroy()



@pytest.mark.parametrize(("field", "number", "expected"), [
    ("mystery", 7, "[NUMBER]"),
    ("customProperty", 42, "[NUMBER]"),
    ("opaque", 1234, "[NUMBER]"),
    # Mandatory secret names already receive an even stronger mask.
    ("sessionFragment", 9999, "[SECRET]"),
    ("unfamiliarOtpNumber", 3210, "[SECRET]"),
])
def test_unknown_short_json_numbers_are_masked_even_if_approved_elsewhere(
    field, number, expected,
):
    from browser_recorder_zip import _json_shape

    assert _json_shape(number, name=field) == expected
    assert _json_shape(number, name=field,
                       approved=frozenset({str(number)})) == expected


def test_approved_identifiers_and_bounded_public_counters_survive():
    from browser_recorder_zip import _json_shape

    projected = _json_shape(
        {"bookId": 214, "count": 2, "year": 2026, "price": 9.5,
         "page": 3, "total": 12, "unfamiliar": 214, "unknownCode": 1234},
        approved=frozenset({"214"}),
    )
    assert projected == {
        "bookId": 214, "count": 2, "year": 2026, "price": 9.5,
        "page": 3, "total": 12, "unfamiliar": "[NUMBER]",
        "unknownCode": "[NUMBER]",
    }


def test_frozen_zip_short_numeric_private_fields_remain_structural():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api/books?BookId=214",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({
            "bookId": 214, "customInternalNumber": 2718,
            "opaque": 987, "year": 2026, "count": 2,
        }),
        response_headers={"content-type": "application/json"},
        response_body='{"customPrivate":1234,"total":12}',
        status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key,
                          "keep" if candidate.value == "214" else "anonymize")
        plan = prepare_reviewed_zip(review)
        members = plan.preview()["members"]
        request = json.loads(next(row["text"] for row in members
                                  if row["name"] == "bodies/0001-request.txt"))
        response = json.loads(next(row["text"] for row in members
                                   if row["name"] == "bodies/0001-response.txt"))
        assert request["bookId"] == 214
        assert request["customInternalNumber"] == "[NUMBER]"
        assert request["opaque"] == "[NUMBER]"
        assert request["year"] == 2026
        assert request["count"] == 2
        assert response["customPrivate"] == "[NUMBER]"
        assert response["total"] == 12
        serial = json.dumps(plan.preview(), sort_keys=True)
        assert '"customInternalNumber": 2718' not in serial
        assert '"opaque": 987' not in serial
        assert '"customPrivate": 1234' not in serial
    finally:
        review.destroy()
        trace.destroy()


@pytest.mark.parametrize(("dynamic_key", "private_fragment"), [
    ("session_PRIVATE_ACCOUNT42", "PRIVATE_ACCOUNT42"),
    ("access_token_FICTIONAL42", "FICTIONAL42"),
    ("customField_123456789", "123456789"),
    ("fingerprint_aabbccddeeff001122", "aabbccddeeff001122"),
])
def test_private_dynamic_schema_keys_never_reach_approved_zip(
    dynamic_key, private_fragment,
):
    """Unreviewed field NAMES must not launder secrets into a ZIP."""
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST",
        url=ORIGIN + "/api/books?" + dynamic_key + "=FICTIONAL_RAW_PRIVATE42&BookId=214",
        request_headers={"content-type": "application/json"},
        request_body=json.dumps({
            dynamic_key: "FICTIONAL_RAW_PRIVATE42", "BookId": 214,
            "access_token": "FICTIONAL_SECRET", "customField": "value",
        }),
        status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key, "keep" if candidate.value == "214"
                          else "anonymize")
        preview = prepare_reviewed_zip(review).preview()
        serialized = json.dumps(preview)
        assert dynamic_key not in serialized
        assert private_fragment not in serialized
        body = json.loads(next(item["text"] for item in preview["members"]
                               if item["name"] == "bodies/0001-request.txt"))
        assert dynamic_key not in body
        assert body["BookId"] == 214
        assert body["access_token"] == "[SECRET]"
        assert body["customField"] == "[TEXT]"
        descriptor = json.loads(next(item["text"] for item in preview["members"]
                                     if item["name"] == "exchanges/0001.json"))
        assert dynamic_key not in descriptor["url"]
        assert "BookId=214" in descriptor["url"]
    finally:
        review.destroy()
        trace.destroy()



@pytest.mark.parametrize(("dynamic_key", "private_fragment"), [
    ("session_PRIVATE_ACCOUNT42", "PRIVATE_ACCOUNT42"),
    ("access_token_FICTIONAL42", "FICTIONAL42"),
    ("customField_123456789", "123456789"),
    ("fingerprint_aabbccddeeff001122", "aabbccddeeff001122"),
])
def test_urlencoded_form_dynamic_private_field_names_never_reach_zip(
    dynamic_key, private_fragment,
):
    """The name of a form field requires the same privacy gate as JSON/URL.

    No network, provider or account is contacted. The malicious provider
    embeds unreviewed private material into an ordinary form field NAME,
    including with an innocuous VALUE and an explicitly kept BookId.
    """
    from browser_recorder_zip import _body

    raw = urlencode([
        ("BookId", "214"),
        (dynamic_key, "FICTIONAL_RAW_PRIVATE42"),
        ("password", "FICTIONAL_PASSWORD"),
        ("customField", "free text"),
    ])
    direct, state = _body(
        raw, "application/x-www-form-urlencoded",
        frozenset({"214"}),
    )
    assert state == "captured_form_structure"
    direct_fields = {item["field"]: item["value"] for item in json.loads(direct)}
    assert dynamic_key not in direct_fields
    assert direct_fields["BookId"] == "214"
    assert direct_fields["password"] == "[SECRET]"
    assert direct_fields["customField"] == "[UNREVIEWED]"

    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api/books?BookId=214",
        request_headers={"content-type": "application/x-www-form-urlencoded"},
        request_body=raw, status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(
                candidate.key,
                "keep" if candidate.value == "214" else "anonymize",
            )
        snapshot = prepare_reviewed_zip(review).preview()
        serialized = json.dumps(snapshot)
        assert dynamic_key not in serialized
        assert private_fragment not in serialized
        assert "FICTIONAL_RAW_PRIVATE42" not in serialized
        assert "FICTIONAL_PASSWORD" not in serialized

        form_text = next(
            row["text"] for row in snapshot["members"]
            if row["name"] == "bodies/0001-request.txt"
        )
        names = {item["field"]: item["value"] for item in json.loads(form_text)}
        assert dynamic_key not in names
        assert names["BookId"] == "214"
        assert names["password"] == "[SECRET]"
        assert names["customField"] == "[UNREVIEWED]"
    finally:
        review.destroy()
        trace.destroy()



@pytest.mark.parametrize("mime", ("text/html", "application/xml"))
@pytest.mark.parametrize(("dynamic_tag", "private_fragment"), [
    ("session-privateaccount", "privateaccount"),
    ("token-abcdef", "abcdef"),
    ("account123456789", "123456789"),
    ("fingerprint:aabbccddeeff001122", "aabbccddeeff001122"),
])
def test_html_xml_custom_tag_names_cannot_publish_unreviewed_secrets(
    mime, dynamic_tag, private_fragment,
):
    """Custom markup element names must obey the JSON/URL form-key policy.

    A provider may encode an account/token in the name of an HTML or XML
    tag, not in its attributes or inner text. Preserve technical book/title
    markup but never publish this opaque dynamic name in the exact ZIP.
    """
    from browser_recorder_zip import _body

    raw = (
        f"<root><{dynamic_tag}>FICTIONAL_PRIVATE_PAYLOAD</{dynamic_tag}>"
        "<book><title>FICTIONAL_PRIVATE_TITLE</title></book></root>"
    )
    shaped, state = _body(raw, mime, frozenset())
    assert state == "captured_markup_structure"
    assert dynamic_tag not in shaped
    assert private_fragment not in shaped
    assert "FICTIONAL_PRIVATE_PAYLOAD" not in shaped
    assert "FICTIONAL_PRIVATE_TITLE" not in shaped
    assert "<root>" in shaped
    assert "<book>" in shaped
    assert "<title>" in shaped
    assert "[TEXT]" in shaped

    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "POST", url=ORIGIN + "/api/books?BookId=214",
        request_headers={"content-type": mime},
        request_body=raw, status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(
                candidate.key,
                "keep" if candidate.value == "214" else "anonymize",
            )
        preview = prepare_reviewed_zip(review).preview()
        serialized = json.dumps(preview)
        assert dynamic_tag not in serialized
        assert private_fragment not in serialized
        assert "FICTIONAL_PRIVATE_PAYLOAD" not in serialized
        assert "FICTIONAL_PRIVATE_TITLE" not in serialized
        text = next(
            row["text"] for row in preview["members"]
            if row["name"] == "bodies/0001-request.txt"
        )
        assert dynamic_tag not in text
        assert "<book>" in text
        assert "<title>" in text
        assert "[TEXT]" in text
        assert "BookId=214" in serialized
    finally:
        review.destroy()
        trace.destroy()


# #2530: Recoverable provider protocol, with mandatory secret isolation.
def test_paired_pagination_and_known_enums_survive_but_opaque_query_does_not():
    from urllib.parse import parse_qs, urlsplit
    from browser_recorder_zip import _safe_url

    url = (ORIGIN + "/api/v3/my-books/download-options?"
           "page=2&limit=20&sort=title&format=epub"
           "&search=PRIVATE_FREE_TEXT&access_token=FICTIONAL_SECRET")
    safe = _safe_url(url, frozenset(), frozenset((ORIGIN,)))
    assert urlsplit(safe).path == "/api/v3/my-books/download-options"
    params = parse_qs(urlsplit(safe).query)
    assert params["page"] == ["2"]
    assert params["limit"] == ["20"]
    assert params["sort"] == ["title"]
    assert params["format"] == ["epub"]
    assert params["search"] == ["[UNREVIEWED]"]
    assert params["access_token"] == ["[SECRET]"]
    assert "FICTIONAL_SECRET" not in safe
    assert "PRIVATE_FREE_TEXT" not in safe

    for query in ("page=2", "limit=20", "page=0007&limit=20",
                  "page=2&limit=0007", "page=2&page=3&limit=20",
                  "page=2&limit=20&limit=40"):
        safe = _safe_url(ORIGIN + "/api/books?" + query,
                         frozenset(), frozenset((ORIGIN,)))
        assert "page=2&limit=20" not in safe
        assert "page=0007" not in safe
        assert "limit=0007" not in safe
    unknown = _safe_url(ORIGIN + "/api/books?sort=SECRET_SORT&format=SECRET_FMT",
                        frozenset(), frozenset((ORIGIN,)))
    assert "SECRET_SORT" not in unknown
    assert "SECRET_FMT" not in unknown


def test_json_business_enums_survive_without_approving_unknown_strings():
    from browser_recorder_zip import _json_shape

    projected = _json_shape({
        "format": "epub", "availability": "ready",
        "status": "active", "sort": "title",
        "downloadable": True, "title": "PRIVATE_READING_TITLE",
        "note": "PRIVATE_ACCOUNT_NOTE", "formatUnknown": "epub",
        "format2": "PRIVATE_FORMAT", "opaque": 2718,
        "access_token": "FAKE_PRIVATE_TOKEN",
    })
    assert projected["format"] == "epub"
    assert projected["availability"] == "ready"
    assert projected["status"] == "active"
    assert projected["sort"] == "title"
    assert projected["downloadable"] is True
    assert projected["title"] == "[TEXT]"
    assert projected["note"] == "[TEXT]"
    assert projected["formatUnknown"] == "[TEXT]"
    assert projected["format2"] == "[TEXT]"
    assert projected["opaque"] == "[NUMBER]"
    assert projected["access_token"] == "[SECRET]"
    assert _json_shape("PRIVATE_UNKNOWN", name="format") == "[TEXT]"


def test_html_stable_selectors_survive_without_private_attributes():
    from browser_recorder_zip import _body

    html = ('<form id="search-form" data-testid="search-form" '
            'action="/private?access_token=SECRET" class="book-card user-987654321">'
            '<input type="text" name="search" value="PRIVATE_QUERY">'
            '<button data-testid="download-button" id="user-123456789">'
            'PRIVATE_PERSONAL_LABEL</button></form>')
    projected, state = _body(html, "text/html", frozenset())
    assert state == "captured_markup_structure"
    assert 'id="search-form"' in projected
    assert 'data-testid="search-form"' in projected
    assert 'data-testid="download-button"' in projected
    assert 'name="search"' in projected
    assert 'class="book-card"' in projected
    for secret in ("SECRET", "PRIVATE", "user-987654321", "user-123456789"):
        assert secret not in projected


def test_zip_only_fixture_preserves_workflow_and_masks_session(tmp_path):
    from urllib.parse import parse_qs, urlsplit

    account = "acct-SYNTHETIC45"
    token = "TOKEN_SYNTHETIC_DO_NOT_EXPORT"
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges.append(PrivateExchange(
        1, "GET",
        url=(ORIGIN + "/api/v3/my-books/download-options?"
             "page=2&limit=20&sort=title&format=epub"
             "&AccountId=" + account + "&access_token=" + token),
        response_headers={"content-type": "application/json"},
        response_body=json.dumps({
            "bookId": 214, "AccountId": account,
            "format": "epub", "availability": "ready",
            "downloadable": True, "title": "PRIVATE_BOOK_TITLE",
            "notes": "PRIVATE_ACCOUNT_NOTE",
        }),
        status=200,
    ))
    review = IdentifierReview(trace)
    try:
        for candidate in review.candidates():
            review.decide(candidate.key,
                          "keep" if candidate.value == "214" else "anonymize")
        plan = prepare_reviewed_zip(review)
        plan.preview()  # Explicit review before writing.
        name = plan.save(tmp_path, user_confirmed=True)
        # A second ZIP reader sees only the exported bytes, not the private trace.
        with ZipFile(tmp_path / name) as archive:
            members = {member: archive.read(member).decode("utf-8")
                       for member in archive.namelist()}
        exchange = json.loads(members["exchanges/0001.json"])
        assert exchange["method"] == "GET"
        assert exchange["status"] == 200
        assert urlsplit(exchange["url"]).path == "/api/v3/my-books/download-options"
        params = parse_qs(urlsplit(exchange["url"]).query)
        assert params["page"] == ["2"]
        assert params["limit"] == ["20"]
        assert params["format"] == ["epub"]
        assert params["sort"] == ["title"]
        assert params["access_token"] == ["[SECRET]"]
        assert params["AccountId"][0].startswith("PSEUDO_")
        body = json.loads(members["bodies/0001-response.txt"])
        assert body["bookId"] == 214
        assert body["AccountId"] == params["AccountId"][0]
        assert body["format"] == "epub"
        assert body["availability"] == "ready"
        assert body["downloadable"] is True
        assert body["title"] == "[TEXT]"
        assert body["notes"] == "[TEXT]"
        full = json.dumps(members)
        for sensitive in (account, token, "PRIVATE_BOOK_TITLE", "PRIVATE_ACCOUNT_NOTE"):
            assert sensitive not in full
    finally:
        review.destroy()
        trace.destroy()
