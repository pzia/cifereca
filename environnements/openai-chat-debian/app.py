#!/usr/bin/env python3
"""A real GTK4 application using fictional local catalogue data."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gtk, Gdk, Gio, GLib


@dataclass(frozen=True)
class Book:
    title: str
    author: str
    year: str
    available: bool
    color: str


BOOKS = [
    Book("Le Tour du monde en quatre-vingts jours", "Jules Verne", "1873", True, "blue"),
    Book("L'Île mystérieuse", "Jules Verne", "1874", False, "ochre"),
    Book("Vingt mille lieues sous les mers", "Jules Verne", "1870", True, "green"),
    Book("Voyage au centre de la Terre", "Jules Verne", "1864", True, "purple"),
]

CSS = b"""
window { background: #f3f5f8; color: #172435; font-family: 'DejaVu Sans'; }
headerbar { background: #ffffff; border-bottom: 1px solid #dce3ec; padding: 8px 16px; }
.brand { font-size: 17px; font-weight: 800; color: #285d9c; letter-spacing: 1px; }
.heading { font-size: 25px; font-weight: 700; }
.subheading { color: #64758b; font-size: 12px; }
.eyebrow { color: #64758b; font-size: 10px; font-weight: 700; letter-spacing: 1px; }
.pill { background: #e7eef8; color: #285d9c; border-radius: 14px; padding: 6px 12px; font-size: 11px; }
.panel { background: #fff; border-radius: 12px; border: 1px solid #dce3ec; padding: 18px; }
.book-list { background: transparent; }
.book-list row { border-radius: 8px; margin: 3px 0; padding: 9px; }
.book-list row:selected { background: #eaf1fb; color: #172435; }
.book-title { font-weight: 600; font-size: 12px; }
.book-author { color: #64758b; font-size: 11px; }
.book-cover { color: white; font-weight: 700; border-radius: 5px; padding: 9px 10px; font-size: 13px; }
.blue { background: #3b71a8; } .green { background: #3e857c; }
.ochre { background: #ac8245; } .purple { background: #7c68aa; }
.ready { color: #27765f; font-size: 10px; }
.unavailable { color: #9b7943; font-size: 10px; }
.detail-title { font-size: 19px; font-weight: 700; }
.detail-author { color: #64758b; font-size: 12px; }
.detail-meta { color: #445973; font-size: 12px; }
button.suggested-action { background: #285d9c; color: white; padding: 10px 16px; border-radius: 8px; }
button.suggested-action:hover { background: #204d83; }
button.suggested-action:disabled { background: #e4e9ef; color: #8b96a4; }
.status { color: #27765f; font-size: 12px; }
.footer { color: #64758b; font-size: 11px; }
entry { border-radius: 8px; padding: 6px 10px; }
"""


def label(text: str, css: str | None = None, **kwargs) -> Gtk.Label:
    widget = Gtk.Label(label=text, xalign=0, **kwargs)
    if css:
        widget.add_css_class(css)
    return widget


def normalized(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c))


class DemoWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="READ · GTK4 sans desktop")
        self.set_default_size(1060, 700)
        self.set_name("read-window")
        self.transfers = 0
        self.selected: Book | None = None
        self.visible_books: list[Book] = []

        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", False)
        settings.set_property("gtk-enable-animations", False)

        header = Gtk.HeaderBar()
        header.set_show_title_buttons(False)
        header.set_title_widget(label("Atelier d'interface", "subheading"))
        header.pack_start(label("READ", "brand"))
        header.pack_end(label("GTK4 · Python", "pill"))
        self.set_titlebar(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        for method in [content.set_margin_top, content.set_margin_bottom, content.set_margin_start, content.set_margin_end]:
            method(24)
        self.set_child(content)

        content.append(label("Bibliothèque de démonstration", "heading"))
        content.append(label("De vrais widgets GTK4, affichés et testés dans un écran virtuel.", "subheading"))

        tools = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.search = Gtk.SearchEntry(hexpand=True)
        self.search.set_name("search-entry")
        self.search.set_property("placeholder-text", "Rechercher un titre ou un auteur…")
        self.search.connect("search-changed", self.refresh)
        tools.append(self.search)
        tools.append(label("Disponibles uniquement", "subheading"))
        self.available = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.available.set_name("available-switch")
        self.available.connect("notify::active", self.refresh)
        tools.append(self.available)
        content.append(tools)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18, vexpand=True)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=9, hexpand=True)
        left.add_css_class("panel")
        left.set_size_request(540, -1)
        self.count = label("", "eyebrow")
        left.append(self.count)
        self.book_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE, vexpand=True)
        self.book_list.add_css_class("book-list")
        self.book_list.set_name("book-list")
        self.book_list.connect("row-selected", self.on_selection)
        left.append(self.book_list)
        body.append(left)

        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=15)
        detail.set_size_request(330, -1)
        detail.add_css_class("panel")
        detail.append(label("LIVRE SÉLECTIONNÉ", "eyebrow"))
        self.detail_title = label("", "detail-title", wrap=True, max_width_chars=26)
        self.detail_author = label("", "detail-author")
        self.detail_meta = label("", "detail-meta")
        for widget in [self.detail_title, self.detail_author, self.detail_meta]:
            detail.append(widget)
        detail.append(Gtk.Box(vexpand=True))
        self.transfer = Gtk.Button(label="Simuler le transfert")
        self.transfer.set_name("transfer-button")
        self.transfer.add_css_class("suggested-action")
        self.transfer.connect("clicked", self.on_transfer)
        detail.append(self.transfer)
        self.status = label("Prêt pour le test d'interaction.", "status", wrap=True, max_width_chars=32)
        self.status.set_name("transfer-status")
        detail.append(self.status)
        body.append(detail)
        content.append(body)
        content.append(label("Écran virtuel Xvfb  •  Rendu logiciel Cairo  •  Données locales de démonstration", "footer"))
        self.refresh()

    def refresh(self, *_args) -> None:
        query = normalized(self.search.get_text())
        self.visible_books = [
            book for book in BOOKS
            if query in normalized(book.title + " " + book.author)
            and (book.available or not self.available.get_active())
        ]
        row = self.book_list.get_first_child()
        while row is not None:
            next_row = row.get_next_sibling()
            self.book_list.remove(row)
            row = next_row
        self.count.set_text(f"{len(self.visible_books)} LIVRE(S) · CATALOGUE LOCAL")
        for index, book in enumerate(self.visible_books):
            row = Gtk.ListBoxRow()
            row.book = book
            box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            cover = label(f"0{index + 1}", "book-cover")
            cover.add_css_class(book.color)
            box.append(cover)
            names = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5, hexpand=True)
            names.append(label(book.title, "book-title", wrap=True, max_width_chars=48))
            names.append(label(f"{book.author} · {book.year}", "book-author"))
            box.append(names)
            box.append(label("EPUB" if book.available else "À acquérir", "ready" if book.available else "unavailable"))
            row.set_child(box)
            self.book_list.append(row)
        self.book_list.select_row(self.book_list.get_row_at_index(0))
        if not self.visible_books:
            self.on_selection(self.book_list, None)

    def on_selection(self, _box, row) -> None:
        self.selected = row.book if row else None
        self.detail_title.set_text(self.selected.title if self.selected else "Aucun résultat")
        self.detail_author.set_text(self.selected.author if self.selected else "Modifiez les critères de recherche.")
        self.detail_meta.set_text(f"{self.selected.year} · EPUB · {'Disponible' if self.selected.available else 'À acquérir'}" if self.selected else "")
        self.transfer.set_sensitive(bool(self.selected and self.selected.available))

    def on_transfer(self, _button) -> None:
        if self.selected and self.selected.available:
            self.transfers += 1
            self.status.set_text(f"Transfert simulé terminé.\n{self.transfers} livre(s) traité(s).")

    def state(self) -> dict:
        return {
            "search": self.search.get_text(),
            "available_only": self.available.get_active(),
            "visible_titles": [book.title for book in self.visible_books],
            "selected_title": self.selected.title if self.selected else None,
            "transfer_enabled": self.transfer.get_sensitive(),
            "transfer_count": self.transfers,
            "status": self.status.get_text(),
        }


class DemoApplication(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.read.Gtk4HeadlessPoC", flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = DemoWindow(self)
        self.window.present()


if __name__ == "__main__":
    raise SystemExit(DemoApplication().run(None))
