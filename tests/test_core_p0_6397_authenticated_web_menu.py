"""#6397: real private SQLite C, shared GTK controller, no provider call."""
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace as NS

import pytest

from cifereca.core.notice_c_authenticated_web_choices import FIELD
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_private_search_web import _source_key
from cifereca.core.notice_cover_repository import persist_notice_cover
from cifereca.core.notice_source_observation import admit_source_notice
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.gui import notice_c_web_action_controller as controller
from cifereca.models import SearchResult
from cifereca.storage import Storage
from tests.test_core_p0_6397_authenticated_web_choices import Host, HOST, URL
from tests.test_core_p0_6397_known_search_web_actions import GTK, deliver

OTHER = HOST + "/resources/book-b"


def setup(db, count):
    core = Host(db)
    account = core.account("alice")
    row = SearchResult(
        id="row", record_id="record-a", docbase="fixture", medium="digital",
        title="Fixture", author=None, type=None, format=None, publisher=None,
        published_at=None, description=None, thumbnail_url=None,
        friendly_url=HOST + "/resources/untrusted-dto", scenario="fixture",
        source_provider=account.provider_id, source_credential=account.credential_id,
        source_account_id=account.unique_id,
    )
    key = _source_key(row, "alice")
    fields = {"cover": None}
    if count:
        fields[FIELD] = {
            "schema": 1,
            "connection_epoch": db.provider_connection_epoch(
                account.provider_id, account.credential_id, "alice"),
            "links": [{"url": uri, "evidence":
                       account.provider_id + ".authenticated_search_record_identifier"}
                      for uri in (URL, OTHER)[:count]],
        }
    obs = admit_source_notice(key=key, parser_id="core.search.authenticated-web",
                              parser_version="1", observed_at=10, raw_fields=fields)
    source = persist_notice_cover(db, obs, field_path=("cover",),
                                  cover_inventory_complete=False)
    persist_consolidated_cover_source(
        db, canonical_edition_id=key.edition_id, source=source,
        relation_proof_id="private-self:controlled-test",
    )
    return core, row


def ui(core):
    pending, actions, launches, toasts = [], [], [], []
    host = NS(service=core, _route_display_token=object(),
              runner=NS(submit=lambda label, work, done, fail, **kw:
                        pending.append((work, done, fail))), toast=toasts.append)
    panel = NS(add_actions=actions.extend, get_mapped=lambda: True)
    gio = NS(AppInfo=NS(launch_default_for_uri=lambda uri, _: launches.append(uri)))
    return host, panel, gio, pending, actions, launches, toasts


@pytest.mark.parametrize("count", [0, 1, 2])
def test_private_menu_zero_one_many_never_opens_dto_url(tmp_path, count):
    with closing(Storage(tmp_path / "menu.sqlite")) as db:
        core, row = setup(db, count)
        host, panel, gio, pending, actions, launches, _ = ui(core)
        scope = capture_search_read_scope(core, "alice")
        assert controller.request_authenticated_search_web_actions(
            host, panel, row, reader_scope=scope, current=lambda: True,
            Gtk=GTK, Gio=gio,
        )
        deliver(pending)
        assert not launches
        if count == 0:
            assert not actions
            return
        root, = actions
        assert root._cifereca_action_id == "open-c-authenticated-web"
        assert "Connexion requise" in root.label
        buttons = [root] if count == 1 else root.popover.child.child.children
        assert len(buttons) == count
        for button in buttons:
            assert HOST not in button.label
            button.callback(button)
            work, done, _ = pending.pop(0)
            uri = work()
            done(uri)
            done(uri)  # duplicate callback must not open again
        assert set(launches) == set((URL, OTHER)[:count])
        assert len(launches) == count
        assert db.entitlements() == db.acquisitions() == db.placements() == []


@pytest.mark.parametrize("change", ["selection", "route", "session", "row"])
def test_private_click_does_not_survive_revocation(tmp_path, change):
    with closing(Storage(tmp_path / "click.sqlite")) as db:
        core, row = setup(db, 1)
        host, panel, gio, pending, actions, launches, _ = ui(core)
        selection = [True]
        assert controller.request_authenticated_search_web_actions(
            host, panel, row, reader_scope=capture_search_read_scope(core, "alice"),
            current=lambda: selection[0], Gtk=GTK, Gio=gio,
        )
        deliver(pending)
        root, = actions
        root.callback(root)
        if change == "selection":
            selection[0] = False
        elif change == "route":
            host._route_display_token = object()
        elif change == "session":
            core._sessions[(row.source_provider, row.source_credential)].connected = False
        else:
            row.record_id = "another-record"
        deliver(pending)
        assert not launches


