"""P1 #2530: only fictional HTTP observations and explicit local ZIP writing."""
from __future__ import annotations

from hashlib import sha256
import json
import os
import stat
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
        "retry-after": "42",
        "x-ratelimit-remaining": "7",
    }
    assert _safe_headers({
        "content-type": "application/vnd.fictional+json",
        "accept": "application/xml; q=0.5, text/html",
        "retry-after": "Wed, 21 Oct 2026 07:28:00 GMT",
        "x-ratelimit-limit": "1000",
    }) == {
        "content-type": "application/vnd.fictional+json",
        "accept": "application/xml, text/html",
        "x-ratelimit-limit": "1000",
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
            "retry-after": "123",
            "x-ratelimit-remaining": "3",
        }
        combined = json.dumps(summary)
        assert "FICTIONAL_PRIVATE_MIME" not in combined
        assert "FICTIONAL_PRIVATE_LIMIT" not in combined
    finally:
        review.destroy()
        trace.destroy()
