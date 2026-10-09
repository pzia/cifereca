#!/usr/bin/env python3
"""P1 #758/#841: real Jeunesse page in the actual CIFERECA GTK shell, offline."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from time import monotonic
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "simulation"))
sys.path.insert(0, str(ROOT / "environnements" / "openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from PIL import ImageGrab

from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.plugins import PackageInstaller, PackageStore
from plugin_scenarios import youth_records
from x11_input import X11Input


def install_youth_package() -> None:
    """Install the unmodified source package through the real ZIP installer."""
    source = ROOT / "src/plugins/youth-catalog"
    archive = Path(os.environ["XDG_CACHE_HOME"]) / "youth-catalog-e2e.zip"
    with ZipFile(archive, "w") as bundle:
        for name in ("cifereca-plugin.json", "youth_catalog.py", "youth_page.py"):
            bundle.write(source / name, arcname=name)
    PackageInstaller(PackageStore()).install(archive)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Xvfb/X11 requis pour la recette Jeunesse")

    install_youth_package()
    core = CiferecaCore.open(
        force_plugins=("catalog.youth", "ui.page.youth"),
        activate_plugins=True,
    )
    catalog = core.plugins.service("catalog.youth")
    page_plugin = core.plugins.service("ui.page.youth")
    if catalog is None or page_plugin is None:
        core.close()
        raise RuntimeError("Jeunesse doit être activée dans le vrai PluginManager")

    synthetic = youth_records()
    books = synthetic["records"]
    catalog.ingest("camille", "booktrust", [books[i] for i in (0, 1, 2, 3, 4, 6)])
    catalog.ingest("camille", "milan", [books[5]])
    assert len(catalog.search_cached("camille")) == 6
    assert not catalog.search_cached("alex")

    try:
        sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=4).strip()
    except (OSError, subprocess.SubprocessError):
        sha = "unknown"
    screen_width = int(os.environ["CODEX_HEADLESS_SCREEN"].split("x")[0])
    report = {
        "scenario": "youth-page-p1", "git_sha": sha,
        "screen_width": screen_width, "backend": "GTK4/X11/Xvfb",
        "source": "source package installed through PackageInstaller",
        "fixture": "simulation/plugin_scenarios.youth_records",
        "network": "no youth.source services installed, no remote collection",
        "steps": [],
    }
    started = monotonic()
    window = None
    device = None
    view = None
    last_capture = None
    rc = [1]

    def rows() -> list[str]:
        store = view.master._page.store
        return [store.get_item(i).title for i in range(store.get_n_items())]

    def check(expected: list[str], label: str) -> None:
        actual = rows()
        if sorted(actual) != sorted(expected):
            raise AssertionError(f"{label}: expected={expected!r} actual={actual!r}")
        if window.view_stack.get_visible_child_name() != "youth":
            raise AssertionError(f"{label}: la vraie page Jeunesse n'est pas visible")

    def capture(label: str) -> None:
        nonlocal last_capture
        file = output / f"{label}.png"
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        image.save(file)
        last_capture = file
        report["steps"].append({
            "step": label, "rows": rows(),
            "identity": view.identity_id(), "age": view.age_value(),
            "age_kind": view.filter_state().age_kind,
            "inferred": view.inferred.get_active(),
            "query": view.query.get_text(),
            "window_width": window.get_width(),
            "window_height": window.get_height(),
            "page_mapped": bool(view.widget.get_mapped()),
            "screenshot": file.name,
            "sha256": hashlib.sha256(file.read_bytes()).hexdigest(),
        })

    def write_report(status: str, error: str = "") -> None:
        report["status"] = status
        if error:
            report["error"] = error
        report["duration_seconds"] = round(monotonic() - started, 2)
        if last_capture is not None:
            shutil.copyfile(last_capture, output / "screen.png")
        (output / "screen.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def fail(message: str) -> None:
        write_report("FAIL", message)
        app.quit()

    def after(ms: int, callback) -> None:
        def run():
            try:
                callback()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, run)

    class App(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.YouthPageE2E")

        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(screen_width - 40, 800)
            window.present()
            device = X11Input()
            after(400, enter_page)

        def do_shutdown(self):
            if device is not None:
                device.close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = App()

    def enter_page() -> None:
        nonlocal view
        if "youth" not in window._pages:
            raise AssertionError("Contribution ui.page.youth absente de MainWindow sur main")
        window.navigate("youth")
        view = page_plugin.view
        if view is None:
            raise AssertionError("Le vrai plugin ne construit pas la page Jeunesse")
        after(350, initial)

    def initial() -> None:
        if not view.widget.get_mapped() or view.identity_id() != "camille":
            raise AssertionError("Page ou identité Camille non mappée")
        if window.get_width() > screen_width or window.get_width() < 760:
            raise AssertionError("Géométrie GTK hors écran")
        check([
            "L'Atlas des lucioles", "Le Jardin des mots", "Le Passage sans âge",
            "La Carte supposée", "L'Atlas des lucioles", "Le Dernier Rivage",
        ][:0] + [
            "L'Atlas des lucioles", "Le Jardin des mots", "Le Passage sans âge",
            "La Carte supposée", "L'Atlas des lucioles", "Le Dernier Rivage",
        ], "initial")
        capture("00-camille-cache")
        view.age.set_selected(9)  # 8 ans
        view.age_kind.set_selected(2)  # intérêt
        after(160, age_interest)

    def age_interest() -> None:
        check(["L'Atlas des lucioles"], "8 ans intérêt")
        capture("01-interest-8")
        view.age.set_selected(13)  # 12 ans
        view.age_kind.set_selected(3)  # lecture
        after(160, age_reading)

    def age_reading() -> None:
        check(["L'Atlas des lucioles"], "12 ans lecture")
        capture("02-reading-12")
        view.age_kind.set_selected(0)
        view.age.set_selected(18)  # 17 ans: dernière entrée de l'UI
        after(160, age_seventeen)

    def age_seventeen() -> None:
        check(["Le Dernier Rivage"], "17 ans")
        if view.age.get_model().get_n_items() != 19 or catalog.search_cached("camille", age=18):
            raise AssertionError("18 ans ne doit pas être proposé ou accepté")
        capture("03-boundary-17")
        view.age.set_selected(0)
        view.identity.set_selected(1)
        after(160, isolated_identity)

    def isolated_identity() -> None:
        check([], "identité Alex isolée")
        capture("04-alex-empty")
        view.identity.set_selected(0)
        view.age.set_selected(13)  # 12 ans
        view.inferred.set_active(True)
        after(160, inferred_opt_in)

    def inferred_opt_in() -> None:
        check(["L'Atlas des lucioles", "La Carte supposée"], "inférence opt-in")
        capture("05-inferred-opt-in")
        report["keyboard_click"] = device.click_widget(view.query)
        after(100, type_query)

    def type_query() -> None:
        if not view.query.get_mapped():
            raise AssertionError("Champ requête Jeunesse non mappé")
        device.type_text("Atlas")  # événements clavier XTEST, pas set_text
        after(200, verify_query)

    def verify_query() -> None:
        if view.query.get_text() != "Atlas":
            raise AssertionError("Saisie clavier XTEST perdue")
        check(["L'Atlas des lucioles"], "filtre texte saisi au clavier")
        view.master._page.selection.set_selected(0)
        after(160, detail)

    def detail() -> None:
        if view.selected_book is None:
            raise AssertionError("Sélection réelle GTK de notice impossible")
        age_labels = [age.source_label for age in view.selected_book.age_ranges]
        if "Interest age 8-11" not in age_labels or "Reading age 10-12" not in age_labels:
            raise AssertionError("Preuves source âge d'intérêt / lecture perdues dans le détail")
        capture("06-keyboard-and-detail")
        view.query.set_text("Passage")
        view.age.set_selected(0)
        view.inferred.set_active(False)
        after(160, unknown_age)

    def unknown_age() -> None:
        check(["Le Passage sans âge"], "âge inconnu")
        view.master._page.selection.set_selected(0)
        if view.selected_book is None or view.selected_book.age_ranges:
            raise AssertionError("L'âge inconnu doit rester inconnu")
        capture("07-unknown-age")
        write_report("PASS")
        rc[0] = 0
        app.quit()

    GLib.timeout_add_seconds(35, lambda: (fail("Timeout GTK Jeunesse 35 s"), False)[1] if rc[0] else False)
    app.run([sys.argv[0]])
    if not (output / "screen.json").is_file():
        write_report("FAIL", "Application GTK fermée avant recette")
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
