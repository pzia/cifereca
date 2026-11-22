"""Exact private series C -> selection controller, without a live provider."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_private_series import (
    _key, capture_series_call, persist_series_rows, stamp_series_rows,
)
from cifereca.core.notice_cover_projection import notice_cover_document_ref
from cifereca.core.notice_cover_repository import evict_notice_cover, read_notice_cover
from cifereca.core.source_context import revoke_source_context
from cifereca.gui import notice_c_private_series_controller as controller
from cifereca.models import SearchResult
from cifereca.storage import Storage
from tests.test_core_p0_6397_authenticated_web_choices import Host


@pytest.fixture
def selected(tmp_path):
    path = tmp_path / "series.sqlite"
    with closing(Storage(path)) as db:
        core = Host(db)
        account = core.account("alice")
        row = SearchResult(
            id="row", record_id="record", docbase="fixture", medium="digital",
            title="Fixture", author=None, type=None, format=None, publisher=None,
            published_at=None, description=None, thumbnail_url=None, friendly_url=None,
            scenario="fixture", source_provider=account.provider_id,
            source_credential=account.credential_id, source_account_id=None,
            metadata={
                "series": ["Cycle témoin"],
                "bibliography_evidence": {"series": {
                    "source": account.provider_id + ".fixture.search",
                    "source_field": "Search.fixture",
                    "scope": "fixture",
                    "confidence": "linked_search_relation",
                    "freshness": "search_response",
                }},
            },
        )
        call = capture_series_call(
            core, core._sessions[(account.provider_id, account.credential_id)], "alice",
        )
        assert call is not None
        stamp_series_rows(core, call, [row])
        assert persist_series_rows(core, [row], identity_id="alice") == 1
        document = notice_cover_document_ref(_key(row, "alice"))
        source = read_notice_cover(db, document)
        assert evict_notice_cover(db, document, expected_epoch=source.epoch)
    # New Core/SQLite, no live session and no series in the DTO.
    with closing(Storage(path)) as db:
        core = Host(db)
        row = replace(row, metadata={})
        pending, displayed = [], []
        generation = [1]
        panel = NS(add_retained_search_series=displayed.append, get_mapped=lambda: True)
        cell = NS(key="selected")
        selection = NS(get_selected_item=lambda: cell)
        host = NS(
            service=core, search_page=NS(detail=panel, selection=selection),
            search_objects={cell.key: row}, _route_display_token=object(),
            runner=NS(submit=lambda label, work, done, fail, **kwargs:
                      pending.append((work, done, fail))),
            _search_detail_context_current=lambda key, ctx:
                key == row.id and ctx[0] == generation[0],
        )
        yield host, row, panel, cell, pending, displayed, generation
        assert db.entitlements() == db.acquisitions() == db.placements() == []
        assert core._sessions == {}


def request(host, row):
    return controller.request_selected_retained_series(
        host, row, selected_id=row.id, context=(1, 1, 1, "alice"),
    )


def test_retained_series_after_restart_one_passive_delivery(selected):
    host, row, panel, cell, pending, displayed, _ = selected
    assert request(host, row)
    assert not request(host, row)
    work, done, _ = pending.pop()
    relation = work()
    assert relation.label == "Cycle témoin"
    assert not displayed
    done(relation)
    done(relation)
    assert displayed == [relation]
    assert row.metadata == {}
    assert not request(host, row)


@pytest.mark.parametrize("change", [
    "selection", "replacement", "record", "scenario", "revision", "route",
    "render", "generation", "source", "account", "core", "unmap", "series",
])
def test_c_read_cannot_be_delivered_to_obsolete_render(selected, change):
    host, row, panel, cell, pending, displayed, generation = selected
    assert request(host, row)
    work, done, _ = pending.pop()
    relation = work()
    assert relation is not None
    if change == "selection":
        host.search_page.selection.get_selected_item = lambda: NS(key=cell.key)
        # A captured bound function may remain callable; the original selection
        # object also has to be checked against the current selected cell.
        host.search_page.selection = NS(get_selected_item=lambda: NS(key=cell.key))
    elif change == "replacement":
        host.search_objects[cell.key] = replace(row)
    elif change == "record":
        row.record_id = "other"
    elif change == "scenario":
        row.scenario = "other"
    elif change == "revision":
        row.revision_id = "other"
    elif change == "route":
        host._route_display_token = object()
    elif change == "render":
        panel._private_series_request_token = None
    elif change == "generation":
        generation[0] += 2
    elif change == "source":
        revoke_source_context(host.service)
    elif change == "account":
        host.service._account_context_epoch = 2
    elif change == "core":
        host.service = NS()
    elif change == "unmap":
        panel.get_mapped = lambda: False
    elif change == "series":
        row.metadata["series"] = ["Existing series"]
    done(relation)
    done(relation)
    assert not displayed


def test_visible_series_is_not_repeated(selected):
    host, row, panel, cell, pending, displayed, _ = selected
    row.metadata = {"catalogue_metadata": {"series": ["Already visible"]}}
    assert not request(host, row)
    assert not pending and not displayed


def test_provider_disabled_before_worker_and_public_rows_refused(selected):
    host, row, panel, cell, pending, displayed, _ = selected
    assert request(host, row)
    host.service.enabled = False
    work, done, _ = pending.pop()
    assert work() is None
    done(None)
    assert not displayed
    panel._private_series_request_token = None
    row.source_credential = "__public__"
    assert not request(host, row)
    assert not pending


def test_account_scoped_series_retained_only_for_exact_registered_account(tmp_path):
    from cifereca.core.notice_c_private_series import read_series_for_search
    from cifereca.core.search_orchestration import capture_search_read_scope

    path = tmp_path / "account-series.sqlite"
    with closing(Storage(path)) as db:
        host = Host(db)
        a = host.account("alice")
        row = SearchResult(
            id="row", record_id="record", docbase="fixture", medium="digital",
            title="Fixture", author=None, type=None, format=None, publisher=None,
            published_at=None, description=None, thumbnail_url=None, friendly_url=None,
            scenario="fixture", source_provider=a.provider_id,
            source_credential=a.credential_id, source_account_id=a.unique_id,
            metadata={"series": ["Cycle exact"],
                      "bibliography_evidence": {"series": {
                          "source": a.provider_id + ".fixture.search",
                          "source_field": "Search.fixture", "scope": "fixture",
                          "confidence": "linked_search_relation", "freshness": "search_response",
                      }}},
        )
        call = capture_series_call(host, host._sessions[(a.provider_id, a.credential_id)], "alice")
        foreign = replace(row, source_account_id="other-account")
        stamp_series_rows(host, call, [foreign, row])
        assert persist_series_rows(host, [foreign, row], identity_id="alice") == 1
        key = _key(row, "alice")
        assert key.account_id == a.unique_id
        assert key.edition_id != _key(replace(row, source_account_id=None), "alice").edition_id
        doc = notice_cover_document_ref(key)
        source = read_notice_cover(db, doc)
        assert evict_notice_cover(db, doc, expected_epoch=source.epoch)
    with closing(Storage(path)) as db:
        host = Host(db)
        row = replace(row, metadata={})
        scope = capture_search_read_scope(host, "alice")
        assert read_series_for_search(host, row, reader_scope=scope).label == "Cycle exact"
        for modified in (replace(row, source_account_id=None), foreign):
            assert read_series_for_search(host, modified, reader_scope=scope) is None
        assert read_series_for_search(host, row, reader_scope=capture_search_read_scope(
            host, "bob",
        )) is None
        with db._lock, db._connect() as sql:
            sql.execute("DELETE FROM provider_accounts WHERE account_id=?", (a.unique_id,))
        assert read_series_for_search(host, row, reader_scope=scope) is None
        assert host._sessions == {}
        assert db.entitlements() == db.acquisitions() == db.placements() == []
