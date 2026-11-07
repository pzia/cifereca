"""#4882: closed HTTPS URL diagnostics at the real Core decision boundary.

All endpoints and tokens are synthetic. The HTTPX network is mocked;
the callback never receives an arbitrary URL, Location or exception.
"""
import httpx
import pytest

from cifereca.core import attested_https_delivery as delivery


FIRST = "https://books.provider.example/get.epub?token=PRIVATE_SYNTHETIC"
CDN = "https://cdn-third-party.example/epub.epub?token=PRIVATE_CDN"


@pytest.mark.parametrize(("url", "code", "category"), [
    (FIRST + "#", "delivery_url_invalid", "fragment_marker"),
    ("http://books.provider.example/file.epub", "delivery_url_invalid",
     "scheme_not_https"),
    ("https://user:secret@books.provider.example/file.epub",
     "delivery_url_invalid", "embedded_credentials"),
    ("https://127.0.0.1/file.epub", "delivery_ip_literal_refused",
     "ip_literal"),
    ("https://books.provider.example:0/file.epub",
     "delivery_url_invalid", "port_invalid"),
    ("https://books.provider.example/file\\bad",
     "delivery_url_invalid", "backslash"),
])
def test_initial_url_closed_reason_before_network(
    monkeypatch, tmp_path, url, code, category,
):
    clients = []
    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: clients.append(kwargs) or pytest.fail("no HTTP client"),
    )
    reported = []
    with pytest.raises(ValueError, match=f"^{code}$"):
        delivery.download_attested_https(
            producer="core.selected.delivery",
            url=url, target=tmp_path / "not-created.epub",
            admission_current=lambda: True,
            on_url_refusal=lambda phase, reason: reported.append((phase, reason)),
        )
    assert reported == [("initial", category)]
    assert not clients
    assert list(tmp_path.iterdir()) == []
    for secret in ("PRIVATE_SYNTHETIC", "PRIVATE_CDN", "https://", "secret"):
        assert secret not in str(reported)


@pytest.mark.parametrize(("location", "code", "category"), [
    (CDN + "#", "delivery_url_invalid", "fragment_marker"),
    ("http://cdn-third-party.example/epub.epub?token=PRIVATE_CDN",
     "delivery_url_invalid", "scheme_not_https"),
    ("https://127.0.0.1/file.epub?token=PRIVATE_CDN",
     "delivery_ip_literal_refused", "ip_literal"),
    ("https://user:secret@cdn-third-party.example/file.epub",
     "delivery_url_invalid", "embedded_credentials"),
])
def test_redirect_url_closed_reason_refuses_before_second_get(
    monkeypatch, tmp_path, location, code, category,
):
    requested = []
    def public_client(*, producer, timeout):
        assert producer == "core.selected.delivery"
        return httpx.Client(
            transport=httpx.MockTransport(lambda request: (
                requested.append(str(request.url))
                or httpx.Response(302, headers={"location": location})
            )),
            trust_env=False, follow_redirects=False,
        )

    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client", public_client,
    )
    reported = []
    with pytest.raises(ValueError, match=f"^{code}$"):
        delivery.download_attested_https(
            producer="core.selected.delivery",
            url=FIRST, target=tmp_path / "not-created.epub",
            admission_current=lambda: True,
            on_url_refusal=lambda phase, reason: reported.append((phase, reason)),
        )
    assert requested == [FIRST]
    assert reported == [("redirect", category)]
    assert list(tmp_path.iterdir()) == []


def test_valid_redirect_has_no_refusal_observations(monkeypatch, tmp_path):
    called = []
    def handler(request):
        called.append(str(request.url))
        if str(request.url) == FIRST:
            return httpx.Response(302, headers={"location": CDN})
        assert str(request.url) == CDN
        return httpx.Response(200, content=b"synthetic valid bytes")

    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: httpx.Client(
            transport=httpx.MockTransport(handler),
            trust_env=False, follow_redirects=False,
        ),
    )
    observed = []
    receipt = delivery.download_attested_https(
        producer="core.selected.delivery", url=FIRST,
        target=tmp_path / "received.epub",
        admission_current=lambda: True,
        on_url_refusal=lambda phase, reason: observed.append((phase, reason)),
    )
    assert receipt.redirects == 1
    assert receipt.path.read_bytes() == b"synthetic valid bytes"
    assert called == [FIRST, CDN]
    assert observed == []


def test_faulty_observer_cannot_change_refusal(monkeypatch, tmp_path):
    attempts = []
    def faulty_observer(phase, reason):
        attempts.append((phase, reason))
        raise RuntimeError("SHOULD_NEVER_LEAK_PRIVATE_TOKEN")

    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: pytest.fail("no network"),
    )
    with pytest.raises(ValueError, match="^delivery_url_invalid$") as exc:
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST + "#",
            target=tmp_path / "not-created.epub",
            admission_current=lambda: True,
            on_url_refusal=faulty_observer,
        )
    assert attempts == [("initial", "fragment_marker")]
    assert "PRIVATE" not in str(exc.value)
    assert not list(tmp_path.iterdir())


def test_noncallable_observer_fails_before_network(monkeypatch, tmp_path):
    monkeypatch.setattr(
        delivery.HTTPX_TRANSPORT, "public_client",
        lambda **kwargs: pytest.fail("no network"),
    )
    with pytest.raises(ValueError, match="^delivery_parameters_invalid$"):
        delivery.download_attested_https(
            producer="core.selected.delivery", url=FIRST,
            target=tmp_path / "not-created.epub",
            admission_current=lambda: True,
            on_url_refusal="https://private.invalid/token",
        )
    assert not list(tmp_path.iterdir())
