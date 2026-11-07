"""#4932: complementary HTTPS origins use the normal capture pipeline.

All fixtures are synthetic. No live identities, tickets, private ZIPs or
provider credentials are committed to source control.
"""
from __future__ import annotations

import json

import pytest

from browser_recorder import RecorderError
from browser_recorder_http import CapturePause, PrivateHttpTrace
from browser_recorder_vault import _record, _unrecord
from test_browser_recorder_http import (
    ORIGIN, FOREIGN, Request, Response, emit,
)

META = "https://metadata.example.org"
EXCLUDED = "https://not-consented.example.org"
ID = "a" * 32


def test_two_normal_origins_share_body_redirect_header_and_budget_pipeline():
    trace = PrivateHttpTrace(
        (ORIGIN, FOREIGN), metadata_only_origins=(META,),
    )
    try:
        main = Request(ORIGIN + "/catalog?genre=history")
        emit(trace, main, Response(main, data=b'{"genre":"history"}'))

        companion = Request(
            FOREIGN + "/catalog?genre=fiction",
            headers={"authorization": "Bearer SYNTHETIC_PRIVATE_SECRET"},
        )
        emit(trace, companion, Response(
            companion, mime="application/json",
            data=b'{"genre":"fiction"}',
            headers={"Set-Cookie": "session=SYNTHETIC_PRIVATE_COOKIE"},
        ))
        redirect = Request(FOREIGN + "/navigate")
        emit(trace, redirect, Response(
            redirect, status=302, data=b"",
            headers={"location": "/catalog?genre=fiction"},
        ))
        limited = Request(META + "/anything?token=HIDDEN", method="POST",
                          data=b"password=HIDDEN")
        limited_response = Response(
            limited, status=302, data=b"",
            headers={"location": META + "/secret?code=HIDDEN"},
        )
        emit(trace, limited, limited_response)
        excluded = Request(EXCLUDED + "/unlisted?password=DO_NOT_KEEP")
        emit(trace, excluded, Response(excluded))

        assert len(trace.exchanges) == 4
        main_row, companion_row, redirect_row, meta_row = trace.exchanges
        assert main_row.response_body == '{"genre":"history"}'
        assert companion_row.response_body == '{"genre":"fiction"}'
        assert companion_row.url.endswith("/catalog?genre=fiction")
        assert companion_row.metadata_only is False
        assert companion_row.request_headers == {}
        assert "set-cookie" not in companion_row.response_headers
        assert redirect_row.redirect_to == "/catalog?genre=fiction"
        assert redirect_row.status == 302
        assert meta_row.metadata_only and meta_row.url == ""
        assert meta_row.request_body is None and meta_row.response_body is None
        assert meta_row.redirect_to is None
        assert limited.read_post_data == 0 and limited_response.body_reads == 0
        assert trace.omitted["out_of_scope"] == 1

        by_origin = trace.coverage_by_origin()
        assert by_origin[ORIGIN] == {
            "mode": "normal", "requests": 1, "text_responses": 1,
            "metadata_only": 0,
        }
        assert by_origin[FOREIGN] == {
            "mode": "normal", "requests": 2, "text_responses": 1,
            "metadata_only": 0,
        }
        assert by_origin[META] == {
            "mode": "metadata_only", "requests": 1, "text_responses": 0,
            "metadata_only": 1,
        }
        assert EXCLUDED not in by_origin

        # The *reviewed ZIP* must accept both fully approved origins, not
        # discard a companion as though it were an unconsented third party.
        from browser_recorder_review import IdentifierReview
        from browser_recorder_zip import prepare_reviewed_zip

        review = IdentifierReview(trace)
        try:
            for candidate in review.candidates():
                review.decide(candidate.key, "anonymize")
            members = prepare_reviewed_zip(review).preview()["members"]
            exported = {
                row["name"]: json.loads(row["text"])
                for row in members if row["name"].startswith("exchanges/")
            }
            assert len(exported) == 4
            extra_export = exported["exchanges/0002.json"]
            assert extra_export["metadata_only"] is False
            assert extra_export["url"] not in {
                "", "[OUT_OF_SCOPE]", "[UNSAFE_TARGET]",
            }
            assert exported["exchanges/0004.json"]["metadata_only"] is True
            assert exported["exchanges/0004.json"]["url"] == ""
            assert "SYNTHETIC_PRIVATE_SECRET" not in json.dumps(members)
        finally:
            review.destroy()

        encrypted_payload = _record(trace, ID, "completed")
        assert b"DO_NOT_KEEP" not in encrypted_payload
        assert b"SYNTHETIC_PRIVATE_COOKIE" not in encrypted_payload
        assert b"SYNTHETIC_PRIVATE_SECRET" not in encrypted_payload
        assert b"password=HIDDEN" not in encrypted_payload
        restored = _unrecord(encrypted_payload, ID).trace
        try:
            assert restored.origins == (ORIGIN, FOREIGN)
            assert restored.metadata_only_origins == (META,)
            assert restored.coverage_by_origin() == by_origin
        finally:
            restored.destroy()
    finally:
        trace.destroy()
    assert trace.exchanges == []


