"""#4882: local-only full K1/K3 URL diagnostics require explicit trace.

These tests use synthetic signed URLs, mocked provider POST and HTTPX.
No live account, KVault token, DRM execution or content download.
"""
from copy import deepcopy
import logging

import httpx
import pytest

from cifereca.logging_utils import diagnostics_level, set_diagnostics_level
from cifereca.plugin_api import ProviderCapabilityError
from cifereca_kobo_private_trace import trace_kobo_descriptor_urls
from tests.test_p0_3252_kdrm_canonical_runtime import ready, selected
from tests.test_p1_4640_k2_k3_normalized_selected_execution import _unknown_flepub
from tests.test_p1_3252_kobo_selected_candidate import (
    admitted, execute, fake_public_client,
)
from tests.test_p1_3252_kobo_selected_delivery_offer import (
    SIGNED, descriptor, setup,
)


@pytest.fixture
def local_level():
    previous = diagnostics_level()
    try:
        yield set_diagnostics_level
    finally:
        set_diagnostics_level(previous)


def _private_lines(caplog):
    return [
        rec.getMessage() for rec in caplog.records
        if rec.getMessage().startswith("TRACE kobo private_candidate_url ")
    ]


@pytest.mark.parametrize("level", ["normal", "verbose", "trace"])
def test_k3_both_distinct_kdrm_urls_revealed_only_in_explicit_trace(
    level, local_level, caplog,
):
    local_level(level)
    s, vault, _broker, account, posts = setup()
    ready(s, vault)
    first = _unknown_flepub()
    second = deepcopy(first)
    second["url"] = "https://cdn.kobo.com/second?token=PRIVATE_K3_SECOND"
    upstream = descriptor(candidate=first)
    upstream["acquisition"]["candidates"] = [first, second]
    vault.acquisition = lambda product, **kw: (
        posts.append((product, kw)) or deepcopy(upstream)
    )

    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            offer = selected(s, account, "book-1")
        assert offer.delivery == "unknown" and offer.delivery_offer_id == ""
        assert len(posts) == 1 and posts[0][1]["mode"] == "descriptor"
        trace = _private_lines(caplog)
        if level == "trace":
            assert len(trace) == 2
            assert "candidate_index=0 handling=kobo_kdrm" in trace[0]
            assert "candidate_index=1 handling=kobo_kdrm" in trace[1]
            assert repr(first["url"]) in trace[0]
            assert repr(second["url"]) in trace[1]
            assert "phase=offer" in trace[0] and "phase=offer" in trace[1]
        else:
            assert trace == []
            assert first["url"] not in caplog.text
            assert second["url"] not in caplog.text
    finally:
        s.close()


@pytest.mark.parametrize("level", ["normal", "verbose", "trace"])
def test_k1_rejected_initial_url_logged_only_with_trace_and_no_get(
    level, local_level, tmp_path, monkeypatch, caplog,
):
    local_level(level)
    s, vault, _broker, account, posts = setup()
    orig = vault.acquisition
    url = SIGNED + "#PRIVATE_K1_FRAGMENT"

    def upstream(product, **kw):
        value = orig(product, **kw)
        value["acquisition"]["candidates"][0]["url"] = url
        return value

    vault.acquisition = upstream
    calls = []
    fake_public_client(monkeypatch, lambda req: (
        calls.append(str(req.url)) or pytest.fail("Core must refuse before GET")
    ), [])

    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            offer = admitted(s, account)
            assert offer
            with pytest.raises(
                ProviderCapabilityError, match="^kobo_candidate_redirect_refused$",
            ):
                execute(s, account, offer, tmp_path)
        assert calls == []
        assert len(posts) == 2
        assert not list(tmp_path.iterdir())
        assert "EVENT kobo candidate_http_boundary cause=delivery_url_invalid" in caplog.text
        lines = _private_lines(caplog)
        if level == "trace":
            assert any("phase=offer" in row and repr(url) in row for row in lines)
            assert any("phase=initial" in row and repr(url) in row for row in lines)
        else:
            assert lines == []
            assert "PRIVATE_K1_FRAGMENT" not in caplog.text
            assert SIGNED not in caplog.text
    finally:
        s.close()


@pytest.mark.parametrize("level", ["normal", "verbose", "trace"])
def test_k1_rejected_redirect_url_logged_only_in_trace_and_never_get_target(
    level, local_level, monkeypatch, tmp_path, caplog,
):
    local_level(level)
    s, vault, _broker, account, _posts = setup()
    redirect = (
        "https://cdn-next.example/file.epub?signature=PRIVATE_REDIRECT"
        "#PRIVATE_FRAGMENT"
    )
    requested = []
    def handler(req):
        requested.append(str(req.url))
        assert str(req.url) == SIGNED
        return httpx.Response(302, headers={"location": redirect})
    fake_public_client(monkeypatch, handler, [])

    try:
        with caplog.at_level(logging.DEBUG, logger="cifereca"):
            offer = admitted(s, account)
            assert offer
            with pytest.raises(
                ProviderCapabilityError, match="^kobo_candidate_redirect_refused$",
            ):
                execute(s, account, offer, tmp_path)
        assert requested == [SIGNED]
        assert not list(tmp_path.iterdir())
        lines = _private_lines(caplog)
        if level == "trace":
            assert any("phase=initial" in row and repr(SIGNED) in row for row in lines)
            assert any("phase=redirect" in row and repr(redirect) in row for row in lines)
        else:
            assert lines == []
            assert "PRIVATE_REDIRECT" not in caplog.text
        assert "EVENT kobo candidate_http_url_refusal phase=redirect category=fragment_marker" in caplog.text
    finally:
        s.close()


def test_untrusted_rows_are_bounded_and_quoted_for_private_local_trace(
    local_level, caplog,
):
    local_level("trace")
    malicious = "https://example.org/file?secret=PRIVATE\\nFORGED WARNING"
    payload = {"acquisition": {"candidates": [
        {"url": malicious, "handling": {"not": "hashable"}},
        {"url": "https://example.org/" * 2000, "handling": "http_plain"},
        {"url": object(), "handling": "kobo_kdrm"},
    ]}}
    with caplog.at_level(logging.DEBUG, logger="cifereca"):
        trace_kobo_descriptor_urls(payload, product_id="book-1")
    lines = _private_lines(caplog)
    assert len(lines) == 1
    assert "handling=other" in lines[0]
    assert "PRIVATE\\\\nFORGED WARNING" in lines[0]
    assert "\\nFORGED WARNING" not in lines[0]
    assert repr(malicious) in lines[0]
