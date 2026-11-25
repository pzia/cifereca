from __future__ import annotations

import json
import logging
import sys
import time

from . import __version__
from .logging_utils import configure_logging, set_diagnostics_level
from .startup_revision import build_identity
from .core import CiferecaCore


def _gtk_import_error(exc: BaseException) -> int:
    message = """
cifereca nécessite GTK 4, libadwaita et PyGObject.

Erreur: dépendances graphiques indisponibles

Sous Debian/Ubuntu :
  sudo apt install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1

Puis relancez :
  cifereca
""".strip()
    print(message, file=sys.stderr)
    return 3


def _bind_desktop_notice_work(core, window):
    """Attach the existing Core pump to the ordinary desktop lifecycle.

    No executor is created here; GTK only submits a bounded hint.
    The observer refreshes N2 on the worker; the existing GTK heartbeat
    consumes a coalesced documentary revision to invalidate local views.
    """
    runtime = getattr(core, "notice_work", None)
    if runtime is None:
        return
    if not runtime.bind_committed_observer(
        window.document_covers.refresh_committed_source_in_worker,
    ):
        return

    def hint():
        if getattr(window, "_closed", False):
            return None
        try:
            return runtime.request_tick()
        except Exception:
            logging.getLogger("cifereca").warning(
                "TRACE notice work lifecycle code=dispatch_unavailable"
            )
            return None

    window._notice_work_tick = hint
    replace_documents = getattr(runtime, "replace_priority_documents", None)
    if callable(replace_documents):
        def refresh_priority_context():
            # GTK owns selection and snapshot lifetimes; no Storage access
            # or job creation on this thread. Clear context on each pulse.
            from .core.notice_c_cover_index import is_canonical_cover_document
            documents = []
            for name in ("search_page", "library_page", "recent_page"):
                page = getattr(window, name, None)
                if page is None or not page.get_mapped():
                    continue
                row = page.selection.get_selected_item()
                document = getattr(row, "_cover_document_ref", None)
                if is_canonical_cover_document(document):
                    documents.append((document, "interactive_visible"))
            recent = getattr(window, "recent_page", None)
            generation = getattr(window, "_identity_context_generation", None)
            if (recent is not None and generation is not None
                    and getattr(window, "_recent_loaded_generation", None) == generation):
                # Only current recent rows with exact Core public C links.
                for index in range(min(32, recent.store.get_n_items())):
                    row = recent.store.get_item(index)
                    link = getattr(row, "_cover_public_c_link", None)
                    document = getattr(row, "_cover_document_ref", None)
                    if (is_canonical_cover_document(document)
                            and getattr(link, "document", None) == document):
                        documents.append((document, "recent_acquisition"))
            replace_documents(tuple(documents))
        window._notice_work_refresh_priorities = refresh_priority_context
    # Do not call GTK from the worker or schedule one idle callback per
    # catalogue commit. The already installed desktop heartbeat consumes
    # only the latest watermark, covering both A/C receipts and C aggregates.
    revision = getattr(runtime, "documentary_revision", None)
    if callable(revision):
        seen = [revision()]

        def refresh_documentary_views():
            current = revision()
            if (getattr(window, "_closed", False) or current is None
                    or current == seen[0]):
                return
            # Existing identity/generation fences and coalescing remain the
            # only owner of the two SQLite-only snapshot refreshes.
            from .gui.library_refresh import invalidate_library
            invalidate_library(window)
            seen[0] = current

        window._notice_work_refresh_documents = refresh_documentary_views


def _start_notice_work_wakeup(window, *, timeout_add_seconds, source_remove):
    """One desktop wake source, not another work scheduler or executor.

    Five seconds bounds the gap between idle hints (subject to admission
    and the main loop). The existing Core pump owns all reads and work.
    """
    hint = getattr(window, "_notice_work_tick", None)
    if not callable(hint) or getattr(window, "_closed", False):
        return
    if callable(getattr(window, "_notice_work_wakeup_stop", None)):
        return
    source = [None]

    def pulse():
        if source[0] is None or getattr(window, "_closed", False):
            source[0] = None
            return False
        priorities = getattr(window, "_notice_work_refresh_priorities", None)
        if callable(priorities):
            try:
                priorities()
            except Exception:
                logging.getLogger("cifereca").warning(
                    "TRACE notice work lifecycle code=priority_unavailable"
                )
        hint()
        refresh = getattr(window, "_notice_work_refresh_documents", None)
        if callable(refresh):
            try:
                refresh()
            except Exception:
                logging.getLogger("cifereca").warning(
                    "TRACE notice work lifecycle code=refresh_unavailable"
                )
        return True

    def stop():
        handle, source[0] = source[0], None
        if handle is not None:
            source_remove(handle)

    source[0] = timeout_add_seconds(5, pulse)
    window._notice_work_wakeup_stop = stop


