"""#3252: Numilog private search rechecks owner after local title filtering.

Synthetic identities and rows; no HTTP, login, purchase, ACSM or file IO.
"""
from types import SimpleNamespace

import pytest

from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_owned_inventory_owner_rebind import session


class Title:
    def __init__(self, text, after_read):
        self.text = text
        self.after_read = after_read

    def casefold(self):
        self.after_read()
        return self.text.casefold()


@pytest.mark.parametrize("owner_after", ["identity-B", None, "identity-A"])
@pytest.mark.parametrize("scope", ["all", "digital"])
def test_search_checks_owner_after_local_projection(owner_after, scope):
    s, broker = session()
    seen = []
    def change():
        seen.append("title")
        broker.owner = owner_after
    s.library = lambda: [SimpleNamespace(title=Title("Livre unique", change))]
    try:
        if owner_after == "identity-A":
            rows = s.search("Livre", scope=scope)
            assert len(rows) == 1
        else:
            with pytest.raises(ProviderCapabilityError, match="identité propriétaire modifiée"):
                s.search("Livre", scope=scope)
        assert seen == ["title"]
        assert broker.calls == 2
    finally:
        s.close()


def test_public_catalog_route_does_not_require_private_owner():
    s, broker = session()
    broker.owner = None
    calls = []
    s.search_public = lambda query, **kw: (calls.append((query, kw)) or ["public"])
    s.library = lambda: pytest.fail("private library called for public query")
    try:
        assert s.search("Livre", scope="catalog") == ["public"]
        assert calls == [("Livre", {"page": 0, "field": "all"})]
        assert broker.calls == 0
    finally:
        s.close()


def test_rejected_private_field_causes_no_owner_or_library_read():
    s, broker = session()
    s.library = lambda: pytest.fail("private library invoked")
    try:
        with pytest.raises(ProviderCapabilityError, match="unsupported_field"):
            s.search("Livre", field="author")
        assert broker.calls == 0
    finally:
        s.close()
