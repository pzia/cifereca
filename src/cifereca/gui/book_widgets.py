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
        self._document_check = None
        self._document_decode_retry = None
        # Optional N0 paint observation: attached only by a Core source
        # adapter that already owns an exact N2 decision/lease. Never a
        # source of bibliographic evidence or provider access.
        self._on_texture_applied: Callable[[str, Path], None] | None = None

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
        self._on_texture_applied = None
        self._document_check = None
        self._document_decode_retry = None
        self._set_logical_size(self._width, self._height)
        self.picture.set_paintable(None)
        self.placeholder.set_visible(True)
        self.spinner.stop()
        self.spinner.set_visible(False)

    def show_loading(self) -> None:
        self.spinner.set_visible(True)
        self.spinner.start()

    def set_texture_applied_observer(
        self, callback: Callable[[str, Path], None] | None,
    ) -> None:
        """Bind an exact Core observer to the current request generation.

        The detail panel can install this after show_presentation() created
        its CoverView. Reset/rebind always clears a prior observer.
        """
        self._on_texture_applied = callback if callable(callback) else None

    def bind_document(
        self, service, document, *, variant: str = "list",
        priority: int = 10,
        on_texture_applied: Callable[[str, Path], None] | None = None,
        is_current: Callable[[], bool] | None = None,
        minimum_revision: int | None = None,
    ) -> None:
        """Bind N2 DocumentRef before N3; same GTK generation and decode sink.

        This path is enabled ONLY when an upstream Core producer registered
        a versioned, exact book/source snapshot in DocumentCoverService.
        The returned physical Path does not prove a document association.
        """
        from .document_cover_service import DocumentCoverService
        from ..core.book_cover_projection import CoverDocumentScope

        if not isinstance(service, DocumentCoverService):
            raise TypeError("expected shared document cover service")
        if type(document) is not CoverDocumentScope:
            raise TypeError("expected exact cover DocumentRef")
        if variant not in ("list", "detail"):
            raise ValueError("invalid cover variant")

        self.reset()
        self._loader = service.loader
        self._priority = priority
        self._on_texture_applied = (
            on_texture_applied if callable(on_texture_applied) else None
        )
        generation = self._generation
        loader = service.loader
        self.show_loading()

        def received(path: Path | None) -> None:
            # An N3 Future may finish on any worker. Keep ALL texture decoding
            # on the GTK main context; recycled cells have a newer generation.
            GLib.idle_add(
                self._apply_result, generation, loader, path,
                priority=GLib.PRIORITY_DEFAULT,
            )

        def rebind_after_qualified_source() -> bool:
            # N2 CAS already revoked the old EPUB token. The next URI must
            # be obtained by a FRESH DocumentRef read on GTK main, not by
            # painting URL bytes through the old request's source_uri.
            if generation != self._generation:
                loader.note_ui_stale()
                return False
            old = getattr(self, "_request", None)
            if old is None or not old.active:
                return False
            # A fresh N2 source event is NOT a new ownership lease. This
            # callback is posted from a worker to GTK and may execute after
            # a profile switch, query change, same-key row replacement or
            # recommendation click revocation. Validate the original
            # producer/selection BEFORE scheduling another N2 get_cover.
            # N2's old CAS token must NOT be tested here: a legitimate
            # source revision intentionally invalidates it before rebind.
            if is_current is not None:
                try:
                    still_owned = is_current() is True
                except Exception:
                    still_owned = False
                if not still_owned:
                    loader.note_ui_stale()
                    # Clear any already painted N4 from the revoked source
                    # as well as its pending N3 request. Do not affect a
                    # newer cell: the generation check above wins first.
                    self.reset()
                    self._show_missing()
                    return False
            try:
                self.bind_document(
                    service, document, variant=variant, priority=priority,
                    on_texture_applied=on_texture_applied,
                    is_current=is_current,
                    **({"minimum_revision": minimum_revision}
                       if minimum_revision is not None else {}),
                )
            except (RuntimeError, ValueError, TypeError):
                self._show_missing()
            return False

        def source_changed() -> None:
            # The EPUB qualification and N2 CAS run on a dedicated worker.
            # There is NEVER a GDK decode or GTK widget mutation there.
            GLib.idle_add(
                rebind_after_qualified_source, priority=GLib.PRIORITY_DEFAULT,
            )

        scale = max(3, int(self.get_scale_factor()))
        request = service.get_cover(
            document, variant, received,
            width=self._width * scale if variant == "list" else self._width,
            height=self._height * scale if variant == "list" else self._height,
            on_source_changed=source_changed,
            **({"minimum_revision": minimum_revision}
               if minimum_revision is not None else {}),
        )
        self._request = request
        # The worker already checked N2, but idle dispatch can run LATER
        # than a Core source revision/identity/placement invalidation.
        self._document_check = lambda: (
            request.still_current and (is_current is None or is_current())
        )
        # This is the N2-selected URI, NOT a reconstructed URL from the
        # thumbnail path. N0 paint receipts retain their exact source.
        self._url = request.source_uri

        def retry_document_decode() -> None:
            # GDK rejected physical N3 bytes, NOT their documentary N2
            # source. Re-enter the exact gateway (including EPUB extraction,
            # source tokens and subscriptions), never force a raw URI load.
            # The corrupt cache file has already been invalidated.
            if generation != self._generation:
                return
            self.bind_document(
                service, document, variant=variant, priority=priority,
                on_texture_applied=on_texture_applied,
                is_current=is_current,
                **({"minimum_revision": minimum_revision}
                   if minimum_revision is not None else {}),
            )
            # bind_document resets the widget. Bound the repair to one
            # attempt per binding/source notification, not an idle loop.
            self._decode_retry = True

        self._document_decode_retry = retry_document_decode

    def bind_url(
        self, loader: CoverLoader, url: str | None, *,
        priority: int = 10,
        on_texture_applied: Callable[[str, Path], None] | None = None,
    ) -> None:
        # A rapid GTK list rebuild can rebind the same URL before the
        # asynchronous cache/preview callback reaches the main loop.
        # Resetting here invalidates that callback's generation repeatedly,
        # leaving even mapped rows with an empty picture at HiDPI scale.
        # Preserve only the identical *in-flight* request. A completed or
        # failed request may still be refreshed by an ordinary rebind, and
        # changing loader, URL or priority retains the normal cancellation.
        if (url and self._loader is loader and self._url == url
                and getattr(self, "_document_check", None) is None
                and self._priority == priority and self.spinner.get_visible()):
            self._on_texture_applied = (
                on_texture_applied if callable(on_texture_applied) else None
            )
            return
        self.reset()
        self._on_texture_applied = (
            on_texture_applied if callable(on_texture_applied) else None
        )
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
            # Default-idle (priority 200) can starve behind repeated GTK
            # layout/timeout work while already validated covers wait to be
            # painted. Dispatch at normal main-context priority (0), keeping
            # the generation check in _apply_result as the safety barrier.
            GLib.idle_add(
                self._apply_result, generation, loader, path,
                priority=GLib.PRIORITY_DEFAULT,
            )

        if self._width<=80 and hasattr(loader,'load_preview'):
            scale=max(3,int(self.get_scale_factor()))
            self._request=loader.load_preview(self._url,received,width=self._width*scale,height=self._height*scale,priority=self._priority,force=force)
        else:
            self._request = loader.load(self._url, received, priority=self._priority, force=force)

    def _apply_result(self, generation: int, loader: CoverLoader, path: Path | None) -> bool:
        if generation != self._generation:
            loader.note_ui_stale()
            return False
        check = getattr(self, "_document_check", None)
        if check is not None:
            try:
                current = check() is True
            except Exception:
                current = False
            if not current:
                # The N2 source changed after N3's worker result. Don't
                # decode the obsolete image or keep a spinner forever.
                self.spinner.stop()
                self.spinner.set_visible(False)
                self._show_missing()
                loader.note_ui_stale()
                return False
        self.spinner.stop()
        self.spinner.set_visible(False)
        if not path:
            self._show_missing()
            return False
        applied = False
        try:
            decode_started = time.perf_counter()
            texture = Gdk.Texture.new_from_file(Gio.File.new_for_path(str(path)))
            loader.note_ui_decode((time.perf_counter() - decode_started) * 1000.0)
            self._set_logical_size(self._width, self._height)
            self.picture.set_paintable(texture)
            self.placeholder.set_visible(False)
            applied = True
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
            if generation != self._generation:
                # A synchronous GTK signal may have rebound this widget.
                # Never schedule repair or clear another binding.
                loader.note_ui_stale()
                return False
            if check is not None:
                # Document bindings must never downgrade to _start_cover_request
                # (N3 URL API). Recheck the original owner's lease AND N2 CAS
                # after GDK failed, before asking N2 for a bounded repair.
                try:
                    current = check() is True
                except Exception:
                    current = False
                repair = getattr(self, "_document_decode_retry", None)
                if current and not self._decode_retry and callable(repair):
                    self._decode_retry = True
                    try:
                        repair()
                    except (TypeError, ValueError, RuntimeError):
                        self._show_missing()
                else:
                    if not current:
                        loader.note_ui_stale()
                    self._show_missing()
            elif not self._decode_retry and self._loader is not None and self._url:
                self._decode_retry = True
                self._start_cover_request(force=True)
            else:
                self._show_missing()
        if applied and check is not None:
            try:
                current = check() is True
            except Exception:
                current = False
            if not current:
                # N2 can change even during GDK decoding. The resulting
                # texture and N0 paint receipt are not authorized.
                self._show_missing()
                loader.note_ui_stale()
                return False
        if applied:
            # A decoded physical file is not a successful GTK cover until
            # its exact N2 selection survived the FINAL paint-time check.
            loader.note_ui_applied()
        if (applied and generation == self._generation
                and self._loader is loader and self._url):
            # A 'resolved' source or cache hit is NOT a GTK paint. Emit only
            # after GDK decoded the bytes and GTK accepted the texture, and
            # never for an obsolete/recycled cover generation. Observer
            # failures must not cause a forced re-download or hide the image.
            request = getattr(self, "_request", None)
            receipt = getattr(request, "note_texture_applied", None)
            if receipt is not None:
                try:
                    receipt(path)
                except Exception:
                    pass
            observer = getattr(self, "_on_texture_applied", None)
            if observer is not None:
                try:
                    observer(self._url, path)
                except Exception:
                    pass
        return False

    def set_path(self, path: Path | None) -> bool:
        loader = self._loader
        if loader is None:
            return False
        return self._apply_result(self._generation, loader, path)


