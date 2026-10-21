"""P0 #2529: generated labels must not alias fictional observed values."""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

import pytest

from browser_recorder import RecorderError
from browser_recorder_http import PrivateExchange, PrivateHttpTrace
from browser_recorder_review import IdentifierReview, MAX_CANDIDATES
from browser_recorder_zip import prepare_reviewed_zip

ORIGIN = "https://books.example.org"


def test_kept_real_identifier_cannot_equal_generated_pseudonym_in_saved_zip(tmp_path):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [PrivateExchange(
        1, "POST", url=ORIGIN + "/books?accountId=alpha&bookId=PSEUDO_I0001",
        response_body=json.dumps({"accountId": "alpha", "bookId": "PSEUDO_I0001"}),
        response_headers={"content-type": "application/json"}, status=200,
    )]
    review = IdentifierReview(trace)
    account = next(c for c in review.candidates() if c.value == "alpha")
    assert account.key != "I0001"
    assert len({c.key for c in review.candidates()}) == 2
    for candidate in review.candidates():
        review.decide(candidate.key, "anonymize" if candidate.value == "alpha" else "keep")
    plan = prepare_reviewed_zip(review)
    plan.preview()
    with ZipFile(tmp_path / plan.save(tmp_path, user_confirmed=True)) as archive:
        row = json.loads(archive.read("exchanges/0001.json"))
        body = json.loads(archive.read(row["bodies"]["response"]["file"]))
        assert body == {"accountId": "PSEUDO_" + account.key, "bookId": "PSEUDO_I0001"}
        assert body["accountId"] != body["bookId"]
        assert parse_qs(urlsplit(row["url"]).query) == {k: [v] for k, v in body.items()}
        assert "alpha" not in archive.read("bodies/0001-response.txt").decode()
    # IDs and labels stay stable throughout decision changes in this capture.
    original_key = account.key
    review.decide(account.key, "keep")
    assert json.loads(review.preview()["exchanges"][0]["response_body"])["accountId"] == "alpha"
    review.decide(account.key, "anonymize")
    assert account.key == original_key
    assert json.loads(review.preview()["exchanges"][0]["response_body"])["accountId"] == "PSEUDO_" + original_key
    review.destroy()
    assert not review._reserved_pseudonyms
    trace.destroy()


@pytest.mark.parametrize("body", [
    '{"accountId":"alpha","notes":"PSEUDO_I0001"}',
    r'{"accountId":"alpha","notes":"PSEUDO_\u00490001"}',
    r'{"accountId":"alpha","examples":["PSEUDO_\u00490001"]}',
    '{"accountId":"alpha","PSEUDO_I0001":"unreviewed"}',
])
def test_unreviewed_marker_in_json_does_not_gain_anonymization_approval(body):
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [PrivateExchange(
        1, "GET", url=ORIGIN + "/books", response_body=body,
        response_headers={"content-type": "application/json"}, status=200,
    )]
    review = IdentifierReview(trace)
    assert len(review.candidates()) == 1
    candidate = review.candidates()[0]
    assert candidate.key != "I0001"
    review.decide(candidate.key, "anonymize")
    members = {m["name"]: m["text"] for m in prepare_reviewed_zip(review).preview()["members"]}
    output = json.loads(members["bodies/0001-response.txt"])
    assert output["accountId"] == "PSEUDO_" + candidate.key
    # Schema keys are structural; unreviewed values never inherit approval.
    assert output.get("notes") != "PSEUDO_I0001"
    assert output.get("examples") != ["PSEUDO_I0001"]
    review.destroy()
    trace.destroy()


def test_secret_and_percent_encoded_path_markers_are_reserved():
    trace = PrivateHttpTrace((ORIGIN,))
    trace.exchanges = [PrivateExchange(
        1, "GET", url=ORIGIN + "/%50SEUDO_I0001?accountId=alpha&token=PSEUDO_I0002",
        response_body='{"accountId":"alpha"}',
        response_headers={"content-type": "application/json"}, status=200,
    )]
    review = IdentifierReview(trace)
    candidate = review.candidates()[0]
    assert candidate.key not in {"I0001", "I0002"}
    review.decide(candidate.key, "anonymize")
    members = prepare_reviewed_zip(review).preview()["members"]
    text = "\n".join(row["text"] for row in members)
    assert "PSEUDO_I0001" not in text and "PSEUDO_I0002" not in text
    assert "PSEUDO_" + candidate.key in text
    review.destroy()
    trace.destroy()


def test_reserved_marker_budget_is_bounded_and_does_not_add_candidates():
    trace = PrivateHttpTrace((ORIGIN,))
    markers = " ".join(f"PSEUDO_I{n:04d}" for n in range(1, 33))
    trace.exchanges = [PrivateExchange(
        1, "POST", url=ORIGIN + "/books?accountId=alpha",
        response_body=markers, response_headers={"content-type": "text/plain"},
    )]
    review = IdentifierReview(trace)
    assert len(review.candidates()) == 1 and review.pending() == 1
    assert review.candidates()[0].key == "I0033"
    review.destroy()
    assert not review._reserved_pseudonyms
    trace.exchanges[0].response_body = " ".join(
        f"PSEUDO_I{n:04d}" for n in range(1, MAX_CANDIDATES * 4 + 2)
    )
    with pytest.raises(RecorderError, match="review_limit"):
        IdentifierReview(trace)
    trace.destroy()
