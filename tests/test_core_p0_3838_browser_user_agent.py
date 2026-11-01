"""#3838: Core HTTP browser identity, explicit exceptions, redirect boundaries."""
import asyncio

import httpx
import pytest

from cifereca.http_trace import (
    BROWSER_USER_AGENT, HTTPX_TRANSPORT, TraceHTTPClient, TraceAsyncHTTPClient,
)


def test_browser_user_agent_on_core_clients_and_send_stream():
    seen = []
    def respond(request):
        seen.append(request)
        return httpx.Response(200, text="ok")
    with HTTPX_TRANSPORT.client(
        producer="core.browser.test", transport=httpx.MockTransport(respond),
        trust_env=False, headers={"Accept-Language": "fr-FR"},
    ) as client:
        built = client.build_request("GET", "https://example.test/a")
        assert built.headers["User-Agent"] == BROWSER_USER_AGENT
        assert client.send(built).text == "ok"
        with client.stream("GET", "https://example.test/b") as response:
            assert response.read() == b"ok"
    assert len(seen) == 2
    assert {r.headers["User-Agent"] for r in seen} == {BROWSER_USER_AGENT}
    assert all(r.headers["Accept-Language"] == "fr-FR" for r in seen)


def test_header_inputs_are_not_mutated_and_explicit_exception_preserved():
    provided = httpx.Headers([("x-other", "one"), ("x-other", "two")])
    with HTTPX_TRANSPORT.client(
        producer="core.browser.test", headers=provided,
        transport=httpx.MockTransport(lambda _: httpx.Response(204)),
    ) as client:
        assert client.headers["User-Agent"] == BROWSER_USER_AGENT
        assert client.headers.get_list("x-other") == ["one", "two"]
    assert "user-agent" not in provided

    # A documented provider contract may require a specific contact UA.
    with HTTPX_TRANSPORT.client(
        producer="provider.gutenberg",
        headers={"user-agent": "contact-required/1.0 (+https://example.test/contact)"},
        transport=httpx.MockTransport(lambda _: httpx.Response(204)),
    ) as client:
        assert client.get("https://example.test/").request.headers["user-agent"].startswith(
            "contact-required/1.0"
        )


def test_browser_identity_survives_cross_origin_redirect_without_leaking_auth():
    seen = []
    def respond(request):
        seen.append(request)
        if request.url.host == "shop.example.test":
            return httpx.Response(
                302, headers={
                    "Location": "https://delivery.example.test/signed.acsm",
                    "Set-Cookie": "session=synthetic; Secure; Path=/",
                }
            )
        return httpx.Response(200, content=b"acsm")

    with HTTPX_TRANSPORT.client(
        producer="core.redirect.test", follow_redirects=True, trust_env=False,
        transport=httpx.MockTransport(respond),
        headers={"Authorization": "Bearer synthetic"},
    ) as client:
        assert client.get("https://shop.example.test/owned").content == b"acsm"
    assert len(seen) == 2
    assert [r.url.host for r in seen] == ["shop.example.test", "delivery.example.test"]
    assert all(r.headers["User-Agent"] == BROWSER_USER_AGENT for r in seen)
    assert seen[0].headers["Authorization"] == "Bearer synthetic"
    assert "authorization" not in seen[1].headers
    assert "cookie" not in seen[1].headers


def test_public_pinned_client_inherits_core_browser_identity():
    with HTTPX_TRANSPORT.public_client(producer="core.pinned.test", timeout=2) as client:
        assert client.headers["User-Agent"] == BROWSER_USER_AGENT
        assert client.follow_redirects is False


def test_direct_wrappers_and_async_service_inherit_core_default():
    observed = []
    async def run():
        async with HTTPX_TRANSPORT.async_client(
            producer="core.async.test",
            transport=httpx.MockTransport(lambda request: (
                observed.append(request) or httpx.Response(200)
            )),
        ) as client:
            await client.get("https://async.example.test/")
    asyncio.run(run())
    assert observed[0].headers["User-Agent"] == BROWSER_USER_AGENT
    with TraceHTTPClient(
        producer="core.wrapper.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    ) as client:
        assert client.headers["User-Agent"] == BROWSER_USER_AGENT
    async def direct():
        async with TraceAsyncHTTPClient(
            producer="core.wrapper.async.test",
            transport=httpx.MockTransport(lambda r: httpx.Response(200)),
        ) as client:
            assert client.headers["User-Agent"] == BROWSER_USER_AGENT
    asyncio.run(direct())


@pytest.mark.parametrize("value", [
    {"User-Agent": ""},
    {"user-agent": "    "},
    httpx.Headers([("User-Agent", "Mozilla/5.0"), ("user-agent", "legacy/1")]),
])
def test_empty_or_duplicate_explicit_user_agent_is_not_a_valid_protocol_exception(value):
    with pytest.raises(ValueError, match="invalid explicit User-Agent"):
        HTTPX_TRANSPORT.client(
            producer="core.browser.test", headers=value,
            transport=httpx.MockTransport(lambda _: pytest.fail("unexpected network")),
        )
    # Caller-owned Headers or dictionaries must never be modified.
    if isinstance(value, dict):
        assert "User-Agent" in value or "user-agent" in value


def test_nonempty_protocol_exception_remains_supported_with_redirects():
    seen = []
    def respond(request):
        seen.append(request)
        if request.url.host == "first.example.test":
            return httpx.Response(302, headers={"location": "https://second.example.test/"})
        return httpx.Response(200)
    with HTTPX_TRANSPORT.client(
        producer="provider.protocol-exception",
        headers={"User-Agent": "provider-required/2"},
        follow_redirects=True, trust_env=False,
        transport=httpx.MockTransport(respond),
    ) as client:
        client.get("https://first.example.test/")
    assert len(seen) == 2
    assert [request.headers["User-Agent"] for request in seen] == [
        "provider-required/2", "provider-required/2",
    ]
