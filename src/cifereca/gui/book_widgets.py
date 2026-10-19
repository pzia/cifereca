from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import gi
import time

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango

from .cover_loader import CoverLoader
from .book_facets import LIST_FACET_MAX_CHARS, compact_facet_label, compact_row_meta


def _add_css(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    for name in classes:
        widget.add_css_class(name)
    return widget

def attribute_tag(label,callback=None,*,group='form',tooltip=None):
    """Compact list facet with non-hyperlink presentation.

    Real GTK runtimes use a flat button for mouse/keyboard activation. The
    label fallback keeps lightweight contract tests independent from Gtk.Button.
    """
    full_label=str(label)
    display_label=compact_facet_label(full_label)
    button_cls = getattr(Gtk, 'Button', None)
    text=Gtk.Label(label=display_label, xalign=0)
    try:
        text.set_single_line_mode(True)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_max_width_chars(LIST_FACET_MAX_CHARS)
        text.set_hexpand(True)
    except Exception:
        pass
    if callback and button_cls is not None:
        tag=button_cls()
        try:
            tag.set_child(text)
            tag.add_css_class('flat')
        except Exception:
            pass
        tag.connect('clicked', lambda _button: callback(None))
    else:
        tag=text
        if callback:
            tag.connect('activate-link', lambda _label, _uri: (callback(None), True)[1])
    try:
        tag.set_halign(Gtk.Align.START); tag.set_valign(Gtk.Align.CENTER)
    except Exception: pass
    _add_css(tag,'book-tag','tag-content' if group=='content' else 'tag-form')
    tag.set_tooltip_text(tooltip or full_label)
    return tag

class CoverView(Gtk.Overlay):
    """Vue de couverture recyclable.

    Core031 prépare en worker une image à 3x la taille logique des listes.
    Le détail garde l’original. Cache original conservé, génération vérifiée.
    """

    def __init__(self, width: int = 60, height: int = 90):
        super().__init__()
        self._width = int(width)
        self._height = int(height)
        self._missing_width = self._width
        self._missing_height = self._height
        self.set_size_request(self._width, self._height)
        _add_css(self, "cover-frame")
        self._generation = 0
        self._request = None
        self._loader: CoverLoader | None = None
        self._url: str | None = None
        self._priority = 10
        self._decode_retry = False

        self.placeholder = Gtk.Image.new_from_icon_name("image-x-generic-symbolic")
        self.placeholder.set_pixel_size(max(24, self._width // 2))
        self.placeholder.set_opacity(0.45)
        self.set_child(self.placeholder)

        self.picture = Gtk.Picture()
        self.picture.set_size_request(self._width, self._height)
        self.picture.set_can_shrink(True)
        try:
            self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        except Exception:
            pass
        self.add_overlay(self.picture)

        self.spinner = Gtk.Spinner()
        self.spinner.set_halign(Gtk.Align.CENTER)
        self.spinner.set_valign(Gtk.Align.CENTER)
        self.spinner.set_visible(False)
        self.add_overlay(self.spinner)

    def _set_logical_size(self, width: int, height: int) -> None:
        width = int(width)
        height = int(height)
        self.set_size_request(width, height)
        self.picture.set_size_request(width, height)
        self.placeholder.set_pixel_size(max(24, width // 2))

    def set_missing_size(self, width: int, height: int) -> None:
        """Use a compact placeholder only once cover absence is confirmed."""
        self._missing_width = int(width)
        self._missing_height = int(height)
        if not self._url and self._request is None:
            self._set_logical_size(self._missing_width, self._missing_height)

    def _show_missing(self) -> None:
        self.picture.set_paintable(None)
        self.placeholder.set_visible(True)
        self._set_logical_size(self._missing_width, self._missing_height)

    def reset(self) -> None:
        if self._request is not None:
            try:
                self._request.cancel()
            except Exception:
                pass
            self._request = None
        self._generation += 1
        self._loader = None
        self._url = None
        self._decode_retry = False
        self._set_logical_size(self._width, self._height)
        self.picture.set_paintable(None)
        self.placeholder.set_visible(True)
        self.spinner.stop()
        self.spinner.set_visible(False)

    def show_loading(self) -> None:
        self.spinner.set_visible(True)
        self.spinner.start()

    def bind_url(self, loader: CoverLoader, url: str | None, *, priority: int = 10) -> None:
        self.reset()
        if not url:
            self._show_missing()
            return
        self._loader = loader
        self._url = url
        self._priority = priority
        self._start_cover_request(force=False)

    def _start_cover_request(self, *, force: bool) -> None:
        if self._loader is None or not self._url:
            return
        generation = self._generation
        loader = self._loader
        self.show_loading()

        def received(path: Path | None) -> None:
            GLib.idle_add(self._apply_result, generation, loader, path)

        if self._width<=80 and hasattr(loader,'load_preview'):
            scale=max(3,int(self.get_scale_factor()))
            self._request=loader.load_preview(self._url,received,width=self._width*scale,height=self._height*scale,priority=self._priority,force=force)
        else:
            self._request = loader.load(self._url, received, priority=self._priority, force=force)

    def _apply_result(self, generation: int, loader: CoverLoader, path: Path | None) -> bool:
        if generation != self._generation:
            loader.note_ui_stale()
            return False
        self.spinner.stop()
        self.spinner.set_visible(False)
        if not path:
            self._show_missing()
            return False
        try:
            decode_started = time.perf_counter()
            texture = Gdk.Texture.new_from_file(Gio.File.new_for_path(str(path)))
            loader.note_ui_decode((time.perf_counter() - decode_started) * 1000.0)
            self._set_logical_size(self._width, self._height)
            self.picture.set_paintable(texture)
            self.placeholder.set_visible(False)
            loader.note_ui_applied()
        except Exception:
            loader.note_ui_failed()
            self.picture.set_paintable(None)
            self.placeholder.set_visible(True)
            loader.invalidate(path, decode_failure=True)
            if loader.diagnostics is not None:
                # GTK decoders can embed private cache paths or a plugin's
                # signed cover URL in exception messages. The error class is
                # also not a public diagnostic contract. No str/repr escapes.
                loader.diagnostics.add(
                    "COVER", "ui decode", "error",
                    detail={"code": "cover_decode_failed"},
                    level="detailed",
                )
            if not self._decode_retry and self._loader is not None and self._url:
                self._decode_retry = True
                self._start_cover_request(force=True)
            else:
                self._show_missing()
        return False

    def set_path(self, path: Path | None) -> bool:
        loader = self._loader
        if loader is None:
            return False
        return self._apply_result(self._generation, loader, path)


class BookListItem(GObject.Object):
    """Modèle léger pour Gtk.ListView ; aucune image n'est chargée ici."""

    __gtype_name__ = "CiferecaBookListItem"

    def __init__(
        self,
        key: str,
        title: str,
        subtitle: str = "",
        meta: str = "",
        badge: str = "",
        thumbnail_url: str | None = None,
        cover_provider: str | None = None,
        cover_record_id: str | None = None,
        cover_title: str | None = None,
        cover_author: str | None = None,
        cover_year: str | None = None,
        default_thumbnail_url: str | None = None,
        action_label: str | None = None,
        action_callback: Callable[[Gtk.Button], None] | None = None,
        action_icon_name: str | None = None,
        badge_style: str | None = None,
    ):
        super().__init__()
        self.key = key
        self.title = title or "Sans titre"
        self.subtitle = subtitle or ""
        self.meta = meta or ""
        self.badge = badge or ""
        self.badge_style = str(badge_style or "")
        self.thumbnail_url = thumbnail_url
        self.cover_provider = cover_provider
        self.cover_record_id = cover_record_id
        self.cover_title = cover_title or self.title
        self.cover_author = cover_author
        self.cover_year = cover_year
        self.default_thumbnail_url = default_thumbnail_url
        self.action_label = action_label or ""
        self.action_callback = action_callback
        self.action_icon_name = action_icon_name or 'view-more-symbolic'


class BookListCell(Gtk.Box):
    """Cellule recyclée par Gtk.ListView.

    Le bind/unbind est la clé de la performance : une couverture n'est demandée
    que lorsque GTK matérialise réellement une cellule (typiquement à l'écran
    ou juste autour de la zone visible).
    """

    def __init__(self, window: Any):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_hexpand(True)
        self.set_halign(Gtk.Align.FILL)
        self.window = window
        self.cover_loader = window.covers
        self._meta_request = None
        self._bound_key: str | None = None
        self.set_margin_top(8)
        self.set_margin_bottom(8)
        self.set_margin_start(8)
        self.set_margin_end(8)
        _add_css(self, "book-row")

        self.cover = CoverView(58, 86)
        self.append(self.cover)

        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        labels.set_hexpand(True)
        labels.set_halign(Gtk.Align.FILL)
        labels.set_valign(Gtk.Align.CENTER)
        self.append(labels)

        self.title_label = Gtk.Label(xalign=0)
        self.title_label.set_wrap(True)
        self.title_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        self.title_label.set_lines(2)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        _add_css(self.title_label, "book-title")
        labels.append(self.title_label)

        self.subtitle_label = Gtk.Label(xalign=0)
        self.subtitle_label.set_ellipsize(Pango.EllipsizeMode.END)
        _add_css(self.subtitle_label, "dim-label")
        labels.append(self.subtitle_label)
        self.author_box = Gtk.FlowBox()
        self.author_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.author_box.set_halign(Gtk.Align.FILL)
        self.author_box.set_hexpand(True)
        self.author_box.set_min_children_per_line(1)
        self.author_box.set_max_children_per_line(4)
        self.author_box.set_column_spacing(4)
        self.author_box.set_row_spacing(4)
        labels.append(self.author_box)

        self.facet_box = Gtk.FlowBox()
        self.facet_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.facet_box.set_halign(Gtk.Align.FILL)
        self.facet_box.set_hexpand(True)
        # The list projection contains at most six facets. Three shrinkable,
        # homogeneous columns therefore bound the facet block to two rows even
        # in the ~440 px desktop master pane.
        self.facet_box.set_homogeneous(True)
        self.facet_box.set_min_children_per_line(3)
        self.facet_box.set_max_children_per_line(3)
        self.facet_box.set_column_spacing(4)
        self.facet_box.set_row_spacing(4)
        labels.append(self.facet_box)
        self.meta_label = Gtk.Label(xalign=0)
        self.meta_label.set_ellipsize(Pango.EllipsizeMode.END)
        _add_css(self.meta_label, "caption", "dim-label")
        labels.append(self.meta_label)

        self.badge_label = Gtk.Label()
        self.badge_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.badge_label.set_max_width_chars(18)
        self._source_callback = None
        def source_link(_label, _uri):
            if self._source_callback: self._source_callback(None)
            return True
        self.badge_label.connect("activate-link", source_link)
        _add_css(self.badge_label, "badge")
        self.badge_label.set_valign(Gtk.Align.CENTER)
        self.append(self.badge_label)

        self.action_button = Gtk.Button()
        self.action_button.add_css_class('flat')
        self.action_button.set_valign(Gtk.Align.CENTER)
        self.action_button.set_visible(False)
        self.action_button.connect("clicked", self._action_clicked)
        self.append(self.action_button)
        self._action_callback: Callable[[Gtk.Button], None] | None = None

    def _action_clicked(self, button: Gtk.Button) -> None:
        callback = self._action_callback
        if callback is not None:
            callback(button)

    def bind(self, item: BookListItem) -> None:
        self._bound_key = item.key
        child = self.facet_box.get_first_child()
        while child:
            nxt = child.get_next_sibling(); self.facet_box.remove(child); child = nxt
        tags=getattr(item,'tags',None)
        if tags is not None:
            for facet,callback in tags:
                guarded=None
                if callable(callback):
                    guarded=lambda _b,cb=callback,key=item.key:cb(_b) if self._bound_key==key else None
                self.facet_box.insert(attribute_tag(facet.label,guarded,group=facet.group,tooltip=facet.tooltip),-1)
        else:
            for label,callback in getattr(item,'facet_actions',()):self.facet_box.insert(attribute_tag(label,callback),-1)
        self.facet_box.set_visible(bool(tags if tags is not None else getattr(item,'facet_actions',())))
        self.title_label.set_label(item.title)
        self.title_label.set_tooltip_text(item.title)
        self.subtitle_label.set_label(item.subtitle)
        authors = getattr(item, "author_actions", ())
        self.subtitle_label.set_visible(bool(item.subtitle) and not authors and not any(f.field=="author" for f,_ in (tags or [])))
        child = self.author_box.get_first_child()
        while child:
            nxt = child.get_next_sibling(); self.author_box.remove(child); child = nxt
        for name, callback in authors:
            link = Gtk.Label(xalign=0)
            link.set_markup('<a href="author">' + GLib.markup_escape_text(name) + '</a>')
            link.set_ellipsize(Pango.EllipsizeMode.END)
            link.set_max_width_chars(LIST_FACET_MAX_CHARS)
            link.set_tooltip_text(name)
            def activate(_label, _uri, cb=callback):
                cb(None); return True
            link.connect("activate-link", activate)
            self.author_box.insert(link, -1)
        self.author_box.set_visible(bool(authors))
        visible_facet_labels = [
            facet.label for facet,_callback in (tags or [])
            if getattr(facet,'field',None) != '_overflow'
        ]
        meta_text = compact_row_meta(item.meta, visible_facet_labels)
        self.meta_label.set_label(meta_text)
        self.meta_label.set_visible(bool(meta_text))
        self._source_callback = getattr(item, "source_action", None)
        for css_class in ("loan-overdue", "loan-imminent", "loan-normal"):
            self.badge_label.remove_css_class(css_class)
        badge_style = str(getattr(item, "badge_style", "") or "")
        if badge_style:
            self.badge_label.add_css_class(badge_style)
        if self._source_callback:
            self.badge_label.set_markup('<a href="source">' + GLib.markup_escape_text(item.badge) + '</a>')
        else:
            self.badge_label.set_label(item.badge)
        visible_keys = {str(label).strip().casefold() for label in visible_facet_labels}
        badge_key = str(item.badge or '').strip().casefold()
        self.badge_label.set_visible(bool(item.badge) and badge_key not in visible_keys)
        self._action_callback = item.action_callback
        try: self.action_button.set_icon_name(getattr(item, 'action_icon_name', None) or 'view-more-symbolic')
        except Exception: pass
        self.action_button.set_tooltip_text(item.action_label or '')
        self.action_button.set_visible(bool(item.action_label and item.action_callback))
        if self._meta_request is not None:
            try:
                self._meta_request.cancel()
            except Exception:
                pass
            self._meta_request = None
        if item.thumbnail_url:
            self.cover.bind_url(self.cover_loader, item.thumbnail_url, priority=10)
        elif item.cover_provider and item.cover_title:
            # L'historique lié observé ne fournit aucune ThumbnailUrl. Résoudre
            # via Search.svc uniquement pour cette cellule visible/recyclée.
            self.cover.reset()
            self.cover.show_loading()
            bound_key = item.key

            def resolved(url: str | None) -> None:
                if self._bound_key != bound_key:
                    return
                self.cover.bind_url(self.cover_loader, url or item.default_thumbnail_url, priority=10)

            self._meta_request = self.window.history_cover_resolver.request(
                item.cover_provider,
                item.cover_record_id,
                item.cover_title,
                item.cover_author,
                item.cover_year,
                item.default_thumbnail_url,
                resolved,
            )
        else:
            self.cover.bind_url(self.cover_loader, item.default_thumbnail_url, priority=10)

    def unbind(self) -> None:
        self._bound_key = None
        if self._meta_request is not None:
            try:
                self._meta_request.cancel()
            except Exception:
                pass
            self._meta_request = None
        self.cover.reset()
        self.title_label.set_label("")
        self.subtitle_label.set_label("")
        self.meta_label.set_label("")
        self.badge_label.set_label("")
        for css_class in ("loan-overdue", "loan-imminent", "loan-normal"):
            self.badge_label.remove_css_class(css_class)
        self._action_callback = None
        self._source_callback = None
        self.author_box.set_visible(False)
        self.facet_box.set_visible(False)
        self.action_button.set_label("")
        self.action_button.set_visible(False)

