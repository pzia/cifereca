"""#2333: honest Baen public search coverage and typed query inputs.

No actual Baen network, accounts, acquisition, or external scraping.
"""
import httpx
import pytest

from cifereca.plugin_api import ProviderCapabilityError
from cifereca_baen import BaenSession, DESCRIPTOR


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


def test_official_zero_marker_is_an_attested_empty_page():
    session, seen = make_session(
        "<html><body>Showing 0-0 of 0 <div>No results</div></body></html>"
    )
    try:
        assert session.search("absent") == []
        assert len(seen) == 1
        assert session.last_search_warnings == []
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
            "partial_results : Baen : absence de résultats non attestée"
        ]
    finally:
        session.close()


def test_warning_is_cleared_on_successful_following_page():
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
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_baen_remains_opt_in_and_declares_only_general_field():
    cap = DESCRIPTOR.search_capabilities
    assert cap.fields == ("all",)
    assert cap.scopes == ("all", "digital")
    assert cap.pagination == "page"
    assert cap.max_pages == 101
