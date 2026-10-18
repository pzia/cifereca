#!/usr/bin/env python3
"""P1 #758: real Jeunesse package/page on the GTK4 window, driven under Xvfb.

The actual youth-catalog package is installed into the isolated Core plugin
store. Only the catalog's local cache is seeded; no provider is simulated as
available and no remote collection is triggered by opening or filtering.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
from pathlib import Path
import subprocess
import shutil
import sys
import tempfile
import zipfile
from time import monotonic

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements" / "openai-chat-debian"))
sys.path.insert(0, str(ROOT / "simulation"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.gui.query_model import QueryState
import cifereca.gui.main_window as gui_main


def install_real_youth(core: CiferecaCore, work: Path, *, with_milan: bool = False) -> None:
    packages = [
        ("youth-catalog", ("cifereca-plugin.json", "youth_catalog.py", "youth_page.py")),
    ]
    if with_milan:
        packages.append(("youth-source-milan", ("cifereca-plugin.json", "milan_youth.py")))
        packages.append(("youth-source-ecole-loisirs", ("cifereca-plugin.json", "ecole_loisirs_youth.py")))
    for name, files in packages:
        source = ROOT / "src/plugins" / name
        archive = work / f"{name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for filename in files:
                bundle.write(source / filename, filename)
        core.plugins.install(archive)
    statuses = {status.descriptor.id: status for status in core.plugins.statuses()}
    plugins = ("catalog.youth", "ui.page.youth")
    if with_milan:
        plugins += ("youth.source.milan", "youth.source.ecole-loisirs")
    for plugin_id in plugins:
        assert statuses[plugin_id].state in {"ready", "degraded"}, (
            f"{plugin_id} not activated: {statuses[plugin_id]}"
        )


def records():
    def book(key, title, age=None, *, kind="recommended", hi=None, isbn=""):
        return {
            "record_id": key, "source_item_id": key, "title": title,
            "authors": ["Autrice UX"], "isbn": isbn,
            "language": "fr", "formats": ["epub"], "book_type": "fiction",
            "age_ranges": ([] if age is None else [{
                "age_min": age, "age_max": hi, "age_kind": kind,
                "source_label": f"{age}–{hi} ans" if hi is not None else f"{age}+",
                "source_id": "fixture-youth", "confidence": 0.8,
            }]),
        }
    return [
        book("zero", "Petit nuage", 0, hi=6),
        book("interest-eight", "Forest aventure", 8, hi=12, kind="interest", isbn="9780306406157"),
        book("reading-ten", "Lecture autonome", 10, hi=12, kind="reading"),
        book("seventeen", "Grand roman", 17),
        book("inferred", "Estimation prudente", 8, hi=12, kind="inferred"),
        book("unknown", "Age inconnu"),
    ], [book("alex-only", "Livre prive Alex", 8, hi=12)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--width", required=True, type=int, choices=(1366, 1600))
    parser.add_argument("--library-search", action="store_true", help="P0 #906: vraie navigation Jeunesse -> Recherche locale -> Retour")
    parser.add_argument("--focus-return", action="store_true", help="UX P2 #962: arrêt et assertion stricts après Retour")
    provider = parser.add_mutually_exclusive_group()
    provider.add_argument("--milan", action="store_true", help="Use installed Milan with offline HTML fixtures")
    provider.add_argument("--editions", action="store_true", help="Offline same-title editions and conflicting provenance on real GTK")
    provider.add_argument("--filters-ux", action="store_true", help="UX #922: strict GTK audit of visible labels and tooltips")
    args = parser.parse_args()
    if args.focus_return and (not args.library_search or args.milan or args.editions or args.filters_ux):
        parser.error("--focus-return exige --library-search seul")
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if os.environ.get("GDK_BACKEND") != "x11" or not os.environ.get("DISPLAY"):
        raise SystemExit("Real GTK4/X11 display required")
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    result = {
        "scenario": "youth-gtk", "git_sha": sha,
        "backend": "GTK4/X11/Xvfb", "screen_width": args.width,
        "real_package": "src/plugins/youth-catalog",
        "milan_source_installed": args.milan,
        "conflicting_editions": args.editions,
        "steps": [], "status": "RUNNING",
    }
    start = monotonic()
    rc = [1]
    window = None
    device = None
    core = CiferecaCore.open(
        no_plugins=False,
        force_plugins=(
            ("catalog.youth", "ui.page.youth", "youth.source.milan",
             "youth.source.ecole-loisirs")
            if args.milan else ("catalog.youth", "ui.page.youth")
        ),
        activate_plugins=False,
    )
    work = tempfile.TemporaryDirectory(prefix="youth-gtk-package-")
    install_real_youth(core, Path(work.name), with_milan=args.milan)
    catalog = core.plugins.service("catalog.youth")
    page = core.plugins.service("ui.page.youth")
    assert catalog is not None and page is not None

    requests = []
    library_search_calls = []
    original_library_search = [None]
    original_online_search = [None]
    online_attempts = []
    fault = {"unavailable": False}
    ecole_calls = []
    ecole_outage = [False]
    notice_sink_path = out / "local-https-handler.jsonl"
    if args.milan:
        # Exercise the real Gtk.LinkButton -> Gio HTTPS handler dispatch while
        # making an external browser/network request impossible. The runner
        # supplies a fresh XDG_CONFIG_HOME/XDG_DATA_HOME for every scenario.
        assert os.environ.get("XDG_DATA_HOME") and os.environ.get("XDG_CONFIG_HOME"), (
            "Refuse to install fake handler outside isolated XDG fixture"
        )
        sink_script = out / "local-https-handler.py"
        sink_script.write_text(
            "#!/usr/bin/python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            f"target = Path({str(notice_sink_path)!r})\n"
            "with target.open('a', encoding='utf-8') as stream:\n"
            "    stream.write(json.dumps({'argv': sys.argv[1:]}) + '\\n')\n",
            encoding="utf-8",
        )
        sink_script.chmod(0o700)
        handler_name = "Cifereca E2E Offline HTTPS Notice Handler"
        handler = Gio.AppInfo.create_from_commandline(
            shlex.quote(str(sink_script)),
            handler_name,
            Gio.AppInfoCreateFlags.SUPPORTS_URIS,
        )
        assert handler is not None, "Could not create offline URI sink"
        assert handler.set_as_default_for_type("x-scheme-handler/https"), (
            "HTTPS scheme not safely mapped to offline URI sink"
        )
        default_https = Gio.AppInfo.get_default_for_uri_scheme("https")
        assert default_https is not None and default_https.get_name() == handler_name, (
            f"Refuse to click external HTTPS link without isolated safe handler: {default_https!r}"
        )
        result["notice_launch_handler"] = handler_name
    if args.milan:
        import httpx
        from milan.pseudo_milan_service import listing, detail

        milan = core.plugins.service("youth.source.milan")
        assert milan is not None, "Installed Milan provider unavailable"

        def milan_respond(request):
            assert request.url.host == "www.editionsmilan.com", request.url
            assert request.url.scheme == "https", request.url
            requests.append(str(request.url))
            if fault["unavailable"]:
                return httpx.Response(503, text="Simulated Milan outage", request=request)
            uri = request.url.path
            if uri == "/livres/":
                payload = listing(1)
            elif uri == "/livres/page/2/":
                payload = listing(2)
            elif uri.startswith("/livres/"):
                payload = detail(uri.rstrip("/").split("/")[-1])
            else:
                raise AssertionError(f"Unexpected Milan URL: {uri}")
            assert payload is not None, f"No fixture for {uri}"
            return httpx.Response(
                200, content=payload,
                headers={"content-type": "text/html; charset=utf-8"},
                request=request,
            )

        milan.client.close()
        milan.client = httpx.Client(
            transport=httpx.MockTransport(milan_respond),
            trust_env=False, follow_redirects=False,
        )

        ecole = core.plugins.service("youth.source.ecole-loisirs")
        assert ecole is not None, "Real École des loisirs provider unavailable"
        # Fully offline HTTP fixture. Only the real GTK Collecter click may
        # reach this MockTransport; the source is actually installed/activated.
        ecole_html = """
        <html><body>
          <div id="products-wrapper">
            <div class="product-item">
              <div class="card-title"><a href="/livre/histoire-petit-loup">L'histoire de Petit Loup</a></div>
              <div class="card-author">Sylvie Neeman</div>
              <div class="card-infos"><span>Age : 3 à 6 ans</span></div>
              <div class="card-cover"><img src="/assets/cover-loup.jpg"></div>
            </div>
            <div class="product-item">
              <div class="card-title"><a href="/livre/roman-ado">Roman du Loup</a></div>
              <div class="card-author">Alice Auteur</div>
              <div class="card-infos"><span>Age : 13 ans et +</span></div>
              <div class="card-cover"><img src="/assets/cover-roman.jpg"></div>
            </div>
          </div>
          <div id="products-thumb-wrapper">
            <div class="product-item-thumb"><a href="/livre/histoire-petit-loup">Vue miniature du loup</a></div>
            <div class="product-item-thumb"><a href="/livre/roman-ado">Vue miniature du roman</a></div>
          </div>
        </body></html>
        """

        def ecole_respond(request):
            # A real publisher adapter may use either public search surface.
            # Record structural evidence only, never the private search term.
            route = request.url.path
            search_key = "search" if route == "/catalogue/romans" else "search_api_views_fulltext"
            result["ecole_request_shape_907"] = {
                "host_is_publisher": request.url.host == "www.ecoledesloisirs.fr",
                "path": route, "query_keys": sorted(request.url.params.keys()),
                "search_present": bool(request.url.params.get(search_key)),
            }
            assert request.url.scheme == "https" and request.url.host == "www.ecoledesloisirs.fr", result["ecole_request_shape_907"]
            assert route in {"/catalogue/romans", "/recherche-avancee"}, result["ecole_request_shape_907"]
            expected_type = ("type", "romans") if route == "/catalogue/romans" else ("types", "livre")
            assert request.url.params.get(expected_type[0]) == expected_type[1], result["ecole_request_shape_907"]
            assert result["ecole_request_shape_907"]["search_present"], result["ecole_request_shape_907"]
            ecole_calls.append(route)  # Never persist user queries.
            return httpx.Response(
                503 if ecole_outage[0] else 200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="Simulated outage" if ecole_outage[0] else ecole_html,
                request=request,
            )

        ecole.client.close()
        ecole.client = httpx.Client(
            transport=httpx.MockTransport(ecole_respond),
            trust_env=False, follow_redirects=False,
        )

    def write_report():
        result["duration_seconds"] = round(monotonic() - start, 2)
        (out / "screen.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8",
        )

    def capture(name):
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"]).convert("RGB")
        assert image.width == args.width, f"wrong virtual screen: {image.width}"
        assert len(image.getcolors(maxcolors=2) or []) != 1, "blank GTK screenshot"
        path = out / f"{name}.png"
        image.save(path)
        shutil.copyfile(path, out / "screen.png")  # Canonical E2E screenshot, including on failure.
        result["steps"].append({
            "stage": name, "image": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "route": window.view_stack.get_visible_child_name(),
            "window_width": window.get_width(),
            "visible_rows": sorted(page.view.rows),
            "filters": str(page.view.filter_state()),
        })

    def fail(reason):
        if result["status"] != "RUNNING":
            return
        result["status"] = "FAIL"
        result["error"] = reason
        if window is not None:
            try:
                capture("failure")
            except Exception:
                pass
        write_report()
        app.quit()

    def later(ms, fn):
        def invoke():
            try:
                fn()
            except Exception as exc:
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, invoke)

    def keys(name):
        device.key(name, True)
        device.key(name, False)

    def assert_rows(*expected):
        actual = {book.record_id for book in page.view.rows.values()}
        assert actual == set(expected), f"expected {expected}, observed {actual}"

    def detail_rows():
        """Read actual libadwaita detail rows, not only catalog data."""
        found = []

        def visit(widget):
            if isinstance(widget, Adw.ActionRow):
                found.append((str(widget.get_title() or ""), str(widget.get_subtitle() or "")))
            child = widget.get_first_child()
            while child is not None:
                visit(child)
                child = child.get_next_sibling()

        visit(page.view.master._page.detail)
        return found


    def filter_help_widgets():
        """Collect GTK widgets and *explicit* bound, mapped mnemonic labels."""
        view = page.view
        return {
            "Profil": view.identity,
            "Recherche": view.query,
            "Âge": view.age,
            "Type d’âge": view.age_kind,
            "Source": view.source,
            "Langue": view.language,
            "Format": view.format,
            "Type de livre": view.book_type,
            "Thèmes": view.themes,
            "Âges inférés": view.inferred,
        }

    def filter_help_snapshot():
        controls = filter_help_widgets()
        bound_labels = []
        def visit(widget):
            if isinstance(widget, Gtk.Label) and widget.get_mapped():
                bound = widget.get_mnemonic_widget()
                if bound is not None:
                    bound_labels.append((bound, str(widget.get_text() or "")))
            child = widget.get_first_child()
            while child is not None:
                visit(child)
                child = child.get_next_sibling()
        visit(page.view.widget)

        rows = {}
        for name, widget in controls.items():
            labels = [label for target, label in bound_labels if target == widget]
            own_label = ""
            if isinstance(widget, Gtk.CheckButton):
                own_label = str(widget.get_label() or "")
                if own_label and widget.get_mapped():
                    labels.append(own_label)
            rows[name] = {
                "visible": bool(widget.get_mapped()),
                "permanent_labels": labels,
                "tooltip": str(widget.get_tooltip_text() or ""),
                "widget_type": type(widget).__name__,
            }
        return rows

    def filter_help_start_ui():
        """P1 #922: audit before and after real keyboard input, local-only."""
        assert not args.milan and not args.editions and not args.library_search
        assert page.view.selected_source_plugin() == "", "Aucune collecte distante implicite"
        result["filter_help_before"] = filter_help_snapshot()
        assert len(result["filter_help_before"]) == 10
        capture("00-jeunesse-controles-1366")
        result["filter_help_query_click"] = device.click_widget(page.view.query)
        device.type_text("Forest")
        later(250, filter_help_after_input)

    def filter_help_after_input():
        assert page.view.query.get_text() == "Forest", "Saisie XTEST non appliquée"
        assert not requests, "Aucun HTTP ne doit être émis pour saisir un filtre local"
        assert page.view.selected_source_plugin() == "", "La source ne change pas implicitement"
        after = filter_help_snapshot()
        result["filter_help_after_input"] = after
        capture("01-jeunesse-controles-saisie-1366")
        before = result["filter_help_before"]

        missing_tooltips = [
            name for name, row in after.items()
            if len(row["tooltip"].strip()) < 18
        ]
        missing_labels = [
            name for name, row in after.items()
            if not row["permanent_labels"] or not row["visible"]
        ]
        lost_labels = [
            name for name, row in after.items()
            if row["permanent_labels"] != before[name]["permanent_labels"]
        ]
        # The help must not imply that selecting "Toutes les sources"
        # triggers global HTTP. One deliberate collect per selected provider.
        source_help = after["Source"]["tooltip"].casefold()
        query_help = after["Recherche"]["tooltip"].casefold()
        misleading_help = []
        if not ("source" in source_help and "collect" in source_help):
            misleading_help.append("Source : filtre du cache ≠ collecte globale")
        if not ("local" in query_help or "cache" in query_help):
            misleading_help.append("Recherche : premier filtrage local explicite")

        result["filter_help_missing_tooltips"] = missing_tooltips
        result["filter_help_missing_labels"] = missing_labels
        result["filter_help_lost_labels_after_input"] = lost_labels
        result["filter_help_misleading_text"] = misleading_help
        result["filter_help_no_remote_requests"] = True
        # Continue through real XTEST Tab/Shift+Tab before the #922
        # acceptance assertion, so the red gate still publishes focus evidence.
        result["filter_help_keyboard"] = {
            "start": filter_help_focus_name(),
            "back_from_search": [],
            "forward_from_search": [],
            "back_from_last_filter": [],
        }
        filter_help_tab_step("back_from_search", 0)

    def filter_help_focus_name():
        """Inspect actual GtkWindow focus, including a Gtk.Entry's inner GtkText."""
        focused = window.get_focus()
        if focused is None:
            return "none"
        for name, control in filter_help_widgets().items():
            # GTK: gtk_widget_is_ancestor(widget, ancestor) expects the
            # *focused descendant* first, not the parent control.
            if control is focused or focused.is_ancestor(control):
                return name
        return "other:" + focused.__gtype__.name

    def filter_help_tab_step(phase, index):
        """Only XTEST key presses drive the keyboard probe (no synthetic focus)."""
        budgets = {
            "back_from_search": 8,
            "forward_from_search": 45,
            "back_from_last_filter": 45,
        }
        if index >= budgets[phase]:
            if phase == "back_from_search":
                # Anchor on the same entry after testing the preceding stops.
                result["filter_help_keyboard"]["reanchor_search"] = device.click_widget(page.view.query)
                later(90, lambda: filter_help_tab_step("forward_from_search", 0))
            elif phase == "forward_from_search":
                # Gtk.CheckButton is used only as a starting point; Shift+Tab
                # navigation itself remains real keyboard input.
                anchored = page.view.inferred.grab_focus()
                assert anchored is not False, "Impossible de focaliser le filtre âges inférés"
                later(90, lambda: filter_help_tab_step("back_from_last_filter", 0))
            else:
                filter_help_keyboard_finish()
            return
        backwards = phase != "forward_from_search"
        if backwards:
            device.key("Shift_L", True)
        keys("Tab")
        if backwards:
            device.key("Shift_L", False)
        later(35, lambda: filter_help_tab_record(phase, index))

    def filter_help_tab_record(phase, index):
        result["filter_help_keyboard"][phase].append(filter_help_focus_name())
        filter_help_tab_step(phase, index + 1)

    def filter_help_keyboard_finish():
        keyboard = result["filter_help_keyboard"]
        reached = set(
            keyboard["back_from_search"] +
            keyboard["forward_from_search"] +
            keyboard["back_from_last_filter"] +
            [keyboard["start"]]
        )
        keyboard["reached"] = sorted(reached.intersection(filter_help_widgets()))
        keyboard["missing_controls"] = [
            name for name in filter_help_widgets() if name not in reached
        ]
        # Gtk.FlowBoxChild must not create an extra anonymous Tab stop
        # before each interactive control (#338 was fixed in QueryControls,
        # but this Youth-specific FlowBox is a separate GTK surface).
        forward = keyboard["forward_from_search"]
        backward = keyboard["back_from_last_filter"]
        forward_end = forward.index("Âges inférés")
        backward_end = backward.index("Recherche")
        keyboard["anonymous_forward_filter_stops"] = [
            index + 1 for index, name in enumerate(forward[:forward_end + 1])
            if name == "other:GtkFlowBoxChild"
        ]
        keyboard["anonymous_backward_filter_stops"] = [
            index + 1 for index, name in enumerate(backward[:backward_end + 1])
            if name == "other:GtkFlowBoxChild"
        ]
        keyboard["no_remote_requests"] = not requests
        assert not requests, "Tab/Shift+Tab ne doivent jamais déclencher de collecte"
        capture("02-jeunesse-navigation-clavier-1366")
        assert not (
            result["filter_help_missing_tooltips"] or
            result["filter_help_missing_labels"] or
            result["filter_help_lost_labels_after_input"] or
            result["filter_help_misleading_text"] or
            keyboard["missing_controls"] or
            keyboard["anonymous_forward_filter_stops"] or
            keyboard["anonymous_backward_filter_stops"]
        ), (
            "UX P1 #922 : contrôles nommés, aidés et navigables requis ; "
            f"tooltips={result['filter_help_missing_tooltips']}, "
            f"labels={result['filter_help_missing_labels']}, "
            f"labels_perdus={result['filter_help_lost_labels_after_input']}, "
            f"contexte={result['filter_help_misleading_text']}, "
            f"clavier={keyboard['missing_controls']}, "
            f"arrêts_anon_allée={keyboard['anonymous_forward_filter_stops']}, "
            f"arrêts_anon_retour={keyboard['anonymous_backward_filter_stops']}"
        )
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    def start_ui():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view is not None, "Real ui.page.youth factory not called"
        assert page.view.widget.get_mapped(), "Youth view not mapped on screen"
        assert window.get_width() <= args.width, "Youth window overflows Xvfb viewport"
        assert page.view.identity.get_model().get_n_items() == 2
        assert page.view.age.get_model().get_n_items() == 19, "Only 0..17 are selectable, not 18"
        if args.filters_ux:
            filter_help_start_ui()
            return
        if args.library_search:
            library_search_start_ui()
            return
        if args.milan:
            milan_start_ui()
            return
        if args.editions:
            editions_start_ui()
            return
        assert len(page.view.sources) == 0, "No external youth source should activate in this fixture"
        assert_rows()
        capture("00-empty-cache")
        camille, alex = records()
        assert catalog.ingest("camille", "fixture-youth", camille).accepted == 6
        milan = dict(
            camille[1],
            record_id="milan-forest",
            source_item_id="milan-forest",
            age_ranges=[{
                "age_min": 8, "age_max": 12, "age_kind": "publisher",
                "source_label": "8–12 ans (Milan)", "source_id": "milan",
                "confidence": 0.9,
            }],
        )
        assert catalog.ingest("camille", "milan", [milan]).accepted == 1
        assert catalog.ingest("alex", "fixture-youth", alex).accepted == 1
        merged = catalog.search_cached("camille", "Forest")
        assert len(merged) == 1 and set(merged[0].provenance) == {"fixture-youth", "milan"}
        page.view.render_cached()
        later(200, cached_camille)


    def collect_button():
        def walk(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Collecter cette source":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = walk(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        button = walk(page.view.widget)
        assert button is not None and button.get_mapped(), "Collect action not mapped"
        return button


    # #906 : réutilise le véritable package, la vraie fenêtre et le XTEST de
    # cette sonde. Aucune nouvelle CI ni collection de source à l'ouverture.
    def library_search_button():
        def visit(widget):
            if isinstance(widget, Gtk.Button) and getattr(widget, "_cifereca_action_id", None) == "search-library":
                assert getattr(widget, "_cifereca_action_label", "") == "Rechercher en bibliothèque", (
                    "Le libellé visible du bouton #906 a changé"
                )
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = visit(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        return visit(page.view.widget)

    def library_search_start_ui():
        assert not requests, "Visiter Jeunesse ne doit pas collecter à distance"
        identity = page.view.identity_id()
        title = "C'est quoi, l'écologie ?"
        assert identity == "camille", identity
        from copy import deepcopy
        a = dict(records()[0][1], record_id="ux906-a", source_item_id="ux906-a", title=title)
        b = dict(deepcopy(a), record_id="ux906-b", source_item_id="ux906-b", isbn="9780140328721")
        assert catalog.ingest(identity, "milan", [a, b]).accepted == 2
        assert len(catalog.search_cached(identity, title)) == 2, "Éditions ISBN distinctes fusionnées"
        core.storage.catalog_entry_put({
            "catalog_entry_id": "ux906-known", "title": title, "author": "Autrice UX",
            "identity_id": identity, "provider_id": "local", "item_id": "ux906-known",
            "local_state": "remote_catalog", "media_format": "epub",
            "metadata": {"medium": "digital", "language": "fr"},
        })
        original_library_search[0] = core.library.search_known_catalog
        def observed_local(*args, **kwargs):
            library_search_calls.append({
                "query": args[0], "scope": args[1],
                "field": kwargs.get("field"), "identity_id": kwargs.get("identity_id"),
            })
            return original_library_search[0](*args, **kwargs)
        core.library.search_known_catalog = observed_local

        original_online_search[0] = gui_main.progressive_online_search
        def forbid_online(*_args, **_kwargs):
            online_attempts.append("unexpected-online")
            raise AssertionError("Aucun HTTP attendu du MVP local #906")
        gui_main.progressive_online_search = forbid_online
        result["library_search_title"] = title
        result["library_search_identity"] = identity
        page.view.query.set_text(title)
        later(240, library_search_select)

    def library_search_select():
        master = page.view.master._page
        assert master.store.get_n_items() == 2, "Les deux éditions attendues ne sont pas affichées"
        assert library_search_button() is None, "Action présente sans sélection"
        master.selection.set_selected(0)
        later(220, library_search_first_click)

    def library_search_first_click():
        action = library_search_button()
        assert action is not None and action.get_mapped(), "Bouton P0 #906 absent de GTK"
        assert not library_search_calls and not online_attempts
        master = page.view.master._page
        selected = master.selection.get_selected_item()
        assert selected is not None
        result["library_search_initial_selection"] = selected.key
        result["library_search_initial_filter"] = page.view.query.get_text()
        # Filtres préexistants contraignants : le routeur doit les neutraliser.
        window.search_controls.set_state(QueryState(
            text="ancien filtre", field="author", scope="digital",
            providers=("local",), languages=("en",),
        ), notify=False)
        capture("12-library-search-action-visible")
        device.click_widget(action)
        later(300, library_search_verify_local)

    def library_search_verify_local():
        assert window.view_stack.get_visible_child_name() == "search", "Bouton n'a pas navigué vers Recherche"
        expected = QueryState(text=result["library_search_title"], field="title", scope="all")
        assert window.search_controls.state() == expected, window.search_controls.state()
        assert core.library.current_identity.identity_id == result["library_search_identity"]
        assert len(library_search_calls) <= 1, library_search_calls
        if (not library_search_calls or window.search_page.store.get_n_items() < 1) and monotonic() - start < 16:
            later(150, library_search_verify_local)
            return
        assert library_search_calls == [{
            "query": result["library_search_title"], "scope": "all",
            "field": "title", "identity_id": result["library_search_identity"],
        }], library_search_calls
        assert window.search_page.store.get_n_items() >= 1, "Aucun résultat dans la liste GTK connue"
        assert window._search_mode == "local" and not online_attempts, online_attempts
        result["library_search_local_call_count"] = len(library_search_calls)
        capture("13-library-search-known-results")
        assert window.can_go_back(), "Retour indisponible après recherche"
        back = getattr(window, "back_button", None)
        if back is not None and back.get_mapped():
            device.click_widget(back)
            result["library_search_back"] = "XTEST"
        else:
            window.go_back()
            result["library_search_back"] = "GTK navigation API"
        # Attendre que la transition GTK se termine avant de mesurer le focus.
        # Le parcours P0 conserve son délai initial.
        later(900 if args.focus_return else 260, library_search_verify_return)

    def library_search_verify_return():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view.identity_id() == result["library_search_identity"]
        assert page.view.query.get_text() == result["library_search_initial_filter"]
        selected = page.view.master._page.selection.get_selected_item()
        assert selected is not None and selected.key == result["library_search_initial_selection"], (
            "Sélection de Jeunesse perdue après Retour"
        )
        action = library_search_button()
        assert action is not None and action.get_mapped()
        focus = window.get_focus()
        result["library_search_action_focus_restored"] = (
            focus is action or (focus is not None and action.is_ancestor(focus))
        )
        if not result["library_search_action_focus_restored"]:
            result["library_search_ux_observation"] = (
                "Le focus ne revient pas au bouton initial ; à traiter séparément en UX"
            )
        capture("14-library-search-return")
        if args.focus_return:
            # #962 : ne pas « réparer » artificiellement le focus dans la sonde.
            # L'ancienne recette #906 appelle grab_focus() pour tester Entrée/Espace ;
            # celle-ci doit constater le focus rendu par la navigation elle-même.
            result["focus_return_action_mapped"] = bool(action.get_mapped())
            result["focus_return_widget"] = (
                type(focus).__name__ if focus is not None else None
            )
            result["focus_return_expected_action"] = str(
                getattr(action, "_cifereca_action_label", "") or "Rechercher en bibliothèque"
            )
            assert result["library_search_action_focus_restored"], (
                "UX #962 : après Retour, le focus clavier doit revenir à l'action "
                "Rechercher en bibliothèque du livre toujours sélectionné ; "
                f"focus observé={result['focus_return_widget']!r}"
            )
            assert not online_attempts and not requests, (
                "Retour clavier : aucune recherche ou collecte réseau implicite"
            )
            result["status"] = "PASS"
            write_report()
            rc[0] = 0
            app.quit()
            return
        assert action.grab_focus() is not False
        keys("Return")
        later(280, library_search_verify_enter)

    def library_search_verify_enter():
        assert window.view_stack.get_visible_child_name() == "search", "Entrée n'active pas le bouton"
        if len(library_search_calls) < 2 and monotonic() - start < 22:
            later(150, library_search_verify_enter)
            return
        assert len(library_search_calls) == 2 and library_search_calls[1] == library_search_calls[0]
        assert window._search_mode == "local" and not online_attempts
        capture("15-library-search-enter")
        window.go_back()
        later(240, library_search_press_space)

    def library_search_press_space():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view.master._page.selection.get_selected_item() is not None
        action = library_search_button()
        assert action is not None and action.get_mapped()
        assert action.grab_focus() is not False
        keys("space")
        later(280, library_search_verify_space)

    def library_search_verify_space():
        assert window.view_stack.get_visible_child_name() == "search", "Espace n'active pas le bouton"
        if len(library_search_calls) < 3 and monotonic() - start < 27:
            later(120, library_search_verify_space)
            return
        assert len(library_search_calls) == 3 and all(
            call == library_search_calls[0] for call in library_search_calls
        ), library_search_calls
        assert window._search_mode == "local" and not online_attempts
        result["library_search_activations"] = ["clic XTEST", "Entrée XTEST", "Espace XTEST"]
        capture("16-library-search-space")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()



    def audit_milan_action_states(stage, expected):
        """#923: real GTK state, tooltips and HTTP counter at every transition."""
        assert args.milan, "Milan action audit only belongs to the offline Milan scenario"
        buttons = page.view._action_buttons
        required = (
            "Collecter cette source", "Plus de résultats",
            "Enrichir la bibliographie", "Ouvrir la fiche Bibliothèque",
        )
        assert set(buttons) == set(required), f"Missing GTK actions: {buttons!r}"
        state = {}
        for label in required:
            button = buttons[label]
            assert isinstance(button, Gtk.Button) and button.get_mapped(), (
                f"{stage}: {label} is missing from the mapped GTK window"
            )
            enabled = bool(button.get_sensitive())
            tooltip = button.get_tooltip_text() or ""
            assert tooltip.strip(), f"{stage}: {label} has no help text"
            state[label] = {"enabled": enabled, "tooltip": tooltip}
            if label in expected:
                assert enabled is expected[label], (
                    f"{stage}: {label} expected {expected[label]} got {enabled}: {tooltip}"
                )
        result.setdefault("milan_action_states_923", []).append({
            "stage": stage,
            "source": page.view.selected_source_plugin(),
            "identity_id": page.view.identity_id(),
            "next_cursor": page.view.next_cursor,
            "selected_book": getattr(page.view.selected_book, "record_id", None),
            "request_count": len(requests),
            "buttons": state,
        })

    def milan_start_ui():
        assert {row.plugin_id for row in page.view.sources} == {
            "youth.source.milan", "youth.source.ecole-loisirs",
        }, "Both real publisher providers must be installed"
        assert page.view.source.get_model().get_n_items() == 3
        assert_rows()
        assert requests == [], "Opening Milan page triggered remote HTTP"
        audit_milan_action_states("empty-all-sources", {
            "Collecter cette source": False,
            "Plus de résultats": False,
            "Enrichir la bibliographie": False,
            "Ouvrir la fiche Bibliothèque": False,
        })
        capture("00-milan-source-no-cache")
        milan_index = next(
            i + 1 for i, item in enumerate(page.view.sources)
            if item.plugin_id == "youth.source.milan"
        )
        page.view.source.set_selected(milan_index)
        later(180, milan_collect)

    def milan_collect():
        assert page.view.selected_source_plugin() == "youth.source.milan"
        assert not requests, "Selecting Milan triggered remote HTTP"
        audit_milan_action_states("milan-selected-no-results", {
            "Collecter cette source": True,
            "Plus de résultats": False,
            "Enrichir la bibliographie": False,
            "Ouvrir la fiche Bibliothèque": False,
        })
        device.click_widget(collect_button())
        later(170, milan_success)

    def milan_success():
        if len(page.view.rows) != 4 and monotonic() - start < 19:
            later(180, milan_success)
            return
        assert len(page.view.rows) == 4, f"Milan cache count: {page.view.rows!r}"
        assert len(requests) == 9, f"Expected listing + 8 bounded detail fetches: {requests!r}"
        assert requests[0] == "https://www.editionsmilan.com/livres/"
        assert "99993-jeunesse-neuf" not in " ".join(requests)
        status = page.view.status.get_text()
        assert "4 notice(s) reçue(s) de Éditions Milan" in status, status
        assert "page suivante disponible" in status, status
        assert len(catalog.search_cached("camille")) == 4
        audit_milan_action_states("milan-first-page", {
            "Collecter cette source": True,
            "Plus de résultats": True,
            "Enrichir la bibliographie": False,
            "Ouvrir la fiche Bibliothèque": False,
        })
        # Immediate capture documents the actual empty -> list CROSSFADE.
        capture("01-milan-collected-four")
        later(1100, milan_list_settled)

    def milan_list_settled():
        # Do not mistake the temporary outgoing "Aucun résultat en cache"
        # status-page overlay for a lasting UX defect.
        stack = page.view.master._page.list_stack
        assert stack.get_visible_child_name() == "list", "Collected rows not made visible"
        assert not stack.get_transition_running(), "Empty-to-list GTK transition still running"
        assert len(page.view.rows) == 4
        capture("01b-milan-list-settled")
        page.view.master._page.selection.set_selected(0)
        later(250, milan_notice)

    def milan_notice():
        book = page.view.selected_book
        assert book is not None, "Selecting Milan book failed"
        assert set(book.provenance) == {"milan"}
        assert len(book.age_ranges) == 1
        assert book.age_ranges[0].age_kind == "publisher"
        assert "À partir de" in book.age_ranges[0].source_label
        expected_uri = book.notice_url
        assert expected_uri.startswith("https://www.editionsmilan.com/livres/")
        assert page.view.master._page.detail._rendered_document_urls == {expected_uri}
        audit_milan_action_states("milan-book-selected", {
            "Collecter cette source": True,
            "Plus de résultats": True,
            "Enrichir la bibliographie": False,
            "Ouvrir la fiche Bibliothèque": True,
        })
        def links(widget):
            result_links = []
            if isinstance(widget, Gtk.LinkButton):
                result_links.append(widget)
            child = widget.get_first_child()
            while child is not None:
                result_links.extend(links(child))
                child = child.get_next_sibling()
            return result_links
        rendered = links(page.view.master._page.detail)
        notice = [link for link in rendered if link.get_uri() == expected_uri]
        assert len(notice) == 1, f"Expected 1 notice link for {expected_uri}: {[(b.get_label(), b.get_uri()) for b in rendered]!r}"
        # GTK4 LinkButton may render its text in a child GtkLabel while
        # Gtk.Button.get_label() returns None. Inspect the actual widget tree.
        def widget_text(widget):
            labels = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
            child = widget.get_first_child()
            while child is not None:
                labels.extend(widget_text(child))
                child = child.get_next_sibling()
            return labels
        label = notice[0].get_label() or " ".join(widget_text(notice[0]))
        assert "Voir la notice publique" in label, f"Notice link text missing in GTK: {label!r}"
        assert notice[0].get_mapped(), "Notice link not mapped in real GTK detail"
        result["milan_notice_uri"] = expected_uri
        capture("02-milan-notice-link")
        # Real XTEST activation with an isolated local HTTPS handler. Its sole
        # executable records the URI and never touches the Internet.
        assert not notice_sink_path.exists(), "Notice URL dispatched without explicit click"
        assert len(requests) == 9, "Detail render unexpectedly fetched HTTP"
        result["milan_notice_click"] = device.click_widget(notice[0])
        later(150, milan_notice_dispatched)

    def milan_notice_dispatched():
        if not notice_sink_path.exists() and monotonic() - start < 33:
            later(160, milan_notice_dispatched)
            return
        assert notice_sink_path.exists(), "GTK LinkButton did not launch isolated HTTPS handler"
        invocations = [
            json.loads(line)["argv"]
            for line in notice_sink_path.read_text(encoding="utf-8").splitlines()
        ]
        assert invocations == [[result["milan_notice_uri"]]], (
            f"Unexpected default browser dispatch: {invocations!r}"
        )
        assert len(requests) == 9, "Clicking notice must not re-collect via source HTTP"
        assert len(page.view.rows) == 4, "Clicking notice mutated catalogue cache"
        result["milan_notice_handler_invocations"] = invocations
        result["milan_notice_dispatched_via"] = result["notice_launch_handler"]
        capture("02b-milan-notice-launched-to-offline-browser")
        def find_more(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Plus de résultats":
                return widget
            child = widget.get_first_child()
            while child is not None:
                found = find_more(child)
                if found is not None:
                    return found
                child = child.get_next_sibling()
            return None
        more = find_more(page.view.widget)
        assert more is not None and more.get_mapped(), "Milan pagination button missing"
        assert more.get_sensitive(), "Available cursor must enable pagination"
        device.click_widget(more)
        later(170, milan_page_two)

    def milan_page_two():
        if len(page.view.rows) != 5 and monotonic() - start < 30:
            later(180, milan_page_two)
            return
        assert len(page.view.rows) == 5, f"Milan append pagination failed: {page.view.rows!r}"
        assert len(requests) == 12, f"Expected listing + 2 page-2 detail requests: {requests!r}"
        assert requests[9] == "https://www.editionsmilan.com/livres/page/2/"
        assert not page.view.next_cursor, "Milan terminal page still exposes next cursor"
        audit_milan_action_states("milan-terminal-page", {
            "Collecter cette source": True,
            "Plus de résultats": False,
        })
        capture("03-milan-page-two-appended")
        fault["unavailable"] = True
        device.click_widget(collect_button())
        later(160, milan_outage)

    def milan_outage():
        message = page.view.status.get_text()
        if "Échec de la source" not in message and monotonic() - start < 40:
            later(170, milan_outage)
            return
        assert message == "Échec de la source. Le cache précédent est conservé.", message
        assert len(requests) == 13, f"Outage requires exactly one new explicit call: {requests!r}"
        assert len(page.view.rows) == 5
        assert len(catalog.search_cached("camille")) == 5
        result["milan_requests"] = list(requests)
        result["milan_outage_feedback"] = message
        audit_milan_action_states("milan-outage-cache-retained", {
            "Collecter cette source": True,
            "Plus de résultats": False,
        })
        capture("04-milan-outage-cache-preserved")
        page.view.age.set_selected(1)  # 0 years; all attested Milan ages start at 1+
        later(200, milan_empty)

    def milan_empty():
        assert_rows()
        assert len(requests) == 13, "Local age filter unexpectedly contacted Milan"
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        audit_milan_action_states("milan-filtered-empty", {
            "Collecter cette source": True,
            "Plus de résultats": False,
            "Enrichir la bibliographie": False,
            "Ouvrir la fiche Bibliothèque": False,
        })
        capture("05-milan-empty-without-request")
        # Exit local no-results filter; only a further XTEST click may retry.
        page.view.age.set_selected(0)
        later(200, milan_retry_after_outage)

    def milan_retry_after_outage():
        assert len(page.view.rows) == 5
        assert len(requests) == 13, "Clearing local filter must not fetch Milan"
        assert "Échec de la source" in page.view.status.get_text()
        fault["unavailable"] = False
        device.click_widget(collect_button())
        later(180, milan_recovered)

    def milan_recovered():
        status = page.view.status.get_text()
        if (len(requests) != 22 or "4 notice(s) reçue(s) de Éditions Milan" not in status) and monotonic() - start < 49:
            later(170, milan_recovered)
            return
        assert len(requests) == 22, f"Retry should fetch listing + eight details: {requests!r}"
        assert requests[13] == requests[0], "Recovery did not restart at first Milan catalogue page"
        assert len(page.view.rows) == 4, "Authoritative recovery must replace, not append stale page-two results"
        assert len(catalog.search_cached("camille")) == 4
        assert page.view.next_cursor == "2", "Recovery should expose the fresh second page"
        assert "page suivante disponible" in status and "Échec de la source" not in status, status
        audit_milan_action_states("milan-recovered", {
            "Collecter cette source": True,
            "Plus de résultats": True,
        })
        result["milan_requests_after_recovery"] = list(requests)
        result["milan_recovery_feedback"] = status
        capture("06-milan-recovered-first-page")
        # #907: existing Milan XTEST path now also proves the cache-only,
        # per-identity source diagnostic projection in the real Youth GTK.
        later(180, youth_diagnostics_milan)


    def diagnostic_labels():
        children = []
        child = page.view.diagnostic_rows_box.get_first_child()
        while child is not None:
            if isinstance(child, Gtk.Label):
                children.append(child.get_text())
            child = child.get_next_sibling()
        return tuple(children)

    def diagnostic_for(identity):
        return {
            row["source_id"]: row
            for row in catalog.source_diagnostics(identity)
        }

    def youth_diagnostics_milan():
        page.view.diagnostic_expander.set_expanded(True)
        page.view.refresh_source_diagnostics()
        camille = diagnostic_for("camille")
        alex = diagnostic_for("alex")
        assert camille["milan"]["collection_state"] == "items", camille["milan"]
        assert camille["milan"]["cache_count"] == 4, camille["milan"]
        assert camille["milan"]["request_count"] == 9, camille["milan"]
        assert camille["milan"]["pipeline"].get("candidate_count", 0) > 0, camille["milan"]
        assert camille["ecole-loisirs"]["collection_state"] == "not_tested"
        assert alex["milan"]["collection_state"] == "not_tested"
        assert alex["ecole-loisirs"]["collection_state"] == "not_tested"
        rendered = " ".join(diagnostic_labels())
        assert "Éditions Milan" in rendered and "collecte avec notices" in rendered, rendered
        assert "L'École des loisirs" in rendered and "non testée" in rendered, rendered
        assert len(requests) == 22, "Reading Diagnostics must not contact Milan"
        result["youth_diagnostics_907"] = {
            "milan_camille": {
                "state": camille["milan"]["collection_state"],
                "cached": camille["milan"]["cache_count"],
                "http_requests_last_attempt": camille["milan"]["request_count"],
                "candidates": camille["milan"]["pipeline"].get("candidate_count"),
            },
            "ecole_before_explicit_request": camille["ecole-loisirs"]["collection_state"],
            "alex_before_explicit_request": alex["milan"]["collection_state"],
        }
        capture("07-diagnostics-milan-camille")
        # Until the real user clicks Collecter, the fully installed École
        # source must remain not_tested and make no HTTP calls.
        assert not ecole_calls, "École des loisirs was fetched on page load"
        page.view.identity.set_selected(1)  # Alex, separate from Milan/Camille.
        page.view.query.set_text("loup")
        ecole_index = next(
            i + 1 for i, item in enumerate(page.view.sources)
            if item.plugin_id == "youth.source.ecole-loisirs"
        )
        page.view.source.set_selected(ecole_index)
        later(220, youth_ecole_collect_click)

    def youth_ecole_collect_click():
        assert page.view.identity_id() == "alex"
        assert page.view.selected_source_plugin() == "youth.source.ecole-loisirs"
        assert not ecole_calls, "Selecting an identity/source must not trigger HTTP"
        assert page.view._action_buttons["Collecter cette source"].get_sensitive()
        assert diagnostic_for("alex")["ecole-loisirs"]["collection_state"] == "not_tested"
        assert not page.view.rows, "Alex initially has no École cache"
        device.click_widget(collect_button())  # REAL GTK/XTEST physical click.
        later(170, youth_ecole_collected)

    def youth_ecole_collected():
        current = diagnostic_for("alex")["ecole-loisirs"]
        if (current["collection_state"] != "items" or len(page.view.rows) != 2) and monotonic() - start < 47:
            later(170, youth_ecole_collected)
            return
        assert current["collection_state"] == "items", current
        assert current["cache_count"] == 2, current
        assert len(page.view.rows) == 2, page.view.rows
        assert len(ecole_calls) == 1, "One GTK click must equal exactly one GET"
        assert current["request_count"] == 1, current
        assert current["pipeline"].get("candidate_count") == 2, current
        assert current["pipeline"].get("age_qualified_count") == 2, current
        assert current["pipeline"].get("isbn_valid_count") == 0, current
        assert current["pipeline"].get("missing_isbn") == 2, current
        assert current["pipeline"].get("cards_detected") == 2, current
        assert current["pipeline"].get("cards_complete") == 2, current
        assert current["pipeline"].get("thumbnail_card_count") == 2, current
        assert current["pipeline"].get("thumbnail_duplicate_count") == 2, current
        assert current["pipeline"].get("cover_urls_valid") == 2, current
        assert "2 notice(s) reçue(s)" in page.view.status.get_text()
        labels = " ".join(diagnostic_labels())
        assert "L'École des loisirs" in labels and "collecte avec notices" in labels, labels
        assert "notices sans ISBN publié : 2" in labels, labels
        assert "cartes catalogue : 2" in labels, labels
        assert "vignettes secondaires : 2" in labels, labels
        assert "vignettes d’éditions déjà présentes : 2" in labels, labels
        assert "loup" not in labels.lower(), "Query leaked into Diagnostics"
        assert diagnostic_for("camille")["ecole-loisirs"]["collection_state"] == "not_tested"
        assert diagnostic_for("alex")["milan"]["collection_state"] == "not_tested"
        assert len(requests) == 22, "École collection must not contact Milan"
        result["youth_diagnostics_907"]["ecole_alex"] = {
            "gtk_xtest_click": True,
            "http_calls": 1,
            "accepted": 2,
            "candidates": 2,
            "qualified_ages": 2,
            "isbn_published": 0,
            "cards_without_isbn": 2,
            "error_state": None,
            "retained_cache": None,
        }
        capture("08-ecole-gtk-xtest-click-two-books")
        ecole_outage[0] = True
        device.click_widget(collect_button())
        later(170, youth_ecole_http_error)

    def youth_ecole_http_error():
        current = diagnostic_for("alex")["ecole-loisirs"]
        if current["collection_state"] != "http_error" and monotonic() - start < 50:
            later(170, youth_ecole_http_error)
            return
        assert current["collection_state"] == "http_error", current
        assert current["http_status"] == 503, current
        assert current["received_count"] is None, current
        assert current["cache_count"] == 2, current
        assert len(page.view.rows) == 2, page.view.rows
        assert len(ecole_calls) == 2, "Second XTEST click must equal one more GET"
        labels = " ".join(diagnostic_labels())
        assert "L'École des loisirs" in labels and "erreur HTTP 503" in labels, labels
        assert "conservé" in labels and "cache : 2 notice(s)" in labels, labels
        assert "Éditions Milan" in labels and "non testée" in labels, labels
        assert "loup" not in labels.lower()
        assert len(requests) == 22
        result["youth_diagnostics_907"]["ecole_alex"].update({
            "error_state": "http_error", "http_error": 503,
            "retained_cache": 2, "second_gtk_xtest_click": True,
        })
        capture("09-ecole-gtk-xtest-http503-cache-kept")
        page.view.source.set_selected(0)
        page.view.query.set_text("")
        page.view.identity.set_selected(0)  # Camille again.
        later(220, youth_diagnostics_camille_restored)

    def youth_diagnostics_camille_restored():
        assert page.view.identity_id() == "camille"
        assert len(page.view.rows) == 4, page.view.rows
        rendered = " ".join(diagnostic_labels())
        assert "Éditions Milan" in rendered and "collecte avec notices" in rendered, rendered
        assert "L'École des loisirs" in rendered and "non testée" in rendered, rendered
        assert len(ecole_calls) == 2 and len(requests) == 22, "Switching identity triggered HTTP"
        result["youth_diagnostics_907"]["camille_restored"] = True
        capture("10-diagnostics-camille-restored")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    def editions_start_ui():
        """Three synthetic source records: same ISBN merges, distinct ISBN does not."""
        assert page.view.sources == (), "No remote provider should run in offline edition fixture"
        assert_rows()
        shared = dict(
            title="Livre miroir", authors=["Autrice commune"],
            language="fr", formats=["epub"], book_type="fiction",
            themes=["aventure"],
        )
        same_isbn = "9780306406157"
        other_isbn = "9780140328721"
        bt = dict(
            shared, record_id="mirror-bt", source_item_id="bt-mirror",
            isbn=same_isbn,
            notice_url="https://www.fixture-youth.org.uk/book-recommendations/bookfinder/mirror-a/",
            age_ranges=[{
                "age_min": 5, "age_max": 7, "age_kind": "interest",
                "source_id": "fixture-youth", "source_label": "Interest age: 5 to 7",
                "confidence": 0.85,
            }],
        )
        milan_same = dict(
            shared, record_id="mirror-milan-a", source_item_id="milan-mirror-a",
            isbn=same_isbn,
            notice_url="https://www.editionsmilan.com/livres/12345-mirror-a/",
            age_ranges=[{
                "age_min": 12, "age_max": None, "age_kind": "publisher",
                "source_id": "milan", "source_label": "À partir de 12 ans",
                "confidence": 1.0,
            }],
        )
        milan_other = dict(
            shared, record_id="mirror-milan-b", source_item_id="milan-mirror-b",
            isbn=other_isbn,
            notice_url="https://www.editionsmilan.com/livres/12346-mirror-b/",
            age_ranges=[{
                "age_min": 8, "age_max": None, "age_kind": "publisher",
                "source_id": "milan", "source_label": "À partir de 8 ans",
                "confidence": 1.0,
            }],
        )
        assert catalog.ingest("camille", "fixture-youth", [bt]).accepted == 1
        assert catalog.ingest("camille", "milan", [milan_same, milan_other]).accepted == 2
        merged = catalog.search_cached("camille", "Livre miroir")
        assert len(merged) == 2, f"Distinct ISBNs were incorrectly collapsed: {merged!r}"
        by_isbn = {book.identifiers.get("isbn"): book for book in merged}
        assert set(by_isbn) == {same_isbn, other_isbn}
        assert len(by_isbn[same_isbn].age_ranges) == 2
        assert set(by_isbn[same_isbn].provenance) == {"fixture-youth", "milan"}
        assert tuple(by_isbn[other_isbn].provenance) == ("milan",)
        assert catalog.search_cached("alex", "Livre miroir") == ()
        page.view.query.set_text("Livre miroir")
        later(230, editions_two_rows)

    def editions_two_rows():
        assert_rows("mirror-bt", "mirror-milan-b")
        master = page.view.master._page
        assert master.store.get_n_items() == 2
        assert master.list_stack.get_visible_child_name() == "list"
        assert not requests, "Offline edits made a remote request"
        # Verify both real Gtk.ListView rows, not just the catalog/projection.
        expected = {
            "mirror-bt": "9780306406157",
            "mirror-milan-b": "9780140328721",
        }
        list_items = {
            master.store.get_item(i).key: master.store.get_item(i).meta
            for i in range(master.store.get_n_items())
        }
        assert set(list_items) == set(page.view.rows)
        for key, isbn in expected.items():
            assert list_items[next(k for k in list_items if page.view.rows[k].record_id == key)].startswith(
                f"ISBN {isbn}"
            ), list_items

        def actual_gtk_cells(widget):
            cells = []
            if hasattr(widget, "_bound_key") and hasattr(widget, "meta_label"):
                cells.append(widget)
            child = widget.get_first_child()
            while child is not None:
                cells.extend(actual_gtk_cells(child))
                child = child.get_next_sibling()
            return cells

        cells = {
            cell._bound_key: cell for cell in actual_gtk_cells(master.listview)
            if cell._bound_key and cell.get_mapped()
        }
        assert len(cells) == 2, f"Expected two mapped Gtk.ListView edition rows: {list(cells)!r}"
        for key, isbn in expected.items():
            actual_key = next(k for k, book in page.view.rows.items() if book.record_id == key)
            cell = cells[actual_key]
            assert cell.meta_label.get_mapped(), f"ISBN label hidden for {key}"
            assert cell.meta_label.get_text().startswith(f"ISBN {isbn}"), cell.meta_label.get_text()
        result["gtk_edition_list_metadata"] = {
            cell._bound_key: cell.meta_label.get_text() for cell in cells.values()
        }
        capture("00-same-title-two-editions")
        page.view.master._page.selection.set_selected(0)
        later(220, editions_conflict_detail)

    def editions_conflict_detail():
        book = page.view.selected_book
        assert book and book.record_id == "mirror-bt"
        assert set(book.provenance) == {"fixture-youth", "milan"}
        assert [(row.age_min, row.age_max) for row in book.age_ranges] == [(5, 7), (12, None)]
        shown = detail_rows()
        assert ("ISBN", "9780306406157") in shown, f"ISBN missing from real first Gtk detail: {shown!r}"
        assert ("ISBN", "9780140328721") not in shown, f"Incorrect second ISBN leaked: {shown!r}"
        assert any("source fixture-youth" in sub and "Interest age: 5 to 7" in sub for _, sub in shown), shown
        assert any("source milan" in sub and "À partir de 12 ans" in sub for _, sub in shown), shown
        sources = [subtitle for title, subtitle in shown if title == "Source"]
        assert len(sources) == 2 and set(sources) == {"fixture-youth", "milan"}, (
            f"Les sources de la première édition doivent être distinctes en GTK : {shown!r}"
        )
        assert not any(title == "Sources" for title, _ in shown), (
            "Une ligne agrégée Sources ne doit plus masquer les provenances individuelles"
        )
        result["same_isbn_detail"] = shown
        capture("01-conflicting-age-sources-preserved")
        later(140, editions_first_isbn_in_view)

    def editions_first_isbn_in_view():
        scroller = page.view.master._page.detail.body_scroll
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(200, editions_first_isbn_scrolled)

    def editions_first_isbn_scrolled():
        assert ("ISBN", "9780306406157") in detail_rows()
        capture("01b-first-isbn-visible-after-scroll")
        page.view.master._page.selection.set_selected(1)
        later(220, editions_second_detail)

    def editions_second_detail():
        book = page.view.selected_book
        assert book and book.record_id == "mirror-milan-b", book
        assert book.identifiers.get("isbn") == "9780140328721"
        assert set(book.provenance) == {"milan"}
        shown = detail_rows()
        assert ("ISBN", "9780140328721") in shown, f"ISBN missing from second Gtk detail: {shown!r}"
        assert ("ISBN", "9780306406157") not in shown, f"First ISBN leaked to second edition: {shown!r}"
        assert any("source milan" in sub and "À partir de 8 ans" in sub for _, sub in shown), shown
        assert not any("source fixture-youth" in sub for _, sub in shown), shown
        assert [subtitle for title, subtitle in shown if title == "Source"] == ["milan"], (
            f"Une édition distincte ne reprend pas la provenance de l'autre édition : {shown!r}"
        )
        result["different_isbn_detail"] = shown
        capture("02-second-edition-separated")
        later(140, editions_second_isbn_in_view)

    def editions_second_isbn_in_view():
        scroller = page.view.master._page.detail.body_scroll
        adjustment = scroller.get_vadjustment()
        adjustment.set_value(max(0, adjustment.get_upper() - adjustment.get_page_size()))
        later(200, editions_second_isbn_scrolled)

    def editions_second_isbn_scrolled():
        assert ("ISBN", "9780140328721") in detail_rows()
        capture("02b-second-isbn-visible-after-scroll")
        page.view.age.set_selected(7)  # âge 6: seulement source fictive 5-7
        later(180, editions_age_six)

    def editions_age_six():
        assert_rows("mirror-bt")
        capture("03-age-six-only-first-edition")
        page.view.age.set_selected(10)  # âge 9: seulement Milan 8+
        later(180, editions_age_nine)

    def editions_age_nine():
        assert_rows("mirror-milan-b")
        capture("04-age-nine-only-second-edition")
        page.view.age_kind.set_selected(2)  # Âge d'intérêt, aucun à 9 ans
        later(180, editions_no_interest)

    def editions_no_interest():
        assert_rows()
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("05-no-interest-age-at-nine")
        page.view.age_kind.set_selected(4)  # Âge éditeur
        page.view.age.set_selected(13)  # âge 12 : les deux éditions distinctes
        later(200, editions_publisher_twelve)

    def editions_publisher_twelve():
        assert_rows("mirror-bt", "mirror-milan-b")
        capture("06-publisher-age-twelve-keeps-both")
        page.view.identity.set_selected(1)  # Alex sans ces notices
        later(200, editions_identity_isolated)

    def editions_identity_isolated():
        assert_rows()
        assert not requests, "Identity/age filters triggered network"
        assert page.view.master._page.list_stack.get_visible_child_name() == "empty"
        capture("07-other-profile-stays-empty")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    def cached_camille():
        assert_rows("zero", "interest-eight", "reading-ten", "seventeen", "inferred", "unknown")
        capture("01-cached-camille")
        page.view.age.set_selected(9)  # 0 = any, 1 = age 0, 9 = age 8
        later(200, age_eight)

    def age_eight():
        assert page.view.filter_state().age == 8
        assert_rows("interest-eight")
        capture("02-age-8-interest")
        page.view.age.set_selected(13)  # age 12
        assert_rows("interest-eight", "reading-ten")
        page.view.age_kind.set_selected(3)  # reading only (kind index 3)
        later(200, age_twelve_reading)

    def age_twelve_reading():
        assert_rows("reading-ten")
        capture("03-age-12-reading")
        page.view.age_kind.set_selected(0)
        page.view.age.set_selected(18)  # age 17; age 18 is not exposed
        later(200, age_seventeen)

    def age_seventeen():
        assert_rows("seventeen")
        capture("04-age-17")
        page.view.age.set_selected(1)  # age 0
        assert_rows("zero")
        page.view.age.set_selected(9)
        page.view.inferred.set_active(True)
        assert_rows("interest-eight", "inferred")
        page.view.inferred.set_active(False)
        assert_rows("interest-eight")
        page.view.identity.set_selected(1)
        later(200, profile_alex)

    def profile_alex():
        assert_rows("alex-only")
        capture("05-identity-alex")
        page.view.identity.set_selected(0)
        assert_rows("interest-eight")
        later(200, keyboard_search)

    def keyboard_search():
        # Real mouse + XTEST keyboard events, not Gtk.Entry.set_text().
        device.click_widget(page.view.query)
        assert page.view.query.get_mapped(), "Keyboard search entry not mapped"
        device.type_text("Forest")
        later(220, finish_keyboard)

    def finish_keyboard():
        assert page.view.query.get_text() == "Forest"
        assert_rows("interest-eight")
        # #910 exploratory observation: emit real GTK activate signals via
        # XTEST without prescribing an (unimplemented) product behavior.
        # Passing E2E means the observation is genuine, NOT that #910 is fixed.
        # Gtk.Entry contains an internal Gtk.Text focus target in GTK4;
        # Entry.has_focus() alone is not a reliable keyboard assertion.
        entry_has_focus = bool(page.view.query.has_focus())
        assert page.view.selected_source_plugin() == "", "Local fixture unexpectedly has a remote source"
        result["keyboard_enter_observation"] = {
            "status_before": page.view.status.get_text(),
            "entry_widget_has_focus": entry_has_focus,
            "query": page.view.query.get_text(),
            "identity_id": page.view.identity_id(),
            "source_plugin": page.view.selected_source_plugin(),
            "row_ids": sorted(book.record_id for book in page.view.rows.values()),
            "gtk_activate_signals": 0,
            "criterion_910_satisfied": None,
            "note": "Observational UX probe; PASS does not mean Enter works as specified in #910.",
        }
        def activated(_entry):
            result["keyboard_enter_observation"]["gtk_activate_signals"] += 1
        page.view.query.connect("activate", activated)
        keys("Return")
        later(170, inspect_return)

    def inspect_return():
        audit = result["keyboard_enter_observation"]
        assert audit["gtk_activate_signals"] == 1, (
            f"Physical Return did not activate Gtk.Entry: {audit!r}"
        )
        assert page.view.query.get_text() == audit["query"]
        assert_rows("interest-eight")
        assert requests == [], "First Enter must never contact a remote source"
        audit["return_status"] = page.view.status.get_text()
        audit["return_visible_feedback"] = audit["return_status"] != audit["status_before"]
        assert audit["return_visible_feedback"], "First Return must acknowledge the local cache search"
        assert "Recherche en cache" in audit["return_status"]
        capture("05b-youth-return-gtk-observation")
        keys("KP_Enter")
        later(170, inspect_keypad_enter)

    def inspect_keypad_enter():
        audit = result["keyboard_enter_observation"]
        assert audit["gtk_activate_signals"] == 2, (
            f"Physical keypad Enter did not activate Gtk.Entry: {audit!r}"
        )
        assert page.view.query.get_text() == audit["query"]
        assert_rows("interest-eight")
        assert requests == [], "No selected source: consecutive Enter must not contact any provider"
        audit["keypad_return_status"] = page.view.status.get_text()
        audit["keypad_visible_feedback"] = (
            audit["keypad_return_status"] != audit["return_status"]
        )
        assert audit["keypad_visible_feedback"], "Second keypad Enter must explain source selection"
        assert "Choisissez une source Jeunesse précise" in audit["keypad_return_status"]
        audit["route_after"] = window.view_stack.get_visible_child_name()
        audit["selected_source_after"] = page.view.selected_source_plugin()
        # The acceptance requirement of #910 also includes explicit remote
        # collection with a chosen provider, untested here. Do not auto-close.
        audit["criterion_910_satisfied"] = False  # Remote selected-source path needs an independent GTK fixture.
        audit["missing_no_source_feedback_observed"] = (
            not audit["keypad_visible_feedback"]
        )
        capture("05c-youth-keypad-enter-gtk-observation")
        page.view.master._page.selection.set_selected(0)
        later(250, finish)

    def finish():
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "interest-eight"
        # The selected book must preserve the real source age label.
        assert page.view.selected_book.age_ranges[0].source_label == "8–12 ans"
        assert set(page.view.selected_book.provenance) == {"fixture-youth", "milan"}
        shown = detail_rows()
        assert any(
            "source fixture-youth" in subtitle and "8–12 ans" in subtitle
            for _, subtitle in shown
        ), f"source fictive age not rendered in GTK detail: {shown!r}"
        assert any(
            "source milan" in subtitle and "8–12 ans (Milan)" in subtitle
            for _, subtitle in shown
        ), f"Milan age not rendered in GTK detail: {shown!r}"
        sources = [subtitle for title, subtitle in shown if title == "Source"]
        assert len(sources) == 2 and set(sources) == {"fixture-youth", "milan"}, (
            f"Provenances individuelles absentes du vrai Détail GTK : {shown!r}"
        )
        assert not any(title == "Sources" for title, _ in shown), (
            "Ne pas restaurer la synthèse Sources désormais obsolète"
        )
        result["detail_provenance"] = shown
        capture("06-keyboard-search-detail")
        later(160, inspect_collect_without_source)

    def inspect_collect_without_source():
        """#923: no source means a disabled GTK action, not a clickable error."""
        assert not page.view.sources, "No remote youth provider should be installed"

        def find_collect(widget):
            if isinstance(widget, Gtk.Button) and widget.get_label() == "Collecter cette source":
                return widget
            child = widget.get_first_child()
            while child is not None:
                button = find_collect(child)
                if button is not None:
                    return button
                child = child.get_next_sibling()
            return None

        button = find_collect(page.view.widget)
        assert button is not None and button.get_mapped(), "Collect action is not visible"
        assert not button.get_sensitive(), "No-source collect action must be disabled"
        reason = button.get_tooltip_text() or ""
        assert "source Jeunesse" in reason and "collecte" in reason, reason
        assert page.view.actions_hint.get_mapped(), "The no-source help must be visible"
        hint = page.view.actions_hint.get_text()
        assert "Toutes les sources" in hint and "cache" in hint.lower(), hint
        assert requests == [], "Disabled no-source action must not start HTTP"
        result["no_source_action_state"] = {
            "enabled": button.get_sensitive(),
            "tooltip": reason,
            "hint": hint,
            "status_before": page.view.status.get_text(),
            "requests_before": len(requests),
        }
        # Do not synthesize a click on an insensitive widget: that is precisely
        # the obsolete behavior which made the GTK E2E red after #923.
        later(200, verify_no_source)

    def verify_no_source():
        assert requests == [], "No remote request without a source"
        assert page.view.status.get_text() == result["no_source_action_state"]["status_before"], (
            "Inspecting an unavailable action must not inject a click or change status"
        )
        assert_rows("interest-eight")
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "interest-eight"
        result["no_source_feedback"] = result["no_source_action_state"]["hint"]
        capture("07-no-source-feedback")
        page.view.age.set_selected(0)
        page.view.query.set_text("Age inconnu")
        later(200, select_unknown_age)

    def select_unknown_age():
        assert_rows("unknown")
        page.view.master._page.selection.set_selected(0)
        later(200, verify_unknown_age)

    def verify_unknown_age():
        assert page.view.selected_book is not None
        assert page.view.selected_book.record_id == "unknown"
        shown = detail_rows()
        assert any(
            title == "Âge" and "Aucune tranche d’âge attestée" in subtitle
            for title, subtitle in shown
        ), f"Unknown age not described in GTK detail: {shown!r}"
        # #891 fallback: if there is no valid ISBN, identify the source
        # unambiguously without pretending a source-local key is an edition.
        assert ("Référence source", "fixture-youth:unknown") in shown, shown
        assert not any(title == "ISBN" for title, _ in shown), shown
        capture("08-unknown-age-detail")
        later(180, open_installed_plugin_manager)

    def plugin_states():
        return {
            row.descriptor.id: (bool(row.enabled), row.state)
            for row in core.plugins.statuses()
        }

    def open_installed_plugin_manager():
        # The real installed package and UI contribution, not a mock list.
        statuses = plugin_states()
        assert statuses.get("catalog.youth", (False, ""))[0], statuses
        assert statuses.get("ui.page.youth", (False, ""))[0], statuses
        assert statuses["ui.page.youth"][1] in {"ready", "degraded"}, statuses
        result["plugin_states_before_manager"] = statuses
        assert requests == [], "Offline Jeunesse manager must not trigger HTTP"
        window.navigate("plugins")
        later(320, plugin_manager_overview)

    def plugin_manager_overview():
        assert window.view_stack.get_visible_child_name() == "plugins"
        manager = window.plugins_page
        assert manager.get_mapped(), "Installed plugin manager not rendered on GTK"
        assert manager.plugin_search.get_mapped(), "Plugin search not visible"
        assert manager.plugin_search.get_text() == "", "Unexpected plugin-manager filter"
        capture("09-real-plugin-manager-installed")
        # Search with real XTEST keyboard events on the manager, not its API.
        device.click_widget(manager.plugin_search)
        device.type_text("youth")
        later(280, plugin_manager_filtered)

    def plugin_manager_filtered():
        manager = window.plugins_page
        assert manager.plugin_search.get_text() == "youth", manager.plugin_search.get_text()
        assert manager._view_query == "youth", "Real plugin-manager search did not apply"
        assert manager.inventory_box.get_mapped()
        def visible_labels(widget):
            labels = []
            if isinstance(widget, Gtk.Label) and widget.get_mapped():
                labels.append(widget.get_text())
            child = widget.get_first_child()
            while child is not None:
                labels.extend(visible_labels(child))
                child = child.get_next_sibling()
            return labels
        texts = visible_labels(manager.inventory_box)
        assert any("Jeunesse" in label or "youth" in label.lower() for label in texts), texts
        assert any("Activation · Activé" in label for label in texts), texts
        assert any(label.startswith("État · ") for label in texts), texts
        result["plugin_manager_youth_labels"] = texts
        capture("10-real-plugin-manager-youth-search")
        assert plugin_states() == result["plugin_states_before_manager"], (
            "Opening or filtering plugin manager unexpectedly modified activation"
        )
        window.navigate("youth")
        later(320, verify_return_after_plugin_manager)

    def verify_return_after_plugin_manager():
        assert window.view_stack.get_visible_child_name() == "youth"
        assert page.view.widget.get_mapped(), "Youth page not restored after plugin manager"
        assert_rows("unknown")
        assert not requests, "Plugin manager/navigation performed unsolicited HTTP"
        assert plugin_states() == result["plugin_states_before_manager"]
        capture("11-youth-restored-after-plugin-manager")
        result["status"] = "PASS"
        write_report()
        rc[0] = 0
        app.quit()

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.YouthE2E")
        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, 880 if args.width == 1600 else 780)
            window.present()
            device = X11Input()
            later(400, lambda: (window.navigate("youth"), later(400, start_ui)))
        def do_shutdown(self):
            if device:
                device.close()
            if original_library_search[0] is not None:
                core.library.search_known_catalog = original_library_search[0]
            if original_online_search[0] is not None:
                gui_main.progressive_online_search = original_online_search[0]
            core.close()
            work.cleanup()
            Gio.Application.do_shutdown(self)

    app = Application()
    def timed_out():
        if rc[0]:
            fail("GTK Youth probe timed out (30s)")
        return False
    GLib.timeout_add_seconds(55 if args.milan else 30, timed_out)
    app.run([sys.argv[0]])
    if not (out / "screen.json").exists():
        result.update(status="FAIL", error="GTK closed before probe completed")
        write_report()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
