"""#6939: native GTK loop remains interactive while real documentary work waits.

Synthetic source, real Core, SQLite, task broker, GLib/GTK and desktop hint.
No provider contact, no timing SLA, no production scheduling change.
"""
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, get_ident
from types import SimpleNamespace

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk

from cifereca import app
from cifereca.core.runtime import CiferecaCore
from cifereca.core import notice_c_job_journal as journal
from cifereca.core.notice_c_exact_recompute import request_exact_public_recompute
from cifereca.core.notice_c_cover_index import (
    persist_exact_public_search_cover, read_consolidated_notice_cover,
)
from cifereca.core.search_orchestration import _public_rows_from_response
from cifereca.core.notice_search_cover_producer import admit_public_search_catalogue
from cifereca.core.notice_cover_repository import persist_notice_cover
from cifereca.models import SearchResult
from cifereca.plugin_api import ProviderSearchResponse, PublicSearchNotice
from cifereca.task_admission import SharedTaskAdmission


def source(db, title, at):
    row = SearchResult(
        id="fixture-row", record_id="fixture-record", docbase="books",
        medium="digital", title=title, author="Auteur témoin", type="book",
        format="EPUB", publisher=None, published_at=None, description=None,
        thumbnail_url=None, friendly_url=None, scenario="search",
        source_provider="fixture", source_credential="__public__",
        source_collection="public_catalog", source_account_id=None,
        owned=False, downloadable=False, download_url=None, metadata={},
    )
    session = SimpleNamespace(provider_id="fixture", credential_id="__public__",
                              credentials={}, _accounts=[])
    response = ProviderSearchResponse([row], public_notices=(PublicSearchNotice(row),))
    projected, = _public_rows_from_response(response, session)
    observation = admit_public_search_catalogue(
        projected, identity_id="fixture", observed_at=at, public_cover_url=None,
    )
    assert observation is not None
    return persist_notice_cover(db, observation, field_path=("search_result", "thumbnail_url"),
                                cover_inventory_complete=True)


def run(root):
    Gtk.init()
    main_thread = get_ident()
    with CiferecaCore.open(
        config_path=root / "config.json", database_path=root / "state.sqlite",
        builtin_ui=False, no_plugins=True, activate_plugins=False, diagnostics_enabled=False,
        notice_work_admission=SharedTaskAdmission(max_inflight=4, reserve_user_slots=1),
    ) as core:
        db = core.storage
        runtime = core.notice_work
        old = persist_exact_public_search_cover(db, source(db, "Ancien", 10))
        source(db, "Nouveau", 11)
        ref = request_exact_public_recompute(db, old.document, now=100, expected_generation=0)
        entered, released, committed = Event(), Event(), Event()
        actual = runtime._pump.tick
        commit_receipts = []

        def paused_tick(*, control=None):
            assert get_ident() != main_thread
            entered.set()
            if not released.wait(12):
                raise RuntimeError("gtk_interaction_not_delivered")
            return actual(control=control)

        runtime._pump.tick = paused_tick

        def observe(document, epoch):
            assert get_ident() != main_thread
            receipt = journal.read_job(db, ref)
            current = read_consolidated_notice_cover(db, document)
            commit_receipts.append((document, epoch, receipt.state, current.epoch))
            committed.set()

        host = SimpleNamespace(_closed=False, document_covers=SimpleNamespace(
            refresh_committed_source_in_worker=observe))
        app._bind_desktop_notice_work(core, host)
        window = Gtk.Window()
        button = Gtk.Button(label="Action témoin")
        window.set_child(button)
        window.present()
        loop = GLib.MainLoop()
        failures, scans, actions = [], [], []

        def clicked(_button):
            assert get_ident() == main_thread
            # An independent GTK signal is delivered before the blocked
            # documentary scan can start touching its journal.
            assert entered.is_set() and not released.is_set() and not committed.is_set()
            actions.append("gtk")
            released.set()

        button.connect("clicked", clicked)

        def start():
            try:
                handle = host._notice_work_tick()
                assert handle is not None
                scans.append(handle)
            except Exception as exc:
                failures.append(exc)
                loop.quit()
            return False

        def advance():
            try:
                if entered.is_set() and not actions:
                    assert not committed.is_set()
                    button.emit("clicked")
                    assert actions == ["gtk"] and released.is_set()
                if committed.is_set():
                    loop.quit()
                    return False
                if scans and scans[0].done():
                    # Propagate a completed worker failure without waiting
                    # on the GTK thread or hiding it until the watchdog.
                    scans[0].result()
                return True
            except Exception as exc:
                failures.append(exc)
                loop.quit()
                return False

        def timeout():
            failures.append(RuntimeError("gtk_watchdog"))
            loop.quit()
            return False

        start_id = GLib.idle_add(start)
        advance_id = GLib.timeout_add(10, advance)
        timeout_id = GLib.timeout_add_seconds(18, timeout)
        try:
            loop.run()
            assert not failures, "native_gtk_heartbeat_failed"
            assert actions == ["gtk"] and committed.is_set()
            scan, = scans
            worker, = scan.result(timeout=10).handles
            assert worker.result(timeout=10) == "current"
            assert commit_receipts == [(old.document, old.epoch + 1, "current", old.epoch + 1)]
            assert read_consolidated_notice_cover(db, old.document).sources[0].public_bibliography.title == "Nouveau"
            assert db.entitlements() == [] and db.acquisitions() == [] and db.placements() == []
            host._closed = True
            assert host._notice_work_tick() is None
        finally:
            released.set()
            host._closed = True
            context = GLib.MainContext.default()
            for tag in (start_id, advance_id, timeout_id):
                pending_source = context.find_source_by_id(tag)
                if pending_source is not None:
                    pending_source.destroy()
            window.destroy()
            # On failure too, release and finish any scan before Core closes.
            for handle in scans:
                try:
                    tick = handle.result(timeout=10)
                    for worker in tick.handles:
                        worker.result(timeout=10)
                except Exception:
                    pass


if __name__ == "__main__":
    with TemporaryDirectory(prefix="notice-gtk-receipt-") as scratch:
        run(Path(scratch))
    print("NOTICE_RUNTIME_NATIVE_GTK:PASS", flush=True)
