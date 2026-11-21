"""Synthetic private C receipts on actual mapped GTK, no network/rights.

Only the controlled native record coordinates are fixture inputs; no claim
of cross-provider or field capture. The same stored sidecar is read after
qualified eviction and restart, using a fresh scoped offline session.
"""
from __future__ import annotations

from threading import get_ident
from time import monotonic
from types import SimpleNamespace as NS

from cifereca.core.notice_c_authenticated_web_choices import FIELD
from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_private_search_web import _source_key
from cifereca.core.notice_cover_repository import persist_notice_cover, evict_notice_cover
from cifereca.core.notice_source_observation import admit_source_notice
from cifereca.core.search_orchestration import capture_search_read_scope
from cifereca.models import Account, SearchResult
from cifereca.providers.base import ProviderCapabilities, ProviderDescriptor, ProviderSession

ORIGIN = "https://private-fixture.example.org"
PROVIDER = "private-fixture"
OWNER = "private-fixture-alice"
FOREIGN = "private-fixture-bob"


class OfflineSession(ProviderSession):
    def __init__(self, account):
        super().__init__(
            descriptor=ProviderDescriptor(
                id=PROVIDER, display_name="Private fixture", description="Fixture",
                state="ready", capabilities=ProviderCapabilities(authentication=True, search=True),
                homepage=ORIGIN,
            ), credential_id=account.credential_id, label="Fixture",
            credentials={}, options={},
        )
        self._accounts = [account]
        self.connected = True
        self._epoch = 1
        self._closed = False

    def connect(self):
        raise AssertionError("unexpected authenticated web login")

    def search(self, *args, **kwargs):
        raise AssertionError("unexpected authenticated web GET")

    def holdings(self, *args, **kwargs):
        raise AssertionError("unexpected authenticated holdings")

    def close(self):
        self.connected = False


def account(owner):
    return Account(identity_id=owner, provider_id=PROVIDER, credential_id="card-" + owner,
                   unique_id="reader-" + owner, is_primary=True, display_name="Fixture")


class OfflineCore:
    def __init__(self, db):
        self.storage = db
        self._source_context_epoch = 1
        self._account_context_epoch = 1
        self.identities = {owner: NS(identity_id=owner) for owner in (OWNER, FOREIGN)}
        self.accounts = [account(owner) for owner in self.identities]
        self._sessions = {
            self._session_key(a.provider_id, a.credential_id): OfflineSession(a)
            for a in self.accounts
        }

    @property
    def current_identity(self):
        raise AssertionError("worker must not consult navigation")

    def identity_by_id(self, identity_id):
        return self.identities.get(identity_id)

    def accounts_for_identity(self, identity_id):
        return [a for a in self.accounts if a.identity_id == identity_id]

    def _provider_enabled_for_identity(self, provider, identity):
        return provider == PROVIDER and identity in self.identities

    @staticmethod
    def _session_key(provider, credential):
        return provider, credential


def seed_private_web_choices(db):
    """Seed three exact native coordinates, none/one/two stable references."""
    core = OfflineCore(db)
    for a in core.accounts:
        connection = db.provider_connection_put({
            "identity_id": a.identity_id, "provider_id": a.provider_id,
            "credential_id": a.credential_id, "enabled": True,
        })
        db.provider_account_put({
            "connection_id": connection, "identity_id": a.identity_id,
            "provider_id": a.provider_id, "credential_id": a.credential_id,
            "account_id": a.unique_id, "is_primary": True,
        })
    a = core.accounts[0]
    fixtures = []
    for count in (0, 1, 2):
        record = "native-" + str(count)
        row = SearchResult(
            id=record, record_id=record, docbase="fixture", medium="digital",
            title="Notice privée témoin", author=None, type="book", format="EPUB",
            publisher=None, published_at=None, description=None, thumbnail_url=None,
            friendly_url=ORIGIN + "/resources/untrusted-dto", scenario="fixture",
            source_provider=PROVIDER, source_credential=a.credential_id,
            source_account_id=a.unique_id,
        )
        key = _source_key(row, OWNER)
        expected = tuple(ORIGIN + "/resources/" + record + "-" + str(i)
                         for i in range(count))
        fields = {"cover": None}
        if count:
            fields[FIELD] = {
                "schema": 1,
                "connection_epoch": db.provider_connection_epoch(
                    PROVIDER, a.credential_id, OWNER),
                "links": [{"url": uri, "evidence":
                           PROVIDER + ".authenticated_search_record_identifier"}
                          for uri in expected],
            }
        obs = admit_source_notice(
            key=key, parser_id="core.search.authenticated-web", parser_version="1",
            observed_at=100, raw_fields=fields,
        )
        source = persist_notice_cover(db, obs, field_path=("cover",),
                                      cover_inventory_complete=False)
        persist_consolidated_cover_source(
            db, canonical_edition_id=key.edition_id, source=source,
            relation_proof_id="private-self:fixture",
        )
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
        fixtures.append((row, expected))
    return tuple(fixtures)


