#!/usr/bin/env python3
"""Recette UX hors CI : vraie fenêtre GTK4 Paramètres → Intelligence artificielle.

App isolée, seul serveur HTTP loopback éphémère, texte synthétique, aucun compte
ni document. Exécution volontaire dans le kit rootless Debian 13, jamais en CI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Thread
from time import monotonic
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow


def walk(widget):
    yield widget
    child = widget.get_first_child()
    while child is not None:
        yield from walk(child)
        child = child.get_next_sibling()


def entry(root, label):
    rows = [w for w in walk(root) if isinstance(w, Adw.EntryRow)
            and w.get_title() == label]
    assert len(rows) == 1, (label, len(rows))
    return rows[0]


def switch(root, title):
    rows = [w for w in walk(root) if isinstance(w, Adw.ActionRow)
            and w.get_title() == title]
    assert len(rows) == 1, (title, len(rows))
    switches = [w for w in walk(rows[0]) if isinstance(w, Gtk.Switch)]
    assert len(switches) == 1, (title, len(switches))
    return switches[0]


def button(root, label):
    buttons = [w for w in walk(root) if isinstance(w, Gtk.Button)
               and w.get_label() == label]
    assert len(buttons) == 1, (label, len(buttons))
    return buttons[0]


class ProbeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ChatHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            count = int(self.headers.get("Content-Length", "-1"))
            assert self.path == "/v1/chat/completions" and 0 < count <= 4096
            payload = json.loads(self.rfile.read(count))
            assert payload["model"] == "modele-test"
            assert len(payload["messages"]) == 1
            prompt = payload["messages"][0]["content"]
            assert prompt == "Test de connexion technique. Réponds uniquement OK."
            assert "Authorization" not in self.headers
            self.server.calls.append("synthetic_request")  # no user payload stored
            output = b'{"choices":[{"message":{"content":"OK"}}]}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
        except Exception:
            self.server.failures += 1
            self.send_error(400)

    def log_message(self, *_args):
        return  # never log requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, default=1366, choices=(1366,))
    args = parser.parse_args()
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Lancer via ./headless sur Xvfb/X11")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    started = monotonic()
    report = {"status": "RUNNING", "scenario": "llm-settings-gtk-1366",
              "display": "GTK4/X11/XTEST", "checks": {}, "captures": []}
    temp = tempfile.TemporaryDirectory(prefix="cifereca-ux-llm-")
    root = Path(temp.name)
    for key, name in (("HOME", "home"), ("XDG_CONFIG_HOME", "config"),
                      ("XDG_DATA_HOME", "data"), ("XDG_CACHE_HOME", "cache"),
                      ("XDG_STATE_HOME", "state")):
        destination = root / name
        destination.mkdir()
        os.environ[key] = str(destination)

    server = ProbeServer(("127.0.0.1", 0), ChatHandler)
    server.calls = []
    server.failures = 0
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    app = Adw.Application(application_id="org.cifereca.LLMSettingsUX")
    window = [None]
    device = [None]
    rc = [1]
    page = [None]
    enabled = [None]
    allow_remote = [None]
    endpoint = [None]
    model = [None]
    status = [None]

    def save_report():
        report["seconds"] = round(monotonic() - started, 2)
        report["synthetic_calls"] = len(server.calls)
        report["server_failures"] = server.failures
        (out / "report.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    def capture(stage):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        assert image.width == args.width
        assert image.getbbox() is not None
        name = stage + ".png"
        image.save(out / name)
        report["captures"].append({
            "file": name, "sha256": hashlib.sha256((out / name).read_bytes()).hexdigest()
        })

    def finish(error=None):
        if error is None:
            report["status"] = "PASS"
            rc[0] = 0
        else:
            report["status"] = "FAIL"
            report["error"] = str(error)[:250]
            try:
                capture("failure")
            except Exception:
                pass
        save_report()
        app.quit()

    def later(ms, operation):
        def run():
            try:
                operation()
            except Exception as exc:
                finish(type(exc).__name__ + ": " + str(exc))
            return False
        GLib.timeout_add(ms, run)

    def wait_until(predicate, next_step, elapsed=0):
        if predicate():
            next_step()
        elif elapsed >= 10000:
            raise AssertionError("Résultat GTK attendu non atteint (10 s)")
        else:
            later(100, lambda: wait_until(predicate, next_step, elapsed + 100))

    def all_status():
        return [w.get_text() for w in walk(page[0]) if isinstance(w, Gtk.Label)]

    def initial():
        w = window[0]
        w.navigate("settings")
        tabs = w._settings_tabs
        tabs.set_visible_child_name("ai")
        page[0] = tabs.get_visible_child()
        assert page[0].get_mapped()
        enabled[0] = switch(page[0], "Activer les fonctions d’intelligence artificielle")
        allow_remote[0] = switch(page[0], "Autoriser les requêtes vers un serveur distant")
        endpoint[0] = entry(page[0], "Endpoint complet (…/v1/chat/completions)")
        model[0] = entry(page[0], "Modèle installé sur le serveur")
        assert not enabled[0].get_active()
        assert not allow_remote[0].get_active()
        assert len(server.calls) == 0
        assert button(page[0], "Tester la connexion").get_sensitive()
        report["checks"]["disabled_offline_initially"] = True
        capture("00-disabled")
        configure_local()

    def configure_local():
        # Exercise real XTEST keyboard focus, not only source inspection.
        enabled[0].set_active(True)
        endpoint[0].set_text(
            f"http://127.0.0.1:{server.server_port}/v1/chat/completions"
        )
        model[0].grab_focus()
        device[0].type_text("modele-test")
        later(350, verify_local)

    def verify_local():
        assert model[0].get_text() == "modele-test", "Saisie XTEST dans Adw.EntryRow"
        assert len(server.calls) == 0, "Appel HTTP implicite lors de la saisie"
        report["checks"]["keyboard_and_no_implicit_network"] = True
        capture("01-local-configured")
        button(page[0], "Tester la connexion").emit("clicked")
        wait_until(lambda: any("Test réussi" in x for x in all_status()), after_probe)

    def after_probe():
        assert server.calls == ["synthetic_request"]
        assert server.failures == 0
        report["checks"]["explicit_synthetic_loopback_probe"] = True
        capture("02-tested")
        save_buttons = [
            w for w in walk(window[0]) if isinstance(w, Gtk.Button)
            and w.get_label() == "Enregistrer" and w.get_sensitive()
        ]
        assert save_buttons, "Bouton Enregistrer sensible absent"
        save_buttons[0].emit("clicked")
        wait_until(
            lambda: core.config.get("llm.enabled") is True
                    and core.config.path.exists(),
            after_save,
        )

    def after_save():
        saved = json.loads(core.config.path.read_text(encoding="utf-8"))
        assert saved["llm"]["enabled"] is True
        assert saved["llm"]["model"] == "modele-test"
        assert saved["llm"]["allow_remote"] is False
        assert saved["llm"]["api_key_env"] == ""
        report["checks"]["atomic_save_and_reloaded_config"] = True
        capture("03-saved")
        # Changing to a remote destination must revoke global consent.
        allow_remote[0].set_active(True)
        endpoint[0].set_text("https://exemple.invalid/v1/chat/completions")
        assert not allow_remote[0].get_active()
        assert server.calls == ["synthetic_request"]
        report["checks"]["endpoint_change_revokes_remote_consent"] = True
        capture("04-remote-revoked")
        button(page[0], "Annuler les modifications").emit("clicked")
        later(200, check_reset)

    def check_reset():
        assert enabled[0].get_active()
        assert endpoint[0].get_text().startswith("http://127.0.0.1:")
        assert model[0].get_text() == "modele-test"
        assert not allow_remote[0].get_active()
        assert len(server.calls) == 1
        report["checks"]["cancel_restores_last_saved_values"] = True
        capture("05-reset")
        finish()

    def activate(_app):
        window[0] = MainWindow(app, core.config, core.library, core.diagnostics)
        window[0].set_default_size(1320, 830)
        window[0].present()
        device[0] = X11Input()
        later(700, initial)

    app.connect("activate", activate)
    GLib.timeout_add_seconds(
        40, lambda: (finish("Timeout global 40 s"), False)[1] if rc[0] else False
    )
    try:
        app.run([sys.argv[0]])
    finally:
        if device[0]:
            device[0].close()
        core.close()
        server.shutdown()
        server.server_close()
        temp.cleanup()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
