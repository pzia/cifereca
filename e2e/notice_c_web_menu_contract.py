"""Separate synthetic public C fixture for real GTK multi-option web actions."""
from __future__ import annotations

from threading import get_ident
from time import monotonic
from types import SimpleNamespace as NS

from cifereca.core.notice_c_cover_index import persist_consolidated_cover_source
from cifereca.core.notice_c_web_choices import read_public_web_choices, resolve_public_web_choice
from cifereca.core.notice_cover_repository import persist_notice_cover, evict_notice_cover
from cifereca.core.notice_search_cover_producer import (
    admit_public_search_catalogue, _CORE_PUBLIC_C_WITNESS,
)
from cifereca.core.notice_source_observation import admit_source_notice
from cifereca.models import SearchResult


def seed_multiple_web_choices(db):
    """Explicit fixture relation, not a provider session or inference."""
    sources = []
    expected = {}
    for provider, record, suffix in (
        ("seller-a", "multiple-a", "one"), ("seller-b", "multiple-b", "two"),
        ("seller-c", "multiple-c", "three"),
    ):
        uri = "https://books.example.org/title/" + suffix
        row = SearchResult(
            id=record, record_id=record, title="Recette menu multiple",
            author="Auteur témoin", type="book", format="EPUB",
            publisher=None, published_at=None, description=None,
            thumbnail_url="https://covers.example.org/multiple.png",
            friendly_url=None, docbase="digital-books", medium="digital",
            scenario="search", source_provider=provider,
            source_credential="__public__", source_collection="public_catalog",
            source_account_id=None, owned=False, downloadable=False,
            download_url=None, metadata={},
        )
        row._core_notice_c_public_bibliography = _CORE_PUBLIC_C_WITNESS
        initial = admit_public_search_catalogue(
            row, identity_id="fixture", observed_at=100, public_cover_url=row.thumbnail_url,
        )
        assert initial is not None
        raw = initial.fields_copy()
        raw["search_result"]["public_web_references"] = {
            "schema": 1, "links": [
                {"url": uri, "evidence": provider + ".public_search_product_card"},
            ],
        }
        observation = admit_source_notice(
            key=initial.key, parser_id=initial.parser_id, parser_version=initial.parser_version,
            observed_at=100, raw_fields=raw, status=initial.status,
        )
        source = persist_notice_cover(
            db, observation, field_path=("search_result", "thumbnail_url"),
            cover_inventory_complete=True,
        )
        sources.append(source)
        expected[provider + " — " + record] = uri
    for source in sources:
        c = persist_consolidated_cover_source(
            db, canonical_edition_id=sources[0].observation.key.edition_id,
            source=source, relation_proof_id="fixture:multi-web",
        )
    for source in sources:
        assert evict_notice_cover(db, source.document, expected_epoch=source.epoch)
    menu = read_public_web_choices(db, c.document)
    assert menu is not None and menu.epoch == c.epoch, "multi-web retained epoch"
    assert {choice.label for choice in menu.choices} == set(expected), "multi-web retained choices"
    for choice in menu.choices:
        assert resolve_public_web_choice(db, c.document, expected_epoch=c.epoch,
                                         choice_id=choice.choice_id) == expected[choice.label], "multi-web retained resolution"
    return c.document, c.epoch, expected


def verify_multiple_web_choices(window, db, fixture, *, offline, later, guard,
                                capture, report, complete, state):
    from gi.repository import Gtk
    from cifereca.gui.detail_panel import DetailPanel
    from cifereca.gui.notice_c_web_action_controller import request_public_web_actions

    document, epoch, expected = fixture
    launches, messages = [], []
    gtk_thread = get_ident()
    generation = [0]
    host = NS(service=NS(storage=db), runner=window.runner, toast=messages.append,
              _closed=False)
    panel = DetailPanel(window)
    probe = state["probe"] = Gtk.Window(transient_for=window, title="Notice C — choix web")
    probe.set_default_size(460, 340)
    probe.set_child(panel)
    probe.present()
    label = "offline" if offline else "cold"
    deadline = monotonic() + 10

    def launch(uri, _context):
        assert get_ident() == gtk_thread
        launches.append(uri)

    assert request_public_web_actions(
        host, panel, document, expected_epoch=epoch,
        current=lambda: generation[0] == 0, Gtk=Gtk,
        Gio=NS(AppInfo=NS(launch_default_for_uri=launch)),
    )

    def poll_menu():
        menus = [widget for widget in panel._action_buttons
                 if getattr(widget, "_cifereca_action_id", None) == "open-c-public-web"]
        if not menus or not menus[0].get_mapped():
            assert monotonic() < deadline, "multi-option GTK menu timeout"
            later(40, guard(poll_menu))
            return
        menu, = menus
        assert isinstance(menu, Gtk.MenuButton)
        menu.popup()
        popover = menu.get_popover()
        # GtkScrolledWindow can insert a GtkViewport for a non-scrollable box.
        box = popover.get_child().get_child()
        if isinstance(box, Gtk.Viewport):
            box = box.get_child()
        assert isinstance(box, Gtk.Box), "multi-web GTK menu content"
        options = {}
        button = box.get_first_child()
        while button is not None:
            assert isinstance(button, Gtk.Button)
            options[button.get_label()] = button
            button = button.get_next_sibling()
        assert set(options) == set(expected)
        assert not launches and not messages
        keys = sorted(options)

        def poll_popover():
            if not all(button.get_mapped() for button in options.values()):
                assert monotonic() < deadline, "multi-option popover not mapped"
                later(40, guard(poll_popover))
                return
            capture("00-notice-c-multiple-web-" + label)
            options[keys[0]].emit("clicked")
            options[keys[1]].emit("clicked")  # second click while first is pending
            later(40, guard(lambda: poll_launch(1)))

        def poll_launch(count):
            if len(launches) < count:
                assert monotonic() < deadline, "multi-option GTK click timeout"
                later(40, guard(lambda: poll_launch(count)))
                return
            assert launches == [expected[key] for key in keys[:count]]
            assert not messages
            if count < len(keys):
                options[keys[count]].emit("clicked")
                later(40, guard(lambda: poll_launch(count + 1)))
                return
            menu.popdown()
            panel.empty("Sélection modifiée", "Menu révoqué")
            assert menu._cifereca_action_context_revoked
            generation[0] += 1
            for button in options.values():
                button.emit("clicked")
            assert launches == [expected[key] for key in keys]
            report["checks"]["P0_6397_real_gtk_multiple_web_" + label] = True
            report["notice_c_multiple_web_" + label] = {
                "choices": len(options), "browser_sink_calls": len(launches),
                "old_menu_revoked": True,
            }
            probe.destroy()
            state["probe"] = None
            window.present()
            complete()

        later(40, guard(poll_popover))

    later(40, guard(poll_menu))
