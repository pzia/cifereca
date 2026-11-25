#!/usr/bin/env python3
"""P0 #1358/#1492: GTK/XTEST safety and cache progress on the tool row.

The fake tool never touches a real book. All HOME/XDG locations come from the
runner's isolated fixture. Explicit actions are sent through XTEST.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import threading
import time
from time import monotonic
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements/openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from x11_input import X11Input
from gtk_layout_critical import GTK_PHASES_2027
from maintenance_hit_target import choose_verified_point
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.locking import InterProcessLock
from cifereca.paths import acquisition_lock_file


def gtk_phase_2027(phase: str) -> None:
    """Emit only reviewed, constant, privacy-safe action boundaries on both streams."""
    assert phase in GTK_PHASES_2027
    marker = f"CIFERECA_E2E_GTK_PHASE:{phase}"
    print(marker, file=sys.stdout, flush=True)
    print(marker, file=sys.stderr, flush=True)


def _maintenance_failure_line(exc: BaseException) -> int | None:
    """Return only the innermost source line in this probe, never traceback text."""
    line = None
    traceback = exc.__traceback__
    while traceback is not None:
        if traceback.tb_frame.f_globals is globals():
            line = traceback.tb_lineno
        traceback = traceback.tb_next
    return line


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


class DummyTool:
    def __init__(self):
        self.plan_calls = 0
        self.run_calls = 0
        self.tool_id = "core.cache.cleanup"
        self.result_ok = True
        self.plan_started = threading.Event()
        self.plan_ready = threading.Event()
        self.release_plan = threading.Event()
        self.release_plan.set()
        self.version = 0
        self.delay = 0.65
        self.progress_sent = threading.Event()
        self.release_run = threading.Event()
        self.run_finished = threading.Event()
        self.worker_threads = []

    def describe(self):
        titles = {
            "core.cache.cleanup": "Nettoyage test sans suppression",
            "library.naming.repair": "Renormaliser les noms de fichiers",
            "library.duplicates.cleanup": "Nettoyer les doublons physiques",
        }
        return SimpleNamespace(
            id=self.tool_id,
            title=titles[self.tool_id], description="Simulation locale",
            supports_dry_run=True, cancellable=True, scope="identity",
            danger_level="repair" if self.tool_id == "library.naming.repair" else "destructive",
            icon_name="edit-delete-symbolic",
        )

    def plan(self, context):
        self.plan_calls += 1
        self.plan_ready.clear()
        self.plan_started.set()
        time.sleep(self.delay)  # an intentionally slow scan, never on GTK
        if not self.release_plan.wait(8):
            raise TimeoutError("GTK did not acknowledge simulated analysis")
        plan = SimpleNamespace(
            tool_id=self.tool_id,
            summary="Aucune suppression réelle",
            changes=tuple(
                f"fichier témoin simulé {i:02d} /chemin/long/vers/la/bibliothèque/romans/doublons/exemple.epub"
                for i in range(20)
            ),
            warnings=("Protection des artefacts Kobo",),
            facts={"version": self.version, "read_failures": 0},
        )
        self.plan_ready.set()
        return plan

    def run(self, plan, control):
        control.check_cancelled()
        self.run_calls += 1
        self.worker_threads.append(threading.get_ident())
        control.report(current=1, total=1, message="Témoin traité")
        self.progress_sent.set()
        try:
            if not self.release_run.wait(8):
                raise TimeoutError("GTK did not release simulated maintenance worker")
            # Exercise late progress after cancellation/refresh: it must never
            # repaint the detached row or the replacement row.
            control.report(current=1, total=1, message="Retour tardif")
            control.check_cancelled()
            return SimpleNamespace(
                ok=self.result_ok, summary="Simulation confirmée",
                details=("Fichiers témoins : 524",),
                warnings=("Durée maximale témoin atteinte.",),
                facts={"private_capability": "NEVER_RENDER_EXECUTION_FACTS"},
            )
        finally:
            self.run_finished.set()


def _native_transfer_fixture(root):
    from datetime import datetime, timezone
    from cifereca.config import Config
    from cifereca.core.canonical_publication import publish_completed_acquisition
    from cifereca.core.source_convergence import SourceKey
    from cifereca.diagnostics import Diagnostics
    from cifereca.plugin_api import ProviderRightObservation, ProviderRightsScope, ProviderRightsSnapshot
    from cifereca.services import LibraryService
    from cifereca.storage import Storage

    config = Config(root / 'config.json')
    library = root / 'library'
    config.set('library.directory', str(library))
    config.set('providers.accounts', [])
    config.set('identities', [
        {'id': 'alice', 'label': 'Alice', 'directory_name': 'alice'},
        {'id': 'bob', 'label': 'Bob', 'directory_name': 'bob'},
        {'id': 'charlie', 'label': 'Charlie', 'directory_name': 'charlie'},
    ])
    service = LibraryService(config, Storage(root / 'state.sqlite'), Diagnostics())
    source = library / 'alice/local/book.pdf'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'%PDF-1.7\nSynthetic native Send acceptance\n')
    scope = ProviderRightsScope('alice', 'fixture.vendor', 'card-A', 'buyer-A')
    connection = service.storage.provider_connection_put({
        'identity_id': scope.identity_id, 'provider_id': scope.provider_id,
        'credential_id': scope.credential_id, 'label': 'Synthetic account A',
    })
    service.storage.provider_account_put({
        'identity_id': scope.identity_id, 'provider_id': scope.provider_id,
        'credential_id': scope.credential_id, 'connection_id': connection,
        'account_id': scope.account_id, 'label': 'Synthetic buyer A',
    })
    observed = ProviderRightObservation(
        'book', 'purchase-A', 'purchase', state='active',
        evidence_kind='provider_response', evidence_ref='synthetic-source-A',
        observed_at=datetime.now(timezone.utc).isoformat(),
    )
    snapshot = ProviderRightsSnapshot(scope, (observed,), status='partial',
                                      coverage='partial', branch='purchases')
    published = publish_completed_acquisition(
        service.storage, library_root=library, path=source,
        source=SourceKey(scope, 'book', 'purchase-A', 'purchase', 'purchases'),
        snapshot=snapshot, final_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        delivery_receipt_ref='synthetic-native-delivery-A',
        adept_receipt_ref='synthetic-native-adept-A',
    )
    service.storage.catalog_entry_put({
        'identity_id': 'alice', 'provider_id': scope.provider_id, 'item_id': 'book',
        'artifact_id': published.artifact_id, 'placement_id': published.placement_id,
        'title': 'Synthetic native transfer', 'media_format': 'pdf',
        'local_state': 'local', 'metadata': {'path': str(source)},
    })
    return service, source, published.placement_id


def exercise_native_transfer(*, parent, Gtk, Adw, device, until, report, capture, done):
    import tempfile
    from gi.repository import Gio
    from cifereca.gui.book_actions import identity_has_attested_possession
    from cifereca.gui.local_book_action_controller import add_local_book_actions
    from cifereca.plugin_api import ArtifactRouteRequest
    from cifereca.gui.task_runner import TaskPriority

    temporary = tempfile.TemporaryDirectory()
    service, source, placement = _native_transfer_fixture(Path(temporary.name))
    host = Adw.Window(transient_for=parent, modal=True, title='Envoi local — recette #3275')
    host.set_default_size(650, 260)
    content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for side in ('top', 'bottom', 'start', 'end'):
        getattr(content, 'set_margin_' + side)(24)
    content.append(Gtk.Label(label='Fichier source Alice → copie indépendante Bob'))
    status = Gtk.Label(label='Aucun envoi effectué')
    content.append(status)
    actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
    content.append(actions)
    host.set_content(content)
    host.service, host.runner = service, parent.runner
    host.plugin_manager, host.diagnostics, host._closed = None, service.diagnostics, False
    host.action_button = parent.action_button
    host._activity_result = status.set_text
    host.refresh_recent = lambda: None
    host.refresh_library = lambda _fast: None
    host._add_catalogue_action = lambda *_: (_ for _ in ()).throw(
        AssertionError('Native send unexpectedly fell back to catalogue'))
    host.show_error = lambda text, *_: status.set_text('Échec : ' + text)
    detail = SimpleNamespace(add_actions=lambda buttons: [actions.append(b) for b in buttons])
    add_local_book_actions(
        host, detail, dict(placement_id=placement, identity_id='alice',
                           provider_id='fixture.vendor', item_id='book'),
        Gtk=Gtk, Gio=Gio, ArtifactRouteRequest=ArtifactRouteRequest,
        TaskPriority=TaskPriority,
        identity_has_attested_possession=identity_has_attested_possession,
    )
    buttons = []
    child = actions.get_first_child()
    while child is not None:
        buttons.append(child)
        child = child.get_next_sibling()
    # #3692: two potential recipients must produce exactly one toolbar action.
    send_actions = [b for b in buttons if
                    getattr(b, '_cifereca_action_id', '').startswith('send-local')]
    assert len(send_actions) == 1
    menu = send_actions[0]
    assert menu._cifereca_action_id == 'send-local-menu'
    popover = menu.get_popover()
    assert popover is not None
    choices = list(walk(popover.get_child()))
    recipients = [b for b in choices if isinstance(b, Gtk.Button) and
                  getattr(b, '_cifereca_action_id', '').startswith('send-local:')]
    assert {b._cifereca_action_id for b in recipients} == {
        'send-local:bob', 'send-local:charlie',
    }
    send = next(b for b in recipients if b._cifereca_action_id == 'send-local:bob')

    def start():
        assert len(service.storage.placements()) == 1
        capture('08-native-send-before')
        device.click_widget(menu)
        until(lambda: send.get_mapped() and send.get_sensitive(), choose_recipient)

    def choose_recipient():
        # A real XTEST click on the popover choice, not an emitted signal.
        device.click_widget(send)
        until(lambda: status.get_text() == 'Livre envoyé', finished)

    def finished():
        recipients = [p for p in service.storage.placements() if p['identity_id'] == 'bob']
        assert len(recipients) == 1
        assert not [p for p in service.storage.placements()
                    if p['identity_id'] == 'charlie']
        report['checks']['P2_3692_one_popover_send_recipient'] = True
        received = Path(recipients[0]['path'])
        assert received == (
            source.parents[2] / 'bob' / 'Acquisitions' / 'Synthetic native transfer.pdf'
        )
        assert received.read_bytes() == source.read_bytes()
        assert (source.stat().st_dev, source.stat().st_ino) != (
            received.stat().st_dev, received.stat().st_ino)
        assert recipients[0]['metadata']['file_origin_identity_id'] == 'alice'
        with service.storage._lock, service.storage._connect() as db:
            assert db.execute("SELECT origin_kind FROM origin_links WHERE identity_id='bob' "
                              "ORDER BY origin_kind").fetchall() == [('cifereca',), ('transfer',)]
            assert db.execute("SELECT reference_kind,synchronizable FROM source_references "
                              "WHERE identity_id='bob'").fetchall() == [('internal', 0)]
        assert service.storage.entitlements() == []
        report['checks']['P1_3275_native_send_taskrunner_copy_origins'] = True
        capture('09-native-send-after')
        host._closed = True
        host.destroy()
        service.close()
        temporary.cleanup()
        done()

    def ready():
        if not menu.get_mapped() or not menu.get_sensitive():
            return False
        ok, box = menu.compute_bounds(host)
        if not ok or box.get_width() <= 0 or box.get_height() <= 0:
            return False
        picked = host.pick(box.get_x() + box.get_width()/2,
                           box.get_y() + box.get_height()/2, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not menu:
            picked = picked.get_parent()
        if picked is not menu:
            return False
        try:
            x, y = device.widget_point(menu)
            return x >= 0 and y >= 0
        except RuntimeError:
            return False

    host.present()
    until(ready, start)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(920, 1024, 1280, 1366, 1600, 1728))
    parser.add_argument("--display-height", required=True, type=int, choices=(620, 768, 800, 895, 900, 1000))
    parser.add_argument("--guided-only", choices=("organizer", "origin", "derived"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    from tempfile import TemporaryDirectory
    from zipfile import ZipFile
    from cifereca.plugins import PackageStore, PluginManager, PluginStateStore
    from cifereca.plugin_api.models import ResolvedPluginService

    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    tool = DummyTool()
    # Install the real page and three independent guided tool packages into
    # an isolated registry, with actual Core read ports and no providers.
    # The dummy tool still exercises the generic confirmation/run lifecycle.
    fixture = TemporaryDirectory(prefix="maintenance-page-fixture-")
    fixture_root = Path(fixture.name)
    manager = PluginManager(
        builtin_ui=True, package_store=PackageStore(fixture_root / "packages"),
        state_store=PluginStateStore(fixture_root / "state.json"),
        brokers=core.brokers,
    )
    guided_packages = {
        "organizer": ("maintenance-library-organizer", "maintenance.library.organizer"),
        "origin": ("maintenance-local-origin", "maintenance.library.local-origin"),
        "derived": ("maintenance-derived-variants", "maintenance.library.derived-variants"),
    }
    selection = (guided_packages[args.guided_only],) if args.guided_only else tuple(guided_packages.values())
    if any(folder == "maintenance-derived-variants" for folder, _key in selection):
        selection += (("maintenance-derived-variants", "maintenance.library.derived-capacity"),)
    for folder in dict.fromkeys(("maintenance-page", *(folder for folder, _key in selection))):
        package = fixture_root / (folder + ".zip")
        with ZipFile(package, "w") as archive:
            for source in sorted((ROOT / "src/plugins" / folder).iterdir()):
                if source.is_file() and source.suffix in (".py", ".json", ".md"):
                    archive.write(source, source.name)
        manager.install(package)
    assert manager.service("ui.page.maintenance") is not None
    for _folder, key in selection:
        assert manager.service(key) is not None
    assert {r.plugin_id for r in manager.services("maintenance.tool")} == {
        key for _folder, key in selection
    }
    original_services, original_service = manager.services, manager.service
    def fixture_services(category):
        if category == "maintenance.tool" and not args.guided_only:
            return (ResolvedPluginService(
                "e2e.maintenance", "1", "fixture", "1", "maintenance.tool", tool,
                registry_generation=manager.registry_generation,
                capabilities=("maintenance.group.organization",),
            ),) + tuple(original_services(category))
        return original_services(category)
    def fixture_service(key):
        return tool if not args.guided_only and key == "e2e.maintenance" else original_service(key)
    manager.services, manager.service = fixture_services, fixture_service
    core.library.attach_plugin_manager(manager)
    report = {
        "status": "RUNNING", "scenario": "maintenance-tools", "width": args.width,
        "scope": ("real installed single guided tool; GTK signals, no destructive IO"
                  if args.guided_only else "real GTK MainWindow/XTEST; simulated tool with zero destructive IO"),
        "display_height": args.display_height,
        "checks": {}, "screenshots": [], "pointer_actions": [],
    }
    window = [None]
    device = [None]
    preview_button = [None]
    errors = []
    lock = [None]
    started = monotonic()
    heartbeat = [0]
    result = [1]
    gtk_thread = threading.get_ident()
    detached_label = [None]

    def line_widgets():
        # The Core-owned review line has no legacy tool progress relay.
        # Target the exact currently installed dummy tool, never row position.
        matches = [w for w in walk(window[0]._tools_box)
                   if isinstance(w, Adw.ActionRow) and w.get_title() == tool.describe().title]
        assert len(matches) == 1, "Expected one exact fixture maintenance row"
        row = matches[0]
        label = next(w for w in walk(row) if isinstance(w, Gtk.Label)
                     and w.get_tooltip_text() == "État de l’opération")
        spinner = next(w for w in walk(row) if isinstance(w, Gtk.Spinner))
        bar = next(w for w in walk(row) if isinstance(w, Gtk.ProgressBar))
        return row, label, spinner, bar

    def line_is(text):
        return line_widgets()[1].get_text() == text

    def assert_line(text, *, fraction=None, busy=False):
        row, label, spinner, bar = line_widgets()
        assert row.get_mapped() and label.get_mapped(), "Progress must appear on the tool row"
        assert label.get_text() == text, label.get_text()
        assert spinner.get_visible() == busy
        assert bar.get_visible() == (fraction is not None)
        if fraction is not None:
            assert abs(bar.get_fraction() - fraction) < 0.001

    def save():
        report["duration_seconds"] = round(monotonic() - started, 2)
        report["plan_calls"] = tool.plan_calls
        report["simulated_runs"] = tool.run_calls
        (args.out / "screen.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    def capture(name):
        image = ImageGrab.grab(xdisplay=__import__("os").environ["DISPLAY"])
        path = args.out / (name + ".png")
        image.save(path)
        image.save(args.out / "screen.png")
        report["screenshots"].append({"name": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})

    def fail(exc):
        if report["status"] != "RUNNING":
            return
        tool.release_plan.set()
        tool.release_run.set()
        report.update(
            status="FAIL", error=f"{type(exc).__name__}: {exc}",
            failure_stack=__import__("traceback").format_exc(limit=12),
        )
        line = _maintenance_failure_line(exc)
        if line is not None:
            report["maintenance_failure_line"] = line
        if lock[0] is not None:
            lock[0].release()
            lock[0] = None
        try:
            capture("failure")
        except Exception:
            pass
        save()
        app.quit()

    def later(callback, delay=100):
        def run():
            try:
                callback()
            except Exception as exc:
                fail(exc)
            return False
        GLib.timeout_add(delay, run)

    def until(predicate, callback, *, timeout=6.0):
        deadline = monotonic() + timeout
        def check():
            if predicate():
                callback()
            elif monotonic() >= deadline:
                if args.guided_only == "organizer":
                    # Fixed allowlist of local test states, not plugin/reader data.
                    markers = {"received": 1, "second_page": 2, "restarted": 3,
                               "prepare_candidate": 4, "confirm_simulation": 5,
                               "open_dialog": 6, "cancel_dialog": 7,
                               "reopen": 8, "apply_dialog": 9, "refused": 10}
                    report["maintenance_failure_stage"] = markers.get(callback.__name__, 0)
                raise AssertionError("GTK maintenance operation timed out")
            else:
                later(check, 95)
        later(check, 90)

    def modal():
        return next((
            item for item in Gtk.Window.list_toplevels()
            if isinstance(item, Adw.MessageDialog) and item.get_visible() and item.get_mapped()
        ), None)

    def physical(button):
        assert button.get_mapped() and button.get_sensitive(), (
            f"GTK action unavailable: mapped={button.get_mapped()} "
            f"sensitive={button.get_sensitive()} page={window[0].view_stack.get_visible_child_name()}"
        )
        if button.get_root() is window[0] and button in tuple(walk(window[0]._tools_box)):
            # At 920px the responsive tools row can wrap vertically.
            # A fixed y=144 may hit an unrelated widget. Before the ONLY
            # XTEST click, verify the button in GTK's actual window hit tree.
            assert window[0].get_width() > 800, "Unexpected GTK window geometry"
            ok, bounds = button.compute_bounds(window[0])
            assert ok, "Preview button horizontal bounds unavailable"
            x, y = choose_verified_point(
                window[0], button, bounds, Gtk.PickFlags.DEFAULT,
            )
            screen_x, screen_y = device[0].window_point(window[0], x, y)
            device[0].click(screen_x, screen_y)
            report["pointer_actions"].append({
                "widget": button.get_name(),
                "x": screen_x, "y": screen_y,
                "source": "XTEST pointer/button events (GTK pick-verified bounds)",
            })
        else:
            report["pointer_actions"].append(device[0].click_widget(button))

    def respond(dialog, label, on_clicked):
        gtk_phase_2027("maint_p1_dialog_visible" if tool.tool_id != "core.cache.cleanup" else "maint_dialog_visible")
        response = next(
            (b for b in walk(dialog) if isinstance(b, Gtk.Button)
             and label in str(b.get_label() or "").replace("_", "")),
            None,
        )
        assert response is not None, f"Missing modal response: {label}"

        def allocated():
            # A GTK transient can be 'mapped' while its X11 origin is still
            # (-5, -5). Do not send XTEST until the real button is on-screen.
            if not dialog.get_visible() or not response.get_mapped() or not response.get_sensitive():
                return False
            try:
                x, y = device[0].widget_point(response)
            except RuntimeError:
                return False
            return x >= 0 and y >= 0

        def click_then_continue():
            gtk_phase_2027("maint_p1_dialog_response" if tool.tool_id != "core.cache.cleanup" else "maint_dialog_response")
            physical(response)
            on_clicked()

        until(allocated, click_then_continue, timeout=6.0)

    def refresh_tools():
        page = window[0]._plugin_page_widgets["tools"]
        button = next(node for node in walk(page)
                      if isinstance(node, Gtk.Button) and node.get_name() == "maintenance-page-refresh")
        assert button.get_label() == "Actualiser"
        # Fixture redraw only; preview/confirmation/cancel retain real XTEST.
        button.emit("clicked")

    def ready():
        gtk_phase_2027("maint_route")
        w = window[0]
        # Real GTK contract: the footer remains visible and responsive, while
        # an ellipsized status retains its full text in the native tooltip.
        from gi.repository import Pango
        assert w.busy_revealer.get_visible(), "activity footer widget hidden"
        assert w.busy_label.get_wrap() is False
        assert w.busy_label.get_ellipsize() == Pango.EllipsizeMode.END
        assert w.busy_label.get_max_width_chars() == 72
        w.busy_label.set_label("Statut complet de l'activité · " + "détails " * 45)
        assert w.busy_label.get_tooltip_text() == w.busy_label.get_label()
        w.busy_label.set_label("")
        report["checks"]["activity_footer_single_line_full_tooltip"] = True
        assert "tools" in w.nav_rows
        # The technical navigation section can be collapsed in GTK; selecting
        # its hidden ListBox row does not necessarily switch the visible page.
        # Start via the public navigation API. If the technical group is
        # collapsed, a hidden ListBox row may not emit a selection change.
        w.navigate("tools")
        if w.view_stack.get_visible_child_name() != "tools":
            w._visit_route("tools")
        assert w.view_stack.get_visible_child_name() == "tools", "Outils page not visible"
        assert w._page_lifecycle_dispatcher.handles("tools")
        assert w._tools_box is not None
        report["checks"]["P0_7100_real_installed_page_contribution"] = True
        if args.guided_only:
            key = guided_packages[args.guided_only][1]
            expected_ids = {selected for _, selected in selection}
            assert {r.plugin_id for r in manager.services("maintenance.tool")} == expected_ids
            expected = {
                "organizer": "Organiser la bibliothèque",
                "origin": "Suggestions de provenance — par lots",
                "derived": "Publications dérivées interrompues",
            }
            title = expected[args.guided_only]

            def titles():
                return [node.get_title() for node in walk(w._tools_box)
                        if isinstance(node, Adw.ActionRow)]

            host = w._plugin_page_widgets["tools"]
            assert titles().count(title) == 1
            assert not any(other in titles() for kind, other in expected.items()
                           if kind != args.guided_only)
            manager.set_enabled(key, False)
            refresh_tools()
            assert manager.service(key) is None and title not in titles()
            if args.guided_only == "derived":
                capacity_key = "maintenance.library.derived-capacity"
                assert titles().count("Capacité du journal des variantes") == 1
                manager.set_enabled(capacity_key, False)
                refresh_tools()
                assert "Capacité du journal des variantes" not in titles()
                manager.set_enabled(capacity_key, True)
                refresh_tools()
                assert titles().count("Capacité du journal des variantes") == 1
            assert w._plugin_page_widgets["tools"] is host
            manager.set_enabled(key, True)
            refresh_tools()
            assert manager.service(key) is not None and titles().count(title) == 1
            assert w._plugin_page_widgets["tools"] is host
            report["checks"]["P0_7100_single_installed_tool_optional"] = True
            until(lambda: not w.view_stack.get_transition_running(), guided_actions)
            return
        review_rows = [node for node in walk(w._tools_box)
                       if isinstance(node, Adw.ActionRow)
                       and node.get_title() == "Publications dérivées interrompues"]
        assert len(review_rows) == 1
        assert "lecture seule" in review_rows[0].get_subtitle()
        assert not any(isinstance(node, Gtk.ProgressBar) for node in walk(review_rows[0]))
        report["checks"]["P0_4520_independent_read_only_review_row"] = True
        historical_rows = [node for node in walk(w._tools_box)
                           if isinstance(node, Adw.ActionRow)
                           and node.get_title() == "Provenances de prêts historiques"]
        assert len(historical_rows) == 1
        assert "sans accès fournisseur" in historical_rows[0].get_subtitle()
        held_rows = [node for node in walk(w._tools_box)
                     if isinstance(node, Adw.ActionRow)
                     and node.get_title() == "Verrous de téléchargements ADEPT"]
        assert len(held_rows) == 1
        def section_contains(row, heading):
            ancestor = row.get_parent()
            while ancestor is not None and ancestor is not w._tools_box:
                first = ancestor.get_first_child()
                if isinstance(first, Gtk.Label) and first.get_text() == heading:
                    return True
                ancestor = ancestor.get_parent()
            return False
        assert section_contains(historical_rows[0], "Origine et provenance")
        assert section_contains(held_rows[0], "Reprise des opérations")
        assert "aucun déverrouillage autorisé" in held_rows[0].get_subtitle()
        assert not any(isinstance(node, Gtk.Button)
                       and getattr(node, "_cifereca_action_label", "") == "Verrous…"
                       for node in walk(historical_rows[0]))
        report["checks"]["P0_976_historical_local_review_row"] = True
        organizer_rows = [node for node in walk(w._tools_box)
                          if isinstance(node, Adw.ActionRow)
                          and node.get_title() == "Organiser la bibliothèque"]
        assert len(organizer_rows) == 1
        assert "Lecture seule" in organizer_rows[0].get_subtitle()
        report["checks"]["P0_3176_organizer_read_only_row"] = True
        group_names = ("Organiser la bibliothèque", "Vérifier l’intégrité",
                       "Qualité documentaire", "Origine et provenance",
                       "Reprise des opérations", "Stockage et entretien technique")
        # Only direct section containers belong to the taxonomy. Nested
        # plugin widgets can have labels with the same text as headings.
        headings = []
        child = w._tools_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Box):
                heading = child.get_first_child()
                if isinstance(heading, Gtk.Label) and heading.get_text() in group_names:
                    headings.append(heading.get_text())
            child = child.get_next_sibling()
        assert tuple(headings) == group_names
        links = [node for node in walk(w._tools_box)
                 if isinstance(node, Gtk.Button)
                 and node.get_label() == "Voir : Reprise des opérations"]
        assert len(links) == 1 and section_contains(links[0], group_names[0])
        retry_rows = [node for node in walk(w._tools_box)
                      if isinstance(node, Adw.ActionRow)
                      and node.get_title() == "Téléchargements à reprendre"]
        assert len(retry_rows) == 1 and section_contains(retry_rows[0], group_names[4])
        capacity_rows = [node for node in walk(w._tools_box)
                         if isinstance(node, Adw.ActionRow)
                         and node.get_title() == "Capacité du journal des variantes"]
        assert len(capacity_rows) == 1 and section_contains(capacity_rows[0], group_names[5])
        assert section_contains(review_rows[0], group_names[4])
        assert tool.plan_calls == tool.run_calls == 0
        report["checks"]["P0_7407_six_groups_and_single_contextual_retry"] = True
        origin_rows = [node for node in walk(w._tools_box)
                       if isinstance(node, Adw.ActionRow)
                       and node.get_title() == "Suggestions de provenance — par lots"]
        assert len(origin_rows) == 1
        assert "Lecture seule" in origin_rows[0].get_subtitle()
        assert {resolved.plugin_id for resolved in original_services("maintenance.tool")} == {
            "maintenance.library.organizer", "maintenance.library.local-origin",
            "maintenance.library.derived-variants", "maintenance.library.derived-capacity",
        }
        # Historical result key retained for downstream CI summaries.
        report["checks"]["P0_7100_three_real_guided_contributions"] = True
        # Prove each installed contribution is individually optional in the
        # *actual* GTK host, not merely in a detached PluginManager.
        titles = {
            "maintenance.library.organizer": "Organiser la bibliothèque",
            "maintenance.library.local-origin": "Suggestions de provenance — par lots",
            "maintenance.library.derived-variants": "Publications dérivées interrompues",
            "maintenance.library.derived-capacity": "Capacité du journal des variantes",
        }
        host_widget = w._plugin_page_widgets["tools"]

        def action_titles():
            return [row.get_title() for row in walk(w._tools_box)
                    if isinstance(row, Adw.ActionRow)]

        for key, title in titles.items():
            assert action_titles().count(title) == 1
            manager.set_enabled(key, False)
            assert manager.service(key) is None
            refresh_tools()
            assert w._plugin_page_widgets["tools"] is host_widget
            names = action_titles()
            assert title not in names, "disabled plugin resurrected its native row"
            assert all(names.count(other) == 1 for other_id, other in titles.items()
                       if other_id != key), "disabled tool affected another plugin"
            assert names.count("Provenances de prêts historiques") == 1
            assert tool.plan_calls == tool.run_calls == 0
            manager.set_enabled(key, True)
            assert manager.service(key) is not None
            refresh_tools()
            names = action_titles()
            assert all(names.count(other) == 1 for other in titles.values())
            assert names.count("Provenances de prêts historiques") == 1
            report["checks"]["P0_7100_gtk_optional_" + key.rsplit(".", 1)[-1]] = True

        # Receive the zero-optional-contribution case in the actual page
        # host as well: disabling each independently is not proof that the
        # empty combination is safe. No registry mock or tool dispatch.
        try:
            for key in titles:
                manager.set_enabled(key, False)
            refresh_tools()
            assert not original_services("maintenance.tool")
            assert w._plugin_page_widgets["tools"] is host_widget
            names = action_titles()
            assert not any(title in names for title in titles.values())
            assert names.count("Provenances de prêts historiques") == 1
            assert names.count("Téléchargements à reprendre") == 1
            seen = []
            child = w._tools_box.get_first_child()
            while child is not None:
                if isinstance(child, Gtk.Box):
                    heading = child.get_first_child()
                    if isinstance(heading, Gtk.Label) and heading.get_text() in group_names:
                        seen.append(heading.get_text())
                child = child.get_next_sibling()
            assert tuple(seen) == group_names
            assert tool.plan_calls == tool.run_calls == 0
        finally:
            for key in titles:
                manager.set_enabled(key, True)
            refresh_tools()
        assert all(action_titles().count(title) == 1 for title in titles.values())
        assert w._plugin_page_widgets["tools"] is host_widget
        report["checks"]["P0_7407_zero_optional_and_restore_real_host"] = True

        # A cached native GTK row can remain rooted while another page is
        # visible. Its old click callback must never start a new worker.
        old_hold, = (node for node in walk(w._tools_box)
                     if isinstance(node, Adw.ActionRow)
                     and node.get_title() == "Verrous de téléchargements ADEPT")
        stale, = (node for node in walk(old_hold)
                  if isinstance(node, Gtk.Button)
                  and getattr(node, "_cifereca_action_label", "") == "Verrous…")
        w.navigate("dashboard")
        assert w.view_stack.get_visible_child_name() == "dashboard"
        submitted = []
        original_submit = w.runner.submit
        try:
            w.runner.submit = lambda *args, **kwargs: submitted.append(args)
            stale.emit("clicked")
        finally:
            w.runner.submit = original_submit
        assert not submitted
        w.navigate("tools")
        assert w.view_stack.get_visible_child_name() == "tools"
        assert w._plugin_page_widgets["tools"] is host_widget
        assert all(action_titles().count(title) == 1 for title in titles.values())
        fresh_hold, = (node for node in walk(w._tools_box)
                       if isinstance(node, Adw.ActionRow)
                       and node.get_title() == "Verrous de téléchargements ADEPT")
        assert fresh_hold is not old_hold and old_hold.get_root() is None
        assert tool.plan_calls == tool.run_calls == 0
        report["checks"]["P0_7407_native_adapter_navigation_revoked"] = True

        # #7407: exercise the actual installed page and identity coordinator
        # across A→B→A. Only the asynchronous identity reads are captured;
        # neither provider traffic nor maintenance work is necessary here.
        if args.width == 1366:
            from cifereca.identities import Identity
            service = w.service
            original_identity = service.current_identity
            assert original_identity is not None
            first_id = original_identity.identity_id
            second_id = "maintenance-aba-fixture"
            assert service.identity_by_id(second_id) is None
            synthetic = Identity(second_id, "Profil témoin Maintenance", directory_name=second_id)
            service.identities.append(synthetic)

            def current_hold():
                row, = (node for node in walk(w._tools_box)
                        if isinstance(node, Adw.ActionRow)
                        and node.get_title() == "Verrous de téléchargements ADEPT")
                button, = (node for node in walk(row) if isinstance(node, Gtk.Button)
                           and getattr(node, "_cifereca_action_label", "") == "Verrous…")
                return row, button

            first_row, first_button = current_hold()
            identity_jobs = []
            original_submit = w.runner.submit
            try:
                w.runner.submit = lambda *a, **kw: identity_jobs.append((a, kw))
                w._activate_identity(second_id)
                assert service.current_identity.identity_id == second_id
                assert w.view_stack.get_visible_child_name() == "tools"
                assert w._plugin_page_widgets["tools"] is host_widget
                second_row, second_button = current_hold()
                assert second_row is not first_row and first_row.get_root() is None
                before = len(identity_jobs)
                first_button.emit("clicked")
                assert len(identity_jobs) == before

                w._activate_identity(first_id)
                assert service.current_identity.identity_id == first_id
                assert w.view_stack.get_visible_child_name() == "tools"
                assert w._plugin_page_widgets["tools"] is host_widget
                restored_row, _ = current_hold()
                assert restored_row is not first_row and restored_row is not second_row
                assert second_row.get_root() is None
                before = len(identity_jobs)
                first_button.emit("clicked")
                second_button.emit("clicked")
                assert len(identity_jobs) == before
                assert all(action_titles().count(title) == 1 for title in titles.values())
                assert action_titles().count("Téléchargements à reprendre") == 1
                assert sum(kw.get("kind") == "identity" for _, kw in identity_jobs) == 2
                assert not any(kw.get("kind") == "maintenance-plan" for _, kw in identity_jobs)
                assert tool.plan_calls == tool.run_calls == 0
            finally:
                w.runner.submit = original_submit
                if service.current_identity.identity_id != first_id:
                    service.select_identity(first_id)
                service.identities.remove(synthetic)
            report["checks"]["P0_7407_real_host_profile_aba_revoked"] = True

        preview_button[0] = next((
            b for b in walk(w._tools_box)
            if isinstance(b, Gtk.Button)
            and getattr(b, "_cifereca_action_label", "") == "Prévisualiser…"
        ), None)
        assert preview_button[0] is not None
        # Check the *real* allocated GTK window against the Xvfb geometry.
        # Assertions are deliberately independent of EWMH/WM maximize state.
        assert 0 < w.get_width() <= args.width
        assert 0 < w.get_height() <= args.display_height
        report["checks"]["P0_2712_window_fits_real_display"] = True
        report["checks"]["real_tools_route_and_action"] = True
        until(
            lambda: preview_button[0].get_mapped()
            and not w.view_stack.get_transition_running(),
            guided_actions if args.width == 1366 else begin_busy,
        )

    def guided_actions():
        # Actual installed plugins, actual Core ports, actual worker/GTK
        # callbacks. The legacy XTEST campaign below remains unchanged.
        # Emit the exact native action signals here (not a pointer-geometry
        # proof); rows below the viewport need not be scrolled into view.
        checks = (
            ("Organiser la bibliothèque", "Prévisualiser…",
             "Aperçu partiel du nommage", "organizer_preview"),
            ("Suggestions de provenance — par lots", "Prévisualiser…",
             "Aucune origine, association ou licence créée.", "origin_preview"),
            ("Publications dérivées interrompues", "Examiner…",
             "Aucune reprise ni suppression autorisée.", "derived_overview"),
            ("Capacité du journal des variantes", "Mesurer…",
             "Aucun quota libéré, aucun octet récupérable attesté.", "derived_capacity"),
            ("Sélection des publications interrompues", "Lister…",
             "coordonnées non vérifiées. Aucune reprise ni suppression.", "derived_list"),
        )
        if args.guided_only:
            keys = {
                "organizer": {"organizer_preview"},
                "origin": {"origin_preview"},
                "derived": {"derived_overview", "derived_capacity", "derived_list"},
            }
            checks = tuple(check for check in checks if check[3] in keys[args.guided_only])
            assert checks
        storage = core.library.storage

        def durable_counts():
            with storage._lock:
                db = storage._connect()
                return tuple(db.execute("SELECT count(*) FROM " + table).fetchone()[0]
                             for table in ("artifacts", "placements", "origin_links",
                                           "acquisition_ledger", "entitlements"))

        # A real corpus (not a fake page DTO): five distinct, unlinked
        # registered PDFs in the runner's isolated library/profile. This
        # exceeds both four-entry Core page limits without any provider call.
        service = core.library
        owner = service.current_identity
        root = Path(service.config.get("library.directory")).expanduser()
        assert root.is_absolute() and root.resolve().is_relative_to(Path.home().resolve())
        assert storage.exact_profile_directory_binding(owner.identity_id) == (
            owner.directory_name, True,
        )
        profile = root / owner.directory_name
        corpus = profile / "Maintenance pagination fixture"
        corpus.mkdir(parents=True, exist_ok=False)
        witnesses = []
        for index in range(5):
            book = corpus / f"pagination-{index}.pdf"
            content = f"%PDF-1.7\\nSynthetic maintenance page {index}\\n".encode("ascii")
            fmt, title, author = "pdf", f"Témoin de pagination {index}", "Auteur témoin"
            scope = {}
            if args.guided_only == "organizer" and index == 4:
                # One exact rename candidate among four non-qualified PDFs.
                # Actual registered EPUB, no synthetic broker return.
                book = corpus / "rename-opaque.epub"
                fmt, title, author = "epub", "Roman témoin", "Autrice témoin"
                from zipfile import ZIP_STORED, ZipFile
                with ZipFile(book, "w") as archive:
                    archive.writestr("mimetype", "application/epub+zip", compress_type=ZIP_STORED)
                    archive.writestr("META-INF/container.xml",
                        '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                        '<rootfiles><rootfile full-path="OPS/content.opf"/></rootfiles></container>')
                    archive.writestr("OPS/content.opf",
                        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
                        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                        '<dc:title>Roman témoin</dc:title><dc:creator>Autrice témoin</dc:creator>'
                        '</metadata><manifest><item id="p" href="page.xhtml" media-type="application/xhtml+xml"/>'
                        '</manifest><spine><itemref idref="p"/></spine></package>')
                    archive.writestr("OPS/page.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body>Fixture</body></html>')
                content = book.read_bytes()
                scope = {"provider_id": "local", "source_item_id": "native-rename-fixture"}
            else:
                book.write_bytes(content)
            digest = hashlib.sha256(content).hexdigest()
            artifact = "sha256:" + digest
            storage.artifact_put({"artifact_id": artifact, "sha256": digest,
                                  "size": len(content), "media_format": fmt})
            pid = storage.placement_put({"artifact_id": artifact,
                "identity_id": owner.identity_id, "path": str(book), "state": "present", **scope})
            storage.catalog_entry_put({"catalog_entry_id": "maintenance-pagination-" + pid,
                "identity_id": owner.identity_id, "provider_id": "local",
                "artifact_id": artifact, "placement_id": pid,
                "title": title, "author": author,
                "media_format": fmt, "local_state": "local",
                **({"item_id": scope["source_item_id"]} if scope else {})})
            witnesses.append((book, content, pid, artifact))

        # Only the isolated derived package gets genuine interrupted
        # receipts. Seventeen rows exceed its public 16-entry page limit.
        # The fixture writes before the review baseline, never via a tool.
        derived_witnesses = []
        if args.guided_only == "derived":
            import os
            from cifereca.core.derived_variant_journal import (
                DerivedIntent, reserve_derived_publication,
            )
            parent, parent_bytes, parent_id, parent_artifact = witnesses[0]
            info = parent.stat()
            parent_stamp = (info.st_dev, info.st_ino, info.st_size,
                            info.st_mtime_ns, info.st_ctime_ns)
            stage_dir = parent.parent / ".cifereca-staging" / "variants"
            stage_dir.mkdir(parents=True, exist_ok=False)
            for number in range(17):
                stage = stage_dir / f"synthetic-{number}.pdf"
                target = parent.parent / f"derived-{number}.pdf"
                content = f"%PDF-1.7\nSynthetic derived receipt {number}\n".encode("ascii")
                stage.write_bytes(content)
                st = stage.stat()
                intent = DerivedIntent(
                    identity_id=owner.identity_id,
                    parent_placement_id=parent_id,
                    parent_sha256=hashlib.sha256(parent_bytes).hexdigest(),
                    parent_path=str(parent), staged_path=str(stage),
                    candidate_path=str(target),
                    output_sha256=hashlib.sha256(content).hexdigest(),
                    output_size=len(content), parent_stamp=parent_stamp,
                    output_stamp=(st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns),
                    transformer_id="document.transformer.synthetic",
                    transformer_version="1", profile_id="",
                    profile_version="", options_fingerprint=f"fixture-{number}",
                    variant_kind="optimized", media_format="pdf",
                )
                reservation = reserve_derived_publication(storage, intent)
                os.link(stage, target)
                derived_witnesses.append((reservation.key, stage, target))

        def fixture_evidence():
            files = tuple((book.read_bytes(), storage.placement_get(pid), artifact)
                          for book, _content, pid, artifact in witnesses)
            receipts = tuple((storage.metadata_get(key), stage.read_bytes(),
                              target.read_bytes(), stage.samefile(target))
                             for key, stage, target in derived_witnesses)
            return files, receipts

        before = durable_counts()
        evidence = fixture_evidence()

        def step(index=0):
            if index == len(checks):
                assert durable_counts() == before, "Read-only guided actions changed inventory"
                assert fixture_evidence() == evidence, "Guided review changed a fixture Placement or PDF"
                assert tool.plan_calls == tool.run_calls == 0
                report["checks"]["P0_7100_guided_fixture_bytes_and_placements_unchanged"] = True
                report["checks"]["P0_7100_guided_actions_no_inventory_mutation"] = True
                capture("00-guided-actions")
                if args.guided_only:
                    # No other packages, no dummy legacy workflow and no
                    # destructive cleanup opt-in for isolated tool scenarios.
                    report["status"] = "PASS"
                    result[0] = 0
                    gtk_phase_2027("maint_done")
                    save()
                    app.quit()
                    return
                # Clear expanded text and restore the exact old XTEST layout.
                refresh_tools()
                preview_button[0] = next(b for b in walk(window[0]._tools_box)
                    if isinstance(b, Gtk.Button)
                    and getattr(b, "_cifereca_action_label", "") == "Prévisualiser…")
                until(lambda: preview_button[0].get_mapped(), begin_busy)
                return
            title, action, expected, key = checks[index]
            row, = (node for node in walk(window[0]._tools_box)
                    if isinstance(node, Adw.ActionRow) and node.get_title() == title)
            button, = (node for node in walk(row)
                       if isinstance(node, Gtk.Button) and node.get_label() == action)
            assert button.get_sensitive()
            # Find the exact plugin contribution, never another label with
            # coincidentally matching text from the host or another row.
            outer = row
            while outer.get_parent().get_parent() is not window[0]._tools_box:
                outer = outer.get_parent()
                assert outer is not None
            assert not any(expected in node.get_text() for node in walk(outer)
                           if isinstance(node, Gtk.Label))
            button.emit("clicked")
            assert not button.get_sensitive(), "Guided worker not admitted"

            def observed():
                return button.get_sensitive() and any(
                    expected in node.get_text() for node in walk(outer)
                    if isinstance(node, Gtk.Label) and node.get_visible()
                )

            def cancel_and_restart():
                # Isolated installed tool: signals in the same GTK turn,
                # before result delivery. No fake broker or sleep.
                cancel, = (node for node in walk(outer)
                           if isinstance(node, Gtk.Button) and node.get_label() == "Annuler")
                assert button.get_sensitive()
                button.emit("clicked")
                assert cancel.get_sensitive() and not button.get_sensitive()
                cancel.emit("clicked")
                assert not button.get_sensitive() and not cancel.get_sensitive()
                cancelled_text = (
                    "Revue annulée. Nouvelle sélection nécessaire" if key == "derived_list"
                    else "Revue arrêtée ou preuves modifiées"
                )
                following, = (node for node in walk(outer)
                              if isinstance(node, Gtk.Button) and node.get_label() == "Page suivante")

                def cancelled_visible():
                    return button.get_sensitive() and any(
                        cancelled_text in node.get_text() for node in walk(outer)
                        if isinstance(node, Gtk.Label) and node.get_visible()
                    )

                def cancelled():
                    assert not following.get_sensitive()
                    assert not cancel.get_sensitive()
                    assert not any(expected in node.get_text() for node in walk(outer)
                                   if isinstance(node, Gtk.Label) and node.get_visible())
                    assert durable_counts() == before and fixture_evidence() == evidence
                    report["checks"]["P0_7092_7093_native_cancel_" + key] = True
                    button.emit("clicked")
                    assert not button.get_sensitive()

                    def restarted():
                        if key == "origin_preview":
                            assert following.get_sensitive()
                            assert any("Page 1 ·" in node.get_text() for node in walk(outer)
                                       if isinstance(node, Gtk.Label) and node.get_visible())
                        else:
                            simulation, = (node for node in walk(outer)
                                           if isinstance(node, Gtk.Button)
                                           and node.get_label() == "Confirmer la simulation sans écriture")
                            assert not simulation.get_sensitive()
                        report["checks"]["P0_7092_7093_native_restart_" + key] = True
                        step(index + 1)

                    until(observed, restarted)

                until(cancelled_visible, cancelled)

            def received():
                report["checks"]["P0_7100_real_guided_" + key] = True
                if args.guided_only == "derived" and key == "derived_list":
                    following, = (node for node in walk(outer)
                                  if isinstance(node, Gtk.Button)
                                  and node.get_label() == "Page suivante")
                    simulation, = (node for node in walk(outer)
                                   if isinstance(node, Gtk.Button)
                                   and node.get_label() == "Confirmer la simulation sans écriture")

                    def labels_contain(fragment):
                        return any(fragment in node.get_text() for node in walk(outer)
                                   if isinstance(node, Gtk.Label) and node.get_visible())

                    assert labels_contain("16 reçu(s)") and following.get_sensitive()
                    assert not simulation.get_sensitive()
                    following.emit("clicked")
                    assert not following.get_sensitive()

                    def second_receipt_page():
                        assert not following.get_sensitive() and not simulation.get_sensitive()
                        assert not labels_contain("16 reçu(s)")
                        report["checks"]["P0_7093_native_derived_page_two"] = True
                        (verify,) = (node for node in walk(outer)
                                     if isinstance(node, Gtk.Button)
                                     and node.get_label() == "Vérifier")
                        verify.emit("clicked")
                        assert not simulation.get_sensitive()

                        def reviewed():
                            assert labels_contain("Lien physique vérifié")
                            assert durable_counts() == before and fixture_evidence() == evidence
                            simulation.emit("clicked")
                            assert not simulation.get_sensitive()

                            def simulated():
                                assert not simulation.get_sensitive()
                                assert durable_counts() == before and fixture_evidence() == evidence
                                report["checks"]["P0_7093_native_confirmed_simulation_no_write"] = True
                                cancel_and_restart()

                            until(lambda: button.get_sensitive() and
                                  labels_contain("Simulation vérifiée ; aucune reprise effectuée."),
                                  simulated)

                        until(lambda: button.get_sensitive() and simulation.get_sensitive(), reviewed)

                    until(lambda: button.get_sensitive() and labels_contain("1 reçu(s)"),
                          second_receipt_page)
                    return
                if key not in ("organizer_preview", "origin_preview"):
                    step(index + 1)
                    return

                def page_visible(number):
                    return any(f"Page {number} ·" in node.get_text()
                               for node in walk(outer) if isinstance(node, Gtk.Label)
                               and node.get_visible())

                assert page_visible(1) and not page_visible(2)
                following, = (node for node in walk(outer)
                              if isinstance(node, Gtk.Button)
                              and node.get_label() == "Page suivante")
                assert following.get_sensitive(), "Five registered candidates must allow a second page"
                # Real GTK signal to the installed contribution, not pointer
                # geometry. No implicit continuation while page 1 is displayed.
                following.emit("clicked")
                assert not following.get_sensitive()

                def second_page():
                    assert page_visible(2) and not page_visible(1)
                    report["checks"]["P0_7100_real_guided_page_two_" + key] = True
                    if args.guided_only == "origin" and key == "origin_preview":
                        cancel_and_restart()
                    elif args.guided_only == "organizer" and key == "organizer_preview":
                        cancel, = (node for node in walk(outer)
                                   if isinstance(node, Gtk.Button)
                                   and node.get_label() == "Annuler")
                        assert cancel.get_sensitive()
                        cancel.emit("clicked")
                        assert not following.get_sensitive() and not cancel.get_sensitive()
                        assert any("Revue annulée. Relancer explicitement." in node.get_text()
                                   for node in walk(outer)
                                   if isinstance(node, Gtk.Label) and node.get_visible())
                        assert durable_counts() == before and fixture_evidence() == evidence
                        report["checks"]["P0_3176_native_organizer_cancel"] = True
                        button.emit("clicked")
                        assert not button.get_sensitive()

                        def restarted():
                            assert page_visible(1) and not page_visible(2)
                            report["checks"]["P0_3176_native_organizer_restart"] = True
                            native_dialog_receipt()

                        def native_dialog_receipt():
                            # Exercise the installed contribution, host, native
                            # confirmation and production refusal end-to-end.
                            # Neither a fixture fence nor a fake writer is used.
                            prepare, = (node for node in walk(outer)
                                        if isinstance(node, Gtk.Button)
                                        and node.get_label() == "Préparer le plan à blanc")
                            selector, = (node for node in walk(outer)
                                          if isinstance(node, Gtk.ComboBoxText))
                            native, = (node for node in walk(window[0]._tools_box)
                                       if isinstance(node, Gtk.Button)
                                       and node.get_label() == "Confirmer l’opération…")
                            assert not native.get_visible()

                            def find_candidate():
                                if not prepare.get_sensitive():
                                    assert following.get_sensitive()
                                    following.emit("clicked")
                                    until(lambda: button.get_sensitive() and page_visible(2), prepare_candidate)
                                else:
                                    prepare_candidate()

                            def prepare_candidate():
                                assert prepare.get_sensitive()
                                selector.set_active_id("rename-only")
                                assert selector.get_active_id() == "rename-only"
                                prepare.emit("clicked")
                                until(lambda: prepare.get_sensitive()
                                      and prepare.get_label() == "Confirmer la simulation",
                                      confirm_simulation)

                            def confirm_simulation():
                                assert not native.get_visible()
                                prepare.emit("clicked")
                                until(lambda: native.get_visible() and native.get_sensitive(), open_dialog)

                            def open_dialog():
                                native.emit("clicked")
                                until(lambda: modal() is not None, cancel_dialog)

                            def cancel_dialog():
                                dialog = modal()
                                assert dialog.get_heading() == "Confirmer le renommage ?"
                                assert dialog.get_default_response() == "cancel"
                                assert "rename-opaque.epub" in dialog.get_body()
                                assert service.preview_library_filename(title="Roman témoin",
                                    author="Autrice témoin", extension="epub") in dialog.get_body()
                                assert fixture_evidence() == evidence
                                # Drive the real GTK response button. Emitting
                                # MessageDialog.response directly would signal
                                # the handler without closing the modal.
                                respond(dialog, "Annuler", lambda: until(
                                    lambda: modal() is None and native.get_sensitive(), reopen))

                            def reopen():
                                report["checks"]["P0_3176_real_native_rename_cancel"] = True
                                native.emit("clicked")
                                until(lambda: modal() is not None, apply_dialog)

                            def apply_dialog():
                                dialog = modal()
                                assert dialog.get_heading() == "Confirmer le renommage ?"
                                # Capture the actual worker result in GTK. This
                                # only observes presentation, not admission.
                                notices = []
                                host_window = window[0]
                                original_toast = host_window.toast
                                def toast(message, *args, **kwargs):
                                    notices.append(message)
                                    return original_toast(message, *args, **kwargs)
                                host_window.toast = toast
                                def refused():
                                    host_window.toast = original_toast
                                    assert any("Renommage indisponible ou refusé" in x for x in notices)
                                    assert not native.get_sensitive()
                                    assert fixture_evidence() == evidence and durable_counts() == before
                                    report["checks"]["P0_3176_real_native_rename_production_hold"] = True
                                    step(index + 1)
                                # Verify the installed native confirmation through
                                # the actual button and GTK response lifecycle.
                                respond(dialog, "Confirmer", lambda: until(
                                    lambda: bool(notices), refused))

                            find_candidate()

                        until(lambda: button.get_sensitive() and page_visible(1),
                              restarted)
                    else:
                        step(index + 1)

                until(lambda: button.get_sensitive() and page_visible(2), second_page)

            until(observed, received)

        step()

    def begin_busy():
        gtk_phase_2027("maint_busy_guard")
        assert_line("Au repos")
        report["checks"]["cache_progress_attached_to_real_tool_row"] = True
        lock[0] = InterProcessLock(acquisition_lock_file())
        lock[0].acquire()
        physical(preview_button[0])
        until(lambda: bool(errors), busy_refused)

    def busy_refused():
        gtk_phase_2027("maint_scan")
        assert "Synchronisation en cours" in errors[-1], errors
        assert tool.plan_calls == 0 and tool.run_calls == 0
        assert modal() is None
        report["checks"]["busy_sync_refuses_preview_before_scan_or_delete"] = True
        lock[0].release()
        lock[0] = None
        errors.clear()
        tool.plan_started.clear()
        tool.release_plan.clear()
        physical(preview_button[0])
        # The worker event is raised off-thread before GLib has necessarily
        # delivered the row's queued "Analyse…" state. Wait for both
        # independent observations, without weakening the busy/spinner check.
        until(
            lambda: tool.plan_started.is_set() and line_is("Analyse…"),
            preview_pending,
        )

    def heartbeat_tick():
        heartbeat[0] += 1
        return report["status"] == "RUNNING"

    def preview_pending():
        gtk_phase_2027("maint_first_dialog")
        assert modal() is None, "GTK dialog opened before slow plan finished"
        assert tool.run_calls == 0
        assert_line("Analyse…", busy=True)
        report["checks"]["analysis_indeterminate_on_tool_row"] = True
        before = heartbeat[0]
        report["checks"]["slow_worker_started_without_mutation"] = True
        def release_preview():
            assert modal() is None and tool.run_calls == 0
            report["checks"]["gtk_heartbeat_during_slow_scan"] = True
            tool.release_plan.set()
            until(preview_allocated, first_dialog)
        # Keep the worker blocked until GTK proves it can process heartbeats.
        until(lambda: heartbeat[0] > before + 3, release_preview)

    def preview_allocated():
        if tool.plan_ready.is_set():
            report["checks"]["initial_maintenance_plan_ready"] = True
        dialog = modal()
        if dialog is None:
            return False
        report["checks"]["initial_maintenance_dialog_mapped"] = True
        preview_scroll = dialog.get_extra_child()
        if not isinstance(preview_scroll, Gtk.ScrolledWindow):
            return True  # first_dialog reports the actual layout violation.
        content = next((w for w in walk(preview_scroll) if isinstance(w, Gtk.Label)), None)
        if content is None:
            return True
        adjustment = preview_scroll.get_vadjustment()
        geometry = {
            "width": preview_scroll.get_width(),
            "height": preview_scroll.get_height(),
            "content_width": content.get_width(),
            "content_height": content.get_height(),
            "upper": adjustment.get_upper(),
            "page_size": adjustment.get_page_size(),
        }
        report["preview_allocation"] = geometry
        # Mapping precedes GTK's first size allocation. Wait for measurable
        # widgets, not for overflow: first_dialog must still detect a broken
        # scroll layout after the allocation has actually happened.
        # Modal mapping and the idle-delivered row state may arrive in
        # different main-loop turns. Wait for both; first_dialog still
        # validates the exact label, spinner, and layout independently.
        confirmation_visible = line_is("Confirmation attendue")
        if confirmation_visible:
            report["checks"]["initial_maintenance_confirmation_visible"] = True
        return confirmation_visible and all(geometry[key] > 0 for key in (
            "width", "height", "content_width", "content_height", "page_size",
        ))

    def first_dialog():
        dialog = modal()
        assert_line("Confirmation attendue")
        report["checks"]["confirmation_on_tool_row_without_percentage"] = True
        preview_scroll = dialog.get_extra_child()
        assert isinstance(preview_scroll, Gtk.ScrolledWindow)
        # GTK may insert a Gtk.Viewport between ScrolledWindow and Label.
        content = next((w for w in walk(preview_scroll) if isinstance(w, Gtk.Label)), None)
        assert content is not None
        body = content.get_text()
        assert all(label in body for label in (
            "Résumé :", "Changements prévus :", "Avertissements :", "Portée :",
        ))
        assert "\n" in body and "\\n" not in body, repr(body)
        assert preview_scroll.get_policy()[1] == Gtk.PolicyType.AUTOMATIC
        buttons = {
            label: next((b for b in walk(dialog) if isinstance(b, Gtk.Button)
                         and label in str(b.get_label() or "").replace("_", "")), None)
            for label in ("Annuler", "Exécuter")
        }
        assert all(b is not None and b.get_mapped() for b in buttons.values()), (
            "Preview response buttons must remain visible outside scrolling content"
        )
        adjustment = preview_scroll.get_vadjustment()
        assert adjustment.get_upper() > adjustment.get_page_size(), (
            "20 long preview entries must scroll without hiding dialog responses"
        )
        adjustment.set_value(adjustment.get_upper() - adjustment.get_page_size())
        assert all(b.get_mapped() for b in buttons.values()), (
            "Dialog action buttons disappeared after scrolling the preview"
        )
        report["checks"]["scrollable_preview_with_visible_actions_20_items"] = True
        assert tool.run_calls == 0, "Acquisition executed without confirmation"
        report["checks"]["real_multiline_confirmation_after_async_scan"] = True
        capture("01-async-preview")
        respond(dialog, "Annuler", lambda: later(after_cancel, 190))

    def after_cancel():
        gtk_phase_2027("maint_cancel_stale")
        assert tool.run_calls == 0
        report["checks"]["physical_cancel_never_runs_destructive_tool"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive()
              and line_is("Annulé"), cancel_scan)

    def cancel_scan():
        assert_line("Annulé")
        tool.plan_started.clear()
        tool.release_plan.clear()
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set() and line_is("Analyse…"), cancel_pending_scan)

    def cancel_pending_scan():
        row = line_widgets()[0]
        cancel = next(w for w in walk(row)
                      if isinstance(w, Gtk.Button) and w.get_label() == "Annuler")
        assert modal() is None and tool.run_calls == 0
        physical(cancel)

        def release_cancelled_scan():
            # XTEST queues input; wait for GTK to acknowledge the cancellation
            # before allowing the off-thread plan to return.
            assert not tool.plan_ready.is_set()
            assert modal() is None and tool.run_calls == 0
            tool.release_plan.set()
            until(lambda: tool.plan_ready.is_set() and not cancel.get_visible()
                  and preview_button[0].get_sensitive(), discarded, timeout=4.0)

        def discarded():
            assert modal() is None and tool.run_calls == 0
            assert_line("Annulé")
            report["checks"]["cancelled_scan_never_reopens_confirmation"] = True
            begin_stale()

        until(lambda: not cancel.get_sensitive() and line_is("Annulé"),
              release_cancelled_scan, timeout=3.0)

    def begin_stale():
        tool.delay = 0.12
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), stale_dialog)

    def stale_dialog():
        tool.version += 1
        assert tool.run_calls == 0
        respond(modal(), "Exécuter", lambda: until(
            lambda: any("bibliothèque a changé" in e for e in errors), stale_refused
        ))

    def stale_refused():
        assert tool.run_calls == 0
        report["checks"]["stale_plan_rejected_under_lock_without_delete"] = True
        until(lambda: modal() is None and preview_button[0].get_sensitive(), begin_final)

    def begin_final():
        gtk_phase_2027("maint_execute")
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), final_dialog)

    def final_dialog():
        assert tool.run_calls == 0
        respond(modal(), "Exécuter", lambda: until(
            lambda: tool.progress_sent.is_set() and line_is("Purge en cours · 99 %"),
            progress_before_completion,
        ))

    def progress_before_completion():
        assert tool.run_calls == 1 and not tool.run_finished.is_set()
        assert tool.worker_threads[0] != gtk_thread, "Tool reports must originate in worker"
        assert_line("Purge en cours · 99 %", fraction=0.99)
        report["checks"]["real_worker_report_caps_percentage_before_success"] = True
        capture("02-worker-progress")
        tool.release_run.set()
        until(lambda: tool.run_finished.is_set() and line_is("Terminé · 100 %"), completed)

    def completed():
        gtk_phase_2027("maint_refresh")
        assert tool.run_calls == 1
        assert lock[0] is None
        report["checks"]["only_explicit_confirmed_safe_simulation_executes"] = True
        assert_line("Terminé · 100 %", fraction=1.0)
        report["checks"]["success_alone_renders_100_percent"] = True
        capture("03-confirmed-noop")
        button = next(node for node in walk(window[0]._tools_box)
                      if isinstance(node, Gtk.Button) and node.get_label() == "Résultats…")
        physical(button)
        until(lambda: modal() is not None, inspect_result)

    def inspect_result():
        dialog = modal()
        text = "\n".join(node.get_text() for node in walk(dialog) if isinstance(node, Gtk.Label))
        assert "Fichiers témoins : 524" in text
        assert "Durée maximale témoin atteinte." in text
        assert "NEVER_RENDER_EXECUTION_FACTS" not in text
        close = next(node for node in walk(dialog)
                     if isinstance(node, Gtk.Button) and "Fermer" in str(node.get_label()))
        assert close.get_mapped() and close.get_sensitive()
        assert dialog.get_width() <= window[0].get_width()
        report["checks"]["local_result_details_and_limits_without_execution_facts"] = True
        respond(dialog, "Fermer", lambda: until(lambda: modal() is None, continue_after_result))

    def continue_after_result():
        tool.progress_sent.clear()
        tool.release_run.clear()
        tool.run_finished.clear()
        physical(preview_button[0])
        until(lambda: modal() is not None and preview_button[0].get_sensitive(), refresh_dialog)

    def refresh_dialog():
        respond(modal(), "Exécuter", lambda: until(
            lambda: tool.progress_sent.is_set() and line_is("Purge en cours · 99 %"),
            refresh_running,
        ))

    def refresh_running():
        detached_label[0] = line_widgets()[1]
        refresh_tools()
        until(lambda: line_is("Au repos"), refresh_ready)

    def refresh_ready():
        assert_line("Au repos")
        assert detached_label[0].get_root() is None
        tool.release_run.set()
        until(lambda: tool.run_finished.is_set(), refresh_finished)

    def refresh_finished():
        def verify():
            assert_line("Au repos")
            assert detached_label[0].get_text() == "Purge en cours · 99 %"
            assert tool.run_calls == 2
            report["checks"]["refresh_cancels_worker_and_discards_late_progress"] = True
            capture("04-refresh-discards-late-worker")
            verify_p1_tool_rows()
        later(verify, 250)

    # #1497/#1498: exercise the real GTK allocation and initial progress row
    # with a fake service. All destructive operations remain completely inert.
    p1_variants = [
        ("library.naming.repair", "Renormaliser les noms de fichiers", "1497-naming"),
        ("library.duplicates.cleanup", "Nettoyer les doublons physiques", "1498-duplicates"),
    ]
    p1_index = [0]
    p1_runs = [0]
    p1_modes = ("success", "partial", "cancelled")
    p1_mode_index = [0]

    def verify_p1_tool_rows():
        if p1_index[0] >= len(p1_variants):
            finish()
            return
        tool_id, title, label = p1_variants[p1_index[0]]
        gtk_phase_2027("maint_p1_naming" if p1_index[0] == 0 else "maint_p1_duplicates")
        tool.tool_id = tool_id
        p1_runs[0] = tool.run_calls
        p1_mode_index[0] = 0
        tool.delay = 0.65
        refresh_tools()
        later(verify_p1_allocation, 220)

    def verify_p1_allocation():
        tool_id, title, label = p1_variants[p1_index[0]]
        row, status, spinner, bar = line_widgets()
        assert row.get_title() == title, (tool_id, row.get_title())
        assert row.get_mapped() and status.get_mapped()
        assert row.get_width() > 0 and row.get_height() > 0
        assert status.get_text() == "Au repos"
        assert not spinner.get_visible() and not bar.get_visible()
        assert tool.run_calls == p1_runs[0], "Rendering must never execute a tool"
        report["checks"][f"P1_{label}_real_gtk_idle_line"] = True
        report.setdefault("p1_tool_allocations", []).append({
            "tool": tool_id, "row_width": row.get_width(),
            "row_height": row.get_height(),
            "status_width": status.get_width(),
            "status_height": status.get_height(),
        })
        capture(f"05-p1-{label}-idle")
        preview_button[0] = next(node for node in walk(window[0]._tools_box)
            if isinstance(node, Gtk.Button)
            and getattr(node, "_cifereca_action_label", "") == "Prévisualiser…")
        # Keep the simulated worker in analysis until GTK actually observes
        # that phase. CI scheduling must not race a fixed-duration scan.
        tool.release_plan.clear()
        tool.plan_started.clear()
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set() and line_is("Analyse…"), p1_analysis)

    def p1_analysis():
        assert_line("Analyse…", busy=True)
        assert tool.run_calls == p1_runs[0]
        assert modal() is None
        report["checks"][f"P1_{p1_variants[p1_index[0]][2]}_analysis_indeterminate"] = True
        tool.release_plan.set()
        until(lambda: modal() is not None and line_is("Confirmation attendue"), p1_cancel_preview)

    def p1_cancel_preview():
        assert_line("Confirmation attendue")
        respond(modal(), "Annuler", lambda: until(
            lambda: modal() is None and line_is("Annulé") and preview_button[0].get_sensitive(),
            p1_preview_cancelled,
        ))

    def p1_preview_cancelled():
        assert_line("Annulé")
        assert tool.run_calls == p1_runs[0]
        report["checks"][f"P1_{p1_variants[p1_index[0]][2]}_preview_cancel_no_run"] = True
        p1_begin_run()

    def p1_begin_run():
        mode = p1_modes[p1_mode_index[0]]
        tool.result_ok = mode != "partial"
        tool.progress_sent.clear()
        tool.release_run.clear()
        tool.run_finished.clear()
        physical(preview_button[0])
        until(lambda: modal() is not None and line_is("Confirmation attendue"), p1_confirm_run)

    def p1_confirm_run():
        assert tool.run_calls == p1_runs[0] + p1_mode_index[0]
        assert_line("Confirmation attendue")
        respond(modal(), "Exécuter", lambda: until(
            lambda: tool.progress_sent.is_set() and line_is(p1_progress_text()),
            p1_progress,
        ))

    def p1_progress_text():
        activity = ("Renormalisation des noms" if p1_index[0] == 0 else "Nettoyage des doublons")
        return activity + " en cours · 99 %"

    def p1_progress():
        assert_line(p1_progress_text(), fraction=.99)
        assert not tool.run_finished.is_set()
        assert tool.worker_threads[-1] != gtk_thread
        mode = p1_modes[p1_mode_index[0]]
        label = p1_variants[p1_index[0]][2]
        report["checks"][f"P1_{label}_{mode}_worker_progress_before_terminal"] = True
        capture(f"06-p1-{label}-{mode}-99")
        if mode == "cancelled":
            cancel = next(node for node in walk(window[0]._tools_box)
                          if isinstance(node, Gtk.Button) and node.get_label() == "Annuler")
            physical(cancel)
        tool.release_run.set()
        expected = {"success": "Terminé · 100 %", "partial": "Échec", "cancelled": "Annulé"}[mode]
        until(lambda: tool.run_finished.is_set() and line_is(expected), p1_terminal)

    def p1_terminal():
        mode = p1_modes[p1_mode_index[0]]
        expected = {"success": "Terminé · 100 %", "partial": "Échec", "cancelled": "Annulé"}[mode]
        assert_line(expected, fraction=1.0 if mode == "success" else None)
        assert tool.run_calls == p1_runs[0] + p1_mode_index[0] + 1
        label = p1_variants[p1_index[0]][2]
        report["checks"][f"P1_{label}_{mode}_terminal_on_own_line"] = True
        capture(f"07-p1-{label}-{mode}-terminal")
        p1_mode_index[0] += 1
        if p1_mode_index[0] < len(p1_modes):
            until(lambda: preview_button[0].get_sensitive() and modal() is None, p1_begin_run)
            return
        p1_index[0] += 1
        verify_p1_tool_rows()

    def finish():
        # The retired reconciliation Maintenance tool is no longer an E2E entrypoint.
        # Continue to exercise the independent explicit library-declaration flow.
        from gi.repository import Pango
        from library_declaration_acceptance import exercise
        exercise(parent=window[0], Gtk=Gtk, Adw=Adw, GLib=GLib, Pango=Pango,
                 device=device[0], until=until, walk=walk, report=report,
                 checkpoint=gtk_phase_2027, done=finish_declaration)

    def finish_declaration():
        if args.width in (1366, 1600):
            # SIGABRT may preclude Python's report/error handling. Bracket
            # the only extra native acceptance at these display widths.
            gtk_phase_2027("maint_transfer_start")
            exercise_native_transfer(parent=window[0], Gtk=Gtk, Adw=Adw,
                                     device=device[0], until=until, report=report,
                                     capture=capture, done=finish_transfer)
        else:
            finish_transfer()

    def finish_transfer():
        if args.width in (1366, 1600):
            gtk_phase_2027("maint_transfer_done")
        gtk_phase_2027("maint_done")
        report["status"] = "PASS"
        result[0] = 0
        save()
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.MaintenanceSafetyE2E")

        def do_activate(self):
            try:
                gtk_phase_2027("maint_window_start")
                w = MainWindow(self, core.config, core.library, core.diagnostics)
                window[0] = w
                w.show_error = lambda message, _title=None: errors.append(str(message))
                # P0 #2712: test the actual Xvfb height, never a 900px
                # display while claiming a shorter laptop/desktop geometry.
                # Leave room for WM chrome without requiring maximize.
                w.set_default_size(args.width - 40, args.display_height - 40)
                w.present()
                gtk_phase_2027("maint_window_shown")
                device[0] = X11Input()
                GLib.timeout_add(45, heartbeat_tick)
                later(ready, 500)
            except Exception as exc:
                fail(exc)

        def do_shutdown(self):
            if device[0] is not None:
                device[0].close()
            manager.close()
            core.close()
            fixture.cleanup()
            Gio.Application.do_shutdown(self)

    app = Application()
    GLib.timeout_add_seconds(
        48, lambda: (fail(TimeoutError("Maintenance GTK recipe timeout")), False)[1]
        if report["status"] == "RUNNING" else False,
    )
    app.run([sys.argv[0]])
    if report["status"] == "RUNNING":
        report.update(status="FAIL", error="GTK exited without executing proof")
        save()
    return result[0]


if __name__ == "__main__":
    raise SystemExit(main())