def test_selected_row_replacement_revokes_pending_private_menu(tmp_path, monkeypatch):
    with closing(Storage(tmp_path / "selection.sqlite")) as db:
        core, row = setup(db, 1)
        host, panel, gio, pending, actions, launches, _ = ui(core)
        cell = NS(key="fixture:row")
        host.search_page = NS(detail=panel, selection=NS(get_selected_item=lambda: cell))
        host.search_objects = {cell.key: row}
        host._search_detail_context_current = lambda selected, context: True
        original = controller.request_authenticated_search_web_actions

        def request(*args, **kwargs):
            return original(*args, **kwargs, Gtk=GTK, Gio=gio)

        monkeypatch.setattr(controller, "request_authenticated_search_web_actions", request)
        assert controller.request_selected_authenticated_web_actions(
            host, row, selected_id=row.id, context=(1, 1, 1, "alice"),
        )
        host.search_objects[cell.key] = replace(row)
        deliver(pending)
        assert not actions and not launches


@pytest.mark.parametrize("phase", ["menu", "click"])
@pytest.mark.parametrize("change", ["record", "scenario", "revision", "session", "generation", "account",
                                    "shared", "stale", "foreign_metadata"])
def test_private_worker_completion_cannot_outlive_exact_coordinates(tmp_path, phase, change):
    with closing(Storage(tmp_path / "late.sqlite")) as db:
        core, row = setup(db, 1)
        host, panel, gio, pending, actions, launches, _ = ui(core)
        assert controller.request_authenticated_search_web_actions(
            host, panel, row, reader_scope=capture_search_read_scope(core, "alice"),
            current=lambda: True, Gtk=GTK, Gio=gio,
        )
        if phase == "click":
            deliver(pending)
            root, = actions
            root.callback(root)
        work, done, _ = pending.pop(0)
        value = work()
        assert value is not None
        session = core._sessions[(row.source_provider, row.source_credential)]
        if change == "record":
            row.record_id = "replacement-record"
        elif change == "scenario":
            row.scenario = "replacement-scenario"
        elif change == "revision":
            row.revision_id = "replacement-revision"
        elif change == "session":
            session.connected = False
        elif change == "generation":
            session._epoch = "replacement-generation"
        elif change == "shared":
            row.metadata = {"catalogue_access": "shared_read_only"}
        elif change == "stale":
            row.metadata = {"search_provenance": "known_catalog_stale"}
        elif change == "foreign_metadata":
            class Foreign:
                def __eq__(self, other):
                    pytest.fail("foreign metadata equality")
            row.metadata = {"catalogue_access": Foreign()}
        else:
            core.account("alice").unique_id = "replacement-account"
        # A valid worker response became obsolete before GLib delivery.
        done(value)
        done(value)
        assert not launches
        if phase == "menu":
            assert not actions


def test_authenticated_search_selection_exposes_one_mixed_c_menu(tmp_path):
    """Use the exact original private source, then the common C inventory."""
    from tests.test_core_p0_6397_c_web_reference_persistence import (
        source as public_source, link, publish,
    )
    public_url = "https://books.example.org/title/mixed-private-selection"
    with closing(Storage(tmp_path / "private-mixed.sqlite")) as db:
        core, row = setup(db, 1)
        key = _source_key(row, "alice")
        source = public_source(db, "seller-a", "a", [link("seller-a", public_url)])
        publish(db, key.edition_id, source)
        host, panel, gio, pending, actions, launches, toasts = ui(core)
        assert controller.request_authenticated_search_web_actions(
            host, panel, row, reader_scope=capture_search_read_scope(core, "alice"),
            current=lambda: True, Gtk=GTK, Gio=gio,
        )
        deliver(pending)
        root, = actions
        assert root.label == "Ouvrir la fiche sur…"
        public, private = root.popover.child.child.children
        assert "Connexion requise" not in public.label
        assert "Connexion requise" in private.label
        private.callback(private)
        deliver(pending)
        assert launches == [URL]
        core._sessions[(row.source_provider, row.source_credential)].connected = False
        private.callback(private)
        deliver(pending)
        assert launches == [URL] and toasts
        public.callback(public)
        deliver(pending)
        assert launches == [URL, public_url]
        assert db.entitlements() == db.acquisitions() == db.placements() == []
