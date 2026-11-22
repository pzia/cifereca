"""Shared GTK detail rendering over the explicitly supplied window host.

Uses the existing neutral projection, action widgets and CoverView/loader.
No runtime MainWindow import or alternate plugin projection is introduced.
"""
from __future__ import annotations

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from typing import TYPE_CHECKING, Iterable
from gi.repository import Adw, Gdk, Gtk, Pango
from ..catalogue_link import safe_document_link_url
from ..core.search import file_format
from ..descriptions import description_text
from ..book_detail_metadata import canonical_book_isbn, safe_detail_value
from ..models import HistoryItem, ItemDetail, Loan, SearchResult
from .book_widgets import CoverView
from .action_widgets import AdaptiveActionBar
from .query_model import result_row
from ..plugin_api import UiBookDetailPresentation, document_links
from .detail_selection_perf import current_detail_trace
from .detail_model import fr_date, text_or_dash, detail_display_value, detail_language, compact_availability_label, compact_detail_rows, detail_subtitle_lines, book_detail_presentation, search_detail_borrow_action_candidate, search_detail_support_label, catalogue_availability_label
from .action_widgets import clear_box, add_css
from .loan_download_status import loan_download_unavailable_message
from ..core.explicit_loan_acsm_visibility import explicit_loan_acsm_action_available
from .loan_view import loan_due_detail_value


if TYPE_CHECKING:
    from .main_window import MainWindow


def _commercial_search_usage(item: SearchResult, service=None, *, purchase_evidence=None) -> str:
    """Read-only commercial scope; a public notice never proves ownership.

    Supplier search rows never prove download permission. Rendering is pure:
    no Vault, provider account connection or remote request is performed.
    """
    # An offline notice is documentary, independently of the supplier or
    # collection. It cannot establish a currently deliverable private offer.
    if (item.metadata or {}).get("search_provenance") == "known_catalog_stale":
        return "Notice connue hors ligne · droits non actualisés"
    # Scope is documentary, not a purchase or delivery authorization.
    if (item.source_credential == "__public__"
            and item.source_collection == "public_catalog"):
        return "Catalogue commercial public · aucun achat attesté"
    if item.source_collection == "library":
        return ("Notice de bibliothèque · aucun droit d'achat attesté"
                " · disponibilité à vérifier auprès du fournisseur")
    return ""

