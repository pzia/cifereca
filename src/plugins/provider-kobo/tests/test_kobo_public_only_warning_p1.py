"""#1917: explicit Kobo coverage when personal account is not attested."""
import pytest

from tests.test_kobo_search_public_only_unbound_p1 import _case


@pytest.mark.parametrize("scope", ("all", "digital"))
def test_unbound_kobo_discloses_public_only_scope_even_when_results_exist(scope):
    session, paths = _case()
    try:
        session.connected = False
        rows = session.search("public", scope=scope)
        assert paths == ["/api/v2/catalog/search"]
        assert [row.record_id for row in rows] == ["public-edition"]
        assert rows[0].owned is False
        assert session.last_search_warnings == [
            "partial_results : compte Kobo non attesté ; catalogue public seulement"
        ]
        # The channel belongs only to the current attempt, not future
        # explicit catalogue searches.
        session.search("public", scope="catalog")
        assert session.last_search_warnings == []
    finally:
        session.close()


def test_verified_combined_search_does_not_claim_partial():
    session, paths = _case(library_status=200)
    try:
        rows = session.search("public", scope="all")
        assert len(rows) == 2
        assert session.last_search_warnings == []
        assert paths == ["/api/v2/catalog/search", "/api/v2/library/search"]
    finally:
        session.close()