def test_authentication_structures_omit_bodies_and_never_store_ticket_values():
    trace = PrivateHttpTrace((ORIGIN, FOREIGN))
    synthetic = (
        "SYNTHETIC_PRIVATE_TICKET",
        "SYNTHETIC_PRIVATE_OTP",
        "SYNTHETIC_PRIVATE_PASSWORD",
        "SYNTHETIC_PRIVATE_STATE",
        "SYNTHETIC_PRIVATE_SERVICE",
    )
    try:
        login = Request(
            FOREIGN + "/cas/login?state=" + synthetic[3],
            method="POST",
            headers={"content-type": "application/x-www-form-urlencoded",
                     "Authorization": "Bearer NO_STORAGE"},
            data=("password=" + synthetic[2] + "&otp=" + synthetic[1]).encode(),
        )
        login_response = Response(
            login, status=302, data=b"",
            headers={"location": ORIGIN + "/loans?ticket=" + synthetic[0]
                     + "&service=" + synthetic[4],
                     "Set-Cookie": "session=NO_STORAGE"},
        )
        emit(trace, login, login_response)
        callback = Request(
            ORIGIN + "/loans?ticket=" + synthetic[0]
            + "&service=" + synthetic[4]
        )
        emit(trace, callback, Response(callback, data=b'{"ok":true}'))
        token_endpoint = Request(
            FOREIGN + "/oauth/callback?code=NEVER_PERSIST",
        )
        token_response = Response(
            token_endpoint, data=b'{"access_token":"NEVER_PERSIST"}'
        )
        emit(trace, token_endpoint, token_response)

        first, second, third = trace.exchanges
        assert first.url == FOREIGN + "/cas/login?state=REDACTED"
        assert first.request_body is None
        assert first.request_body_reason == "auth_redacted"
        assert first.response_body is None
        assert first.redirect_to == ORIGIN + (
            "/loans?ticket=REDACTED&service=REDACTED"
        )
        assert login.read_post_data == 0 and login_response.body_reads == 0
        assert second.url.endswith("/loans?ticket=REDACTED&service=REDACTED")
        assert second.response_body == '{"ok":true}'
        assert third.response_body is None
        assert third.response_body_reason == "auth_redacted"
        assert token_response.body_reads == 0
        text = _record(trace, ID, "completed").decode("utf-8")
        for secret in (*synthetic, "NEVER_PERSIST", "NO_STORAGE"):
            assert secret not in text
        assert json.loads(text)["metadata_only_counts"] == {}
    finally:
        trace.destroy()


def test_metadata_consent_cannot_be_silently_upgraded_and_pause_is_global():
    with pytest.raises(RecorderError, match="invalid_origin"):
        PrivateHttpTrace((ORIGIN, FOREIGN), metadata_only_origins=(FOREIGN,))

    control = CapturePause()
    trace = PrivateHttpTrace((ORIGIN, FOREIGN), pause_control=control)
    try:
        control.set_paused(True)
        skipped = Request(FOREIGN + "/catalog")
        emit(trace, skipped, Response(skipped))
        assert not trace.exchanges
        assert trace.omitted["paused"] >= 1
        control.set_paused(False)
        allowed = Request(FOREIGN + "/catalog")
        emit(trace, allowed, Response(allowed))
        assert len(trace.exchanges) == 1
        assert trace.coverage_by_origin()[FOREIGN]["requests"] == 1
        trace.finish_open_requests()
        assert not trace._pending
    finally:
        trace.destroy()


def test_legacy_vault_without_optional_counters_still_opens():
    trace = PrivateHttpTrace((ORIGIN,), metadata_only_origins=(FOREIGN,))
    try:
        old = json.loads(_record(trace, ID, "completed"))
        old.pop("metadata_only_counts")
        loaded = _unrecord(json.dumps(old).encode("utf-8"), ID).trace
        try:
            assert loaded.origins == (ORIGIN,)
            assert loaded.metadata_only_origins == (FOREIGN,)
            assert loaded.coverage_by_origin()[FOREIGN]["metadata_only"] == 0
        finally:
            loaded.destroy()
    finally:
        trace.destroy()