class BookListItem(GObject.Object):
    """Modèle léger pour Gtk.ListView ; aucune image n'est chargée ici."""

    __gtype_name__ = "CiferecaBookListItem"
    __gsignals__ = {
        "thumbnail-changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
    }

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

    def set_thumbnail_url(self, url: str | None) -> bool:
        """Publish a validated presentation-only cover without replacing selection.

        The producer owns URL validation and exact source binding. This signal
        only updates mapped, currently bound GTK cover cells; no business DTO
        or provider permission is ever changed.
        """
        if self.thumbnail_url == url:
            return False
        previous = self.thumbnail_url
        self.thumbnail_url = url
        # The common MasterDetailPage append-fast path compares presentation
        # signatures, not mutable BookListItem fields. Keep the Search
        # signature coherent with an in-place image update so a later
        # no-op filter render does not accidentally reset selected detail.
        content = getattr(self, "_public_row_content", None)
        if (type(content) is tuple and len(content) >= 2
                and content[0] == "search" and content[-1] == previous):
            self._public_row_content = (*content[:-1], url)
        elif (type(content) is tuple and len(content) >= 7
              and content[0] == "history" and content[6] == previous):
            # The History signature includes its cover at offset six, not
            # at the tail as for Search. Preserve stable selection on repaint.
            self._public_row_content = (
                *content[:6], url, *content[7:],
            )
        self.emit("thumbnail-changed")
        return True


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
        self._bind_epoch = 0
        self._thumbnail_item: BookListItem | None = None
        self._thumbnail_handler_id: int | None = None
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

    def _unbind_thumbnail_signal(self) -> None:
        if self._thumbnail_item is not None and self._thumbnail_handler_id is not None:
            self._thumbnail_item.disconnect(self._thumbnail_handler_id)
        self._thumbnail_item = None
        self._thumbnail_handler_id = None

    def _admit_core_local_cover(self, item: BookListItem, service, document) -> bool:
        """Publish a Core-attested N1 packet lazily for this visible GTK cell.

        Do not read every Library row, infer by URL/title, or make a source
        decision from cached N3 media. A rejected packet never falls back
        silently to a lower-priority supplier URL.
        """
        borrower_public = getattr(item, "_cover_borrower_public_c_receipt", None)
        if borrower_public is not None:
            # N2 loads only the public canonical C on its own worker.
            # Recycled GTK cells may neither borrow another identity's
            # occurrence nor reuse a stale account/source generation.
            if any(getattr(item, name, None) is not None for name in (
                "_cover_local_evidence", "_cover_history_evidence",
                "_cover_loan_supplier_evidence", "_cover_search_evidence",
            )):
                return False
            from .borrower_public_c_bridge import admit_borrower_public_c_cell
            return admit_borrower_public_c_cell(
                self.window, item, document,
            )
        plugin_source = getattr(item, "_cover_plugin_c_source", None)
        if plugin_source is not None:
            from .plugin_public_c_guard import plugin_public_c_cell_current
            return (
                getattr(item, "_cover_document_ref", None) == document
                and plugin_public_c_cell_current(
                    item, getattr(item, "_cover_plugin_c_receipt", None),
                )
            )
        known_source = getattr(item, "_cover_known_public_c_source", None)
        if known_source is not None:
            # A persisted C-first row has a valid public document but a
            # time-scoped Search association. The N3 worker may not paint
            # it after mode/identity/known-source changes.
            from .search_known_public_c_guard import known_public_c_cell_current
            return (
                getattr(item, "_cover_document_ref", None) == document
                and known_public_c_cell_current(
                    self.window, item,
                    getattr(item, "_cover_known_public_c_receipt", None),
                )
            )
        search = getattr(item, "_cover_search_evidence", None)
        if search is not None:
            from .search_document_cover_bridge import register_exact_search_document
            return register_exact_search_document(
                self.window, search.source_item, service, search, document,
            )
        supplier = getattr(item, "_cover_loan_supplier_evidence", None)
        if supplier is not None:
            # Supplier proof and EPUB Placement are incompatible owners.
            if getattr(item, "_cover_local_evidence", None) is not None:
                return False
            from cifereca.gui.loan_supplier_document_cover_bridge import (
                register_exact_loan_supplier,
            )
            return register_exact_loan_supplier(
                self.window, getattr(item, "_loan_source_item", None),
                service, supplier, document,
            )
        public_link = getattr(item, "_cover_public_c_link", None)
        if public_link is not None:
            from .local_public_c_cell_guard import local_public_c_cell_current
            owner = getattr(self.window, "service", None)
            context = (
                getattr(getattr(owner, "current_identity", None),
                        "identity_id", None),
                getattr(owner, "_account_context_epoch", None),
                getattr(owner, "_source_context_epoch", None),
            )
            # The document association must still belong to the exact
            # current Library/Recent mapping, not just a valid older proof
            # on an equal-key cell. N3 repeats the SAME predicate after IO.
            return (
                getattr(item, "_cover_document_ref", None) == document
                and local_public_c_cell_current(
                    self.window, item, service=owner, context=context,
                )
            )
        facts = getattr(item, "_cover_local_evidence", None)
        history = getattr(item, "_cover_history_evidence", None)
        if facts is not None and history is not None:
            return False  # Two incompatible provenance families on one row.
        if history is not None:
            from .history_document_cover_bridge import (
                register_exact_history_document,
            )
            return register_exact_history_document(
                self.window,
                getattr(item, "_history_source_item", None),
                service, history, document,
            )
        if facts is None:
            # Other producers can pre-register their own exact DocumentRef.
            return True
        adapter = getattr(self.window, "local_cover_adapter", None)
        if adapter is None:
            return False
        try:
            return adapter.register(service, facts) == document
        except (TypeError, ValueError, RuntimeError):
            return False

    def _search_cover_guard(self, item: BookListItem) -> dict:
        plugin_source = getattr(item, "_cover_plugin_c_source", None)
        if plugin_source is not None:
            from .plugin_public_c_guard import plugin_public_c_cell_current
            receipt = getattr(item, "_cover_plugin_c_receipt", None)
            bind_epoch = self._bind_epoch
            return {"is_current": lambda: (
                self._bind_epoch == bind_epoch
                and self._bound_key == item.key
                and self._thumbnail_item is item
                and plugin_public_c_cell_current(item, receipt)
            )}
        known_source = getattr(item, "_cover_known_public_c_source", None)
        if known_source is not None:
            from .search_known_public_c_guard import known_public_c_cell_current
            receipt = getattr(item, "_cover_known_public_c_receipt", None)
            bind_epoch = self._bind_epoch
            return {"is_current": lambda: (
                self._bind_epoch == bind_epoch
                and self._bound_key == item.key
                and self._thumbnail_item is item
                and known_public_c_cell_current(
                    self.window, item, receipt,
                )
            ), "minimum_revision": getattr(receipt, "epoch", None)}
        public_link = getattr(item, "_cover_public_c_link", None)
        if public_link is not None:
            # Bind-time owner and two Core epochs, never a mutable current
            # account lookup at N3 callback time. An A→B→A epoch cannot
            # revive an old cell, even if its opaque C URL is still cached.
            from .local_public_c_cell_guard import local_public_c_cell_current
            owner = getattr(self.window, "service", None)
            context = (
                getattr(getattr(owner, "current_identity", None),
                        "identity_id", None),
                getattr(owner, "_account_context_epoch", None),
                getattr(owner, "_source_context_epoch", None),
            )
            bind_epoch = self._bind_epoch
            return {"is_current": lambda: (
                self._bind_epoch == bind_epoch
                and self._bound_key == item.key
                and self._thumbnail_item is item
                and local_public_c_cell_current(
                    self.window, item, service=owner, context=context,
                )
            ), "minimum_revision": getattr(public_link, "epoch", None)}
        borrower = getattr(item, "_cover_borrower_public_c_receipt", None)
        if borrower is not None:
            # Also recheck after a delayed N3 callback. A recycled cell or
            # A->B->A account/source transition is not a current occurrence.
            from .borrower_public_c_bridge import admit_borrower_public_c_cell
            return {"is_current": lambda: admit_borrower_public_c_cell(
                self.window, item, getattr(item, "_cover_document_ref", None),
            ), "minimum_revision": getattr(borrower, "epoch", None)}
        proof = getattr(item, "_cover_search_evidence", None)
        from .search_document_cover_bridge import search_document_is_current
        options = {"is_current": lambda: search_document_is_current(self.window, proof)}
        revision = getattr(proof, "minimum_revision", None)
        if revision is not None:
            options["minimum_revision"] = revision
        return options

    def _thumbnail_changed(self, item: BookListItem) -> None:
        if self._thumbnail_item is not item or self._bound_key != item.key:
            return
        document = getattr(item, "_cover_document_ref", None)
        service = getattr(self.window, "document_covers", None)
        if document is not None and service is not None:
            if not self._admit_core_local_cover(item, service, document):
                self.cover.bind_url(self.cover_loader, None, priority=10)
                return
            self.cover.bind_document(
                service, document, variant="list", priority=10,
                on_texture_applied=getattr(item, "_cover_paint_observer", None),
                **(self._search_cover_guard(item)
                   if (getattr(item, "_cover_search_evidence", None) is not None
                       or getattr(item, "_cover_borrower_public_c_receipt", None)
                       is not None
                       or getattr(item, "_cover_public_c_link", None)
                       is not None
                       or getattr(item, "_cover_known_public_c_source", None)
                       is not None
                       or getattr(item, "_cover_plugin_c_source", None)
                       is not None) else {}),
            )
            return
        if getattr(item, "_cover_plugin_c_source", None) is not None:
            # Missing N2 service or revoked document is NOT permission
            # to load the old plugin thumbnail through the raw URL path.
            self.cover.bind_url(self.cover_loader, None, priority=10)
            return
        self.cover.bind_url(
            self.cover_loader, item.thumbnail_url or item.default_thumbnail_url,
            priority=10,
            on_texture_applied=getattr(item, "_cover_paint_observer", None),
        )

    def bind(self, item: BookListItem) -> None:
        self._unbind_thumbnail_signal()
        # Each materialization is a new selection, even for an ABA key.
        # A completed older metadata lookup must not decorate a later cell.
        self._bind_epoch += 1
        binding_epoch = self._bind_epoch
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
        document = getattr(item, "_cover_document_ref", None)
        service = getattr(self.window, "document_covers", None)
        if document is not None and service is not None:
            # An exact Core packet is registered only for a mapped cell.
            # A physical N3 hit never creates a book/cover association.
            if not self._admit_core_local_cover(item, service, document):
                self.cover.bind_url(self.cover_loader, None, priority=10)
            else:
                self.cover.bind_document(
                    service, document, variant="list", priority=10,
                    on_texture_applied=getattr(item, "_cover_paint_observer", None),
                    **(self._search_cover_guard(item)
                       if (getattr(item, "_cover_search_evidence", None) is not None
                       or getattr(item, "_cover_borrower_public_c_receipt", None)
                       is not None
                       or getattr(item, "_cover_public_c_link", None)
                       is not None
                       or getattr(item, "_cover_known_public_c_source", None)
                       is not None
                       or getattr(item, "_cover_plugin_c_source", None)
                       is not None) else {}),
                )
        elif getattr(item, "_cover_plugin_c_source", None) is not None:
            # A C-specific widget cannot fall back to an unqualified URI
            # just because its exact N2 service became unavailable.
            self.cover.bind_url(self.cover_loader, None, priority=10)
        elif item.thumbnail_url:
            self.cover.bind_url(
                self.cover_loader, item.thumbnail_url, priority=10,
                on_texture_applied=getattr(item, "_cover_paint_observer", None),
            )
        elif item.cover_provider and item.cover_title:
            # L'historique lié observé ne fournit aucune ThumbnailUrl. Résoudre
            # via Search.svc uniquement pour cette cellule visible/recyclée.
            self.cover.reset()
            self.cover.show_loading()
            bound_key = item.key
            from .history_cover_presentation import (
                history_cover_context, history_cover_request_scope,
                publish_history_cover,
            )
            context = history_cover_context(self.window)
            source_item = getattr(item, "_history_source_item", None)

            def resolved(url: str | None) -> None:
                if self._bound_key != bound_key or self._bind_epoch != binding_epoch:
                    return
                if (context is not None
                        and history_cover_context(self.window) != context):
                    return  # A→B→A stale async UI result.
                sources = getattr(self.window, "history_objects", None)
                if (context is not None and type(sources) is dict
                        and sources.get(bound_key) is not source_item):
                    return  # Same ID, different HistoryItem after refresh.
                # Only the same HistoryItem, profile and Core source/account
                # epochs may update its row. The BookListItem signal rebinds
                # every mapped cell; no list splice or duplicate holdings call.
                if url and context is not None and source_item is not None:
                    admitted = publish_history_cover(
                        self.window, source_item, item, url, context=context,
                    )
                    if admitted and self._thumbnail_item is item:
                        return
                    if not admitted:
                        url = None  # Never bind an unadmitted cross-row image.
                self.cover.bind_url(
                    self.cover_loader, url or item.default_thumbnail_url,
                    priority=10,
                )

            self._meta_request = self.window.history_cover_resolver.request(
                item.cover_provider,
                item.cover_record_id,
                item.cover_title,
                item.cover_author,
                item.cover_year,
                item.default_thumbnail_url,
                resolved, scope=history_cover_request_scope(context),
            )
        else:
            self.cover.bind_url(
                self.cover_loader, item.default_thumbnail_url, priority=10,
                on_texture_applied=getattr(item, "_cover_paint_observer", None),
            )
        # No source-key mutation or selection invalidation is necessary when
        # asynchronous detail discovery provides a stronger cover for this row.
        self._thumbnail_item = item
        self._thumbnail_handler_id = item.connect(
            "thumbnail-changed", self._thumbnail_changed,
        )

    def unbind(self) -> None:
        self._unbind_thumbnail_signal()
        self._bind_epoch += 1
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

