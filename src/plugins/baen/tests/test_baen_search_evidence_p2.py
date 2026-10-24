"""#2333: honest Baen public search coverage and typed query inputs.

No actual Baen network, accounts, acquisition, or external scraping.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_baen import BaenSession, DESCRIPTOR, UNVERIFIED_SEARCH_WARNING


class PoisonQuery:
    def __str__(self):
        raise AssertionError("PRIVATE_TOKEN_IS_NOT_PUBLIC_QUERY")


class QuerySubclass(str):
    pass


def make_session(body):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, text=body)

    obj = BaenSession(
        credential_id="public", label="", credentials={},
        options={}, config={}, diagnostics=None, transport=None,
    )
    obj.client.close()
    obj.client = httpx.Client(transport=httpx.MockTransport(handler))
    obj.connect()
    return obj, seen


@pytest.mark.parametrize("bad", [
    PoisonQuery(), QuerySubclass("PRIVATE"), 3, False, [], {},
    b"SECRET", "x" * 2049, "foo\nbar", "foo\x00bar",
    "foo\u202eBAR",
])
def test_bad_general_query_never_enters_baen_http_or_becomes_zero(bad):
    session, seen = make_session("<html>Unexpected</html>")
    try:
        with pytest.raises(ProviderCapabilityError, match="invalid_query") as err:
            session.search(bad)
        assert seen == []
        assert session.last_search_warnings == []
        assert "PRIVATE" not in str(err.value)
    finally:
        session.close()


def test_zero_markers_do_not_attest_query_or_pagination_on_legacy_route():
    session, seen = make_session(
        "<html><body>Showing 0-0 of 0 <div>No results</div></body></html>"
    )
    try:
        assert session.search("absent") == []
        assert len(seen) == 1
        assert session.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
    finally:
        session.close()


@pytest.mark.parametrize("html", [
    "<html><body>Old login content</body></html>",
    '<html><body><a href="/allbooks/private">More...</a></body></html>',
    "<html><body>Showing 1-20 of 82</body></html>",
    "<html><body>No results</body></html>",
    "<html><body>Showing 0-0 of 0</body></html>",
])
def test_missing_rows_without_two_public_markers_are_partial_not_proven_zero(html):
    session, seen = make_session(html)
    try:
        assert session.search("absent") == []
        assert len(seen) == 1
        assert session.last_search_warnings == [
            UNVERIFIED_SEARCH_WARNING,
            "partial_results : Baen : absence de résultats non attestée"
        ]
    finally:
        session.close()


def test_dom_warning_is_cleared_but_unqualified_search_warning_remains():
    bodies = [
        "<html>Unverified zero</html>",
        '<a href="/allbooks/example-book">An Attested Public Book</a>',
    ]
    session, seen = make_session("")
    n = iter(bodies)
    session.client.close()
    session.client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, text=next(n))
    ))
    try:
        assert session.search("missing") == []
        assert session.last_search_warnings
        rows = session.search("good", page=2, scope="digital")
        assert len(rows) == 1
        assert rows[0].owned is False and rows[0].downloadable is False
        assert session.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
    finally:
        session.close()


def test_baen_remains_opt_in_and_declares_only_general_field():
    cap = DESCRIPTOR.search_capabilities
    assert cap.fields == ("all",)
    assert cap.scopes == ("all", "digital")
    assert cap.pagination == "page"
    assert cap.max_pages == 101


@pytest.mark.parametrize("query", ["", "author words", None])
@pytest.mark.parametrize("page", [0, 2, 100])
def test_nonempty_page_is_always_partial_without_verified_server_semantics(query, page):
    session, seen = make_session(
        '<a href="/allbooks/public-book">A Public Book</a>'
    )
    try:
        for _ in range(2):
            rows = session.search(query, page=page)
            assert len(rows) == 1
            assert rows[0].owned is False
            assert rows[0].downloadable is False
            assert rows[0].download_url is None
            assert session.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
        assert len(seen) == 2  # One GET per call, no retry or extra page.
        assert all(request.method == "GET" for request in seen)
        assert all(request.url.params["page"] == str(page + 1) for request in seen)
    finally:
        session.close()


def test_transport_failure_clears_previous_partial_response_warning():
    from cifereca.plugin_api import ProviderError
    session, seen = make_session('<a href="/allbooks/book">Public Book</a>')
    try:
        assert len(session.search("first")) == 1
        assert session.last_search_warnings == [UNVERIFIED_SEARCH_WARNING]
        session.client.close()
        def unavailable(request):
            raise httpx.ConnectError("PRIVATE_UPSTREAM", request=request)
        session.client = httpx.Client(transport=httpx.MockTransport(unavailable))
        with pytest.raises(ProviderError, match="service_unavailable") as caught:
            session.search("second")
        assert session.last_search_warnings == []
        assert "PRIVATE_UPSTREAM" not in str(caught.value)
    finally:
        session.close()


def test_manifest_does_not_claim_server_search_qualified_or_enable_baen():
    import json
    from pathlib import Path
    manifest = json.loads((Path(__file__).resolve().parents[1] /
                           "cifereca-plugin.json").read_text())
    provider = manifest["plugins"][0]
    assert provider["enabled_by_default"] is False
    assert "restent non qualifiées" in provider["help_text"]
    assert "même vide" in provider["help_text"]
    assert any("non qualifié" in note for note in DESCRIPTOR.search_capabilities.notes)
