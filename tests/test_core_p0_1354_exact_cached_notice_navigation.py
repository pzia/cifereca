"""#1354: exact SQLite-only recommendations → shared Search navigation.

Real LibraryService/SQLite mirror, synthetic accounts/sessions and headless
navigation: no provider search, holdings, loan, ADEPT, token or network I/O.
"""
from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.known_search_notice_navigation import (
    _exact_known_row, can_open_known_search_notice, open_known_search_notice,
)
from cifereca.core.search_orchestration import mirrored_catalogue_scoped_id
from tests.test_catalog_083 import _service
from tests.test_core_p0_1354_mirrored_source_scope import notice


def _prepare(tmp_path, *, add_b=True):
    service = _service(str(tmp_path))
    events = []
    service.searchable_providers = lambda: [("paris", "Paris")]
    loans = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("no network"))
    service.holdings = loans
    for credential in (("reader-A", "reader-B") if add_b else ("reader-A",)):
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": credential, "enabled": True,
        })
        service._sessions[service._session_key("paris", credential)] = NS(
            connected=True, _closed=False,
            provider_id="paris", credential_id=credential,
        )
    rows = [notice(), notice(credential="reader-B"),
            notice(docbase="EDITION-2"),
            notice(collection="catalogue-B")]
    assert service._mirror_remote_results(rows, identity_id="default") == 4
    rowset = []
    win = NS(
        service=service, _closed=False, _search_generation=1,
        _search_detail_request_generation=1, _last_search_query="old",
        navigate=lambda name: events.append(("navigate", name)),
        search_controls=NS(set_state=lambda state, *, notify=False: (
            events.append(("query", state, notify))
        )),
        _populate_local_search=lambda rows: (
            events.append(("local", rows)), rowset.extend(rows)
        ),
        search_page=NS(select_key=lambda key: events.append(("select", key)) or True),
    )
    coords = dict(provider="paris", credential="reader-A",
                  record_id="same-supplier-record", docbase="EDITION-1",
                  source_collection="catalogue-A", identity_id="default")
    return service, win, events, rowset, coords


def test_exact_v2_cached_source_opens_only_one_search_row_and_never_requests_holdings(
    tmp_path,
):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        exact = _exact_known_row(win, **coords)
        assert exact is not None
        assert exact["state"] == "remote_catalog" and exact["local"] is False
        assert can_open_known_search_notice(win, **coords)
        assert events == []
        assert open_known_search_notice(win, **coords)
        assert events[0] == ("navigate", "search")
        assert events[1][0] == "query"
        assert events[1][1].providers == ("paris",)
        assert events[2][0] == "local" and len(events[2][1]) == 1
        assert events[3] == ("select", exact["key"])
        assert len(rows) == 1 and rows[0]["item_id"] == coords["record_id"]
        assert rows[0]["metadata"]["source_credential"] == "reader-A"
        assert rows[0]["metadata"]["source_collection"] == "catalogue-A"
        assert win._search_generation == 2
        assert win._search_detail_request_generation == 2
        assert service.storage.acquisitions() == []
        assert service.storage.entitlements() == []
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    {"credential": "reader-B"},
    {"docbase": "EDITION-2"},
    {"source_collection": "catalogue-B"},
])
def test_same_book_id_isolated_by_credential_docbase_and_collection(
    tmp_path, change,
):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        source = dict(coords, **change)
        assert can_open_known_search_notice(win, **source)
        assert open_known_search_notice(win, **source)
        assert len(rows) == 1
        projected = rows[0]["metadata"]
        assert projected["source_credential"] == source["credential"]
        assert projected["docbase"] == source["docbase"]
        assert (projected["source_collection"] or "") == source["source_collection"]
        assert rows[0]["key"] == "known:" + mirrored_catalogue_scoped_id(
            "default", "paris", "same-supplier-record", source["credential"],
            source["docbase"], source["source_collection"],
        )
    finally:
        service.close()


@pytest.mark.parametrize("change", [
    {"identity_id": "alex"}, {"credential": ""}, {"docbase": ""},
    {"record_id": "different-record"}, {"source_collection": "wrong"},
    {"source_collection": None}, {"source_collection": "x" * 513},
])
def test_invalid_ambiguous_or_missing_original_coordinates_fail_closed(tmp_path, change):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        source = dict(coords, **change)
        assert not can_open_known_search_notice(win, **source)
        assert not open_known_search_notice(win, **source)
        assert events == [] and rows == []
    finally:
        service.close()


def test_B_disconnected_with_A_still_connected_cannot_reuse_A_session(tmp_path):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        b = dict(coords, credential="reader-B")
        assert can_open_known_search_notice(win, **b)
        service._sessions.pop(service._session_key("paris", "reader-B"))
        assert can_open_known_search_notice(win, **coords)
        assert not can_open_known_search_notice(win, **b)
        assert not open_known_search_notice(win, **b)
        assert not events and not rows
    finally:
        service.close()


def test_disabled_connection_never_exposes_cached_holdings_action(tmp_path):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        assert can_open_known_search_notice(win, **coords)
        # Account suspension/disablement must trump a connected flag that is
        # still True in the stale in-memory provider session.
        service.storage.provider_connection_put({
            "identity_id": "default", "provider_id": "paris",
            "credential_id": "reader-A", "enabled": False,
        })
        assert not can_open_known_search_notice(win, **coords)
        assert not open_known_search_notice(win, **coords)
        assert events == [] and rows == []
        assert service.storage.acquisitions() == []
    finally:
        service.close()


def test_explicit_navigation_revokes_when_identity_or_catalogue_changes(tmp_path):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        def navigate(_name):
            events.append(("navigate", "search"))
            service.current_identity = next(
                row for row in service.identities if row.identity_id == "alex"
            )
        win.navigate = navigate
        assert not open_known_search_notice(win, **coords)
        assert events == [("navigate", "search")]
        assert rows == []
    finally:
        service.close()


def test_unqualified_legacy_mirror_can_be_searched_but_not_navigated(tmp_path):
    service, win, events, rows, coords = _prepare(tmp_path)
    try:
        legacy = replace(notice(), source_credential="", docbase="")
        service._mirror_remote_results([legacy], identity_id="default")
        matching = service.search_known_catalog("livre partagé", identity_id="default")
        assert any(row["metadata"].get("catalogue_scope_version") is None for row in matching)
        assert not can_open_known_search_notice(win, **dict(coords, record_id="unknown"))
        assert not events
    finally:
        service.close()
