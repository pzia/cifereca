"""#1917: accepted Paris scope aliases never widen to both Syracuse catalogues.

All calls are synthetic; no Paris credentials, HTTP, loans or DRM.
"""
import pytest

from cifereca_paris import ParisProviderSession
from test_search_capability_contract_p1 import FakeClient, make_session


@pytest.mark.parametrize("incoming,canonical", [
    (" DIGITAL ", "digital"),
    ("Digital", "digital"),
    (" PHYSICAL ", "physical"),
    ("Physical", "physical"),
    (" ALL ", "all"),
    ("All", "all"),
])
def test_accepted_scope_is_canonical_before_catalogue_dispatch(incoming, canonical):
    fake = FakeClient()
    session = make_session(fake)
    try:
        assert session.search("Doyle", scope=incoming) == []
        assert fake.calls == [("Doyle", canonical, 0)]
        assert session.last_search_warnings == (
            ["Catalogue physique momentanément indisponible"]
            if canonical == "all" else []
        )
    finally:
        session.close()


@pytest.mark.parametrize("incoming,expected", [
    (" DIGITAL ", "EBOOKS"),
    (" PHYSICAL ", "CATALOGUE"),
])
def test_real_paris_client_dispatches_only_the_requested_catalogue(incoming, expected):
    session = make_session(FakeClient())
    # Patch the actual ParisClient dispatcher rather than a fake client whose
    # permissive search() could conceal an unintended all-catalogue search.
    real_client = session.client
    from cifereca_paris_client import ParisClient
    actual = ParisClient("https://example.test", diagnostics=session.diagnostics)
    calls = []
    actual._search_scenario = lambda _q, scenario, _page, _size: (
        calls.append(scenario) or []
    )
    session.client = actual
    try:
        assert session.search("Doyle", scope=incoming) == []
        assert calls == [expected]
        assert session.last_search_warnings == []
    finally:
        session.close()
