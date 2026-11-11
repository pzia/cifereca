"""Selected-source acquisition dispatch belongs to session ABI, not provider IDs.

No network, entitlement storage or provider import is needed to choose a path.
"""
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.services import LibraryService
from tests.test_core_p0_1862_numilog_store_mirror import _row


def _service(session):
    sessions = {"fixture:credential": session}
    return NS(
        _sessions=sessions,
        _session_key=lambda provider, credential: f"{provider}:{credential}",
        acquire_direct_item=lambda _item: pytest.fail("no legacy provider GET"),
        catalog_index_path=lambda *_a, **_kw: None,
        identity_for_connection=lambda *_a, **_kw: None,
    )


@pytest.mark.parametrize("branch,shape", [
    ("purchases", None),
    ("", None),
    (123, None),
    (None, ("library", "PRIVATE", "credential_scoped_record_v1")),
])
def test_any_selected_source_declaration_uses_canonical_path(
    monkeypatch, branch, shape,
):
    from cifereca.core import selected_owned_search
    item = replace(
        _row("credential", 1),
        source_provider="fixture", source_credential="credential",
    )
    session = NS(selected_purchase_branch=branch)
    if shape is not None:
        session.private_inventory_row_contract = shape
    service = _service(session)
    sentinel = object()
    calls = []
    monkeypatch.setattr(
        selected_owned_search, "obtain_selected_owned",
        lambda received_service, received_item: (
            calls.append((received_service, received_item)) or sentinel
        ),
    )
    assert LibraryService.obtain_search_item(service, item) is sentinel
    assert calls == [(service, item)]


def test_undeclared_session_keeps_legacy_materialization(monkeypatch):
    from cifereca.core import materialization
    item = replace(
        _row("credential", 2),
        source_provider="fixture", source_credential="credential",
    )
    service = _service(NS())
    calls = []
    sentinel = object()

    class Legacy:
        def __init__(self, **kwargs):
            calls.append(("init", kwargs))
        def obtain(self, selected):
            calls.append(("obtain", selected))
            return sentinel

    monkeypatch.setattr(materialization, "MaterializationCoordinator", Legacy)
    assert LibraryService.obtain_search_item(service, item) is sentinel
    assert [name for name, _ in calls] == ["init", "obtain"]
    assert calls[1][1] is item


def test_missing_selected_session_cannot_create_an_implicit_offer(monkeypatch):
    """Routing alone never connects a supplier or creates a selected offer."""
    from cifereca.core import materialization
    item = replace(
        _row("credential", 3),
        source_provider="fixture", source_credential="credential",
    )
    service = _service(None)
    service._sessions.clear()

    class Legacy:
        def __init__(self, **_kwargs):
            pass
        def obtain(self, selected):
            assert selected is item
            raise RuntimeError("no implicit session creation")

    monkeypatch.setattr(materialization, "MaterializationCoordinator", Legacy)
    with pytest.raises(RuntimeError, match="no implicit session creation"):
        LibraryService.obtain_search_item(service, item)
