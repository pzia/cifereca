"""#1884: synthetic, action-scoped delivery diagnostics without signed details.

All HTTP requests use MockTransport. Neither a real Numilog account nor an
actual signed URL is used, and no Core diagnostics implementation is required.
"""
from __future__ import annotations

from time import perf_counter

import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError, ProviderError
from cifereca_numilog import NumilogSession
from cifereca_numilog_delivery_trace import DeliveryTrace, destination_category, mime_group, public_http_code
from tests.test_entrepot2r_acsm_origin_p0 import SIGNED
from tests.test_entrepot2r_fulfillment_mock_p0 import ACSM, START, make_session


class Capture:
    def __init__(self):
        self.events = []

    def add(self, *args, **kwargs):
        self.events.append((args, kwargs))

    def acquisition(self):
        return [
            (args, kwargs) for args, kwargs in self.events
            if args[:2] == ("NUMILOG", "acquisition_delivery")
        ]

    def completed(self):
        return [
            (args, kw) for args, kw in self.acquisition()
            if kw["detail"]["phase"] == "completed"
        ]


def build(tmp_path, handler, **options):
    session, broker = make_session(handler, tmp_path, **options)
    diagnostics = Capture()
    session.diagnostics = diagnostics
    return session, broker, diagnostics


def assert_private_data_absent(diagnostics):
    serialized = repr(diagnostics.events)
    for forbidden in (
        "FAKE-SIGNATURE", "SYNTHETIC_SIGNATURE", "FAKE-ORDER",
        "FICTIONAL", "PRIVATE_ACCOUNT", "PRIVATE_HTML",
        "auth=", "orderid=", "rights=", "resid=",
    ):
        assert forbidden not in serialized
    for args, kwargs in diagnostics.acquisition():
        assert args[:2] == ("NUMILOG", "acquisition_delivery")
        assert set(kwargs) <= {"detail", "duration_ms", "level"}
        detail = kwargs["detail"]
        assert set(detail) <= {
            "action", "source", "phase", "code", "hops",
            "http_status", "mime", "html_kind", "origin_kind", "path_kind", "candidates",
        }
        assert set(detail) >= {"action", "source", "phase", "code"}
        assert len(detail["action"]) == 12
        assert all(char in "0123456789abcdef" for char in detail["action"])
        assert kwargs["level"] in {"normal", "detailed"}


@pytest.mark.parametrize("via_html", (False, True))
def test_fictional_acsm_delivery_events_share_one_ephemeral_action(tmp_path, via_html):
    seen = []
    def handler(request):
        url = str(request.url)
        seen.append(url)
        if url == START:
            if via_html:
                return httpx.Response(200, headers={"content-type": "text/html"},
                                      text=f'<script>window.location = "{SIGNED}"</script>')
            return httpx.Response(302, headers={"location": SIGNED})
        assert url == SIGNED
        return httpx.Response(
            200, headers={"content-type": "application/vnd.adobe.adept+xml"},
            content=ACSM,
        )
    session, broker, diag = build(tmp_path, handler)
    try:
        file, source = session._download(
            START, tmp_path / "out.epub", title="Public synthetic",
            source="readbook",
        )
        assert file.is_file() and source == session.library_url
        assert seen == [START, SIGNED] and len(broker.calls) == 1
        events = diag.acquisition()
        assert len(diag.completed()) == 1
        summary = diag.completed()[0]
        assert summary[0][2] == "ok"
        assert summary[1]["detail"]["code"] == "acsm_complete"
        assert summary[1]["detail"]["hops"] == 2
        assert summary[1]["detail"]["source"] == "readbook"
        assert type(summary[1]["duration_ms"]) is int
        assert 0 <= summary[1]["duration_ms"] <= 600_000
        assert summary[1]["level"] == "normal"
        assert all(x[1]["detail"]["action"] == summary[1]["detail"]["action"] for x in events)
        assert {
            x[1]["detail"]["http_status"] for x in events if "http_status" in x[1]["detail"]
        } == ({200} if via_html else {200, 302})
        assert any(x[1]["detail"].get("mime") == "acsm_xml" for x in events)
        assert any(x[1]["detail"].get("origin_kind") == "acsm_fulfillment"
                   and x[1]["detail"].get("path_kind") == "acsm_fulfillment"
                   for x in events)
        assert not list(tmp_path.glob(".numilog-*"))
        assert_private_data_absent(diag)
    finally:
        session.close()


