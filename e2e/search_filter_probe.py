#!/usr/bin/env python3
"""P0 #660: replay real Search filters in the running CIFERECA GTK window.

The XTEST input targets the actual GTK widgets and Core search service.
The online facet stage uses a synthetic provider response.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback
from time import monotonic
from threading import Event
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "environnements" / "openai-chat-debian"))

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gio, Gtk

from PIL import ImageGrab
from x11_input import X11Input
from cifereca.core import CiferecaCore
from cifereca.gui.main_window import MainWindow
from cifereca.models import Account, SearchResult, ItemDetail
from cifereca.providers.base import (
    ProviderSession, ProviderDescriptor, ProviderCapabilities,
)
from cifereca.plugin_api import (
    CatalogSearchFacet, CatalogSearchFacetValue, ProviderSearchResponse,
)


class _OfflineParisSession(ProviderSession):
    """Real ProviderSession shape with zero lender or network dependencies."""

    def __init__(self, credential: str, identity: str):
        super().__init__(
            descriptor=ProviderDescriptor(
                id="paris", display_name="Bibliothèques de Paris",
                description="Fixture GTK sans réseau", state="ready",
                capabilities=ProviderCapabilities(search=True, holdings=True, borrow=True),
            ),
            credential_id=credential, label="Paris hors ligne",
            credentials={}, options={},
        )
        self.connected = True
        self._accounts = [Account(
            unique_id="", identity_id=identity, provider_id="paris",
            credential_id=credential, display_name="Lecteur fictif",
        )]
        self.search_calls = 0
        self.holdings_calls = 0
        self.borrow_calls = 0

    def connect(self):
        return list(self._accounts)

    def close(self):
        self.connected = False

    def search(self, query, scope="all", page=0, field="all"):
        self.search_calls += 1
        assert query == "Serpentine"
        return [SearchResult(
            id="paris:" + self.credential_id,
            record_id="edition-" + self.credential_id,
            docbase="DILICOM", medium="digital",
            title="Serpentine", author="Mélanie Fazi", type="ebook",
            format="EPUB", publisher=None, published_at=None,
            description="Notice fictive", thumbnail_url=None,
            friendly_url=None, scenario="EBOOKS",
            source_provider="paris", source_credential=self.credential_id,
            source_collection="catalog",
        )]

    def holdings(self, item, search_terms=""):
        self.holdings_calls += 1
        assert item.source_credential == self.credential_id
        return ItemDetail(
            record_id=item.record_id, docbase=item.docbase,
            title=item.title, author=item.author, type=item.type,
            year=None, thumbnail_url=None, availability_status="available",
            availability_label="Disponible à l'emprunt", holdings=[],
            source_provider="paris", source_credential=self.credential_id,
        )

    def borrow(self, item, account):
        self.borrow_calls += 1
        raise AssertionError("No loan mutation in GTK acceptance")

    def borrow_explicit_with_claim(self, *args, **kwargs):
        self.borrow_calls += 1
        raise AssertionError("No loan claim/mutation in GTK acceptance")


class _ParisFixtureManager:
    """Add one explicit tested plugin declaration without removing the host."""

    def __init__(self, delegate):
        self.delegate = delegate

    def status(self, plugin_id):
        if plugin_id == "fixture.paris":
            return SimpleNamespace(
                enabled=True, state="ready",
                descriptor=SimpleNamespace(
                    id=plugin_id, type="provider.library",
                    instantiation="multiple",
                    capabilities=("acquisition.borrow",),
                ),
            )
        return self.delegate.status(plugin_id)

    def __getattr__(self, key):
        return getattr(self.delegate, key)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--width", type=int, choices=(1024, 1366, 1600), default=1366)
    args = parser.parse_args()
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if not os.environ.get("DISPLAY") or os.environ.get("GDK_BACKEND") != "x11":
        raise SystemExit("GTK/Xvfb X11 requis")

    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, timeout=4,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        sha = "unknown"

    core = CiferecaCore.open(no_plugins=True, activate_plugins=False)
    for i in range(36):
        # A loaded local index with two sources, two formats and two identities.
        core.storage.catalog_entry_put({
            "catalog_entry_id": f"p0-camille-{i:03d}",
            "title": f"Camille et les livres {i:03d}",
            "author": "Autrice E2E",
            "identity_id": "camille",
            "provider_id": "gallica" if i % 2 else "local",
            "item_id": f"camille-{i:03d}",
            "local_state": "remote_catalog",
            "media_format": "epub" if i % 3 else "pdf",
            "metadata": {"medium": "digital", "language": "fr"},
        })
    for i in range(4):
        core.storage.catalog_entry_put({
            "catalog_entry_id": f"p0-alex-{i:03d}",
            "title": f"Camille, livre privé {i}",
            "identity_id": "alex", "provider_id": "local",
            "item_id": f"alex-{i}", "local_state": "remote_catalog",
            "media_format": "epub", "metadata": {"medium": "digital"},
        })

    result = {
        "scenario": "search-filter-p0", "git_sha": sha,
        "backend": "GTK4/X11/Xvfb", "identities": ["camille", "alex"],
        "steps": [], "checks": {"query": True, "rows": True, "count": True,
                               "selection": True, "scroll": True, "search_requests": True},
    }
    result["p1_3687_phase"] = "setup"
    capture = None
    window = None
    device = None
    baseline = None
    started = monotonic()
    search_requests = [0]
    delayed_started, delayed_returned, delayed_release = Event(), Event(), Event()
    before_stale_delivery = [None]
    stale_deadline = [0.0]
    rc = [1]
    actions = [
        ("source", lambda: window.search_controls.provider),
        ("format", lambda: window.search_controls.types),
        ("language", lambda: window.search_controls.languages),
        ("field", lambda: window.search_controls.field),
        ("support", lambda: window.search_controls.scope),
    ]

    original_search = core.library.search_known_catalog

    def counted_search(*a, **kw):
        search_requests[0] += 1
        return original_search(*a, **kw)

    core.library.search_known_catalog = counted_search

    def verify_local_facets():
        """#3687: GTK facettes disponibles, sans appel fournisseur ni mutation."""
        from cifereca.gui.query_controls import LocalQueryControls
        from cifereca.gui.master_detail import MasterDetailPage

        # Real Gtk.Label + real shared MasterDetailPage counting method.
        # A filter that hides every loaded item still reports the observed
        # total; a genuinely empty source reports no stale total.
        count_label = Gtk.Label()
        count_page = SimpleNamespace(
            store=SimpleNamespace(get_n_items=lambda: 0),
            count_label=count_label, _total_count=0,
            _count_total_only=False,
            _item_count_label=MasterDetailPage._item_count_label,
        )
        MasterDetailPage._update_count(count_page, 7)
        assert count_label.get_label() == "0 / 7 éléments"
        MasterDetailPage._update_count(count_page, 0)
        assert count_label.get_label() == ""
        result["checks"]["zero_visible_count"] = True

        events = []
        local = LocalQueryControls(lambda: events.append("changed"), "Filtre local")
        assert not local.provider.get_visible()
        assert not local.languages.get_visible()
        assert not local.types.get_visible()
        assert not local.scope.get_visible()

        local.set_choices(
            sources=(("paris", "Paris"), ("gallica", "Gallica")),
            languages=(("fr", "Français"), ("en", "English")),
        )
        assert local.provider.get_visible() and local.languages.get_visible()
        local.provider.set_values(("gallica",))
        local.languages.set_values(("fr",))
        assert local.selected_sources() == ("gallica",)
        assert local.selected_languages() == ("fr",)
        before = len(events)
        local.set_choices(
            sources=(("gallica", "Gallica"), ("kobo", "Kobo")),
            languages=(("fr", "Français"),),
        )
        assert local.selected_sources() == ("gallica",)
        assert local.selected_languages() == ("fr",)
        assert len(events) == before  # Choices updated without a new search.

        local.set_choices(sources=(), languages=())
        assert not local.provider.get_visible() and not local.languages.get_visible()
        assert local.selected_sources() == () and local.selected_languages() == ()
        assert len(events) == before
        local.set_choices(
            sources=(("kobo", "Kobo"),), languages=(("en", "English"),),
        )
        assert local.provider.get_visible() and local.languages.get_visible()
        assert local.selected_sources() == () and local.selected_languages() == ()

        # #3687: real GTK local reset is one user-visible change, not a
        # new recommendation query. Available facet choices must survive.
        local.provider.set_values(("kobo",))
        local.languages.set_values(("en",))
        local.entry.set_text("titre")
        assert local.clear_button.get_sensitive()
        reset_events = len(events)
        local.clear_button.emit("clicked")
        assert local.filter_text() == ""
        assert local.selected_sources() == ()
        assert local.selected_languages() == ()
        assert len(events) == reset_events + 1
        assert not local.clear_button.get_sensitive()
        assert tuple(local.provider.buttons) == ("kobo",)
        assert tuple(local.languages.buttons) == ("en",)
        result["checks"]["local_filter_reset"] = True

        # #3687: one malformed provider's file format must not terminate
        # GTK facet rebuilding or obscure the attested format of another
        # already-loaded document. No provider/LLM call or entitlement.
        class OpaqueFormat:
            def __str__(self):
                raise RuntimeError("invalid supplier format")

            def __repr__(self):
                raise RuntimeError("invalid supplier format")

        valid_epub = {"medium": "digital", "format": "EPUB"}
        invalid = {"medium": "digital", "format": OpaqueFormat()}
        before_documents = len(events)
        local.set_document_choices((invalid, valid_epub))
        assert local.types.get_visible() and local.scope.get_visible()
        assert tuple(local.types.buttons) == ("epub",)
        assert len(events) == before_documents
        local.types.set_values(("epub",))
        assert local.matches_document(valid_epub)
        assert not local.matches_document(invalid)
        local.types.set_values(())
        local.set_document_choices((invalid,))
        assert not local.types.get_visible() and not local.scope.get_visible()
        assert len(events) == before_documents + 2
        result["checks"]["malformed_local_document_facets"] = True

        # Local-only hiding must never change the global Search controls.
        assert window.search_controls.provider.get_visible()
        assert window.search_controls.languages.get_visible()
        result["checks"]["local_facets"] = True

    class Application(Adw.Application):
        def __init__(self):
            super().__init__(application_id="org.cifereca.SearchFiltersP0")

        def do_activate(self):
            nonlocal window, device
            window = MainWindow(self, core.config, core.library, core.diagnostics)
            window.set_default_size(args.width - 40, {1024: 700, 1366: 780, 1600: 880}[args.width])
            window.present()
            device = X11Input()
            later(600, start_search)

        def do_shutdown(self):
            if device is not None:
                device.close()
            core.close()
            Gio.Application.do_shutdown(self)

    app = Application()

    def later(ms, callback):
        def run():
            try:
                if args.width == 1024:
                    allowed = {
                        "start_search": "start", "focus_search_entry": "focus",
                        "type_search": "type", "wait_results": "results",
                        "setup_scroll": "scroll", "set_baseline": "baseline",
                        "begin_delayed_search": "post_filters",
                    }
                    result["p1_3687_phase"] = allowed.get(
                        getattr(callback, "__name__", ""), "continuation",
                    )
                callback()
            except Exception as exc:
                if args.width == 1024:
                    try:
                        frames = [
                            frame.lineno for frame in traceback.extract_tb(exc.__traceback__)
                            if Path(frame.filename).name == "search_filter_probe.py"
                            and 1 <= frame.lineno <= 1100
                        ]
                        if frames:
                            result["p1_3687_probe_line"] = frames[-1]
                    except Exception:
                        pass
                fail(f"{type(exc).__name__}: {exc}")
            return False
        GLib.timeout_add(ms, run)

    def capture_stage(stage: str, *, popup: bool = False):
        nonlocal capture
        image = ImageGrab.grab(xdisplay=os.environ["DISPLAY"])
        if args.width == 1024:
            result["p1_3687_phase"] = "geometry"
            # An actual 1024x768 Xvfb screen and allocated GTK window, not
            # a requested window size rendered on a larger display.
            allocated_width, allocated_height = window.get_width(), window.get_height()
            result["search_1024_geometry"] = {
                "display_width": image.width, "display_height": image.height,
                "window_width": allocated_width, "window_height": allocated_height,
            }
            if allocated_width > 1024:
                # Closed numerical diagnosis of the true GTK minimum-width
                # contributors. No labels, identities, titles or URLs.
                widgets = {
                    "shell_header": window.shell_header,
                    "header_controls": window._header_controls,
                    "identity_dropdown": window.identity_dropdown,
                    "account_dropdown": window.account_dropdown,
                    "statistics_button": window.statistics_button,
                    "sidebar": window.sidebar,
                    "view_stack": window.view_stack,
                    "search_page": window.search_page,
                    "search_controls": window.search_controls,
                    "search_detail": window.search_page.detail,
                }
                measured = {}
                for key, widget in widgets.items():
                    minimum, natural, _min_baseline, _nat_baseline = widget.measure(
                        Gtk.Orientation.HORIZONTAL, -1
                    )
                    measured[key] = (int(minimum), int(natural), int(widget.get_width()))
                result["p1_3687_minimum_widths"] = measured
                print(
                    "P1_3687_GTK_MIN_WIDTHS="
                    + ",".join(
                        f"{key}:{minimum}/{natural}/{allocated}"
                        for key, (minimum, natural, allocated) in measured.items()
                    ), flush=True,
                )
            assert image.size == (1024, 768), (
                "Recette Search 1024: dimensions physiques X11 non conformes"
            )
            assert 924 <= allocated_width <= 1024, (
                "Recette Search 1024: fenêtre réelle hors budget horizontal"
            )
            assert 500 <= allocated_height <= 768, (
                "Recette Search 1024: fenêtre réelle hors budget vertical"
            )
            result["checks"]["P1_3687_search_real_1024_geometry"] = True
        path = output / f"{stage}.png"
        image.save(path)
        capture = path
        state = snapshot()
        result["steps"].append({
            "stage": stage, "popup_expected": popup,
            "screenshot": path.name,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            **state,
        })
        if popup and image.convert("RGB").getcolors(maxcolors=2) is not None:
            raise AssertionError("Capture GTK vide après ouverture filtre")

    def snapshot():
        page = window.search_page
        selected = page.selection.get_selected_item()
        adjust = page.scroll.get_vadjustment()
        return {
            "query": window.search_controls.entry.get_text(),
            "count": page.count_label.get_label(),
            "rows": [page.store.get_item(i).key for i in range(page.store.get_n_items())],
            "selected": getattr(selected, "key", None),
            "scroll": round(float(adjust.get_value()), 2),
            "search_requests": search_requests[0],
            "route": window.view_stack.get_visible_child_name(),
        }

    def check(stage):
        state = snapshot()
        if baseline is None:
            raise AssertionError("Recherche non initialisée avant filtre")
        for key in ("query", "rows", "count", "selected", "search_requests", "route"):
            if state[key] != baseline[key]:
                result["checks"][{"query": "query", "rows": "rows", "count": "count",
                                  "selected": "selection", "search_requests": "search_requests",
                                  "route": "route"}.get(key, key)] = False
                raise AssertionError(f"{stage}: {key} modifié par ouverture/fermeture du filtre")
        if abs(state["scroll"] - baseline["scroll"]) > 2:
            result["checks"]["scroll"] = False
            raise AssertionError(f"{stage}: scroll modifié par le filtre")

    def start_search():
        verify_local_facets()
        window.navigate("search")
        # The Stack transition must map Search before computing GTK/X11 bounds.
        later(400, focus_search_entry)

    def focus_search_entry():
        controls = window.search_controls
        coords = device.click_widget(controls.entry)  # real XTEST mouse click
        result["search_click"] = coords
        later(180, type_search)

    def type_search():
        controls = window.search_controls
        result["entry_mapped"] = bool(controls.entry.get_mapped())
        # GtkSearchEntry is a composite widget: its internal text child may
        # own key focus while GtkSearchEntry.has_focus() is false.
        result["search_entry_focus_grab"] = bool(controls.entry.grab_focus())
        if not controls.entry.get_mapped():
            raise AssertionError("Champ Recherche non visible/mappé sous GTK")
        device.type_text("Camille")           # real keyboard events
        device.key("Return", True)
        device.key("Return", False)
        later(220, wait_results)

    def wait_results():
        state = snapshot()
        if monotonic() - started > 12:
            active = core.library.current_identity
            result["preflight"] = {
                "identity": getattr(active, "identity_id", None),
                "catalogue_rows": len(core.library.catalog_entries()),
                "search_state": repr(window.search_controls.state()),
                "loaded_state": state,
            }
            raise AssertionError(
                "Recherche locale non chargée sous GTK : "
                f"query={state['query']!r} rows={len(state['rows'])} "
                f"requests={search_requests[0]} route={state['route']!r}"
            )
        if len(state["rows"]) < 30 or search_requests[0] < 1:
            later(150, wait_results)
            return
        if len(state["rows"]) != 36 or state["query"] != "Camille":
            raise AssertionError(f"Fixture locale inattendue : {len(state['rows'])} résultats")
        page = window.search_page
        page.selection.set_selected(3)
        later(250, setup_scroll)

    def setup_scroll():
        nonlocal baseline
        page = window.search_page
        adjustment = page.scroll.get_vadjustment()
        room = adjustment.get_upper() - adjustment.get_page_size()
        if room <= 20:
            raise AssertionError("Liste trop courte pour valider le scroll GTK")
        adjustment.set_value(min(120, room / 2))
        later(250, set_baseline)

    def set_baseline():
        nonlocal baseline
        baseline = snapshot()
        if (baseline["count"] != "36 résultats · Connu"
                or baseline["selected"] is None
                or baseline["search_requests"] != 1
                or baseline["scroll"] < 5):
            raise AssertionError(f"Avant filtre non probant : {baseline}")
        capture_stage("00-before")
        next_filter(0)

    def next_filter(index: int):
        if args.width == 1024:
            result["p1_3687_phase"] = "filter_" + str(min(index, 5))
        if index == len(actions):
            begin_delayed_search()
            return
        name, widget_factory = actions[index]
        widget = widget_factory()
        widget.grab_focus()
        # Wait for GTK to scroll the horizontal filter toolbar into view.
        later(170, lambda: open_filter(index, name, widget))

    def open_filter(index, name, widget):
        result["steps"].append({
            "stage": f"{name}-click-open",
            "input": device.click_widget(widget),
        })
        later(220, lambda: observe_open(index, name, widget))

    def observe_open(index, name, widget):
        check(f"{name} ouvert")
        if isinstance(widget, Gtk.MenuButton) and not widget.get_popover().get_mapped():
            raise AssertionError(f"{name}: popover non ouvert via XTEST")
        capture_stage(f"{index+1:02d}-{name}-open", popup=True)
        device.click_widget(widget)
        later(220, lambda: observe_closed(index, name, widget))

    def observe_closed(index, name, widget):
        check(f"{name} fermé")
        if isinstance(widget, Gtk.MenuButton) and widget.get_popover().get_mapped():
            raise AssertionError(f"{name}: popover non fermé via XTEST")
        capture_stage(f"{index+1:02d}-{name}-close")
        next_filter(index + 1)

    def begin_delayed_search():
        # Exercise the REAL search widget/controller and TaskRunner while the
        # existing local service deliberately holds an older response.
        assert snapshot()["query"] == "Camille"
        def delayed_search(*args, **kwargs):
            search_requests[0] += 1
            delayed_started.set()
            if not delayed_release.wait(4):
                raise RuntimeError("local fixture search not released")
            answer = original_search(*args, **kwargs)
            # A bounded subset makes a stale UI commit visibly different
            # from the 36 initial rows, even if the editor keeps old rows.
            assert len(answer) >= 2, "Local fixture unexpectedly empty"
            delayed_returned.set()
            return answer[:1]
        core.library.search_known_catalog = delayed_search
        window.run_search(False)
        stale_deadline[0] = monotonic() + 5
        later(60, edit_while_search_pending)

    def edit_while_search_pending():
        if not delayed_started.is_set():
            if monotonic() > stale_deadline[0]:
                raise AssertionError("Delayed local query did not start")
            later(75, edit_while_search_pending)
            return
        assert not window.search_button.get_sensitive(), "Search did not stay pending"
        # A real GTK SearchEntry edit (signal + filtered view), *without*
        # launching another query: old results may not repopulate this view.
        window.search_controls.entry.set_text("Introuvable après édition")
        # The editor itself schedules a local filter projection through GTK.
        # Comparing the old, not-yet-filtered rows against the eventual view
        # mistakes that legitimate deferred projection for a stale reply.
        # Keep the deliberately delayed provider reply HELD until the edit
        # has settled, then assert that it cannot overwrite this baseline.
        later(180, capture_edited_view_before_stale_delivery)

    def capture_edited_view_before_stale_delivery():
        assert delayed_started.is_set() and not delayed_returned.is_set()
        before_stale_delivery[0] = snapshot()
        assert before_stale_delivery[0]["query"] == "Introuvable après édition"
        delayed_release.set()
        later(90, check_delayed_search)

    def check_delayed_search():
        if not delayed_returned.is_set() or not window.search_button.get_sensitive():
            if monotonic() > stale_deadline[0]:
                raise AssertionError("Delayed local search did not complete")
            later(80, check_delayed_search)
            return
        after = snapshot()
        before = before_stale_delivery[0]
        for key in ("query", "rows", "count", "selected", "route"):
            assert after[key] == before[key], (
                "Stale local result mutated GTK view after query edit", key,
                before[key], after[key],
            )
        assert search_requests[0] == 2, "Unexpected third search after editing"
        result["checks"]["stale_local_reply_ignored_after_query_edit"] = True
        result["stale_local_reply"] = {
            "pending_request_count": search_requests[0],
            "ui_query": after["query"],
            "rows_unchanged": True,
        }
        core.library.search_known_catalog = counted_search
        capture_stage("06-stale-local-reply-ignored")
        begin_response_facets()

    facet_queries = []

    def begin_response_facets():
        def search_facets(query, _scope, **_options):
            facet_queries.append(query)
            return ProviderSearchResponse([], (
                CatalogSearchFacet("CATALOGUE", 0, "Subj", "Sujets", (
                    CatalogSearchFacetValue("Théâtre", 7, False),
                )),
                CatalogSearchFacet("EBOOKS", 1, "Kind", "Types"),
            ))

        core.library._sessions["synthetic-facets"] = SimpleNamespace(
            provider_id="synthetic-facets", credential_id="fixture", connected=True,
            descriptor=SimpleNamespace(display_name="Catalogue de test"),
            capabilities=SimpleNamespace(search=True),
            search_with_response_facets=search_facets, close=lambda: None,
        )
        window.search_controls.entry.set_text("Facettes")
        assert not window._search_response_facets.get_visible()
        later(100, lambda: (
            device.click_widget(window.search_global_button),
            later(100, wait_response_facets),
        ))

    def wait_response_facets():
        button = window._search_response_facets
        if not button.get_mapped():
            later(100, wait_response_facets)
            return
        assert facet_queries == ["Facettes"]
        assert window.search_page.store.get_n_items() == 0
        device.click_widget(button)
        later(200, check_response_facets)

    def check_response_facets():
        popover = window._search_response_facets.get_popover()
        assert popover.get_mapped(), "Le bouton XTEST n’a pas ouvert les facettes"
        content = popover.get_child().get_child()
        # GtkScrolledWindow wraps a non-scrollable child in a GtkViewport.
        if isinstance(content, Gtk.Viewport):
            content = content.get_child()
        labels = [item.get_label() for item in content if isinstance(item, Gtk.Label)]
        assert any("Filtrage distant non vérifié" in label for label in labels)
        assert "Catalogue de test · CATALOGUE · page 1" in labels
        assert "Catalogue de test · EBOOKS · page 2" in labels
        assert "Types · valeurs non vérifiées" in labels
        expander, = (item for item in content if isinstance(item, Gtk.Expander))
        device.click_widget(expander)
        later(200, lambda: check_response_facet_values(expander))

    def check_response_facet_values(expander):
        assert expander.get_expanded()
        assert expander.get_child().get_label() == "Théâtre · 7"
        assert facet_queries == ["Facettes"]
        capture_stage("07-response-facets", popup=True)
        window.search_controls.entry.set_text("Autre recherche")
        later(200, check_response_facets_cleared)

    def check_response_facets_cleared():
        assert not window._search_response_facets.get_visible()
        assert facet_queries == ["Facettes"]
        result["checks"]["response_facets_descriptive_and_scoped"] = True
        result["p0_4673_stage"] = "fixture_setup"
        begin_paris_profiles()

    # P0 #4673: real GTK Search + Core session routing with two owned Paris
    # accounts. UI clicks and selection are native; all provider I/O is fake.
    paris = {}
    paris_deadline = [0.0]

    def begin_paris_profiles():
        from cifereca.gui.query_model import QueryState
        service = core.library
        saved = [dict(row) for row in core.config.identities()]
        assert {"camille", "alex"} <= {row["id"] for row in saved}
        for row in saved:
            if row["id"] in {"camille", "alex"}:
                row["primary_paris_connection_id"] = "card-" + row["id"]
        core.config.set("identities", saved)
        core.config.set("providers.accounts", [
            {"id": "card-" + identity, "provider": "paris",
             "identity_id": identity, "enabled": True}
            for identity in ("camille", "alex")
        ])
        camille = _OfflineParisSession("card-camille", "camille")
        alex = _OfflineParisSession("card-alex", "alex")
        paris.update(camille=camille, alex=alex)
        service._sessions.pop("synthetic-facets", None)
        # Bob first is essential: a provider-id-first selector would return
        # Alex's credential under Camille, reproducing the original bug.
        service._sessions[service._session_key("paris", "card-alex")] = alex
        service._sessions[service._session_key("paris", "card-camille")] = camille
        service.accounts = [*camille._accounts, *alex._accounts]
        for identity in ("camille", "alex"):
            service.storage.provider_connection_put({
                "identity_id": identity, "provider_id": "paris",
                "credential_id": "card-" + identity,
                "label": "Paris fictif", "enabled": True, "metadata": {},
            })
        service._provider_plugin_ids["paris"] = "fixture.paris"
        service._plugin_manager = _ParisFixtureManager(service._plugin_manager)
        service._mirror_remote_results = lambda *_a, **_kw: 0
        service.refresh_identity_loans = lambda **_kw: []
        window.search_controls.set_state(QueryState(text="Serpentine"), notify=False)
        window.navigate("search")
        assert service.current_identity.identity_id == "camille"
        assert not any(s.search_calls for s in paris.values())
        result["p0_4673_stage"] = "initial_camille"
        search_paris("camille")

    def search_paris(identity):
        # One actual GTK button click, not a direct call to the selector.
        result["p0_4673_stage"] = "query_" + identity
        assert core.library.current_identity.identity_id == identity
        assert window.search_global_button.get_mapped()
        paris_deadline[0] = monotonic() + 6
        device.click_widget(window.search_global_button)
        later(100, lambda: await_paris_results(identity))

    def await_paris_results(identity):
        result["p0_4673_stage"] = "await_" + identity
        credential = "card-" + identity
        rows = list(window._remote_search_results)
        if (len(rows) != 1 or rows[0].source_credential != credential
                or window.search_page.store.get_n_items() != 1):
            if monotonic() < paris_deadline[0]:
                later(80, lambda: await_paris_results(identity))
                return
            raise AssertionError("Paris exact source not visible in real GTK Search")
        assert rows[0].source_provider == "paris"
        window.search_page.selection.set_selected(0)
        later(180, lambda: check_paris_action(identity))

    def check_paris_action(identity):
        result["p0_4673_stage"] = "action_" + identity
        source = paris[identity]
        book = window._remote_search_results[0]
        assert book.source_credential == source.credential_id
        # Read-only, exact Core diagnostic: no supplier GET or POST. Publish
        # only the closed code through a source-reviewed allowlist in runner.
        diagnosis = core.library.paris_borrow_diagnostic(book)
        result["p0_4673_local_reason"] = diagnosis["reason_code"]
        result["p0_4673_stage"] = "admission_" + identity
        assert diagnosis["local_action_possible"] is True
        assert core.library.can_borrow_paris(book) is True
        result["p0_4673_stage"] = "button_" + identity
        actions = [
            button for button in window.search_page.detail._action_buttons
            if getattr(button, "_cifereca_action_label", None) == "Emprunter"
        ]
        if not actions or source.holdings_calls == 0:
            if monotonic() < paris_deadline[0]:
                later(90, lambda: check_paris_action(identity))
                return
            result["p0_4673_stage"] = (
                "holdings_" + identity if source.holdings_calls == 0
                else "button_" + identity
            )
            raise AssertionError("Current Paris account has no GTK borrow action")
        assert len(actions) == 1
        assert source.borrow_calls == 0
        assert paris["alex" if identity == "camille" else "camille"].borrow_calls == 0
        result["checks"]["P0_4673_gtk_" + identity + "_exact_borrow_action"] = True
        capture_stage(
            "10-paris-camille-return"
            if identity == "camille" and paris.get("returned_from_alex")
            else "08-paris-" + identity
        )
        if identity == "alex":
            disconnect_alex()
            return
        if identity == "camille" and not paris.get("returned_from_alex"):
            old_button = actions[0]
            window._activate_identity("alex")
            assert window._remote_search_results == []
            assert window.search_page.store.get_n_items() == 0
            assert old_button.get_mapped() is False
            later(170, begin_alex_search)
        else:
            result["checks"]["P0_4673_gtk_A_B_A_no_cross_account"] = True
            result["p0_4673_stage"] = "complete"
            finish()

    def begin_alex_search():
        result["p0_4673_stage"] = "switch_alex"
        from cifereca.gui.query_model import QueryState
        assert core.library.current_identity.identity_id == "alex"
        window.search_controls.set_state(QueryState(text="Serpentine"), notify=False)
        search_paris("alex")

    def disconnect_alex():
        result["p0_4673_stage"] = "disconnect_alex"
        # Kept read-only: no supplier login/logout or account switch HTTP.
        paris["alex"].connected = False
        before = paris["alex"].search_calls
        paris["alex_prior_queries"] = before
        paris["camille_prior_queries"] = paris["camille"].search_calls
        paris["camille_prior_holdings"] = paris["camille"].holdings_calls
        assert core.library.current_identity.identity_id == "alex"
        # Force a new request with the exact same text. The former result,
        # actions and cache must be cleared BEFORE the worker finishes.
        device.click_widget(window.search_global_button)
        paris_deadline[0] = monotonic() + 6
        # XTEST injects asynchronously: the GTK event loop must first process
        # the physical button click before checking synchronous action removal.
        later(100, check_disconnected_alex_cleared)

    def check_disconnected_alex_cleared():
        result["p0_4673_stage"] = "clear_disconnected"
        # #4909: the old Alex notice must vanish, but the fresh Camille
        # bibliography may already have arrived. Do not mistake the new
        # shared catalogue for an illegal cross-profile loan.
        rows = list(window._remote_search_results)
        assert all(
            row.source_credential == "card-camille"
            and row.metadata.get("catalogue_access") == "shared_read_only"
            for row in rows
        )
        assert window.search_page.store.get_n_items() <= 1
        assert not any(
            getattr(button, "_cifereca_action_label", None) == "Emprunter"
            for button in window.search_page.detail._action_buttons
        )
        later(130, finish_disconnected_alex)

    def finish_disconnected_alex():
        result["p0_4673_stage"] = "finish_disconnected"
        if not window.search_global_button.get_sensitive():
            if monotonic() < paris_deadline[0]:
                later(100, finish_disconnected_alex)
                return
            raise AssertionError("Paris disconnected search did not finish")
        rows = list(window._remote_search_results)
        if len(rows) != 1 or window.search_page.store.get_n_items() != 1:
            if monotonic() < paris_deadline[0]:
                later(100, finish_disconnected_alex)
                return
            raise AssertionError("Paris shared bibliography not visible after disconnect")
        book = rows[0]
        assert book.source_credential == "card-camille"
        assert book.metadata.get("catalogue_access") == "shared_read_only"
        assert book.owned is False and book.downloadable is False
        assert core.library.paris_borrow_diagnostic(book)["local_action_possible"] is False
        assert core.library.can_borrow_paris(book) is False
        assert not any(
            getattr(button, "_cifereca_action_label", None) == "Emprunter"
            for button in window.search_page.detail._action_buttons
        )
        assert paris["alex"].search_calls == paris["alex_prior_queries"]
        assert paris["camille"].search_calls > paris["camille_prior_queries"]
        assert paris["camille"].holdings_calls == paris["camille_prior_holdings"]
        assert paris["camille"].borrow_calls == paris["alex"].borrow_calls == 0
        result["checks"]["P1_4909_gtk_shared_bibliography_no_foreign_rights"] = True
        capture_stage("09-paris-shared-bibliography")
        paris["returned_from_alex"] = True
        window._activate_identity("camille")
        assert window._remote_search_results == []
        later(170, begin_return_to_camille)

    def begin_return_to_camille():
        result["p0_4673_stage"] = "return_camille"
        from cifereca.gui.query_model import QueryState
        window.search_controls.set_state(QueryState(text="Serpentine"), notify=False)
        search_paris("camille")

    def finish():
        result["status"] = "PASS"
        result["duration_seconds"] = round(monotonic() - started, 2)
        result["before"] = baseline
        if capture is not None:
            shutil.copyfile(capture, output / "screen.png")
        write_report()
        rc[0] = 0
        app.quit()

    def fail(message):
        result["status"] = "FAIL"
        result["error"] = message
        if window is not None:
            try:
                result["failure_state"] = snapshot()
            except Exception:
                pass
        result["duration_seconds"] = round(monotonic() - started, 2)
        if capture is not None:
            shutil.copyfile(capture, output / "screen.png")
        write_report()
        app.quit()

    def write_report():
        (output / "screen.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    GLib.timeout_add_seconds(50, lambda: (fail("timeout GTK P0 (50s)"), False)[1] if rc[0] else False)
    app.run([sys.argv[0]])
    if not (output / "screen.json").is_file():
        result["status"] = "FAIL"
        result["error"] = "application GTK fermée avant preuve"
        write_report()
    return rc[0]


if __name__ == "__main__":
    raise SystemExit(main())