class DetailPanel(Gtk.Box):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self._meta_request = None
        # Exact last rendered remote Search detail; never an authorization cache.
        self._rendered_search_detail = None
        self._pending_action_focus_key = ""
        # Used only during one synchronous local-detail render. Never a
        # profile-wide association or secondary documentary cache.
        self._next_cover_evidence = None
        self._next_cover_document_ref = None
        # Single-render borrowed-public C guard, never a retained GTK proof.
        self._next_borrower_public_c_receipt = None
        self._next_local_public_c_check = None
        self.set_orientation(Gtk.Orientation.VERTICAL)
        self.set_vexpand(True)
        self.action_scroll = AdaptiveActionBar(
            fraction=1.0,
            source=self,
            overflow=True,
            preserve_primary_label=True,
        )
        self._action_bar = self.action_scroll.box
        self._action_bar.add_css_class('toolbar')
        self._action_bar.add_css_class('detail-actions')
        self._action_bar.set_margin_start(12); self._action_bar.set_margin_end(12)
        self.append(self.action_scroll)
        self.body_scroll = Gtk.ScrolledWindow()
        self.body_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.body_scroll.set_propagate_natural_height(False)
        self.body_scroll.set_min_content_height(0)
        self.body_scroll.set_min_content_width(240)
        self.body_scroll.set_vexpand(True); self.body_scroll.set_hexpand(True)
        self.append(self.body_scroll)
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.content.set_margin_top(14)
        self.content.set_margin_bottom(14)
        self.content.set_margin_start(16)
        self.content.set_margin_end(16)
        self.body_scroll.set_child(self.content)
        self.empty("Sélectionnez un livre", "Les détails et actions apparaîtront ici.")

    def _cancel_meta_request(self) -> None:
        if self._meta_request is not None:
            try:
                self._meta_request.cancel()
            except Exception:
                pass
            self._meta_request = None

    def empty(self, title: str, description: str, icon: str = "book-open-symbolic") -> None:
        self._rendered_search_detail = None
        self._private_series_request_token = None
        self._cancel_meta_request()
        self._pending_action_focus_key = ""
        clear_box(self.content)
        self._clear_actions()
        status = Adw.StatusPage()
        status.set_title(title)
        status.set_description(description)
        status.set_icon_name(icon)
        status.set_vexpand(True)
        self._append_content(status)

    def _bind_cover(
        self, cover: CoverView, thumbnail: str | None, *,
        cover_evidence=None,
    ) -> None:
        """Only an attested N1 source can select the shared N2/N3 path.

        Do not downgrade a proved embedded EPUB to a warm supplier URL if
        the scoped Core evidence was invalidated or N3 cannot materialize it.
        The legacy URL path applies only when no Core proof was supplied.
        """
        if cover_evidence is None:
            cover.bind_url(self.window.covers, thumbnail, priority=0)
            return
        from cifereca.gui.loan_supplier_document_cover_bridge import (
            VerifiedLoanSupplierCover, register_exact_loan_supplier,
        )
        if type(cover_evidence) is VerifiedLoanSupplierCover:
            from .document_cover_service import DocumentCoverService
            service = getattr(self.window, "document_covers", None)
            if (type(service) is DocumentCoverService
                    and register_exact_loan_supplier(
                        self.window, cover_evidence.source_item, service,
                        cover_evidence, cover_evidence.document,
                    )):
                cover.bind_document(
                    service, cover_evidence.document, variant="detail", priority=0,
                )
            else:
                cover.bind_url(self.window.covers, None, priority=0)
            return
        from ..core.local_cover_evidence import VerifiedLocalCoverEvidence
        from .local_cover_adapter import LocalCoverEvidenceAdapter
        from .document_cover_service import DocumentCoverService
        from .history_document_cover_bridge import (
            VerifiedHistoryCoverEvidence, register_exact_history_document,
        )

        from .search_document_cover_bridge import (
            VerifiedSearchDocumentCover, register_exact_search_document,
            search_document_is_current,
        )

        if type(cover_evidence) is VerifiedSearchDocumentCover:
            service = getattr(self.window, "document_covers", None)
            document = cover_evidence.document
            if (type(service) is DocumentCoverService
                    and register_exact_search_document(
                        self.window, cover_evidence.source_item,
                        service, cover_evidence, document,
                    )):
                cover.bind_document(
                    service, document, variant="detail", priority=0,
                    is_current=lambda: search_document_is_current(self.window, cover_evidence),
                    **({"minimum_revision": cover_evidence.minimum_revision}
                       if cover_evidence.minimum_revision is not None else {}),
                )
            else:
                cover.bind_url(self.window.covers, None, priority=0)
            return

        if type(cover_evidence) is VerifiedHistoryCoverEvidence:
            service = getattr(self.window, "document_covers", None)
            document = cover_evidence.document
            if (type(service) is DocumentCoverService
                    and register_exact_history_document(
                        self.window, cover_evidence.source_item,
                        service, cover_evidence, document,
                    )):
                cover.bind_document(
                    service, document, variant="detail", priority=0,
                )
            else:
                # Never paint a cached URL for a stale/foreign occurrence.
                cover.bind_url(self.window.covers, None, priority=0)
            return

        adapter = getattr(self.window, "local_cover_adapter", None)
        service = getattr(self.window, "document_covers", None)
        if (type(cover_evidence) is not VerifiedLocalCoverEvidence
                or type(adapter) is not LocalCoverEvidenceAdapter
                or type(service) is not DocumentCoverService):
            cover.bind_url(self.window.covers, None, priority=0)
            return
        try:
            document = adapter.register(service, cover_evidence)
            cover.bind_document(service, document, variant="detail", priority=0)
        except (TypeError, ValueError, RuntimeError):
            cover.bind_url(self.window.covers, None, priority=0)

    def _cover_and_title(
        self,
        title: str,
        subtitle: str | None,
        thumbnail: str | None,
        badges: Iterable[str] = (),
        *,
        essential=(),
    ) -> CoverView:
        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        header.add_css_class("detail-heading")
        cover = CoverView(112, 168)
        cover.set_missing_size(40, 60)
        cover.set_halign(Gtk.Align.START)
        cover.set_valign(Gtk.Align.START)
        header.append(cover)
        evidence = getattr(self, "_next_cover_evidence", None)
        document = getattr(self, "_next_cover_document_ref", None)
        if evidence is not None:
            # An independently attested local/search N1 source takes priority.
            self._bind_cover(cover, thumbnail, cover_evidence=evidence)
        elif document is not None:
            # Borrower C is public but its association with a Loan/History
            # item is private and time-scoped. Unlike a generic public Search
            # ref, a delayed N3 return MUST recheck that exact occurrence.
            borrower = getattr(
                self, "_next_borrower_public_c_receipt", None,
            )
            if borrower is not None:
                from .borrower_public_c_bridge import (
                    bind_borrower_public_c_detail,
                )
                bind_borrower_public_c_detail(
                    self.window, cover, document, borrower,
                )
            else:
                from .declared_document_cover import bind_declared_document_cover
                bind_declared_document_cover(
                    cover, self.window.covers,
                    getattr(self.window, "document_covers", None), document,
                    variant="detail", priority=0,
                    is_current=getattr(
                        self, "_next_local_public_c_check", None,
                    ),
                    **({"minimum_revision": self._next_public_c_minimum_revision}
                       if getattr(self, "_next_public_c_minimum_revision", None)
                       is not None else {}),
                )
        else:
            cover.bind_url(self.window.covers, thumbnail, priority=0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        text.set_hexpand(True)
        text.set_valign(Gtk.Align.START)
        header.append(text)
        label = Gtk.Label(label=title or "Sans titre", xalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(3)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        label.set_tooltip_text(title or "Sans titre")
        add_css(label, "detail-title")
        text.append(label)
        if subtitle:
            sub = Gtk.Label(label=subtitle, xalign=0)
            sub.set_wrap(True)
            sub.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            sub.set_lines(2)
            sub.set_ellipsize(Pango.EllipsizeMode.END)
            sub.set_tooltip_text(subtitle)
            add_css(sub, "detail-subtitle", "dim-label")
            text.append(sub)
            self._subtitle_label = sub
        else:
            self._subtitle_label = None
        badges_box = Gtk.FlowBox()
        badges_box.set_selection_mode(Gtk.SelectionMode.NONE)
        badges_box.set_halign(Gtk.Align.START)
        badges_box.set_hexpand(True)
        badges_box.set_min_children_per_line(1)
        badges_box.set_max_children_per_line(4)
        badges_box.set_column_spacing(6)
        badges_box.set_row_spacing(6)
        for value in badges:
            if not value:
                continue
            chip = Gtk.Label(label=value, xalign=0)
            chip.set_wrap(True)
            chip.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            chip.set_tooltip_text(str(value))
            add_css(chip, "badge")
            badges_box.insert(chip, -1)
        if badges_box.get_first_child() is not None:
            text.append(badges_box)
        essential_grid = None
        if essential:
            essential_grid = self._group(
                "Essentiel", [(field.label, field.value) for field in essential],
                fields=essential, parent=text,
            )

        def arrange(width):
            side_by_side = width >= 400
            orientation = Gtk.Orientation.HORIZONTAL if side_by_side else Gtk.Orientation.VERTICAL
            if header.get_orientation() != orientation:
                header.set_orientation(orientation)
            if essential_grid is not None and len(essential) > 1:
                # Budget for the full cover even if its placeholder is smaller.
                text_width = width - 128 if side_by_side else width
                columns = 3 if text_width >= 690 else 2 if text_width >= 400 else 1
                if essential_grid.get_min_children_per_line() != columns:
                    essential_grid.set_min_children_per_line(columns)

        self._observe_detail_width(header, arrange)
        self._append_content(header)
        return cover


    def _observe_detail_width(self, widget, arrange):
        """Reflow mapped content in place, without a continuous frame tick."""
        clock = handler = last_width = None

        def painted(_clock):
            nonlocal last_width
            # The viewport is not widened by a child's minimum request. This
            # also lets an expanded two-column grid shrink again reliably.
            width = (self.body_scroll.get_child().get_width()
                     - self.content.get_margin_start() - self.content.get_margin_end())
            if width > 0 and width != last_width:
                last_width = width
                arrange(width)

        def mapped(_widget):
            nonlocal clock, handler
            clock = widget.get_frame_clock()
            if clock is not None:
                handler = clock.connect("after-paint", painted)
                painted(clock)

        def unmapped(_widget):
            nonlocal clock, handler, last_width
            if clock is not None and handler is not None:
                clock.disconnect(handler)
            clock = handler = last_width = None

        widget.connect("map", mapped)
        widget.connect("unmap", unmapped)

    def _group(self, title: str, rows: list[tuple[str, str]], *, fields=(), parent=None, field_actions=True):
        from cifereca.gui.detail_field_provenance import annotate_detail_field
        description_property = getattr(getattr(Gtk, "AccessibleProperty", None), "DESCRIPTION", None)
        if title == "Bibliothèques et accès":
            # #4682 / mockup #566: the projection already groups exact
            # placements, source references and rights by profile. Present
            # each attested line separately, not as one raw paragraph.
            group = Adw.PreferencesGroup()
            group.set_title(title)
            for profile, description in rows:
                if not description:
                    continue
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                card.add_css_class("detail-profile-card")
                heading = Gtk.Label(label=str(profile), xalign=0)
                heading.add_css_class("heading")
                card.append(heading)
                for line in str(description).splitlines():
                    if not line.strip():
                        continue
                    kind, marker, value = line.partition(": ")
                    record = Adw.ActionRow()
                    record.set_use_markup(False)
                    record.set_title(kind.strip() if marker else "Information")
                    compact = value if marker else line
                    record.set_subtitle(compact)
                    record.set_subtitle_lines(1)
                    record.set_tooltip_text(line)
                    record.add_css_class("detail-access-line")
                    card.append(record)
                group.add(card)
            if parent is None:
                self._append_content(group)
            else:
                parent.append(group)
            return
        if title == "Détail":
            # Explicit and local-only. Per-element actions; no network on display.
            group = Adw.PreferencesGroup()
            expander = Adw.ExpanderRow()
            expander.set_title("Détail")
            expander.set_expanded(bool(getattr(self, "_detail_expanded", False)))

            def save_expanded(widget, _pspec):
                self._detail_expanded = bool(widget.get_expanded())

            expander.connect("notify::expanded", save_expanded)
            grid = Gtk.FlowBox()
            grid.add_css_class("detail-fields")
            grid.set_selection_mode(Gtk.SelectionMode.NONE)
            grid.set_min_children_per_line(1)
            grid.set_max_children_per_line(2)
            grid.set_homogeneous(True)
            grid.set_column_spacing(8)
            grid.set_row_spacing(4)
            visible_count = 0
            for index, (label, value) in enumerate(rows):
                safe_value = safe_detail_value(value)
                if not safe_value:
                    continue
                field = fields[index] if index < len(fields) else None
                kind = getattr(field, "kind", "copy")
                row = Adw.ActionRow()
                # Metadata is plain text, never Pango markup. In particular
                # an ordinary ampersand must not erase the visible subtitle.
                row.set_use_markup(False)
                display_value, full_value = detail_display_value(label, safe_value)
                row.set_title(label)
                row.set_subtitle(display_value)
                row.set_subtitle_lines(0)
                annotate_detail_field(row, label, full_value, field,
                                      description_property=description_property)

                if field_actions:
                    self._add_metadata_actions(row, label, safe_value, kind)
                row.set_size_request(210, -1)
                grid.insert(row, -1)
                visible_count += 1
            if visible_count:
                # Count only rows admitted by the safety filter.  A rejected
                # URL/credential must not leak through the subtitle or an
                # otherwise empty detail section.
                expander.set_subtitle(f"{visible_count} champ(s) bibliographiques")
                expander.add_row(grid)
                if visible_count > 1:
                    def arrange(width):
                        columns = 2 if width >= 490 else 1
                        if grid.get_min_children_per_line() != columns:
                            grid.set_min_children_per_line(columns)
                    self._observe_detail_width(grid, arrange)
                group.add(expander)
                self._append_content(group)
            return
        group = Adw.PreferencesGroup()
        # #4682: the leading cover/title block already provides its heading.
        # Keep the internal "Essentiel" section and all metadata rows, but
        # don't render a redundant visual section heading above those rows.
        # Other sections keep their original headings and action semantics.
        group.set_title("" if title == "Essentiel" else title)
        grid = None
        if title in {"Essentiel","Notice","Catalogue","Notice EPUB","Bibliothèque","Catalogue connu","Récupération","Historique","Prêt","Réservation"}:
            grid = Gtk.FlowBox(); grid.set_selection_mode(Gtk.SelectionMode.NONE)
            grid.set_min_children_per_line(1)
            grid.set_max_children_per_line(3 if title == "Essentiel" else 2)
            grid.set_homogeneous(True); grid.set_column_spacing(6); grid.set_row_spacing(2)
            if title == "Essentiel":
                grid.add_css_class("detail-compact-fields")
            group.add(grid)
        for index, (label, value) in enumerate(rows):
            row = Adw.ActionRow()
            row.set_use_markup(False)
            field = fields[index] if index < len(fields) else None
            if field_actions and field is not None and field.kind != "text":
                safe_value = safe_detail_value(value)
                if not safe_value:
                    continue
                self._add_metadata_actions(row, label, safe_value, field.kind)
                row.add_css_class("detail-metadata-actions")
            display_value, tooltip_value = detail_display_value(label, value)
            row.set_title(label); row.set_subtitle(display_value)
            row.set_subtitle_lines(detail_subtitle_lines(title, display_value) if grid is not None else 0)
            annotate_detail_field(row, label, tooltip_value, field,
                                  description_property=description_property)
            if grid is not None:
                row.set_size_request(185 if title == "Essentiel" else 160, -1)
                if title == "Essentiel":
                    row.add_css_class("detail-compact-value")
                grid.insert(row, -1)
            else:
                group.add(row)
        if parent is None:
            self._append_content(group)
        else:
            parent.append(group)
        return grid

    def _add_metadata_actions(self, row, label, safe_value, kind):
        search = getattr(self.window, "search_books", None)
        service = self.window.service
        current = getattr(service, "current_identity", None)
        identity_id = getattr(current, "identity_id", None)
        account_epoch = getattr(service, "_account_context_epoch", None)
        source_epoch = getattr(service, "_source_context_epoch", None)
        route = getattr(self.window, "_route_display_token", None)

        def field_scope_current():
            # Both copy and online search are explicit actions of THIS
            # render. Matching the profile label after A→B→A is not enough.
            active_service = getattr(self.window, "service", None)
            active_identity = getattr(active_service, "current_identity", None)
            return (not getattr(self.window, "_closed", False)
                    and active_service is service
                    and active_identity is current
                    and getattr(active_identity, "identity_id", None) == identity_id
                    and getattr(service, "_account_context_epoch", None) == account_epoch
                    and getattr(service, "_source_context_epoch", None) == source_epoch
                    and getattr(self.window, "_route_display_token", None) is route)

        copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy.add_css_class("flat")
        # Name the individual value, including for repeated labels.
        # Keep spoken text bounded; the clipboard retains all of it.
        action_value = " ".join(safe_value.split())
        if len(action_value) > 180:
            action_value = action_value[:140] + "…" + action_value[-39:]
        copy_label = f"Copier {label} : {action_value}"
        copy.set_tooltip_text(copy_label)
        copy.update_property([Gtk.AccessibleProperty.LABEL], [copy_label])

        def copy_one(_button, value=safe_value):
            if not _button.get_mapped() or not field_scope_current():
                return
            display = Gdk.Display.get_default()
            if display is None:
                self.window.toast("Presse-papiers indisponible")
                return
            # Some PyGObject GTK4 bindings lack Clipboard.set_text.
            # Keep the provider alive for asynchronous X11/Wayland reads.
            provider = Gdk.ContentProvider.new_for_value(value)
            self.window._clipboard_provider = provider
            if display.get_clipboard().set_content(provider):
                self.window.toast("Copié")
            else:
                self.window.toast("Copie impossible")
        copy.connect("clicked", copy_one)
        row.add_suffix(copy)

        query_field = kind if kind in {
            "title", "author", "subject", "series", "collection",
            "language", "isbn", "all",
        } else ("all" if kind == "text" else "")
        query_value = safe_value
        if query_field == "isbn":
            query_value = canonical_book_isbn(query_value)
            if not query_value:
                query_field = "all"  # Never treat an EAN/invalid ID as ISBN
        if callable(search) and query_field and 2 <= len(query_value) <= 512:
            lookup = Gtk.Button.new_from_icon_name("system-search-symbolic")
            lookup.add_css_class("flat")
            tooltip = ("Rechercher en ligne dans tous les attributs disponibles"
                       if query_field == "all" else
                       "Rechercher en ligne : " + label)
            tooltip += " : " + action_value
            lookup.set_tooltip_text(tooltip)
            lookup.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
            def search_one(button, value=query_value, field=query_field,
                           identity=identity_id):
                # The selected profile may change while a detail/GTK
                # callback remains alive. Never dispatch an explicit online
                # search with the identity captured on an old presentation,
                # or from a detached/closed DetailPanel button.
                if getattr(self.window, "_closed", False) or not button.get_mapped():
                    return False
                active = getattr(
                    getattr(getattr(self.window, "service", None),
                            "current_identity", None), "identity_id", None,
                )
                if active != identity or not field_scope_current():
                    return False
                return search(
                    value, field=field, online=True, identity_id=identity,
                )

            lookup.connect("clicked", search_one)
            row.add_suffix(lookup)

    def _append_content(self, widget):
        self.content.append(widget)

    @staticmethod
    def _action_focus_key(button) -> str:
        identifier = str(getattr(button, '_cifereca_action_id', '') or '')
        if identifier:
            return 'id:' + identifier
        label = str(getattr(button, '_cifereca_action_label', '') or '')
        if not label:
            getter = getattr(button, 'get_label', None)
            if callable(getter):
                label = str(getter() or '')
        return 'label:' + label if label else ''

    def _focused_action_key(self) -> str:
        for button in getattr(self, '_action_buttons', ()):
            has_focus = getattr(button, 'has_focus', None)
            if callable(has_focus) and has_focus():
                return self._action_focus_key(button)
        return ''

    def _clear_actions(self):
        # The GTK button may still be referenced by an asynchronous callback
        # after a new Search/Recommendations selection replaces this detail.
        # Revoke it before removing the action widgets from their container.
        for button in getattr(self, "_action_buttons", ()):
            button._cifereca_action_context_revoked = True
        # show_presentation()/empty() clear the whole detail body before this.
        self._cached_section_widgets = []
        self._cached_action_ids = set()
        self._action_ids = set()
        self._action_buttons = []
        self._rendered_action_count = 0
        self.action_scroll.clear()
        self.action_scroll.set_visible(False)

    @staticmethod
    def _detail_action_priority(button) -> int:
        explicit = getattr(button, '_cifereca_action_priority', None)
        if isinstance(explicit, int):
            return explicit
        identifier = str(getattr(button, '_cifereca_action_id', '') or '')
        label = str(getattr(button, '_cifereca_action_label', '') or '')
        if not label:
            getter = getattr(button, 'get_label', None)
            if callable(getter):
                label = str(getter() or '')
        folded = label.casefold()
        if identifier.startswith((
            'recommendation.loaded-in-place.',
            'recommendation.known-in-place.',
        )):
            # #4664: at narrow GTK widths the native action bar may show
            # only one control. Keep exact in-panel actions reachable before
            # the legacy redirect to Search, without inventing any right.
            return 9
        if identifier in {'open-catalogue', 'open-notice'}:
            return 30
        if identifier == 'open-local' or folded.startswith('ouvrir'):
            return 10
        if folded.startswith('emprunter'):
            return 11
        if folded.startswith(('télécharger', 'sauvegarder', 'vérifier et télécharger', 'vérifier et sauvegarder')):
            return 12
        if 'voir dans bibliothèque' in folded:
            return 20
        if identifier == 'open-catalogue' or folded.startswith(('disponibilités', 'étendre en ligne', 'en ligne', 'chercher en ligne', 'catalogue en ligne')):
            return 30
        if identifier.startswith('send-local:') or identifier == 'artifact-routing' or folded.startswith('envoyer'):
            return 40
        return 50

    def _actions(self, buttons: list[Gtk.Button]) -> None:
        if not buttons:
            return
        trace = current_detail_trace()
        started = trace.clock() if trace else None
        current = getattr(self, '_action_buttons', None)
        if current is None:
            current = []
            self._action_buttons = current
        changed = False
        for button in buttons:
            identifier = getattr(button, '_cifereca_action_id', None)
            if identifier:
                seen = getattr(self, '_action_ids', set())
                if identifier in seen:
                    continue
                seen.add(identifier)
                self._action_ids = seen
            current.append(button)
            changed = True
        if not changed:
            return
        priority = getattr(self, '_detail_action_priority', None)
        if not callable(priority):
            priority = lambda _button: 50
        ordered = sorted(
            enumerate(current),
            key=lambda item: (priority(item[1]), item[0]),
        )
        current[:] = [button for _index, button in ordered]
        primary_styled = False
        for button in current:
            remover = getattr(button, 'remove_css_class', None)
            if callable(remover):
                remover('suggested-action')
        for button in current:
            has_class = getattr(button, 'has_css_class', None)
            destructive = (getattr(button, '_cifereca_action_destructive', False) is True
                           or (callable(has_class) and has_class('destructive-action')))
            if not primary_styled and not destructive and priority(button) < 20:
                adder = getattr(button, 'add_css_class', None)
                if callable(adder):
                    adder('suggested-action')
                primary_styled = True
        clear = getattr(self.action_scroll, 'clear', None)
        if callable(clear):
            clear()
        else:
            existing = getattr(self, '_rendered_action_count', 0)
            if existing:
                return
        for button in current:
            self.action_scroll.append(button)
        self._rendered_action_count = len(current)
        self.action_scroll.set_visible(True)
        pending_focus = getattr(self, '_pending_action_focus_key', '')
        if pending_focus:
            for button in current:
                if self._action_focus_key(button) == pending_focus:
                    button.grab_focus()
                    self._pending_action_focus_key = ''
                    break
        if trace is not None:
            trace.record("actions", started)

    def show_local_presentation(
        self, presentation: UiBookDetailPresentation, *,
        cover_evidence, description: object | None = None,
    ) -> None:
        """Render a Core-attested local book through the unchanged UI facade.

        The temporary packet is consumed synchronously by _cover_and_title;
        it is cleared on exceptions and never reused by a different book.
        """
        if cover_evidence is None:
            self.show_presentation(presentation, description=description)
            return
        if getattr(self, "_next_cover_evidence", None) is not None:
            raise RuntimeError("nested exact local cover render")
        self._next_cover_evidence = cover_evidence
        try:
            self.show_presentation(presentation, description=description)
        finally:
            self._next_cover_evidence = None

    def show_presentation(
        self,
        presentation: UiBookDetailPresentation,
        *,
        description: object | None = None,
    ) -> None:
        """Render the shared neutral book-detail projection.

        When present, the editorial summary is kept in the primary reading
        flow: immediately after Essentiel and before bibliographic sections.
        """
        trace = current_detail_trace()
        started = trace.clock() if trace else None
        self._rendered_search_detail = None
        self._private_series_request_token = None
        self._cancel_meta_request()
        self._pending_action_focus_key = self._focused_action_key()
        clear_box(self.content)
        self._clear_actions()
        sections_started = trace.clock() if trace else None
        sections = sorted(presentation.sections, key=lambda item: item.order)
        essential = [field for section in sections if section.title == "Essentiel"
                     for field in section.fields]
        # The list and detail get the same opaque Core document. Scope lives
        # only for this synchronous rendering, never as a second cache.
        previous_ref = getattr(self, "_next_cover_document_ref", None)
        # The loan-only public C fallback stages an exact Core reference
        # synchronously. All other callers keep their regular presentation
        # ref; a nested render never transfers the staged ref to another book.
        self._next_cover_document_ref = (
            getattr(presentation, "cover_document_ref", None) or previous_ref
        )
        try:
            self._cover_and_title(
                presentation.title,
                presentation.subtitle or None,
                presentation.thumbnail_url,
                presentation.badges,
                essential=essential,
            )
        finally:
            self._next_cover_document_ref = previous_ref
        self._show_description(description)
        for section in sections:
            if section.title == "Essentiel":
                continue
            rows = [(field.label, text_or_dash(field.value)) for field in section.fields]
            if rows:
                # The neutral plugin model already declares per-value
                # affordances (author, language, copy). Preserve those types
                # in Catalogue as well as Search's Détail; otherwise the
                # shared renderer silently drops its own copy/search actions.
                # Other contextual sections remain passive.
                if section.title == "Détail" or section.title == "Catalogue":
                    self._group(section.title, rows, fields=section.fields)
                else:
                    self._group(section.title, rows, fields=section.fields, field_actions=False)
        if trace is not None:
            trace.record("sections", sections_started)
        link_buttons = []
        self._rendered_document_urls = set()
        for link in presentation.links:
            # Plugins can supply UiBookDetailPresentation.links directly:
            # enforce the public URL contract at the final GTK link sink too.
            uri = safe_document_link_url(link.url, relation=link.relation)
            if not uri or uri in self._rendered_document_urls:
                continue
            button = Gtk.LinkButton(uri=uri, label=str(link.label))
            button.set_tooltip_text(str(link.label))
            button.connect(
                "activate-link",
                lambda clicked: getattr(
                    clicked, "_cifereca_action_context_revoked", False,
                ) is True,
            )
            button._cifereca_action_id = f"document-link:{link.id}"
            button._cifereca_action_priority = 30
            link_buttons.append(button)
            self._rendered_document_urls.add(uri)
        if link_buttons:
            self._actions(link_buttons)
        if trace is not None:
            trace.record("render", started)  # Inclusive of nested sections/actions.

    def show_summary(
        self,
        *,
        title: str,
        subtitle: str | None = None,
        thumbnail: str | None = None,
        badges: Iterable[str] = (),
        group_title: str | None = None,
        rows: list[tuple[str, str]] | None = None,
    ) -> None:
        """Compatibility helper backed by the shared detail projection."""
        self.show_presentation(book_detail_presentation(
            title=title,
            subtitle=subtitle,
            thumbnail=thumbnail,
            badges=badges,
            group_title=group_title,
            rows=rows or (),
        ))

    def update_cached_book_details(self, contributions) -> None:
        """Replace only cached plugin groups/actions; preserve the base detail."""
        for widget in getattr(self, "_cached_section_widgets", ()):
            if widget.get_parent() is self.content:
                self.content.remove(widget)
        self._cached_section_widgets = []
        old_ids = set(getattr(self, "_cached_action_ids", ()))
        self._cached_action_ids = set()
        if old_ids:
            # Rebuild the action bar without discarding its ordinary actions.
            kept = [
                button for button in getattr(self, "_action_buttons", ())
                if getattr(button, "_cifereca_action_id", "") not in old_ids
            ]
            self._clear_actions()
            # This is only a cached-extension update: the same base Search
            # detail retains its buttons. Re-admit those exact live buttons
            # after revoking the discarded cached ones; a full book redraw
            # never enters this path and keeps every old button revoked.
            for button in kept:
                button._cifereca_action_context_revoked = False
            self._actions(kept)
        buttons = []
        for contribution in contributions:
            plugin_id = str(contribution.plugin_id)[:100]
            cached = contribution.detail
            source = str(cached.source.source if cached.source else plugin_id)[:100]
            freshness = "Cache ancien" if cached.stale else "Cache local"
            updated = str(cached.updated_at or "Date inconnue")[:48]
            for section in cached.sections:
                rows = [(str(field.label), str(field.value)) for field in section.fields]
                rows.append(("Provenance", f"{source} · {freshness} · {updated}"))
                self._group(f"{section.title} · {plugin_id}", rows,
                            fields=section.fields, field_actions=False)
                self._cached_section_widgets.append(self.content.get_last_child())
            for action in cached.actions:
                if type(action.uri) is not str:
                    continue
                if action.uri:
                    # Already validated by collect_cached_book_details; keep the
                    # final GTK action sink explicit as defence in depth.
                    uri = safe_document_link_url(action.uri, relation="related")
                    if not uri:
                        continue
                    button = Gtk.LinkButton(uri=uri, label=str(action.title))
                    button.connect(
                        "activate-link",
                        lambda clicked: getattr(
                            clicked, "_cifereca_action_context_revoked", False,
                        ) is True or getattr(
                            clicked, "_cifereca_action_state_blocked", False,
                        ) is True,
                    )
                elif callable(action.callback):
                    button = self.window.action_button(str(action.title), str(action.icon_name))
                    def activate_cached(clicked, cb=action.callback):
                        if (getattr(clicked, "_cifereca_action_context_revoked", False)
                                or getattr(clicked, "_cifereca_action_state_blocked", False)):
                            return
                        return cb()
                    button.connect("clicked", activate_cached)
                else:
                    from cifereca.plugin_api.detail_action_descriptor import inactive_without_target
                    if not inactive_without_target(action):
                        continue
                    # A declared inactive state is visible, but has no
                    # callback, URI or activate-link handler at all.
                    button = self.window.action_button(str(action.title), str(action.icon_name))
                from cifereca.gui.detail_action_state import apply_detail_action_state
                apply_detail_action_state(button, action)
                identifier = f"book-cache:{plugin_id}:{action.id}"
                button._cifereca_action_id = identifier
                if type(getattr(action, "priority", None)) is not int:
                    button._cifereca_action_priority = 35
                self._cached_action_ids.add(identifier)
                buttons.append(button)
        self._actions(buttons)

    def add_group(self, title: str, rows: list[tuple[str, str]]) -> None:
        self._group(title, rows)

    def add_retained_search_series(self, relation) -> None:
        """Passive historical catalogue evidence, not a fresh search or offer."""
        from cifereca.core.public_search_projection import PublicSeriesRelation
        from cifereca.book_detail_metadata import safe_detail_value
        if type(relation) is not PublicSeriesRelation:
            return
        label = safe_detail_value(relation.label)
        source = safe_detail_value(relation.source_field)
        if label and source:
            self._group("Catalogue · observation conservée", [
                ("Série", label), ("Champ source", source),
            ], field_actions=False)

    def add_review_group(self, title: str, rows: list[tuple[str, str]]):
        """Own one detachable plugin result, without metadata action buttons."""
        # These native section names have dedicated presentation branches.
        # They are not an extension point for a contextual review producer.
        if title in ("Détail", "Bibliothèques et accès"):
            return None
        owner = self.content
        slot = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._group(title, rows, parent=slot, field_actions=False)
        owner.append(slot)

        def remove():
            if slot.get_parent() is owner:
                owner.remove(slot)

        return remove

    def remove_review_action(self, button) -> None:
        """Physically retire only the exact contextual action, preserving peers."""
        current = getattr(self, "_action_buttons", ())
        if (getattr(button, "_cifereca_review_action_retired", False) is not True
                or not any(item is button for item in current)):
            return
        # Remove the old widget and ID, not merely hide it. A later action
        # bar rebuild or cached contribution refresh must not revive it.
        kept = [item for item in current if item is not button]
        self.action_scroll.clear()
        self._action_ids = set()
        self._action_buttons = []
        self._rendered_action_count = 0
        self.action_scroll.set_visible(False)
        self._actions(kept)

    def add_actions(self, buttons: list[Gtk.Button]) -> None:
        self._actions(buttons)

    def _direct_download_action(self, item: SearchResult) -> None:
        from cifereca.gui.offer_actions import (
            requires_selected_offer_check, download_action_label,
            private_notice_download_hint_allowed,
            public_catalogue_download_hint_allowed,
        )
        # Descriptive public catalogue rows have no implicit download offer.
        # This guard is independent of supplier identity and does no I/O.
        if not public_catalogue_download_hint_allowed(item):
            return
        if not private_notice_download_hint_allowed(
            item, service=self.window.service,
            identity_id=getattr(getattr(self.window.service, "current_identity", None),
                                "identity_id", None),
        ):
            return
        if not self.window.service.can_direct_download(item):
            return
        content_type = self.window.service.content_type_for(item)
        action_label = "le livre audio" if content_type == "audiobook" else (file_format(item.format).upper() or "le fichier")
        label = download_action_label(
            item, check_required=requires_selected_offer_check(self.window.service, item),
            detailed_format=action_label,
        )
        direct = self.window.action_button(label, "document-save-symbolic", suggested=True)
        direct.connect("clicked", lambda _b: self.window.download_direct_item(item, direct))
        self._actions([direct])

    def show_loan_public_c(self, loan: Loan, *, document) -> None:
        """Render an image-less private Loan with a separately attested
        PUBLIC C cover. This does not alter loan actions or permissions.
        """
        from ..core.notice_c_cover_index import is_canonical_cover_document
        from .borrower_public_c_bridge import exact_loan_public_c
        receipt = exact_loan_public_c(self.window, loan)
        if (type(loan) is not Loan
                or loan.thumbnail_url is not None
                or not is_canonical_cover_document(document)
                or receipt is None or receipt.document != document
                or self._next_cover_document_ref is not None
                or self._next_cover_evidence is not None
                or self._next_borrower_public_c_receipt is not None):
            raise ValueError("invalid_loan_public_c_detail")
        self._next_cover_document_ref = document
        self._next_borrower_public_c_receipt = receipt
        try:
            self.show_loan(loan)
        finally:
            self._next_cover_document_ref = None
            self._next_borrower_public_c_receipt = None

    def show_loan_local(self, loan: Loan, *, cover_evidence) -> None:
        """Loan view with an owner/entitlement-attested EPUB DocumentRef.

        Keep show_loan's public ABI and its renewal/return/download actions.
        The staging packet is consumed solely for this synchronous render,
        never for a later loan, identity, or selection.
        """
        if getattr(self, "_next_cover_evidence", None) is not None:
            raise RuntimeError("nested exact loan cover render")
        self._next_cover_evidence = cover_evidence
        try:
            self.show_loan(loan)
        finally:
            self._next_cover_evidence = None

    def show_loan(self, loan: Loan, include_pnb_action: bool = True) -> None:
        self._cancel_meta_request()
        source = self.window.service.provider_display_name(str(loan.source_provider or loan.provider))
        state = "En retard" if loan.is_late else (loan.state or "Emprunté")
        self.show_presentation(book_detail_presentation(
            title=loan.title,
            subtitle=loan.author or "",
            thumbnail=loan.thumbnail_url,
            badges=[],
            section_rows=[
                ("Essentiel", [
                    ("Source", source),
                    ("Format", file_format(loan.media_format).upper()),
                    ("Langue", detail_language(loan.raw)),
                    ("Disponibilité", compact_availability_label(state)),
                ]),
                ("Prêt", [
                    ("Retour prévu", loan_due_detail_value(
                        loan.due_at, is_late=bool(loan.is_late),
                    )),
                    ("Bibliothèque", loan.library or ""),
                    ("État", state),
                    ("Support", loan.access_medium or loan.document_type or ""),
                ]),
            ],
        ))
        buttons: list[Gtk.Button] = []
        downloadable = (
            self.window.service.can_download_loan(loan)
            if loan.medium == "digital" else False
        )
        if loan.medium == "digital" and not downloadable:
            # Presentation is read-only. Explain a withheld action without
            # logging at every GTK rerender or triggering any provider fetch.
            eligibility = getattr(
                self.window.service, "loan_download_eligibility", None,
            )
            if callable(eligibility):
                try:
                    reason = loan_download_unavailable_message(
                        eligibility(loan),
                    )
                except Exception:
                    reason = None
                if reason:
                    self._group(
                        "Téléchargement du prêt", [("Disponibilité", reason)],
                    )
        # Capture the navigation selection at the synchronous CoreUI edge.
        # The read-only Core capability projection receives an exact scope,
        # never a live GTK getter. The executor independently revalidates.
        try:
            selected = self.window.service.current_identity if include_pnb_action else None
            selected_identity_id = getattr(selected, "identity_id", None)
        except Exception:
            selected_identity_id = None
        if (include_pnb_action and explicit_loan_acsm_action_available(
            self.window.service, loan, selected_identity_id=selected_identity_id,
        )):
            # The static installed session contract selects this action.
            # Button visibility never proves consent, rights or a live offer.
            prepare_token = self.window.action_button(
                "Récupérer le jeton ACSM du prêt…", "document-save-symbolic",
            )
            prepare_token.set_tooltip_text(
                "Action volontaire : vérifie le prêt du compte exact, "
                "puis demande séparément l'accord pour l'import ADEPT. "
                "Aucun nouvel emprunt."
            )
            prepare_token.connect(
                "clicked",
                lambda _b: self.window.prepare_explicit_loan_acsm_for_loan(loan, prepare_token),
            )
            buttons.append(prepare_token)
        if include_pnb_action and downloadable:
            prepare = self.window.action_button("Télécharger le livre du prêt", "document-save-symbolic", suggested=True)
            prepare.connect("clicked", lambda _b: self.window.prepare_pnb_for_loan(loan, send=False, control=prepare))
            prepare.set_tooltip_text("Récupère le prêt et traite son ACSM avec Kobo-Adept. Un fichier déjà présent est conservé ; une nouvelle copie peut être créée. Aucun nouvel emprunt.")
            buttons.append(prepare)
        if loan.medium == "digital":
            binding = self.window.service.adept_return_status_for_loan(loan)
            labels = {"ready": "Liaison enregistrée", "loan_id_missing": "Identifiant ADEPT absent du résultat d’acquisition", "profile_missing": "Profil d’origine non identifié", "no_acquisition": "Aucune acquisition liée à ce prêt", "returned": "Retour enregistré dans ADEPT, jeton absent", "returned_unverified": "Ancien état restitué à vérifier dans ADEPT", "provider_conflict": "Retour local enregistré, mais prêt fournisseur encore présent · rattachement possible", "verification_failed": "Confirmation ADEPT indisponible : vérifier le prêt", "expired": "Prêt ADEPT expiré", "token_missing": "Jeton de retour absent", "unresolved": "Fichier absent de l’inventaire ADEPT", "ambiguous": "Plusieurs prêts correspondent au fichier", "inventory_error": "Inventaire ADEPT incomplet", "binding_conflict": "Identifiant ADEPT différent de la liaison enregistrée"}
            self._group("État du prêt ADEPT (lecture seule)", [("Liaison", labels.get(binding["status"], "Indisponible")),
                ("Identifiant ADEPT", binding.get("adept_loan_id") or "—"), ("Profil d’origine", binding.get("profile_name") or "—")])
            if binding.get("acquisition_id") and hasattr(self.window.service, "reconcile_adept_acquisition"):
                check = self.window.action_button("Vérifier le prêt ADEPT", "view-refresh-symbolic")
                check.set_tooltip_text("Interroge le registre public du profil lié à l’identité ; aucun téléchargement.")
                check.connect("clicked", lambda _b: self.window.reconcile_adept_from_detail(binding["acquisition_id"], loan, check))
                buttons.append(check)
            if binding.get("acquisition_id") and hasattr(self.window.service, "adept_loan_choices"):
                attach = self.window.action_button("Rattacher un prêt ADEPT…", "insert-link-symbolic")
                attach.set_tooltip_text("Choisir un prêt par titre et profil, puis confirmer le rattachement. Aucun téléchargement ni retour.")
                attach.connect("clicked", lambda _b: self.window.choose_adept_loan(binding["acquisition_id"], loan, attach))
                buttons.append(attach)
        # Offer renewal only for eligible physical loans. The Core
        # verifies the exact loan and account before renewing.
        # Local import also keeps the historical isolated show_loan adapter safe.
        from cifereca.gui.loan_renewal_visibility import physical_loan_renewal_visible
        if physical_loan_renewal_visible(loan):
            renew = Gtk.Button(label="Prolonger")
            renew.connect("clicked", lambda _b: self.window.confirm_renew(loan, renew))
            buttons.append(renew)
        self._actions(buttons)
        if loan.cannot_renew_reason:
            note = Gtk.Label(label=loan.cannot_renew_reason, xalign=0)
            note.set_wrap(True)
            add_css(note, "dim-label", "caption")
            self._append_content(note)
        self._decorate(loan)

    def _decorate(self, item):
        from dataclasses import asdict
        decorate = getattr(self.window, '_decorate_book_detail', None)
        if callable(decorate): decorate(self, asdict(item))

    def show_history_public_c(self, item: HistoryItem, *, document) -> None:
        """History lists/details share a Core-public N2 C; no title search."""
        from ..core.notice_c_cover_index import is_canonical_cover_document
        from .borrower_public_c_bridge import exact_history_public_c
        receipt = exact_history_public_c(self.window, item)
        if (type(item) is not HistoryItem
                or item.thumbnail_url is not None
                or not is_canonical_cover_document(document)
                or receipt is None or receipt.document != document
                or self._next_cover_document_ref is not None
                or self._next_cover_evidence is not None
                or self._next_borrower_public_c_receipt is not None):
            raise ValueError("invalid_history_public_c_detail")
        self._next_cover_document_ref = document
        self._next_borrower_public_c_receipt = receipt
        try:
            self.show_history(item)
        finally:
            self._next_cover_document_ref = None
            self._next_borrower_public_c_receipt = None

    def show_history_document(
        self, item: HistoryItem, *, cover_evidence,
    ) -> None:
        """Render the same History detail through its exact source N2.

        Keep the public show_history signature, source fields and actions
        unchanged. The attested occurrence is staged only synchronously;
        a later selection cannot inherit its document reference.
        """
        if getattr(self, "_next_cover_evidence", None) is not None:
            raise RuntimeError("nested exact History cover render")
        self._next_cover_evidence = cover_evidence
        try:
            self.show_history(item)
        finally:
            self._next_cover_evidence = None

    def show_history(self, item: HistoryItem) -> None:
        self._cancel_meta_request()
        clear_box(self.content)
        self._clear_actions()
        from .history_cover_presentation import (
            cached_history_cover, history_cover_context,
            history_cover_request_scope, publish_history_cover,
        )
        present_cover = cached_history_cover(self.window, item)
        medium = "Numérique" if item.medium == "digital" else "Physique"
        cover = self._cover_and_title(
            item.title, item.author,
            item.thumbnail_url or present_cover or item.default_thumbnail_url,
            [medium, item.year or ""],
        )
        if (not item.thumbnail_url and not present_cover
                and self._next_cover_document_ref is None
                and self.window.service.current_account.is_primary):
            context = history_cover_context(self.window)
            cover.show_loading()

            def resolved(url: str | None) -> None:
                if (context is not None
                        and history_cover_context(self.window) != context):
                    return  # A prior account/identity epoch is no longer UI.
                sources = getattr(self.window, "history_objects", None)
                if (context is not None and type(sources) is dict
                        and sources.get(item.id) is not item):
                    return  # The exact HistoryItem was superseded.
                if url:
                    page = getattr(self.window, "history_page", None)
                    selection = getattr(page, "selection", None)
                    row = (
                        selection.get_selected_item()
                        if selection is not None else None
                    )
                    if row is not None:
                        publish_history_cover(
                            self.window, item, row, url, context=context,
                        )
                    cover.bind_url(self.window.covers, url, priority=0)
                else:
                    cover.reset()

            self._meta_request = self.window.history_cover_resolver.request(
                item.provider, item.record_id, item.title, item.author, item.year,
                item.default_thumbnail_url, resolved,
                scope=history_cover_request_scope(context),
            )
        elif (not item.thumbnail_url and not present_cover
              and self._next_cover_document_ref is None):
            # Compte lié : pas de requêtes de reconstruction automatiques.
            cover.reset()
        self._group("Historique", [
            ("Emprunté le", fr_date(item.borrowed_at)),
            ("Retourné le", fr_date(item.returned_at)),
            ("Type", item.document_type or "—"),
            ("Fournisseur", item.provider),
        ])
        self._decorate(item)

    def show_search_document(
        self, item: SearchResult, *, cover_evidence,
        loading: bool = False, detail: ItemDetail | None = None,
    ) -> None:
        """Use the active Search cover proof for this synchronous presentation."""
        if self._next_cover_evidence is not None:
            raise RuntimeError("nested exact Search cover render")
        self._next_cover_evidence = cover_evidence
        try:
            if detail is None:
                self.show_search(item, loading=loading)
            else:
                self.show_item_detail(item, detail)
        finally:
            self._next_cover_evidence = None

    def show_public_c_search_document(
        self, item: SearchResult, *, public_c_document,
        loading: bool = False, detail: ItemDetail | None = None,
    ) -> None:
        """Render a verified C reference without changing the Search facade ABI."""
        from cifereca.core.notice_c_cover_index import is_canonical_cover_document
        if not is_canonical_cover_document(public_c_document):
            raise ValueError("unverified public C document")
        if getattr(self, "_next_public_c_document", None) is not None:
            raise RuntimeError("nested public C detail render")
        self._next_public_c_document = public_c_document
        try:
            if detail is None:
                self.show_search(item, loading=loading)
            else:
                self.show_item_detail(item, detail)
        finally:
            self._next_public_c_document = None

    def show_search(self, item: SearchResult, loading: bool = False) -> None:
        from cifereca.core.notice_c_cover_index import is_canonical_cover_document
        candidate = getattr(self, "_next_public_c_document", None)
        document = candidate if is_canonical_cover_document(candidate) else None
        medium = search_detail_support_label(item)
        source = self.window.service.provider_display_name(item.source_provider)
        fmt = file_format(item.format).upper()
        essential_support = medium if not fmt else ""
        stale_known = (item.metadata or {}).get("search_provenance") == "known_catalog_stale"
        usage = _commercial_search_usage(item)
        freshness_rows = [
            ("Recherche en ligne", "Indisponible"),
        ] if stale_known else []
        self.show_presentation(book_detail_presentation(
            title=item.title,
            subtitle=item.author,
            thumbnail=item.thumbnail_url,
            badges=["Résultat connu · hors ligne"] if stale_known else [],
            section_rows=[
                ("Essentiel", [
                    ("Source", source),
                    *([("Usage", usage)] if usage else []),
                    ("Format", fmt),
                    ("Support", essential_support),
                    ("Langue", detail_language(item.metadata)),
                    ("Disponibilité", "Non vérifiée"),
                    *freshness_rows,
                ]),
                ("Notice", [
                    ("Type", item.type or ""),
                    ("Éditeur", item.publisher or ""),
                    ("Publication", item.published_at or ""),
                    ("Support", medium if fmt else ""),
                ]),
            ],
            metadata=item.metadata,
            cover_document_ref=document,
        ), description=item.description)
        if stale_known:
            # A public cache row is neither a current download offer nor a
            # reason to invoke plugin decorators while the source is offline.
            return
        # A direct-download action must be visible immediately.  Do not wait for
        # the asynchronous holdings request: Gallica has no meaningful holding
        # list and a failed availability lookup must never hide the download.
        # Let the shared source-neutral offer guard and Core broker decide.
        # A catalogue-only row is rejected by _direct_download_action before
        # any Core eligibility call, regardless of the supplier name.
        self._direct_download_action(item)
        availability = Adw.PreferencesGroup()
        availability.set_title("Disponibilités")
        row = Adw.ActionRow()
        if loading:
            row.set_title("Chargement des exemplaires…")
            spinner = Gtk.Spinner(); spinner.start(); row.add_suffix(spinner)
        else:
            row.set_title("Disponibilités non actualisées")
        availability.add(row)
        self._append_content(availability)
        self._decorate(item)

    def _show_description(self, description):
        text = description_text(description) or ""
        if not text:
            return
        group = Adw.PreferencesGroup(); group.set_title("Résumé")
        label = Gtk.Label(label=text, xalign=0)
        label.set_wrap(True)
        label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_selectable(True)
        label.set_hexpand(True)
        label.set_lines(6)
        label.set_ellipsize(Pango.EllipsizeMode.END)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_hexpand(True)
        box.append(label)
        toggle = Gtk.Button(label="Lire la suite")
        toggle.add_css_class("flat")
        toggle.set_halign(Gtk.Align.START)
        toggle.set_visible(False)  # No button or reserved height until Pango attests truncation.
        box.append(toggle)
        expanded = {"value": False}
        watch = {"clock": None, "handler": None, "key": None, "overflow": False}

        def apply_state():
            collapsed = watch["overflow"] and not expanded["value"]
            lines = 6 if collapsed else -1
            ellipsize = Pango.EllipsizeMode.END if collapsed else Pango.EllipsizeMode.NONE
            if label.get_lines() != lines:
                label.set_lines(lines)
            if label.get_ellipsize() != ellipsize:
                label.set_ellipsize(ellipsize)
            if not watch["overflow"] and toggle.has_focus():
                label.grab_focus()
            toggle.set_visible(watch["overflow"])
            toggle.set_label("Réduire" if expanded["value"] else "Lire la suite")

        def measure(_frame_clock=None):
            if label.get_root() is None:
                return
            width = label.get_width()
            if width <= 0:
                return
            context = label.get_pango_context()
            key = (width, context.get_serial(), label.get_scale_factor())
            if key == watch["key"]:
                return
            watch["key"] = key
            # Measure the *complete*, unellipsized text at the actual GTK width.
            # Unlike Gtk.Label.get_layout(), this layout is not limited to six
            # lines and cannot confuse a long but fully visible summary (#1018).
            layout = label.create_pango_layout(text)
            layout.set_wrap(Pango.WrapMode.WORD_CHAR)
            layout.set_width(width * Pango.SCALE)
            watch["overflow"] = layout.get_line_count() > 6
            if not watch["overflow"]:
                expanded["value"] = False
            apply_state()

        def mapped(_label):
            clock = label.get_frame_clock()
            if clock is not None and watch["handler"] is None:
                watch["clock"] = clock
                # GTK4 has no "notify::width": observe only painted frames
                # (not a continuously running tick), including resize and font
                # changes, then compare width + Pango context serial.
                watch["handler"] = clock.connect("after-paint", measure)
                measure()

        def unmapped(_label):
            if watch["clock"] is not None and watch["handler"] is not None:
                watch["clock"].disconnect(watch["handler"])
            watch["clock"] = None
            watch["handler"] = None
            watch["key"] = None

        def toggle_summary(_button):
            expanded["value"] = not expanded["value"]
            apply_state()

        toggle.connect("clicked", toggle_summary)
        label.connect("map", mapped)
        label.connect("unmap", unmapped)
        group.add(box)
        self._append_content(group)

    def show_item_detail(self, item: SearchResult, detail: ItemDetail) -> None:
        from cifereca.core.notice_c_cover_index import is_canonical_cover_document
        candidate = getattr(self, "_next_public_c_document", None)
        document = candidate if is_canonical_cover_document(candidate) else None
        medium = search_detail_support_label(item)
        source = self.window.service.provider_display_name(item.source_provider)
        fmt = file_format(item.format).upper()
        essential_support = medium if not fmt else ""
        availability = compact_availability_label(self.window.service.availability_display_label(item, detail))
        visible_availability = catalogue_availability_label(item, availability)
        usage = _commercial_search_usage(item)
        self.show_presentation(book_detail_presentation(
            title=detail.title or item.title,
            subtitle=detail.author or item.author,
            thumbnail=detail.thumbnail_url or item.thumbnail_url,
            badges=[],
            section_rows=[
                ("Essentiel", [
                    ("Source", source),
                    *([("Usage", usage)] if usage else []),
                    ("Format", fmt),
                    ("Support", essential_support),
                    ("Langue", detail_language(item.metadata)),
                    ("Disponibilité", visible_availability),
                ]),
                ("Notice", [
                    ("Type", detail.type or item.type or ""),
                    ("Éditeur", item.publisher or ""),
                    ("Publication", detail.year or item.published_at or ""),
                    ("Support", medium if fmt else ""),
                ]),
            ],
            links=document_links(result_row(item)),
            metadata=item.metadata,
            cover_document_ref=document,
        ), description=getattr(detail, "description", None) or item.description)
        from cifereca.gui.offer_actions import (
            private_notice_download_hint_allowed,
            public_catalogue_download_hint_allowed,
        )
        private_hint_ok = private_notice_download_hint_allowed(
            item, service=self.window.service,
            identity_id=getattr(getattr(self.window.service, "current_identity", None),
                                "identity_id", None),
        )
        direct_download = (
            self.window.service.can_direct_download(item)
            if private_hint_ok and public_catalogue_download_hint_allowed(item)
            else False
        )
        # A spent, uncertain loan may no longer be advertised as
        # "Empruntable" on the public catalogue. The previously submitted
        # request still needs a read-only follow-up after restart.
        followup_reader = getattr(
            self.window.service, "manual_borrow_followup_available", None,
        )
        try:
            pending_followup = (
                callable(followup_reader) and followup_reader(item) is True
            )
        except Exception:
            pending_followup = False
        # An uncertain spent claim is a read-only follow-up, not evidence
        # that the catalogue currently permits another borrow.
        if direct_download:
            self._direct_download_action(item)
        elif pending_followup or search_detail_borrow_action_candidate(item, availability):
            # Commercial store inventory/catalogues never become loan sources,
            # even if a generic availability label is malformed.
            action = Adw.PreferencesGroup()
            action.set_title("Action")
            if not pending_followup and self.window.can_borrow_item(item):
                borrow = self.window.action_button("Emprunter", "document-save-symbolic", suggested=True)
                borrow.connect("clicked", lambda _b: self.window.borrow_search_item(item, borrow))
                self._actions([borrow])
            else:
                row = Adw.ActionRow()
                row.set_title("État de la demande d’emprunt")
                # A public catalogue cannot attest an account's loan rights,
                # while a missing Core action may also represent a spent claim.
                # Neither situation authorizes a new borrow.
                explanation = (
                    "Éligibilité du compte non vérifiée auprès du fournisseur. "
                    "L'emprunt est indisponible pour cette identité et ce compte. "
                    "Une demande antérieure peut exister : consultez Mes emprunts. "
                    "Cifereca ne répétera pas automatiquement la demande."
                )
                # Only a previously spent exact Core claim can expose
                # this read-only action. No inferred borrower eligibility,
                # new claim, provider GET or mutating fallback is authorized.
                if pending_followup:
                    explanation = (
                        "Demande de prêt déjà envoyée ; confirmation "
                        "indisponible. Ne réempruntez pas. "
                        "La vérification ci-dessous ne soumet aucun prêt."
                    )
                    verify = self.window.action_button(
                        "Vérifier le prêt sans réemprunter",
                        "view-refresh-symbolic",
                    )
                    verify.set_name("cifereca-borrow-followup")
                    verify.connect(
                        "clicked",
                        lambda _b: self.window.borrow_search_item(item, verify),
                    )
                    self._actions([verify])
                row.set_subtitle(explanation)
                action.add(row)
                self._append_content(action)
        elif item.owned:
            # A supplier's raw owned=True flag is never an entitlement.
            # The Core identity-scoped ledger, not the UI, authorizes delivery.
            action = Adw.PreferencesGroup()
            action.set_title("Acquisition locale")
            row = Adw.ActionRow()
            row.set_title("Acquisition locale non disponible")
            row.set_subtitle(
                "La propriété et le téléchargement doivent être attestés "
                "pour ce compte avant de proposer une acquisition locale."
            )
            action.add(row)
            self._append_content(action)
        self._decorate(item)
        if direct_download and not detail.holdings:
            return
        group = Adw.PreferencesGroup()
        group.set_title("Disponibilités")
        if not detail.holdings:
            row = Adw.ActionRow()
            row.set_title("Détail des exemplaires non fourni par cette source")
            group.add(row)
        for holding in detail.holdings:
            row = Adw.ActionRow()
            # Library names, sections, and call numbers are plain provider text.
            # ActionRow otherwise interprets ampersands and angle brackets as markup.
            row.set_use_markup(False)
            row.set_title(holding.library or holding.location or "Exemplaire")
            pieces = [holding.status_label or holding.status]
            if holding.section:
                pieces.append(holding.section)
            if holding.call_number:
                pieces.append(f"Cote {holding.call_number}")
            if holding.due_at:
                pieces.append(f"Retour {fr_date(holding.due_at)}")
            row.set_subtitle(" · ".join(x for x in pieces if x))
            if holding.is_available:
                chip = Gtk.Label(label="Disponible")
                add_css(chip, "badge", "success-badge")
                row.add_suffix(chip)
            elif holding.is_reservable:
                chip = Gtk.Label(label="Réservable")
                add_css(chip, "badge")
                row.add_suffix(chip)
            group.add(row)
        self._append_content(group)
        self._rendered_search_detail = (item, detail)
        # #4988: potential *local* lender accounts, not verified supplier
        # offers. The shared Search/Recommendations detail is read-only;
        # no loan/copy action is attached until a distinct Core admission.
        try:
            from .cross_profile_possible_lender_detail import (
                append_possible_lender_summary,
            )
            append_possible_lender_summary(self, item)
        except Exception:
            pass
        # Shared Search and Recommendations: already confirmed local lender
        # claims only, no provider I/O or implicit send consent.
        try:
            from .cross_profile_existing_loan_detail import (
                append_existing_lender_loan_summary,
            )
            append_existing_lender_loan_summary(self, item)
        except Exception:
            pass

    def show_cross_profile_borrow_detail(
        self, preview, *, expected_item: SearchResult,
        expected_edition_id: str, expected_context_epoch: int,
        expected_source_epoch: int,
    ) -> bool:
        """#4662: append neutral lender rows only to the same visible detail.

        A late GTK callback cannot attach account A's lender information to
        an identity B, a replaced provider session or a different edition.
        No provider I/O, action button, loan claim or copy is generated.
        """
        from ..core.borrow_for_identity_detail import (
            cross_profile_borrow_detail_section,
        )
        section = cross_profile_borrow_detail_section(preview)
        if (section is None
                or type(expected_item) is not SearchResult
                or type(expected_edition_id) is not str
                or not expected_edition_id
                or type(expected_context_epoch) is not int
                or type(expected_source_epoch) is not int
                or expected_context_epoch < 0 or expected_source_epoch < 0
                or any(option.edition_id != expected_edition_id
                       for option in preview.options)):
            return False

        try:
            service = self.window.service
            requester = service.current_identity
        except Exception:
            return False

        def current() -> bool:
            try:
                rendered = self._rendered_search_detail
                return bool(
                    type(rendered) is tuple and len(rendered) == 2
                    and rendered[0] is expected_item
                    and service.current_identity is requester
                    and getattr(requester, "identity_id", None) ==
                        preview.requesting_identity_id
                    and service._account_context_epoch == expected_context_epoch
                    and service._source_context_epoch == expected_source_epoch
                )
            except Exception:
                return False

        if not current():
            return False
        group = Adw.PreferencesGroup()
        group.set_title(section.title)
        for field in section.fields:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(field.label)
            row.set_subtitle(str(field.value))
            group.add(row)
        if not current():
            return False
        self._append_content(group)
        return True

    def show_cross_profile_borrow_progress_detail(
        self, progress, *, expected_item: SearchResult,
        requesting_identity_id: str, expected_context_epoch: int,
        expected_source_epoch: int,
    ) -> bool:
        """#4988: show read-only loan/delivery/transfer status for one detail.

        Never fetch a lender's receipt or create buttons here. The caller
        separately obtains a privacy-scoped Core progress DTO; this renderer
        rejects stale Search panes and ABA context/source replacements.
        """
        from ..core.borrow_for_identity_detail import (
            cross_profile_borrow_progress_detail_section,
        )
        section = cross_profile_borrow_progress_detail_section(progress)
        if (section is None
                or type(expected_item) is not SearchResult
                or type(requesting_identity_id) is not str
                or not requesting_identity_id
                or type(expected_context_epoch) is not int
                or type(expected_source_epoch) is not int
                or expected_context_epoch < 0 or expected_source_epoch < 0):
            return False
        try:
            service = self.window.service
            identity = service.current_identity
        except Exception:
            return False

        def current() -> bool:
            try:
                rendered = self._rendered_search_detail
                return bool(
                    type(rendered) is tuple and len(rendered) == 2
                    and rendered[0] is expected_item
                    and service.current_identity is identity
                    and identity.identity_id == requesting_identity_id
                    and service._account_context_epoch == expected_context_epoch
                    and service._source_context_epoch == expected_source_epoch
                )
            except Exception:
                return False

        if not current():
            return False
        group = Adw.PreferencesGroup()
        group.set_title(section.title)
        for field in section.fields:
            row = Adw.ActionRow()
            row.set_use_markup(False)
            row.set_title(field.label)
            row.set_subtitle(str(field.value))
            group.add(row)
        if not current():
            return False
        self._append_content(group)
        return True

    def refresh_confirmed_borrow_detail(self, item: SearchResult) -> bool:
        """Reproject the exact visible detail from Core state, without I/O.

        Only a confirmed callback guarded by identity/account/selection epochs
        may invoke this method. A later presentation invalidates the cache.
        """
        cached = self._rendered_search_detail
        if cached is None or cached[0] is not item:
            return False
        self.show_item_detail(item, cached[1])
        return True