@pytest.mark.parametrize("status,expected", (
    (401, "http_unauthorized"),
    (403, "http_forbidden"),
    (410, "link_expired"),
    (429, "http_rate_limited"),
    (500, "http_error"),
))
def test_http_denials_have_stable_public_codes(tmp_path, status, expected):
    session, broker, diag = build(
        tmp_path,
        lambda _: httpx.Response(
            status, headers={"content-type": "application/xml"},
            text="PRIVATE_HTML auth=FAKE-SIGNATURE",
        ),
    )
    try:
        with pytest.raises(ProviderError):
            session._download(SIGNED, tmp_path / "out.epub", source="owned_card")
        assert broker.calls == []
        summary = diag.completed()
        assert len(summary) == 1
        assert summary[0][0][2] == "error"
        assert summary[0][1]["detail"]["code"] == expected
        assert summary[0][1]["detail"]["source"] == "owned_card"
        assert_private_data_absent(diag)
    finally:
        session.close()


@pytest.mark.parametrize("html,expected", (
    ('<input type="password" value="PRIVATE_HTML"/>', "session_expired_confirmed"),
    ("<h1>Erreur</h1><div>PRIVATE_HTML</div>", "html_error"),
    ("<html><body>PRIVATE_HTML without delivery</body></html>", "html_delivery_link_missing"),
    (f'<a href="{SIGNED}">Link A</a><a href="{SIGNED}&extra=1">Link B</a>',
     "html_delivery_ambiguous"),
))
def test_html_selection_cause_and_page_shape_are_public(tmp_path, html, expected):
    session, broker, diag = build(
        tmp_path,
        lambda _: httpx.Response(
            200, headers={"content-type": "text/html"}, text=html,
        ),
    )
    try:
        with pytest.raises(ProviderError):
            session._download(START, tmp_path / "out.epub", source="readbook")
        assert broker.calls == []
        summary = diag.completed()
        assert len(summary) == 1
        assert summary[0][1]["detail"]["code"] == expected
        assert summary[0][1]["detail"]["hops"] == 1
        assert any(
            x[1]["detail"]["phase"] == "html_selection"
            and x[1]["detail"]["code"] == expected
            for x in diag.acquisition()
        )
        candidate_values = [
            x[1]["detail"]["candidates"] for x in diag.acquisition()
            if x[1]["detail"]["phase"] == "html_selection"
            and "candidates" in x[1]["detail"]
        ]
        if expected == "html_delivery_link_missing":
            assert candidate_values == [0]
        elif expected == "html_delivery_ambiguous":
            assert candidate_values == [2]
        else:
            assert candidate_values == []
        assert_private_data_absent(diag)
    finally:
        session.close()


@pytest.mark.parametrize("content_type,body,expected", (
    ("application/pdf", b"PRIVATE_HTML", "mime_unexpected"),
    ("application/xml", b"<not-ACSM/>", "xml_not_acsm"),
))
def test_wrong_mime_and_non_acsm_xml_are_discriminated(tmp_path, content_type, body, expected):
    session, broker, diag = build(
        tmp_path,
        lambda _: httpx.Response(200, headers={"content-type": content_type}, content=body),
    )
    try:
        with pytest.raises((ProviderCapabilityError, ProviderError)):
            session._download(SIGNED, tmp_path / "out.epub")
        assert broker.calls == []
        assert diag.completed()[0][1]["detail"]["code"] == expected
        assert_private_data_absent(diag)
    finally:
        session.close()


def test_adept_action_required_is_distinct_from_network_or_missing_link(tmp_path):
    session, broker, diag = build(
        tmp_path, lambda _: httpx.Response(
            200, headers={"content-type": "application/xml"}, content=ACSM,
        ),
        status="attention_required",
    )
    try:
        with pytest.raises(ProviderCapabilityError, match="adept_profile_required"):
            session._download(SIGNED, tmp_path / "out.epub")
        assert len(broker.calls) == 1
        assert diag.completed()[0][1]["detail"]["code"] == "adept_attention_required"
        assert not list(tmp_path.glob(".numilog-*"))
        assert_private_data_absent(diag)
    finally:
        session.close()


