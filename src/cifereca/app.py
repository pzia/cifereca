from __future__ import annotations

import sys
import time

from . import __version__
from .logging_utils import configure_logging, set_diagnostics_level
from .startup_revision import startup_git_commit
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


def main() -> int:
    if "--version" in sys.argv[1:]:
        print(f"cifereca {__version__}")
        return 0
    if "--help" in sys.argv[1:] or "-h" in sys.argv[1:]:
        print(
            "Usage: cifereca [--trace] [--no-plugins] [--force-plugin ID]\n"
            "       cifereca [--help | --version]\n\n"
            "  --trace         Activer le niveau Trace dans le Core et l'interface\n"
            "  --debug         Alias de --trace (compatibilité)\n"
            "  --verbose       Alias de --trace (compatibilité)\n"
            "  --no-plugins    Désactiver les plugins au lancement\n"
            "  --force-plugin  Activer le plugin indiqué (répétable)"
        )
        return 0
    verbose = any(arg in {"--trace", "--verbose", "--debug"} for arg in sys.argv[1:])
    no_plugins = "--no-plugins" in sys.argv[1:]
    force_plugins: list[str] = []
    cleaned = [sys.argv[0]]
    index = 1
    while index < len(sys.argv):
        arg = sys.argv[index]
        if arg in {"--trace", "--verbose", "--debug", "--no-plugins"}:
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
    logger.info("TRACE startup begin version=%s git_commit=%s verbose=%s no_plugins=%s forced_plugin_count=%s", __version__, startup_git_commit() or "unavailable", verbose, no_plugins, len(force_plugins))
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gio
        from .gui import MainWindow
    except Exception as exc:
        return _gtk_import_error(exc)

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
    core = CiferecaCore.open(**core_options)
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
                self.window = MainWindow(self, config, service, diagnostics)
                logger.info("TRACE gui MainWindow-init end dt=%.3fs", time.monotonic()-t0)
            self.window.present()
            presented_ms = (time.monotonic() - t0) * 1000.0
            logger.info("TRACE gui window-presented duration_ms=%.1f", presented_ms)
            try:
                self.window.ui_perf.record("startup.window_presented", presented_ms)
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