def verify_private_web_choices(window, db, fixtures, *, offline, later, guard,
                               capture, report, complete, state):
    from gi.repository import Gtk
    from cifereca.gui.detail_panel import DetailPanel
    from cifereca.gui.notice_c_web_action_controller import request_authenticated_search_web_actions

    def phase(name):
        report["notice_c_private_web_phase"] = name
        print("NOTICE_C_PRIVATE_WEB_PHASE=" + name, flush=True)

    phase("construct")
    core = OfflineCore(db)
    label = "offline" if offline else "cold"
    gtk_thread = get_ident()
    launches, messages = [], []
    generation = [0]
    host = NS(service=core, runner=window.runner, toast=messages.append,
              _closed=False, _route_display_token=object())
    phase("panel")
    panel = DetailPanel(window)
    probe = state["probe"] = Gtk.Window(transient_for=window, title="Notice C — fiches privées")
    probe.set_default_size(460, 340)
    probe.set_child(panel)
    probe.present()
    deadline = monotonic() + 15
    phase("mapped_request_setup")

    def launch(uri, _context):
        assert get_ident() == gtk_thread
        launches.append(uri)

    gio = NS(AppInfo=NS(launch_default_for_uri=launch))
    stats = {}
    # Capture completion even when no button is expected. The actual
    # MainWindow runner still executes each read and click resolver.
    class Runner:
        completed = 0

        def submit(self, title, work, done, fail, **kw):
            def checked_work():
                assert get_ident() != gtk_thread
                return work()

            def checked_done(value):
                assert get_ident() == gtk_thread
                def publish():
                    done(value)
                    self.completed += 1
                guard(publish)()

            def checked_fail(error):
                phase("worker_failed")
                guard(lambda: (_ for _ in ()).throw(AssertionError("private web task failed")))()

            window.runner.submit(title, checked_work, checked_done, checked_fail, **kw)

    runner = host.runner = Runner()

    def menus():
        return [widget for widget in getattr(panel, "_action_buttons", ())
                if getattr(widget, "_cifereca_action_id", None) == "open-c-authenticated-web"]

    def run_case(index):
        phase("foreign" if index == len(fixtures) else ("zero", "one", "many")[index])
        if index == len(fixtures):
            # A different reader has no reference to the original account.
            row, _ = fixtures[1]
            generation[0] += 1
            panel.empty("Autre lecteur", "Aucun droit transféré")
            wanted = runner.completed + 1
            assert request_authenticated_search_web_actions(
                host, panel, row, reader_scope=capture_search_read_scope(core, FOREIGN),
                current=lambda: True, Gtk=Gtk, Gio=gio,
            )

            def foreign_done():
                if runner.completed < wanted:
                    assert monotonic() < deadline, "foreign private web timeout"
                    later(40, guard(foreign_done))
                    return
                assert not menus() and len(launches) == 3 and not messages
                report["checks"]["P0_6397_real_gtk_private_web_" + label] = True
                report["notice_c_private_web_" + label] = {
                    "choices": stats, "browser_sink_calls": len(launches),
                    "foreign_reader_refused": True, "old_menu_revoked": True,
                    "qualified_eviction": True, "provider_io": 0,
                }
                probe.destroy()
                state["probe"] = None
                window.present()
                complete()

            later(40, guard(foreign_done))
            return

        row, expected = fixtures[index]
        generation[0] += 1
        lease = generation[0]
        phase("reset_" + str(index))
        panel.empty("Notice privée témoin", "Référence exacte")
        wanted = runner.completed + 1
        phase("submit_" + str(index))
        assert request_authenticated_search_web_actions(
            host, panel, row, reader_scope=capture_search_read_scope(core, OWNER),
            current=lambda: generation[0] == lease, Gtk=Gtk, Gio=gio,
        )

        def poll():
            if runner.completed < wanted or (expected and
                    (not menus() or not menus()[0].get_mapped())):
                assert monotonic() < deadline, "private web menu timeout"
                later(40, guard(poll))
                return
            phase("received_" + str(index))
            stats[str(index)] = len(expected)
            if not expected:
                assert not menus() and not launches
                run_case(index + 1)
                return
            phase("check_widget")
            root, = menus()
            assert "Connexion requise" in root.get_label()
            if len(expected) == 1:
                assert isinstance(root, Gtk.Button)
                buttons = [root]
            else:
                assert isinstance(root, Gtk.MenuButton)
                phase("popover")
                root.popup()
                box = root.get_popover().get_child().get_child()
                if isinstance(box, Gtk.Viewport):
                    box = box.get_child()
                buttons = []
                child = box.get_first_child()
                while child is not None:
                    buttons.append(child)
                    child = child.get_next_sibling()
                assert len(buttons) == len(expected)
                assert all("Connexion requise" in b.get_label() for b in buttons)
            assert all(ORIGIN not in b.get_label() for b in buttons)
            start = len(launches)

            def click():
                if not all(button.get_mapped() for button in buttons):
                    assert monotonic() < deadline, "private web popover timeout"
                    later(40, guard(click))
                    return
                phase("click")
                capture("00-notice-c-private-web-" + str(index) + "-" + label)
                buttons[0].emit("clicked")
                buttons[-1].emit("clicked")
                later(40, guard(lambda: poll_launch(1)))

            def poll_launch(count):
                if len(launches) < start + count:
                    assert monotonic() < deadline, "private web click timeout"
                    later(40, guard(lambda: poll_launch(count)))
                    return
                phase("launch_check")
                assert launches[start:] == list(expected[:count]) and not messages
                if count < len(expected):
                    buttons[count].emit("clicked")
                    later(40, guard(lambda: poll_launch(count + 1)))
                    return
                if len(expected) > 1:
                    root.popdown()
                phase("revoke")
                panel.empty("Sélection modifiée", "Menu révoqué")
                assert root._cifereca_action_context_revoked
                for button in buttons:
                    button.emit("clicked")
                assert launches[start:] == list(expected)
                run_case(index + 1)

            later(40, guard(click))

        later(40, guard(poll))

    # Wait for the actual mapped GTK widget before enqueuing the first read.
    # Otherwise a fast local worker can complete before the new transient
    # is mapped, and the controller correctly discards its callback.
    def start_mapped():
        if not panel.get_mapped():
            assert monotonic() < deadline, "private web panel map timeout"
            later(40, guard(start_mapped))
            return
        run_case(0)

    later(40, guard(start_mapped))