def test_redirected_untrusted_origin_is_blocked_before_fetch(tmp_path):
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            302, headers={"location": "https://bad.invalid/path?auth=PRIVATE_ACCOUNT"},
        )
    session, broker, diag = build(tmp_path, handler)
    try:
        with pytest.raises(ProviderError):
            session._download(SIGNED, tmp_path / "out.epub")
        assert seen == [SIGNED]
        assert broker.calls == []
        assert diag.completed()[0][1]["detail"]["code"] == "origin_rejected"
        assert_private_data_absent(diag)
    finally:
        session.close()


def test_broken_or_missing_diagnostics_does_not_change_delivery_semantics(tmp_path):
    class Exploding:
        def add(self, *args, **kwargs):
            raise RuntimeError("PRIVATE_ACCOUNT")

    session, broker, _ = build(
        tmp_path,
        lambda _: httpx.Response(
            403, text="PRIVATE_ACCOUNT",
        ),
    )
    try:
        for sink in (None, Exploding()):
            session.diagnostics = sink
            with pytest.raises(ProviderError):
                session._download(SIGNED, tmp_path / "out.epub")
        assert broker.calls == []
    finally:
        session.close()


def test_unknown_provider_values_never_escape_trace_schema():
    diag = Capture()
    start = perf_counter()
    with pytest.raises(ValueError):
        with DeliveryTrace(diag, source="PRIVATE_ACCOUNT") as trace:
            trace.event(
                "delivery_http", "http_ok", status="PRIVATE_ACCOUNT",
                mime="application/private; secret=PRIVATE_ACCOUNT",
                html_kind="PRIVATE_ACCOUNT", hops=222,
            )
            trace.fail("PRIVATE_ACCOUNT")
            raise ValueError("PRIVATE_HTML")
    assert perf_counter() - start < 5
    assert diag.completed()[0][1]["detail"]["code"] == "unclassified_error"
    assert diag.completed()[0][1]["detail"]["source"] == "direct_url"
    assert_private_data_absent(diag)
    assert mime_group("application/xml; charset=PRIVATE_ACCOUNT") == "xml"
    assert public_http_code(401) == "http_unauthorized"
    assert public_http_code("403") == "http_error"
    assert destination_category(SIGNED) == ("acsm_fulfillment", "acsm_fulfillment")
    assert destination_category("https://unknown.invalid/a?auth=PRIVATE_ACCOUNT") == ("other", "other")


def test_legacy_http_diagnostics_never_export_user_configured_base_hostname(tmp_path):
    diag = Capture()
    host = "private-account.example.invalid"
    base = "https://" + host + "/"
    session = NumilogSession(
        credential_id="synthetic", label="Synthetic",
        credentials={"email": "reader@example.invalid", "password": "fictional"},
        options={"base_url": base}, config={}, diagnostics=diag,
    )
    seen = []
    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            403, headers={"content-type": "text/html"},
            text="PRIVATE_HTML orderid=FAKE-ORDER",
        )
    session.client.close()
    session.client = httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False,
        trust_env=False,
    )
    try:
        with pytest.raises(ProviderError):
            session._page(base)
        with pytest.raises(ProviderError):
            session._download(
                base + "delivery?auth=FAKE-SIGNATURE",
                tmp_path / "out.epub",
            )
        assert seen == [base, base + "delivery?auth=FAKE-SIGNATURE"]
        assert host not in repr(diag.events)
        assert "FAKE-SIGNATURE" not in repr(diag.events)
        assert "FAKE-ORDER" not in repr(diag.events)
        http = [
            kwargs["detail"] for args, kwargs in diag.events
            if args[:2] == ("NUMILOG", "HTTP")
        ]
        assert len(http) == 2
        assert all(row["origin_kind"] == "other" and "host" not in row for row in http)
        assert diag.completed()[0][1]["detail"]["code"] == "http_forbidden"
        assert_private_data_absent(diag)
    finally:
        session.close()