def _stop_notice_work_wakeup(window):
    stop = getattr(window, "_notice_work_wakeup_stop", None)
    if callable(stop):
        try:
            stop()
        except Exception:
            pass


def _run_application(*, capture_mode: bool = False) -> int:
    if "--version" in sys.argv[1:]:
        print(f"cifereca {__version__}")
        return 0
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "Usage: cifereca [--perf] [--trace] [--capture [--capture-dir DIR]] [--no-plugins] [--force-plugin ID]\n"
            "       cifereca [--help | --version]\n\n"
            "  --perf          Capture détaillée des performances dès le démarrage (session uniquement)\n"
            "  --trace         Activer le niveau Trace dans le Core et l'interface\n"
            "  --debug         Alias de --trace (compatibilité)\n"
            "  --verbose       Alias de --trace (compatibilité)\n"
            "  --capture       Journal et HTTP/HTML de cette session (ZIP privé)\n"
            "  --capture-dir   Dossier du ZIP (défaut : Téléchargements XDG)\n"
            "  --no-plugins    Désactiver les plugins au lancement\n"
            "  --force-plugin  Activer le plugin indiqué (répétable)"
        )
        return 0
    perf_requested = "--perf" in sys.argv[1:]
    verbose = capture_mode or any(arg in {"--trace", "--verbose", "--debug"} for arg in sys.argv[1:])
    no_plugins = "--no-plugins" in sys.argv[1:]
    force_plugins: list[str] = []
    cleaned = [sys.argv[0]]
    index = 1
    while index < len(sys.argv):
        arg = sys.argv[index]
        if arg in {"--trace", "--verbose", "--debug", "--no-plugins", "--perf"}:
            index += 1
            continue
        if arg == "--force-plugin":
            if index + 1 >= len(sys.argv):
                print("--force-plugin requiert un identifiant de plugin", file=sys.stderr)
                return 2
            force_plugins.append(sys.argv[index + 1])
            index += 2
            continue
        if arg.startswith("--force-plugin="):
            force_plugins.append(arg.split("=", 1)[1])
            index += 1
            continue
        cleaned.append(arg)
        index += 1
    sys.argv = cleaned
    logger = configure_logging(verbose=verbose)
    t0 = time.monotonic()
    # The same monitor used by Settings starts before GTK imports and Core
    # opening; no persistent setting, extra profiler or network trace.
    performance_monitor = None
    if perf_requested:
        try:
            from .gui.ui_perf import UiPerfMonitor
            performance_monitor = UiPerfMonitor()
            performance_monitor.enable_detailed_capture(True)
        except Exception:
            print("performance_capture_unavailable", file=sys.stderr)
            return 2
    identity = build_identity()
    # One structured startup event, before GTK import or any provider connection.
    # Its SHA comes from deployed artifact metadata, never mutable HEAD.
    logger.info("DIAG_JSON %s", json.dumps({
        "schema_version": 1, "category": "CORE", "operation": "build_identity",
        "status": "ok" if identity["status"] == "known" else "warning",
        "message": "build_identity", "detail": identity,
    }, ensure_ascii=False, sort_keys=True))
    logger.info("TRACE startup begin version=%s git_commit=%s verbose=%s no_plugins=%s forced_plugin_count=%s",
                __version__, identity["commit_sha"] or "unavailable",
                verbose, no_plugins, len(force_plugins))
    gtk_import_started = time.perf_counter()
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
        from .gui import MainWindow
    except Exception as exc:
        return _gtk_import_error(exc)

    if performance_monitor is not None:
        performance_monitor.record_phase(
            "startup", "gtk_import",
            (time.perf_counter() - gtk_import_started) * 1000.0,
            trigger="automatic", outcome="success",
        )
    logger.info("TRACE startup gtk-import-ok dt=%.3fs", time.monotonic()-t0)
    logger.info("TRACE startup core-open begin")
    core_options = dict(
        no_plugins=no_plugins,
        force_plugins=tuple(force_plugins),
        activate_plugins=False,
    )
    # One budget across the pre-existing Core broker and GTK task runner.
    # No remote fetch port or replay registry is installed automatically.
    # Diagnostic capture and explicit no-plugin startup keep legacy behavior.
    if not no_plugins and not capture_mode:
        from .task_admission import SharedTaskAdmission
        core_options["notice_work_admission"] = SharedTaskAdmission(
            max_inflight=16, reserve_user_slots=3,
        )
    # Apply an explicit CLI override before constructing services/plugins.
    # Do not persist it: only an IHM preference change saves the next launch.
    if verbose:
        core_options["diagnostics_level"] = "trace"
    core_open_started = time.perf_counter()
    core = CiferecaCore.open(**core_options)
    if performance_monitor is not None:
        performance_monitor.record_phase(
            "startup", "core_open",
            (time.perf_counter() - core_open_started) * 1000.0,
            trigger="automatic", outcome="success",
        )
    config, service, diagnostics = core.config, core.library, core.diagnostics
    set_diagnostics_level(diagnostics.level)
    logger.info(
        "TRACE startup core-open end schema=%s dt=%.3fs",
        core.storage.schema_version(), time.monotonic()-t0,
    )

    class CiferecaApplication(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.Cifereca")
            self.window = None
            self._notice_work_admission = core_options.get("notice_work_admission")

        def do_activate(self):
            if self.window is None:
                logger.info("TRACE gui MainWindow-init begin")
                # Hand off the already-active monitor: MainWindow and Settings
                # must not allocate a replacement or lose early samples.
                monitor_kw = (
                    {"ui_perf": performance_monitor}
                    if performance_monitor is not None else {}
                )
                self.window = MainWindow(
                    self, config, service, diagnostics, **monitor_kw
                )
                _bind_desktop_notice_work(core, self.window)
                logger.info("TRACE gui MainWindow-init end dt=%.3fs", time.monotonic()-t0)
            self.window.present()
            presented_ms = (time.monotonic() - t0) * 1000.0
            logger.info("TRACE gui window-presented duration_ms=%.1f", presented_ms)
            try:
                self.window.ui_perf.record("startup.window_presented", presented_ms)
                if self.window.ui_perf.detailed_capture_enabled:
                    self.window.ui_perf.record_phase(
                        "startup", "window_presented", presented_ms,
                        trigger="automatic", outcome="success",
                    )
            except Exception:
                pass
            if not getattr(self, "_startup_plugins_scheduled", False):
                self._startup_plugins_scheduled = True
                from gi.repository import GLib
                GLib.idle_add(self.window.activate_startup_plugins)
                if callable(getattr(self.window, "_notice_work_tick", None)):
                    _start_notice_work_wakeup(
                        self.window, timeout_add_seconds=GLib.timeout_add_seconds,
                        source_remove=GLib.source_remove,
                    )

        def do_shutdown(self):
            _stop_notice_work_wakeup(self.window)
            try:
                core.close()
            except Exception:
                pass
            # Adw.Application -> Gtk.Application -> Gio.Application.  The
            # virtual method is declared on Gio.Application; calling super()
            # here is broken on some PyGObject versions and resolves to the
            # shutdown signal wrapper. Chain the virtual explicitly.
            Gio.Application.do_shutdown(self)

    logger.info("TRACE startup application-created dt=%.3fs", time.monotonic()-t0)
    app = CiferecaApplication()
    try:
        logger.info("TRACE startup app.run begin dt=%.3fs", time.monotonic()-t0)
        rc = int(app.run(sys.argv))
        logger.info("TRACE shutdown app.run end rc=%s dt=%.3fs", rc, time.monotonic()-t0)
        return rc
    except Exception:
        # GTK/plugin tracebacks may include signed loan URLs, local paths or
        # credentials. Never forward an untrusted exception to application logs.
        logger.error("Fatal GTK GUI error code=gtk_runtime_failed")
        return 1
    finally:
        _stop_notice_work_wakeup(app.window)


def main() -> int:
    """GUI entry: --capture is a session-only opt-in to a private local ZIP."""
    arguments = sys.argv[1:]
    if "--help" in arguments or "-h" in arguments or "--version" in arguments:
        return _run_application()

    capture = "--capture" in arguments
    directory_arg: str | None = None
    cleaned = [sys.argv[0]]
    idx = 0
    while idx < len(arguments):
        arg = arguments[idx]
        if arg == "--capture":
            idx += 1
            continue
        if arg == "--capture-dir":
            if idx + 1 == len(arguments) or not arguments[idx + 1].strip():
                print("capture_directory_required", file=sys.stderr)
                return 2
            if directory_arg is not None:
                print("capture_directory_duplicate", file=sys.stderr)
                return 2
            directory_arg = arguments[idx + 1]
            idx += 2
            continue
        if arg.startswith("--capture-dir="):
            if directory_arg is not None or not arg.split("=", 1)[1]:
                print("capture_directory_invalid", file=sys.stderr)
                return 2
            directory_arg = arg.split("=", 1)[1]
            idx += 1
            continue
        cleaned.append(arg)
        idx += 1
    if directory_arg is not None and not capture:
        print("capture_directory_requires_capture", file=sys.stderr)
        return 2
    if not capture:
        return _run_application()

    from pathlib import Path
    from .core.capture_session_export import (
        preflight_capture_destination, export_capture_session,
    )
    from .core.capture_session_journal import CaptureSessionJournal
    from .core.capture_http_window import capture_http_window
    from .http_trace import HTTP_TRACE
    from .install_paths import downloads_dir
    from .logging_utils import diagnostics_level, trace_disable_generation, set_diagnostics_level
    import logging

    directory = Path(directory_arg).expanduser() if directory_arg is not None else downloads_dir()
    try:
        preflight_capture_destination(directory)
    except (OSError, ValueError):
        print(f"capture_directory_unavailable: {directory}", file=sys.stderr)
        return 2

    # Force Trace only for this process. Keep the persistent user preference.
    # Raw console mirroring in a terminal would print sensitive cookies/URLs;
    # mute that optional mirror only for this opt-in capture session.
    previous_level = diagnostics_level()
    previous_argv = sys.argv
    previous_logger_level = logging.getLogger("cifereca").level
    preexisting_console = {
        handler for handler in logging.getLogger("cifereca").handlers
        if getattr(handler, "_cifereca_trace_console", False)
    }
    logger = configure_logging(verbose=True)
    # Snapshot only AFTER enabling Trace for this session. The marker also
    # detects OFF -> ON transitions when Trace happens to be active at exit.
    trace_disable_at_start = trace_disable_generation()

    class _NoRawConsole:
        def filter(self, _record):
            return False

    mute = _NoRawConsole()
    mirrors = [
        handler for handler in logger.handlers
        if getattr(handler, "_cifereca_trace_console", False)
    ]
    for handler in mirrors:
        handler.addFilter(mute)
    # HTTP_TRACE is process-global and can contain a previous --capture run
    # (notably when a GUI is embedded/restarted in the same interpreter).
    # Its append-only cursor isolates THIS run without mutating Diagnostics.
    trace_start = HTTP_TRACE.status()
    journal = CaptureSessionJournal()
    journal.start(logger)
    sys.argv = cleaned
    print(
        "ATTENTION : --capture conserve des données BRUTES privées "
        "(cookies, tokens, URLs, HTML, journaux). Le dossier Téléchargements "
        "peut être synchronisé par un service externe. Aucun envoi automatique.",
        file=sys.stderr,
    )
    rc = 1
    export_failed = False
    try:
        try:
            rc = _run_application(capture_mode=True)
        except KeyboardInterrupt:
            logger.warning("TRACE capture interrupted by operator")
            rc = 130
        except Exception:
            # Core startup may fail before the GUI's guarded app.run() block.
            # --capture is an explicit opt-in to a PRIVATE, RAW diagnostic
            # archive: preserve the original exception and traceback there.
            # The raw Trace console mirror remains muted for this session;
            # stdout/stderr must receive only the closed failure code/path.
            logger.exception(
                "Fatal GUI capture session error code=capture_runtime_failed"
            )
            rc = 1
    finally:
        try:
            journal.finish()
            published = export_capture_session(
                directory, journal=journal.snapshot(),
                http_snapshot=capture_http_window(
                    HTTP_TRACE.snapshot(), trace_start,
                ), app_exit_code=rc,
                build_identity=build_identity(),
                trace_active_at_finish=(diagnostics_level() == "trace"),
                trace_uninterrupted=(
                    trace_disable_generation() == trace_disable_at_start
                ),
            )
            print(f"capture_zip: {published}", file=sys.stderr)
        except Exception:
            export_failed = True
            print(f"capture_export_failed: {directory}", file=sys.stderr)
        finally:
            for handler in mirrors:
                handler.removeFilter(mute)
                if handler not in preexisting_console:
                    # This raw mirror belongs to --capture only, never to
                    # another invocation in the same interpreter.
                    logger.removeHandler(handler)
                    handler.close()
            # Restore process state after collecting the Trace coverage
            # snapshot. Existing settings remain untouched on disk.
            set_diagnostics_level(previous_level)
            # configure_logging(verbose=True) also sets the Python logger
            # threshold to DEBUG. Restore it for embedded/reused processes.
            logger.setLevel(previous_logger_level)
            sys.argv = previous_argv
    return 2 if export_failed and rc == 0 else rc
