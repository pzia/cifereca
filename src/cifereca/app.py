from __future__ import annotations

import json
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

        def do_shutdown(self):
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
