"""#4882: opt-in private URL tracing at Core's HTTPS decision boundary.

Synthetic URLs only. Production URL admission, DNS protection and returned
public errors are unchanged; trace callbacks never authorize requests.
"""
import httpx
import pytest

from cifereca.core import attested_https_delivery as delivery
from cifereca.logging_utils import diagnostics_level, set_diagnostics_level


FIRST = "https://books.provider.example/file.epub?signature=PRIVATE_INITIAL"
CDN = "https://cdn-provider.example/file.epub?signature=PRIVATE_REDIRECT"


@pytest.fixture
def private_trace_level():
    previous = diagnostics_level()
    try:
        yield set_diagnostics_level
    finally:
        set_diagnostics_level(previous)


@pytest.mark.parametrize("level", ["normal", "verbose", "trace"])
def test_refused_initial_url_only_reaches_trace_observer_in_trace(
    monkeypatch, tmp_path, private_trace_level, level,
):
    private_trace_level(level)
    opened = []
    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: opened.append(1) or pytest.fail("No GET allowed"),
    )
    traced = []
    url = FIRST + "#PRIVATE_FRAGMENT"
    with pytest.raises(ValueError, match="^delivery_url_invalid$"):
        delivery.download_attested_https(
            producer="provider.kobo.delivery", url=url,
            target=tmp_path / "none.epub", admission_current=lambda: True,
            on_trace_url=lambda phase, raw: traced.append((phase, raw)),
        )
    assert traced == ([("initial", url)] if level == "trace" else [])
    assert not opened
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("level", ["normal", "verbose", "trace"])
def test_refused_redirect_url_logged_only_under_trace_with_no_second_get(
    monkeypatch, tmp_path, private_trace_level, level,
):
    private_trace_level(level)
    visited = []
    redirect = CDN + "#PRIVATE_FRAGMENT"

    def handler(request):
        visited.append(str(request.url))
        assert str(request.url) == FIRST
        return httpx.Response(302, headers={"Location": redirect})

    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=False, trust_env=False,
        ),
    )
    traced = []
    refused = []
    with pytest.raises(ValueError, match="^delivery_url_invalid$"):
        delivery.download_attested_https(
            producer="provider.kobo.delivery", url=FIRST,
            target=tmp_path / "none.epub", admission_current=lambda: True,
            on_url_refusal=lambda phase, reason: refused.append((phase, reason)),
            on_trace_url=lambda phase, raw: traced.append((phase, raw)),
        )
    assert visited == [FIRST]
    assert refused == [("redirect", "fragment_marker")]
    assert traced == (
        [("initial", FIRST), ("redirect", redirect)]
        if level == "trace" else []
    )
    assert list(tmp_path.iterdir()) == []


def test_trace_callback_failure_never_changes_public_error_or_requests(
    monkeypatch, tmp_path, private_trace_level,
):
    private_trace_level("trace")
    created = []
    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: created.append(1) or pytest.fail("No GET allowed"),
    )
    def observer(*args):
        raise RuntimeError("PRIVATE_CALLBACK_EXCEPTION_SHOULD_NOT_LEAK")

    with pytest.raises(ValueError, match="^delivery_url_invalid$") as exc:
        delivery.download_attested_https(
            producer="provider.kobo.delivery", url=FIRST + "#PRIVATE",
            target=tmp_path / "none.epub", admission_current=lambda: True,
            on_trace_url=observer,
        )
    assert not created
    assert "PRIVATE" not in str(exc.value)
    assert not list(tmp_path.iterdir())


def test_noncallable_raw_observer_fails_closed_before_network(
    monkeypatch, tmp_path, private_trace_level,
):
    private_trace_level("trace")
    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: pytest.fail("No GET allowed"),
    )
    with pytest.raises(ValueError, match="^delivery_parameters_invalid$"):
        delivery.download_attested_https(
            producer="provider.kobo.delivery", url=FIRST,
            target=tmp_path / "none.epub", admission_current=lambda: True,
            on_trace_url="PRIVATE_NOT_A_CALLBACK",
        )
    assert list(tmp_path.iterdir()) == []


def test_valid_redirect_in_trace_remains_downloadable(
    monkeypatch, tmp_path, private_trace_level,
):
    private_trace_level("trace")
    requested = []
    def handler(req):
        requested.append(str(req.url))
        if str(req.url) == FIRST:
            return httpx.Response(307, headers={"Location": CDN})
        assert str(req.url) == CDN
        return httpx.Response(200, content=b"synthetic EPUB payload")

    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False, follow_redirects=False,
        ),
    )
    traced = []
    receipt = delivery.download_attested_https(
        producer="provider.kobo.delivery", url=FIRST,
        target=tmp_path / "received.epub", admission_current=lambda: True,
        on_trace_url=lambda phase, raw: traced.append((phase, raw)),
    )
    assert receipt.redirects == 1
    assert receipt.path.read_bytes() == b"synthetic EPUB payload"
    assert requested == [FIRST, CDN]
    assert traced == [("initial", FIRST), ("redirect", CDN)]
