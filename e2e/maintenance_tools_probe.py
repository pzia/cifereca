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
        self.plan_started.set()
        time.sleep(self.delay)  # an intentionally slow scan, never on GTK
        return SimpleNamespace(
            tool_id=self.tool_id,
            summary="Aucune suppression réelle",
            changes=tuple(
                f"fichier témoin simulé {i:02d} /chemin/long/vers/la/bibliothèque/romans/doublons/exemple.epub"
                for i in range(20)
            ),
            warnings=("Protection des artefacts Kobo",),
            facts={"version": self.version, "read_failures": 0},
        )

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
    send = next(b for b in buttons if getattr(b, '_cifereca_action_id', '') == 'send-local:bob')

    def start():
        assert len(service.storage.placements()) == 1
        capture('08-native-send-before')
        device.click_widget(send)
        until(lambda: status.get_text() == 'Livre envoyé', finished)

    def finished():
        recipients = [p for p in service.storage.placements() if p['identity_id'] == 'bob']
        assert len(recipients) == 1
        received = Path(recipients[0]['path'])
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
        if not send.get_mapped() or not send.get_sensitive():
            return False
        ok, box = send.compute_bounds(host)
        if not ok or box.get_width() <= 0 or box.get_height() <= 0:
            return False
        picked = host.pick(box.get_x() + box.get_width()/2,
                           box.get_y() + box.get_height()/2, Gtk.PickFlags.DEFAULT)
        while picked is not None and picked is not send:
            picked = picked.get_parent()
        if picked is not send:
            return False
        try:
            x, y = device.widget_point(send)
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
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    tool = DummyTool()
    report = {
        "status": "RUNNING", "scenario": "maintenance-tools", "width": args.width,
        "scope": "real GTK MainWindow/XTEST; simulated tool with zero destructive IO",
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
        row = next(w for w in walk(window[0]._tools_box) if isinstance(w, Adw.ActionRow))
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
        tool.release_run.set()
        report.update(
            status="FAIL", error=f"{type(exc).__name__}: {exc}",
            failure_stack=__import__("traceback").format_exc(limit=12),
        )
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
        w._refresh_tools_page()
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
            begin_busy,
        )

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
        def after():
            assert heartbeat[0] > before + 3, "GTK main loop blocked by scanning"
            until(preview_allocated, first_dialog)
        later(after, 260)

    def preview_allocated():
        dialog = modal()
        if dialog is None:
            return False
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
        return line_is("Confirmation attendue") and all(geometry[key] > 0 for key in (
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
        report["checks"]["gtk_heartbeat_during_slow_scan"] = heartbeat[0] > 5
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
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set() and line_is("Analyse…"), cancel_pending_scan)

    def cancel_pending_scan():
        cancel = next(w for w in walk(window[0]._tools_box)
                      if isinstance(w, Gtk.Button) and w.get_label() == "Annuler")
        physical(cancel)
        def discarded():
            assert modal() is None and tool.run_calls == 0
            assert_line("Annulé")
            report["checks"]["cancelled_scan_never_reopens_confirmation"] = True
            until(lambda: preview_button[0].get_sensitive(), begin_stale)
        # Cancellation completes asynchronously after the simulated plan worker
        # has observed its cancellation token. A fixed 850 ms delay races
        # runner scheduling (the fake scan itself takes 650 ms), so assert
        # the eventual GTK state with an explicit upper bound instead.
        # A broken cancellation still fails rather than being silently retried.
        until(lambda: line_is("Annulé"), discarded, timeout=3.0)

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
        window[0]._refresh_tools_page()
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
        window[0]._refresh_tools_page()
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
        tool.plan_started.clear()
        physical(preview_button[0])
        until(lambda: tool.plan_started.is_set() and line_is("Analyse…"), p1_analysis)

    def p1_analysis():
        assert_line("Analyse…", busy=True)
        assert tool.run_calls == p1_runs[0]
        assert modal() is None
        report["checks"][f"P1_{p1_variants[p1_index[0]][2]}_analysis_indeterminate"] = True
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
        from gi.repository import Pango
        from library_inspection_acceptance import exercise
        exercise(parent=window[0], Gtk=Gtk, Adw=Adw, GLib=GLib, Pango=Pango,
                 device=device[0], until=until, walk=walk, report=report,
                 checkpoint=gtk_phase_2027, done=finish_inspection)

    def finish_inspection():
        from gi.repository import Pango
        from library_declaration_acceptance import exercise
        exercise(parent=window[0], Gtk=Gtk, Adw=Adw, GLib=GLib, Pango=Pango,
                 device=device[0], until=until, walk=walk, report=report,
                 checkpoint=gtk_phase_2027, done=finish_declaration)

    def finish_declaration():
        if args.width in (1366, 1600):
            exercise_native_transfer(parent=window[0], Gtk=Gtk, Adw=Adw,
                                     device=device[0], until=until, report=report,
                                     capture=capture, done=finish_transfer)
        else:
            finish_transfer()

    def finish_transfer():
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
                w.plugin_manager = SimpleNamespace(services=lambda name: (
                    [SimpleNamespace(plugin_id="e2e.maintenance", service=tool)]
                    if name == "maintenance.tool" else ()
                ))
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
            core.close()
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
