from __future__ import annotations

import re
import logging
import time
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from .. import __version__
from ..config import Config
from ..core.search import file_format
from ..identity_selection import identity_change_required
from ..language import language_label
from ..diagnostics import Diagnostics
from ..descriptions import description_text
from ..epub_metadata import notice_rows
from ..models import Booking, HistoryItem, Holding, ItemDetail, Loan, SearchResult
from ..services import LibraryService
from .cover_loader import CoverLoader
from .book_widgets import BookListCell, BookListItem, CoverView, attribute_tag
from .book_actions import identity_has_attested_possession
from .booking_view import booking_presentation
from .layout import MASTER_DETAIL_NARROW_WINDOW_WIDTH, master_detail_is_narrow
from .settings_context import settings_action_state
from .loan_view import (
    LOAN_FILTER_LABELS,
    loan_context_label,
    loan_due_presentation,
    loan_medium_counts,
)
from .recent_time import format_recent_timestamp
from .sync_view import sync_identity_action_label, sync_source_presentation
from .task_runner import TaskPriority, TaskRunner
from .ui_perf import UiPerfMonitor
from .activity_feedback import ActivityFeedback
from .plugin_manager_page import PluginManagerPage
from .query_controls import QueryControls, MultiSelect
from .action_widgets import AdaptiveActionBar
from .query_model import result_row, online_search, progressive_online_search, merge_results
from .shell_model import resolve_pages, visible_actions, page_actions, delegated_accounts, menu_sections, contextual_actions, validate_action_selection
from ..plugin_api import (
    ArtifactFormat, ArtifactRef, ArtifactRouteRequest, BookRef, BookReferenceRecommendationService, DetailField, DetailSection, DocumentLink,
    MaintenanceContext, SyncRequest, UiActionContext, UiBookDetailPresentation, UiPageContribution, document_links,
)

_TRACE_LOG = logging.getLogger("cifereca")


def fr_date(value: str | None, with_time: bool = False) -> str:
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone()
        return dt.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")
    except Exception:
        return str(value)


def text_or_dash(value: Any) -> str:
    text = str(value or "").strip()
    return text or "—"


def detail_display_value(label: str, value: Any) -> tuple[str, str]:
    """Return the compact value and the complete tooltip value for a detail row."""
    full = text_or_dash(value)
    if label == "Fichier local" and full != "—":
        compact = Path(full).name or full
        return compact, full
    return full, full


def detail_language(metadata: object) -> str:
    """Return a compact, human-readable language list for shared book details."""
    data = metadata if isinstance(metadata, dict) else {}
    raw = data.get("languages") or data.get("language") or ()
    values = raw if isinstance(raw, (list, tuple, set)) else (raw,)
    labels: list[str] = []
    for value in values:
        label = language_label(value)
        if label and label not in labels:
            labels.append(label)
    return ", ".join(labels)


def compact_availability_label(value: object) -> str:
    """Normalize verbose provider prose into a short business status."""
    text = str(value or "").strip()
    folded = text.casefold()
    if not text:
        return ""
    if "non vérifi" in folded or "non verifi" in folded:
        return "Non vérifiée"
    if "dans votre bibliothèque" in folded or "dans votre bibliotheque" in folded:
        return "Dans la bibliothèque"
    if "téléchargement libre" in folded or "telechargement libre" in folded:
        return "Téléchargement libre"
    if "disponible à l’emprunt" in folded or "disponible a l'emprunt" in folded:
        return "Empruntable"
    if "disponible" in folded and len(text) > 32:
        return "Disponible"
    if len(text) > 40 and " — " in text:
        return text.split(" — ", 1)[0].strip()
    return text


def compact_detail_rows(rows: Iterable[tuple[str, object]]) -> tuple[tuple[str, object], ...]:
    """Drop empty filler cells from dense detail sections."""
    result: list[tuple[str, object]] = []
    for label, value in rows:
        text = str(value or "").strip()
        if not text or text in {"—", "Inconnu"}:
            continue
        result.append((str(label), value))
    return tuple(result)


def detail_subtitle_lines(section: str, value: object) -> int:
    """Keep short metadata dense while leaving long values fully readable."""
    text = str(value or "")
    if "\n" in text:
        return 0
    limit = 36 if section == "Essentiel" else 72
    if len(text) > limit:
        return 0
    return 1 if section == "Essentiel" else 2



def book_detail_presentation(
    *,
    title: str,
    subtitle: str | None = None,
    thumbnail: str | None = None,
    badges: Iterable[str] = (),
    group_title: str | None = None,
    rows: Iterable[tuple[str, object]] = (),
    section_rows: Iterable[tuple[str, Iterable[tuple[str, object]]]] = (),
    links: Iterable[DocumentLink] = (),
) -> UiBookDetailPresentation:
    """Build the neutral book-detail projection used by Core and plugin views."""
    sections_list: list[DetailSection] = []
    for order, (section_title, section_values) in enumerate(section_rows):
        compact_rows = compact_detail_rows(section_values)
        fields = tuple(
            DetailField(id=f"section-{order}-field-{index}", label=str(label), value=value)
            for index, (label, value) in enumerate(compact_rows)
        )
        if fields:
            sections_list.append(
                DetailSection(id=f"section-{order}", title=str(section_title), fields=fields, order=order)
            )
    if not sections_list and group_title:
        fields = tuple(
            DetailField(id=f"field-{index}", label=str(label), value=value)
            for index, (label, value) in enumerate(compact_detail_rows(rows))
        )
        if fields:
            sections_list.append(DetailSection(id="primary", title=group_title, fields=fields, order=0))
    return UiBookDetailPresentation(
        title=str(title or "Sans titre"),
        subtitle=str(subtitle or ""),
        thumbnail_url=thumbnail,
        badges=tuple(dict.fromkeys(str(value) for value in badges if value)),
        sections=tuple(sections_list),
        links=tuple(links),
    )

def clear_box(box: Gtk.Box) -> None:
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def add_css(widget: Gtk.Widget, *classes: str) -> Gtk.Widget:
    for name in classes:
        widget.add_css_class(name)
    return widget


def action_button(
    label: str,
    icon_name: str | None = None,
    *,
    suggested: bool = False,
    destructive: bool = False,
    tooltip: str | None = None,
) -> Gtk.Button:
    """Create a consistently labelled action button with an optional icon."""
    button = Gtk.Button()
    button._cifereca_action_label = label
    button._cifereca_action_icon = icon_name or 'view-more-symbolic'
    if icon_name:
        try:
            content = Adw.ButtonContent()
            content.set_label(label)
            content.set_icon_name(icon_name)
            button.set_child(content)
        except (AttributeError, TypeError):
            # Keep the action usable on older libadwaita runtimes where
            # ButtonContent is not available. The label remains authoritative.
            button.set_label(label)
    else:
        button.set_label(label)
    if suggested:
        button.add_css_class("suggested-action")
    if destructive:
        button.add_css_class("destructive-action")
    button.set_tooltip_text(tooltip or label)
    return button


class _MetaRequest:
    def __init__(self, holder: dict[str, bool]):
        self.holder = holder

    def cancel(self) -> None:
        self.holder["active"] = False


class HistoryCoverResolver:
    """Résolution lazy des couvertures absentes de l'historique lié.

    La 0.2.6 n'utilise plus les RecordId ListHandings pour GetListHoldings : les RecordId observés sur un
    profil lié provoquent systématiquement une erreur Syracuse. On recherche
    donc le document par titre/auteur/année uniquement pour les cellules
    réellement matérialisées, avec deux workers et déduplication.
    """

    def __init__(self, service: LibraryService, diagnostics: Diagnostics):
        self.service = service
        self.diagnostics = diagnostics
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="cifereca-history-cover")
        self._lock = threading.Lock()
        self._pending: dict[str, list[tuple[dict[str, bool], Callable[[str | None], None]]]] = {}
        self._memory: dict[str, tuple[str | None, str]] = {}
        self._stats = {
            "requested": 0,
            "memory_hit": 0,
            "matched": 0,
            "ambiguous": 0,
            "not_found": 0,
            "no_cover": 0,
            "error": 0,
            "completed": 0,
        }

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)

    def stats(self) -> dict[str, int]:
        with self._lock:
            out = dict(self._stats)
            out["pending"] = len(self._pending)
        out["workers"] = 2
        return out

    @staticmethod
    def _key(provider: str, record_id: str | None, title: str, author: str | None, year: str | None) -> str:
        return "\0".join([
            str(provider or ""), str(record_id or ""), str(title or ""),
            str(author or ""), str(year or ""),
        ])

    def request(
        self,
        provider: str,
        record_id: str | None,
        title: str,
        author: str | None,
        year: str | None,
        default_url: str | None,
        callback: Callable[[str | None], None],
    ) -> _MetaRequest:
        holder = {"active": True}
        request = _MetaRequest(holder)
        title = str(title or "").strip()
        if not title:
            GLib.idle_add(callback, default_url)
            return request
        key = self._key(provider, record_id, title, author, year)
        with self._lock:
            self._stats["requested"] += 1
            if key in self._memory:
                self._stats["memory_hit"] += 1
                value, _status = self._memory[key]
                GLib.idle_add(callback, value or default_url)
                return request
            callbacks = self._pending.setdefault(key, [])
            callbacks.append((holder, callback))
            if len(callbacks) > 1:
                return request

        future = self.pool.submit(
            self.service.resolve_history_thumbnail,
            provider, record_id, title, author, year, default_url,
        )

        def done(fut) -> None:
            try:
                value, status = fut.result()
            except Exception:
                value, status = default_url, "error"
            with self._lock:
                self._memory[key] = (value, status)
                callbacks = self._pending.pop(key, [])
                self._stats[status if status in {"matched", "ambiguous", "not_found", "no_cover", "error"} else "error"] += 1
                self._stats["completed"] += 1
                snapshot = dict(self._stats)
                snapshot["pending"] = len(self._pending)
                should_log = self._stats["completed"] == 1 or self._stats["completed"] % 10 == 0
            if should_log:
                snapshot["workers"] = 2
                self.diagnostics.add(
                    "COVER", "history search pipeline", "ok",
                    detail=snapshot,
                    level="detailed",
                )
            for state, cb in callbacks:
                if state.get("active"):
                    GLib.idle_add(cb, value)

        future.add_done_callback(done)
        return request


class DetailPanel(Gtk.Box):
    def __init__(self, window: "MainWindow"):
        super().__init__()
        self.window = window
        self._meta_request = None
        self._pending_action_focus_key = ""
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
        self.content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.content.set_margin_top(20)
        self.content.set_margin_bottom(20)
        self.content.set_margin_start(20)
        self.content.set_margin_end(20)
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

    def _cover_and_title(
        self,
        title: str,
        subtitle: str | None,
        thumbnail: str | None,
        badges: Iterable[str] = (),
    ) -> CoverView:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        self._append_content(header)
        cover = CoverView(112, 168)
        cover.set_missing_size(40, 60)
        header.append(cover)
        cover.bind_url(self.window.covers, thumbnail, priority=0)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
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
        text.append(badges_box)
        return cover


    def _group(self, title: str, rows: list[tuple[str, str]]) -> None:
        group = Adw.PreferencesGroup()
        group.set_title(title)
        grid = None
        if title in {"Essentiel","Notice","Catalogue","Notice EPUB","Bibliothèque","Catalogue connu","Récupération","Historique","Prêt","Réservation"}:
            grid = Gtk.FlowBox(); grid.set_selection_mode(Gtk.SelectionMode.NONE)
            columns = 2 if title == "Essentiel" else 3
            grid.set_min_children_per_line(1); grid.set_max_children_per_line(columns)
            grid.set_homogeneous(True); grid.set_column_spacing(12); grid.set_row_spacing(8)
            group.add(grid)
        for label, value in rows:
            row = Adw.ActionRow()
            display_value, tooltip_value = detail_display_value(label, value)
            row.set_title(label); row.set_subtitle(display_value)
            row.set_subtitle_lines(detail_subtitle_lines(title, display_value) if grid is not None else 0)
            row.set_tooltip_text(label+' : '+tooltip_value)
            if grid is not None:
                row.set_size_request(160, -1); grid.insert(row, -1)
            else:
                group.add(row)
        self._append_content(group)

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
        if identifier == 'open-local' or folded.startswith('ouvrir'):
            return 10
        if folded.startswith('emprunter'):
            return 11
        if folded.startswith(('télécharger', 'sauvegarder')):
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
            if not primary_styled and priority(button) < 20:
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
        self._cancel_meta_request()
        self._pending_action_focus_key = self._focused_action_key()
        clear_box(self.content)
        self._clear_actions()
        self._cover_and_title(
            presentation.title,
            presentation.subtitle or None,
            presentation.thumbnail_url,
            presentation.badges,
        )
        summary_rendered = False
        for section in sorted(presentation.sections, key=lambda item: item.order):
            rows = [(field.label, text_or_dash(field.value)) for field in section.fields]
            if rows:
                self._group(section.title, rows)
            if section.title == "Essentiel":
                self._show_description(description)
                summary_rendered = True
        if not summary_rendered:
            self._show_description(description)
        link_buttons = []
        self._rendered_document_urls = set()
        for link in presentation.links:
            uri = str(link.url or "")
            if not uri.startswith(("http://", "https://")) or uri in self._rendered_document_urls:
                continue
            button = Gtk.LinkButton(uri=uri, label=str(link.label))
            button.set_tooltip_text(str(link.label))
            button._cifereca_action_id = f"document-link:{link.id}"
            button._cifereca_action_priority = 30
            link_buttons.append(button)
            self._rendered_document_urls.add(uri)
        if link_buttons:
            self._actions(link_buttons)

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

    def add_group(self, title: str, rows: list[tuple[str, str]]) -> None:
        self._group(title, rows)

    def add_actions(self, buttons: list[Gtk.Button]) -> None:
        self._actions(buttons)

    def _direct_download_action(self, item: SearchResult) -> None:
        if not self.window.service.can_direct_download(item):
            return
        content_type = self.window.service.content_type_for(item)
        action_label = "le livre audio" if content_type == "audiobook" else (file_format(item.format).upper() or "le fichier")
        label = "Sauvegarder via kobo-adept" if item.source_provider == "kobo" else f"Télécharger {action_label}"
        direct = self.window.action_button(label, "document-save-symbolic", suggested=True)
        direct.connect("clicked", lambda _b: self.window.download_direct_item(item, direct))
        self._actions([direct])

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
                    ("Retour prévu", fr_date(loan.due_at)),
                    ("Bibliothèque", loan.library or ""),
                    ("État", state),
                    ("Support", loan.access_medium or loan.document_type or ""),
                ]),
            ],
        ))
        buttons: list[Gtk.Button] = []
        if include_pnb_action and self.window.service.can_download_loan(loan):
            prepare = self.window.action_button("Télécharger le livre du prêt", "document-save-symbolic", suggested=True)
            prepare.connect("clicked", lambda _b: self.window.prepare_pnb_for_loan(loan, send=False, control=prepare))
            prepare.set_tooltip_text("Récupère le prêt et traite son ACSM avec Kobo-Adept. Un fichier déjà présent est conservé ; une nouvelle copie peut être créée. Aucun nouvel emprunt.")
            buttons.append(prepare)
        if loan.medium == "digital":
            binding = self.window.service.adept_return_status_for_loan(loan)
            labels = {"ready": "Liaison enregistrée", "loan_id_missing": "Identifiant ADEPT absent du résultat d’acquisition", "profile_missing": "Profil d’origine non identifié", "no_acquisition": "Aucune acquisition liée à ce prêt", "returned": "Retour enregistré dans ADEPT, jeton absent", "returned_unverified": "Ancien état restitué à vérifier dans ADEPT", "provider_conflict": "Retour local enregistré, mais prêt fournisseur encore présent · rattachement possible", "verification_failed": "Confirmation ADEPT indisponible : vérifier le prêt", "expired": "Prêt ADEPT expiré", "token_missing": "Jeton de retour absent", "unresolved": "Fichier absent de l’inventaire ADEPT", "ambiguous": "Plusieurs prêts correspondent au fichier", "inventory_error": "Inventaire ADEPT incomplet", "binding_conflict": "Identifiant ADEPT différent de la liaison enregistrée"}
            self._group("Restitution ADEPT", [("Liaison", labels.get(binding["status"], "Indisponible")),
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
            if binding.get("return_available"):
                ret_adept = self.window.action_button("Restituer le prêt ADEPT", "edit-undo-symbolic")
                ret_adept.set_tooltip_text("Utilise la liaison ADEPT et le profil d’origine ; conserve les fichiers locaux.")
                ret_adept.connect("clicked", lambda _b: self.window.return_adept_from_detail(binding["acquisition_id"], ret_adept))
                buttons.append(ret_adept)
        experimental = bool(self.window.config.get("experimental_actions", False))
        if experimental and loan.renewable:
            renew = Gtk.Button(label="Prolonger")
            renew.connect("clicked", lambda _b: self.window.confirm_renew(loan, renew))
            buttons.append(renew)
        if experimental and loan.can_return:
            ret = Gtk.Button(label="Rendre maintenant")
            ret.add_css_class("destructive-action")
            ret.connect("clicked", lambda _b: self.window.confirm_return(loan, ret))
            buttons.append(ret)
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

    def show_history(self, item: HistoryItem) -> None:
        self._cancel_meta_request()
        clear_box(self.content)
        self._clear_actions()
        medium = "Numérique" if item.medium == "digital" else "Physique"
        cover = self._cover_and_title(
            item.title, item.author, item.thumbnail_url or item.default_thumbnail_url,
            [medium, item.year or ""],
        )
        if not item.thumbnail_url and self.window.service.current_account.is_primary:
            cover.show_loading()

            def resolved(url: str | None) -> None:
                if url:
                    cover.bind_url(self.window.covers, url, priority=0)
                else:
                    cover.reset()

            self._meta_request = self.window.history_cover_resolver.request(
                item.provider, item.record_id, item.title, item.author, item.year,
                item.default_thumbnail_url, resolved,
            )
        elif not item.thumbnail_url:
            # Compte lié : pas de requêtes de reconstruction automatiques.
            cover.reset()
        self._group("Historique", [
            ("Emprunté le", fr_date(item.borrowed_at)),
            ("Retourné le", fr_date(item.returned_at)),
            ("Type", item.document_type or "—"),
            ("Fournisseur", item.provider),
        ])
        self._decorate(item)

    def show_booking(self, item: Booking) -> None:
        self._cancel_meta_request()
        clear_box(self.content)
        self._clear_actions()
        medium = "Numérique" if item.medium == "digital" else "Physique"
        presentation = booking_presentation(item)
        self._cover_and_title(item.title, None, None, [medium, presentation.status_label])
        self._group("Réservation", [
            ("État", presentation.status_label),
            ("Réservé le", fr_date(item.booking_date)),
            ("Disponible depuis", fr_date(item.availability_date)),
            ("Disponible jusqu’au", fr_date(item.available_until)),
            ("Bibliothèque", item.location or "—"),
        ])
        self._decorate(item)


    def show_search(self, item: SearchResult, loading: bool = False) -> None:
        content_type = self.window.service.content_type_for(item)
        medium = "Livre audio" if content_type == "audiobook" else ("Numérique" if item.medium == "digital" else "Physique")
        source = self.window.service.provider_display_name(item.source_provider)
        fmt = file_format(item.format).upper()
        essential_support = medium if not fmt else ""
        self.show_presentation(book_detail_presentation(
            title=item.title,
            subtitle=item.author,
            thumbnail=item.thumbnail_url,
            badges=[],
            section_rows=[
                ("Essentiel", [
                    ("Source", source),
                    ("Format", fmt),
                    ("Support", essential_support),
                    ("Langue", detail_language(item.metadata)),
                    ("Disponibilité", "Non vérifiée"),
                ]),
                ("Notice", [
                    ("Type", item.type or ""),
                    ("Éditeur", item.publisher or ""),
                    ("Publication", item.published_at or ""),
                    ("Support", medium if fmt else ""),
                ]),
            ],
        ), description=item.description)
        # A direct-download action must be visible immediately.  Do not wait for
        # the asynchronous holdings request: Gallica has no meaningful holding
        # list and a failed availability lookup must never hide the download.
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
        is_long = len(text) > 700 or text.count("\n") >= 6
        if is_long:
            label.set_lines(6)
            label.set_ellipsize(Pango.EllipsizeMode.END)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.append(label)
            toggle = Gtk.Button(label="Lire la suite")
            toggle.add_css_class("flat")
            toggle.set_halign(Gtk.Align.START)

            expanded = {"value": False}

            def toggle_summary(button):
                expanded["value"] = not expanded["value"]
                if expanded["value"]:
                    label.set_lines(-1)
                    label.set_ellipsize(Pango.EllipsizeMode.NONE)
                    button.set_label("Réduire")
                else:
                    label.set_lines(6)
                    label.set_ellipsize(Pango.EllipsizeMode.END)
                    button.set_label("Lire la suite")

            toggle.connect("clicked", toggle_summary)
            box.append(toggle)
            group.add(box)
        else:
            group.add(label)
        self._append_content(group)


    def show_item_detail(self, item: SearchResult, detail: ItemDetail) -> None:
        content_type = self.window.service.content_type_for(item)
        medium = "Livre audio" if content_type == "audiobook" else ("Numérique" if item.medium == "digital" else "Physique")
        source = self.window.service.provider_display_name(item.source_provider)
        fmt = file_format(item.format).upper()
        essential_support = medium if not fmt else ""
        availability = compact_availability_label(self.window.service.availability_display_label(item, detail))
        self.show_presentation(book_detail_presentation(
            title=detail.title or item.title,
            subtitle=detail.author or item.author,
            thumbnail=detail.thumbnail_url or item.thumbnail_url,
            badges=[],
            section_rows=[
                ("Essentiel", [
                    ("Source", source),
                    ("Format", fmt),
                    ("Support", essential_support),
                    ("Langue", detail_language(item.metadata)),
                    ("Disponibilité", availability),
                ]),
                ("Notice", [
                    ("Type", detail.type or item.type or ""),
                    ("Éditeur", item.publisher or ""),
                    ("Publication", detail.year or item.published_at or ""),
                    ("Support", medium if fmt else ""),
                ]),
            ],
            links=document_links(result_row(item)),
        ), description=getattr(detail, "description", None) or item.description)
        direct_download = self.window.service.can_direct_download(item)
        if direct_download:
            self._direct_download_action(item)
        elif item.medium == "digital" and availability == "Empruntable":
            action = Adw.PreferencesGroup()
            action.set_title("Action")
            if self.window.can_borrow_item(item):
                borrow = self.window.action_button("Emprunter", "document-save-symbolic", suggested=True)
                borrow.connect("clicked", lambda _b: self.window.borrow_search_item(item, borrow))
                self._actions([borrow])
            else:
                row = Adw.ActionRow()
                row.set_title("Emprunt requis avant téléchargement")
                row.set_subtitle("Cette source ne publie pas de primitive d’emprunt utilisable.")
                action.add(row)
                self._append_content(action)
        elif item.source_provider == "kobo" and item.owned:
            action = Adw.PreferencesGroup()
            action.set_title("Acquisition locale")
            row = Adw.ActionRow()
            row.set_title("Livre possédé · acquisition locale")
            row.set_subtitle("Le bouton Sauvegarder apparaît lorsque Kobo-Adept est disponible et que Kobo-Vault marque ce titre téléchargeable.")
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

class MasterDetailPage(Gtk.Box):
    """Page maître/détail basée sur Gtk.ListView virtualisé."""

    def __init__(self, window: "MainWindow", title: str, subtitle: str, empty_title: str, empty_desc: str):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.window = window
        self.title = title
        self._selected_callbacks: list[Callable[[BookListItem | None], None]] = []

        # Keep headings independent from action controls.  On 1024px-wide
        # windows the previous single horizontal row forced search/filter
        # controls to compete with the page title and description.
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.page_header = header
        header.set_margin_top(16)
        header.set_margin_start(18)
        header.set_margin_end(18)
        headings = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        headings.set_hexpand(True)
        header.append(headings)
        heading = Gtk.Label(label=title, xalign=0); add_css(heading, "page-title"); headings.append(heading)
        if subtitle:
            desc = Gtk.Label(label=subtitle, xalign=0); desc.set_wrap(True); add_css(desc, "dim-label"); headings.append(desc)
        self.count_label = Gtk.Label(label="", xalign=1)
        self._count_total_only = False
        add_css(self.count_label, "caption", "dim-label")
        header.append(self.count_label)
        self.header_actions = AdaptiveActionBar(source=header)
        self.header_actions.set_halign(Gtk.Align.END); self.header_actions.set_valign(Gtk.Align.START)
        header.append(self.header_actions)
        self.append(header)

        self.toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.toolbar.set_margin_top(10)
        self.toolbar.set_margin_bottom(12)
        self.toolbar.set_margin_start(18)
        self.toolbar.set_margin_end(18)
        self.toolbar.set_hexpand(True)
        self.append(self.toolbar)

        self.detail_back = Gtk.Button(label="Retour à la liste")
        self.detail_back.set_tooltip_text("Revenir à la liste des documents")
        self.detail_back.set_halign(Gtk.Align.START)
        self.detail_back.set_margin_start(18)
        self.detail_back.set_margin_bottom(8)
        self.detail_back.set_visible(False)
        self.detail_back.connect("clicked", lambda _button: self._show_narrow_list())
        self.append(self.detail_back)

        self.paned = Gtk.Paned.new(Gtk.Orientation.HORIZONTAL)
        self.paned.set_wide_handle(True)
        self.paned.set_position(440)
        self.paned.set_shrink_start_child(True)
        self.paned.set_shrink_end_child(True)
        self.paned.set_vexpand(True)
        self._narrow_master_detail = False
        self._narrow_detail_visible = False
        self._narrow_sidebar_restore_visible: bool | None = None
        self._narrow_breakpoint = MASTER_DETAIL_NARROW_WINDOW_WIDTH

        def adapt_paned_position(paned, _pspec=None) -> None:
            max_position = int(paned.get_property("max-position"))
            if max_position <= 0:
                return
            try:
                window_width = int(self.window.get_width())
            except (AttributeError, TypeError, ValueError):
                window_width = 0
            narrow = master_detail_is_narrow(
                window_width,
                breakpoint=self._narrow_breakpoint,
            )
            if narrow != self._narrow_master_detail:
                self._narrow_master_detail = narrow
                if not narrow:
                    self._narrow_detail_visible = False
                    self._set_narrow_detail_context(False)
                    self.detail_back.set_visible(False)
            if self._narrow_master_detail:
                paned.set_position(0 if self._narrow_detail_visible else max_position)
                self.detail_back.set_visible(self._narrow_detail_visible)
            else:
                paned.set_position(min(440, max(0, max_position - 360)))

        self.paned.connect("notify::max-position", adapt_paned_position)
        self.append(self.paned)

        self.list_stack = Gtk.Stack()
        self.list_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.list_stack.set_hexpand(True)
        self.list_stack.set_vexpand(True)
        self.empty_page = Adw.StatusPage()
        self.empty_page.set_title(empty_title)
        self.empty_page.set_description(empty_desc)
        self.empty_page.set_icon_name("accessories-dictionary-symbolic")
        self.list_stack.add_named(self.empty_page, "empty")

        self.store = Gio.ListStore.new(BookListItem)
        self.selection = Gtk.SingleSelection.new(self.store)
        self.selection.set_autoselect(False)
        self.selection.set_can_unselect(True)
        self.selection.connect("notify::selected-item", self._selection_changed)

        self.factory = Gtk.SignalListItemFactory()
        self.factory.connect("setup", self._factory_setup)
        self.factory.connect("bind", self._factory_bind)
        self.factory.connect("unbind", self._factory_unbind)

        self.listview = Gtk.ListView.new(self.selection, self.factory)
        self.listview.set_single_click_activate(False)
        self.listview.set_hexpand(True)
        self.listview.set_halign(Gtk.Align.FILL)
        self.scroll = Gtk.ScrolledWindow()
        # The list viewport must own horizontal allocation. GTK_POLICY_NEVER
        # lets child natural width drive the scroller, which makes pathological
        # row metadata capable of shifting the whole list outside the viewport.
        self.scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_propagate_natural_width(False)
        self.scroll.set_min_content_width(0)
        self.scroll.set_hexpand(True)
        self.scroll.set_vexpand(True)
        self.scroll.set_child(self.listview)
        self.list_stack.add_named(self.scroll, "list")
        self.paned.set_start_child(self.list_stack)
        self.detail = DetailPanel(window)
        self.paned.set_end_child(self.detail)
        self.listview.connect("activate", lambda _view, _position: self.detail.child_focus(Gtk.DirectionType.TAB_FORWARD))
        keys = Gtk.EventControllerKey()
        def key_pressed(_controller, keyval, _keycode, state):
            if keyval == Gdk.KEY_Escape:
                if self._narrow_master_detail and self._narrow_detail_visible:
                    self._show_narrow_list()
                else:
                    self.selection.set_selected(Gtk.INVALID_LIST_POSITION)
                    self.listview.grab_focus()
                return True
            if state & Gdk.ModifierType.CONTROL_MASK and keyval in (Gdk.KEY_f, Gdk.KEY_F):
                controls = getattr(window, "library_controls" if title == "Bibliothèque" else "search_controls", None)
                if controls: controls.entry.grab_focus(); return True
            return False
        keys.connect("key-pressed", key_pressed); self.add_controller(keys)
        self.list_stack.set_visible_child_name("empty")
        # A narrow detail may hide shell navigation. Restore it while this page
        # is unmapped, then re-apply the detail context if the user comes back.
        self.connect("unmap", lambda _widget: self._set_narrow_detail_context(False))
        self.connect("map", lambda _widget: self._set_narrow_detail_context(self._narrow_detail_visible))

    def _factory_setup(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        list_item.set_child(BookListCell(self.window))

    def _factory_bind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        cell = list_item.get_child()
        item = list_item.get_item()
        if isinstance(cell, BookListCell) and isinstance(item, BookListItem):
            cell.bind(item)

    def _factory_unbind(self, _factory: Gtk.SignalListItemFactory, list_item: Gtk.ListItem) -> None:
        cell = list_item.get_child()
        if isinstance(cell, BookListCell):
            cell.unbind()

    def connect_selected(self, callback: Callable[[BookListItem | None], None]) -> None:
        self._selected_callbacks.append(callback)

    def _selection_changed(self, _selection: Gtk.SingleSelection, _pspec) -> None:
        item = self.selection.get_selected_item()
        selected = item if isinstance(item, BookListItem) else None
        if selected is None:
            self.detail.empty("Aucun document sélectionné", "Sélectionnez un document pour afficher ses détails.")
        for callback in self._selected_callbacks:
            callback(selected)
        if self._narrow_master_detail and selected is not None:
            self._show_narrow_detail()

    def select_key(self, key: str) -> bool:
        """Select a visible row by stable key and reveal it without changing filters."""
        target = str(key)
        for index in range(self.store.get_n_items()):
            row = self.store.get_item(index)
            if isinstance(row, BookListItem) and row.key == target:
                self.selection.set_selected(index)
                try:
                    self.listview.scroll_to(index, Gtk.ListScrollFlags.FOCUS, None)
                except (AttributeError, TypeError):
                    pass
                return True
        return False

    def _set_narrow_detail_context(self, active: bool) -> None:
        """Reserve the narrow page for the active detail without losing list state."""
        self.page_header.set_visible(not active)
        self.toolbar.set_visible(not active)
        sidebar = getattr(self.window, "sidebar", None)
        if sidebar is None:
            return
        if active:
            if self._narrow_sidebar_restore_visible is None:
                self._narrow_sidebar_restore_visible = bool(sidebar.get_visible())
            sidebar.set_visible(False)
            return
        restore = self._narrow_sidebar_restore_visible
        self._narrow_sidebar_restore_visible = None
        if restore is not None:
            sidebar.set_visible(bool(restore))

    def _show_narrow_detail(self) -> None:
        if not self._narrow_master_detail:
            return
        self._narrow_detail_visible = True
        self._set_narrow_detail_context(True)
        self.detail_back.set_visible(True)
        self.paned.set_position(0)
        self.detail.child_focus(Gtk.DirectionType.TAB_FORWARD)

    def _show_narrow_list(self) -> None:
        if not self._narrow_master_detail:
            return
        self._narrow_detail_visible = False
        self._set_narrow_detail_context(False)
        self.detail_back.set_visible(False)
        max_position = int(self.paned.get_property("max-position"))
        if max_position > 0:
            self.paned.set_position(max_position)
        self.listview.grab_focus()

    def clear_rows(self) -> None:
        self.selection.set_selected(0xFFFFFFFF)
        try:
            self.store.remove_all()
        except Exception:
            while self.store.get_n_items() > 0:
                self.store.remove(self.store.get_n_items() - 1)
        self.count_label.set_label("")
        self._total_count = 0
        # Never keep stale metadata from a row that belonged to the previous
        # account/provider/filter. This is especially visible when switching
        # to an anonymous catalogue account with no selected loan.
        self.detail.empty("Aucun document sélectionné", "Sélectionnez un document dans la liste.")

    def set_total_only_count(self, enabled: bool = True) -> None:
        self._count_total_only = bool(enabled)
        self._update_count()

    @staticmethod
    def _item_count_label(count: int) -> str:
        return f"{count} élément" if count == 1 else f"{count} éléments"

    def _update_count(self, total_count: int | None = None) -> None:
        displayed = self.store.get_n_items()
        total = int(total_count if total_count is not None else getattr(self, "_total_count", displayed))
        self._total_count = max(total, displayed)
        if not displayed:
            self.count_label.set_label("")
        elif hasattr(self, "_count_total_only") and self._count_total_only:
            self.count_label.set_label(self._item_count_label(self._total_count))
        elif self._total_count > displayed:
            self.count_label.set_label(f"{displayed} / {self._total_count} élément(s)")
        else:
            self.count_label.set_label(f"{displayed} élément(s)")

    def show_rows(self, rows: list[BookListItem], total_count: int | None = None) -> None:
        started = time.perf_counter()
        selected = self.selection.get_selected_item()
        selected_key = selected.key if isinstance(selected, BookListItem) else None
        existing_count = self.store.get_n_items()
        existing_keys = [self.store.get_item(index).key for index in range(existing_count)]
        new_keys = [row.key for row in rows]

        # Progressive search normally grows by appending provider batches. Keep
        # the already-materialised GTK rows in place instead of splicing the
        # complete ListStore on every batch. The final authoritative ranking may
        # reorder rows; that falls through to the full reconciliation below.
        if existing_count and existing_keys == new_keys[:existing_count] and len(rows) >= existing_count:
            tail = rows[existing_count:]
            if tail:
                try:
                    self.store.splice(existing_count, 0, tail)
                except Exception:
                    for row in tail:
                        self.store.append(row)
            self._update_count(total_count if total_count is not None else len(rows))
            self.list_stack.set_visible_child_name("list" if rows else "empty")
            if hasattr(self.window, "ui_perf"):
                self.window.ui_perf.record(f"list:{self.title}:append-fast", (time.perf_counter() - started) * 1000.0)
            return

        self.selection.set_selected(0xFFFFFFFF)
        try:
            self.store.splice(0, self.store.get_n_items(), rows)
        except Exception:
            self.clear_rows()
            for row in rows:
                self.store.append(row)
        self._update_count(total_count if total_count is not None else len(rows))
        self.list_stack.set_visible_child_name("list" if rows else "empty")
        if rows and selected_key is not None:
            restored = next((index for index, row in enumerate(rows) if row.key == selected_key), None)
            if restored is not None:
                self.selection.set_selected(restored)
        if hasattr(self.window, "ui_perf"):
            self.window.ui_perf.record(f"list:{self.title}:replace", (time.perf_counter() - started) * 1000.0)

    def append_rows(self, rows: list[BookListItem], total_count: int | None = None) -> None:
        started = time.perf_counter()
        if rows:
            try:
                self.store.splice(self.store.get_n_items(), 0, rows)
            except Exception:
                for row in rows:
                    self.store.append(row)
        self._update_count(total_count)
        if self.store.get_n_items() > 0:
            self.list_stack.set_visible_child_name("list")
        if hasattr(self.window, "ui_perf"):
            self.window.ui_perf.record(f"list:{self.title}:append", (time.perf_counter() - started) * 1000.0)


class PublicBookMasterDetailView:
    """Public adapter backed by the same MasterDetailPage used by Core pages."""

    def __init__(
        self, window: "MainWindow", *, title: str, subtitle: str = "",
        empty_title: str = "Aucun document",
        empty_desc: str = "Aucun document à afficher.",
    ) -> None:
        self._page = MasterDetailPage(window, title, subtitle, empty_title, empty_desc)
        self._rows: dict[str, Any] = {}

    @property
    def widget(self):
        return self._page

    def append_toolbar(self, widget: object) -> None:
        self._page.toolbar.append(widget)

    def show_rows(self, rows, total_count: int | None = None) -> None:
        self._rows = {str(row.key): row for row in rows}
        ui_rows = [
            BookListItem(
                str(row.key),
                str(row.title or "Sans titre"),
                str(row.subtitle or ""),
                str(row.meta or ""),
                str(row.badge or ""),
                getattr(row, "thumbnail_url", None),
            )
            for row in rows
        ]
        self._page.show_rows(ui_rows, total_count=total_count)

    def set_list_state(self, title: str, description: str = "", icon_name: str = "system-search-symbolic") -> None:
        self._page.selection.set_selected(Gtk.INVALID_LIST_POSITION)
        self._page.empty_page.set_title(str(title))
        self._page.empty_page.set_description(str(description or ""))
        self._page.empty_page.set_icon_name(str(icon_name or "system-search-symbolic"))
        self._page.list_stack.set_visible_child_name("empty")

    def connect_selected(self, callback) -> None:
        def selected(item):
            callback(self._rows.get(str(item.key)) if item is not None else None)
        self._page.connect_selected(selected)

    def show_detail(self, detail) -> None:
        panel = self._page.detail
        panel.show_presentation(detail)
        buttons = []
        for action in getattr(detail, "actions", ()) or ():
            uri = str(getattr(action, "uri", "") or "")
            if uri.startswith(("http://", "https://")):
                button = Gtk.LinkButton(uri=uri, label=str(action.title))
                button.set_tooltip_text(str(action.title))
            else:
                button = action_button(str(action.title), str(action.icon_name or "view-more-symbolic"))
                callback = getattr(action, "callback", None)
                if callable(callback):
                    button.connect("clicked", lambda _button, cb=callback: cb())
            button._cifereca_action_id = str(action.id)
            buttons.append(button)
        panel.add_actions(buttons)

    def empty_detail(
        self,
        title: str = "Aucun document sélectionné",
        description: str = "Sélectionnez un document pour afficher ses détails.",
    ) -> None:
        self._page.detail.empty(title, description)




class MainWindow(Adw.ApplicationWindow):
    NAV_ITEMS = [
        ("dashboard", "Accueil", "view-dashboard-symbolic"),
        ("search", "Recherche", "system-search-symbolic"),
        ("library", "Bibliothèque", "accessories-dictionary-symbolic"),
        ("recent", "Récents", "document-open-recent-symbolic"),
        ("loans", "Emprunts", "folder-documents-symbolic"),
        ("plugins", "Plugins", "application-x-addon-symbolic"),
    ]

    def __init__(self, app: Adw.Application, config: Config, service: LibraryService, diagnostics: Diagnostics):
        super().__init__(application=app)
        self.config = config
        self.service = service
        self.diagnostics = diagnostics
        self.plugin_manager = getattr(service, "plugin_manager", None)
        self._plugin_page_widgets: dict[str, Gtk.Widget] = {}
        self._closed = False
        self._account_changing = False
        self._identity_changing = False
        self._identity_context_generation = 0
        self._selected_search_id: str | None = None
        self._last_search_query = ""
        self._search_mode = "local"
        self._search_local_objects: dict[str, dict[str, Any]] = {}
        self._kobo_backup_timer_id: int | None = None
        self.main_shell: Gtk.Widget | None = None
        self.loan_objects: dict[str, Loan] = {}
        self.history_objects: dict[str, HistoryItem] = {}
        self._history_rows: list[HistoryItem] = []
        self._history_loaded_count = 0
        self._history_page_size = 30
        self._history_append_scheduled = False
        self.search_objects: dict[str, SearchResult] = {}
        self.booking_objects: dict[str, Booking] = {}
        self._library_entries_cache: list[dict[str, Any]] = []
        self._recent_entries_cache: list[dict[str, Any]] = []
        self._library_refresh_pending = False
        self._recent_refresh_pending = False
        self._active_tasks: dict[int, tuple[int, str, str]] = {}
        self._finished_task_ids: set[int] = set()
        self._activity_hide_source: int | None = None
        self._last_finished_task_id: int | None = None
        self.activity_feedback = ActivityFeedback(limit=20)
        self.ui_perf = UiPerfMonitor()
        self.covers = CoverLoader(diagnostics=diagnostics, max_workers=6)
        self.history_cover_resolver = HistoryCoverResolver(service, diagnostics)
        self.runner = TaskRunner(self)
        _TRACE_LOG.info("TRACE gui init pre-css")
        self._install_css()
        _TRACE_LOG.info("TRACE gui init post-css")

        width = int(config.get("ui.width", 1320))
        height = int(config.get("ui.height", 840))
        self.set_default_size(width, height)
        self.set_size_request(760, 540)
        self.set_title(f"cifereca — v{__version__}")

        self.window_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(self.window_root)
        self.root_stack = Gtk.Stack()
        self.root_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.root_stack.set_vexpand(True)
        self.window_root.append(self.root_stack)

        self.notification_revealer = Gtk.Revealer()
        self.notification_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.notification_revealer.set_reveal_child(False)
        notification_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_css(notification_bar, "notification-bar")
        self.notification_label = Gtk.Label(label="", xalign=0)
        self.notification_label.set_hexpand(True)
        self.notification_label.set_wrap(True)
        notification_bar.append(self.notification_label)
        self.notification_close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.notification_close.set_tooltip_text("Fermer la notification")
        self.notification_close.connect("clicked", lambda _button: self._dismiss_notification())
        notification_bar.append(self.notification_close)
        self.notification_revealer.set_child(notification_bar)
        self.window_root.append(self.notification_revealer)
        self._notification_hide_source: int | None = None
        startup_started = time.perf_counter()
        _TRACE_LOG.info("TRACE gui build-startup begin")
        self.root_stack.add_named(self._build_startup(), "startup")
        _TRACE_LOG.info("TRACE gui build-startup end")
        _TRACE_LOG.info("TRACE gui build-main-shell begin")
        self.main_shell = self._build_main_shell()
        self.root_stack.add_named(self.main_shell, "main")
        # Identities are Core configuration, not plugin state. Populate them
        # immediately so a deferred/plugin UI failure cannot blank the selector.
        self._populate_accounts()
        self.root_stack.set_visible_child_name("main")
        shell_ms = (time.perf_counter() - startup_started) * 1000.0
        self.ui_perf.record("startup.shell_interactive", shell_ms)
        _TRACE_LOG.info("TRACE gui shell-interactive duration_ms=%.1f", shell_ms)
        self.connect("close-request", self._on_close_request)
        self._install_shell_shortcuts()
        # Provider sessions are secondary startup work. They are scheduled
        # after deferred plugin activation so provider services are available.

    def action_button(
        self, label: str, icon_name: str | None = None, *,
        suggested: bool = False, destructive: bool = False, tooltip: str | None = None,
    ) -> Gtk.Button:
        """Create an action using the highest-priority ``ui.widgets`` catalog."""
        try:
            catalog = self.plugin_manager.widget_catalog() if self.plugin_manager is not None else None
        except Exception:
            catalog = None
        role = "action.primary" if suggested else ("action.destructive" if destructive else "action.secondary")
        if catalog is not None:
            try:
                spec = catalog.describe(role) or {}
            except Exception:
                spec = {}
            icon_name = str(spec.get("icon_name") or icon_name or "") or None
            suggested = bool(spec.get("suggested", suggested))
            destructive = bool(spec.get("destructive", destructive))
        return action_button(label, icon_name, suggested=suggested, destructive=destructive, tooltip=tooltip)

    # ---------- infrastructure ----------
    def _install_css(self) -> None:
        css = b"""
        .login-card { background: @card_bg_color; border-radius: 16px; padding: 28px; }
        .sidebar { background: @sidebar_bg_color; border-right: 1px solid @borders; }
        .sidebar row { border-radius: 8px; margin: 2px 8px; padding: 4px; }
        .sidebar row:selected { background: @accent_bg_color; color: @accent_fg_color; }
        .book-row { border-radius: 10px; }
        .book-row:hover { background: alpha(currentColor, 0.055); }
        .book-title { font-weight: 650; font-size: 1.05em; }
        .page-title { font-weight: 750; font-size: 1.55em; }
        .detail-title { font-weight: 800; font-size: 1.7em; }
        .detail-subtitle { font-size: 1.08em; }
        .badge { border-radius: 999px; padding: 3px 8px; background: alpha(currentColor, 0.10); font-size: 0.88em; }
        .book-tag { border-radius: 10px; padding: 2px 6px; font-size: 0.88em; }
        .tag-content { background: alpha(@accent_color, 0.14); font-weight: 600; }
        .tag-form { background: alpha(@success_color, 0.14); }
        .book-tag:focus { outline: 2px solid @accent_color; }
        .facet-value { border-radius: 8px; padding: 2px 6px; background: alpha(@accent_color, 0.08); }
        .facet-value:hover { background: alpha(@accent_color, 0.16); }
        .facet-value:focus { outline: 2px solid @accent_color; }
        .success-badge { background: alpha(@success_color, 0.20); color: @success_color; }
        .cover-frame { border-radius: 7px; background: alpha(currentColor, 0.06); }
        .busy-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 8px 14px; }
        .notification-bar { background: @headerbar_bg_color; border-top: 1px solid @borders; padding: 7px 12px; }
        .settings-tabs button { padding-left: 8px; padding-right: 8px; min-width: 0; }
        .activity-success { color: @success_color; }
        .activity-error { color: @error_color; }
        .loan-overdue { background: alpha(@error_color, 0.14); color: @error_color; font-weight: 650; }
        .loan-imminent { background: alpha(@warning_color, 0.14); color: @warning_color; font-weight: 650; }
        .loan-normal { opacity: 0.85; }
        .metric { font-size: 1.35em; font-weight: 700; }
        .login-brand { font-size: 2.1em; font-weight: 800; }
        """
        provider = Gtk.CssProvider()
        provider.load_from_data(css)
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _cancel_notification_hide(self) -> None:
        source = self._notification_hide_source
        if source is None:
            return
        try:
            GLib.source_remove(source)
        except Exception:
            pass
        self._notification_hide_source = None

    def _hide_notification(self) -> bool:
        self._notification_hide_source = None
        self.notification_revealer.set_reveal_child(False)
        return False

    def _dismiss_notification(self) -> None:
        self._cancel_notification_hide()
        self.notification_revealer.set_reveal_child(False)

    def toast(self, message: str, timeout: int = 3) -> None:
        self._cancel_notification_hide()
        self.notification_label.set_label(str(message))
        self.notification_revealer.set_reveal_child(True)
        bounded_timeout = max(1, min(int(timeout), 10))
        self._notification_hide_source = GLib.timeout_add_seconds(
            bounded_timeout,
            self._hide_notification,
        )

    def show_error(self, message: str, title: str = "Une erreur est survenue") -> None:
        display_message = self._functional_error_message(message)
        self.toast(display_message, 6)
        dialog = Adw.MessageDialog.new(self, title, display_message)
        dialog.add_response("ok", "Fermer")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present()

    def show_attention(self, message: str, *, code: str = "attention_required") -> None:
        """Present a remediable condition without framing it as a crash."""
        dialog = Adw.MessageDialog.new(self, "Action requise", message)
        dialog.add_response("close", "Fermer")
        dialog.set_default_response("close")
        dialog.set_close_response("close")
        dialog.set_body_use_markup(False)
        dialog.present()
        self.diagnostics.add(
            "UX", "attention required", "warning",
            detail={"code": code, "message": message}, level="detailed",
        )

    def _cancel_activity_hide(self) -> None:
        if self._activity_hide_source is not None:
            try:
                GLib.source_remove(self._activity_hide_source)
            except Exception:
                pass
            self._activity_hide_source = None

    def _refresh_activity_history(self) -> None:
        if not hasattr(self, "activity_history_box"):
            return
        clear_box(self.activity_history_box)
        rows = self.activity_feedback.rows()
        for entry in rows:
            label = Gtk.Label(label=entry.display_text, xalign=0)
            label.set_wrap(True)
            label.add_css_class("caption")
            if entry.state == "error":
                label.add_css_class("warning")
            self.activity_history_box.append(label)
        self.activity_history_button.set_visible(bool(rows))

    def _show_pending_activity_error(self) -> bool:
        entry = self.activity_feedback.latest_unacknowledged_error()
        if entry is None or not hasattr(self, "busy_revealer"):
            return False
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(True)
        self.activity_status_icon.set_from_icon_name("dialog-warning-symbolic")
        self.activity_status_icon.add_css_class("activity-error")
        self.activity_status_icon.remove_css_class("activity-success")
        self.busy_label.set_visible(True)
        self.busy_label.set_label(entry.display_text)
        self.activity_dismiss.set_visible(True)
        self.busy_revealer.set_reveal_child(True)
        return True

    def _hide_activity_feedback(self) -> bool:
        self._activity_hide_source = None
        if self._active_tasks:
            return False
        if self._show_pending_activity_error():
            return False
        if not hasattr(self, "busy_revealer"):
            return False
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(False)
        self.activity_dismiss.set_visible(False)
        rows = self.activity_feedback.rows()
        self.busy_label.set_visible(False)
        self.activity_history_button.set_visible(bool(rows))
        self.busy_revealer.set_reveal_child(bool(rows))
        return False

    def _dismiss_activity_feedback(self) -> None:
        self._cancel_activity_hide()
        self.activity_feedback.acknowledge_errors()
        self._refresh_activity_history()
        self._hide_activity_feedback()

    def _schedule_activity_reduce(self, delay: int = 4) -> None:
        self._cancel_activity_hide()
        self._activity_hide_source = GLib.timeout_add_seconds(
            max(1, int(delay)),
            self._hide_activity_feedback,
        )

    def _activity_result(
        self,
        message: str,
        *,
        success: bool = True,
        persistent: bool = False,
        task_id: int | None = None,
    ) -> None:
        resolved_id = int(task_id) if task_id is not None else self._last_finished_task_id
        if resolved_id is None:
            return
        self.activity_feedback.result(resolved_id, str(message), success=bool(success))
        self._refresh_activity_history()
        if self._active_tasks or not hasattr(self, "busy_revealer"):
            return
        if not success:
            self._show_pending_activity_error()
            return
        # A later success must never mask an earlier error that the user has
        # not acknowledged yet.
        if self._show_pending_activity_error():
            return
        self.busy_spinner.stop(); self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(True)
        self.activity_status_icon.set_from_icon_name("emblem-ok-symbolic")
        self.activity_status_icon.add_css_class("activity-success")
        self.activity_status_icon.remove_css_class("activity-error")
        self.busy_label.set_visible(True)
        self.busy_label.set_label(f"Terminé · {message}")
        self.activity_dismiss.set_visible(bool(persistent))
        self.busy_revealer.set_reveal_child(True)
        if not persistent:
            self._schedule_activity_reduce()

    def _task_started(self, task_id: int, count: int, label: str, priority: int, kind: str) -> bool:
        if getattr(self, '_closed', False):return False
        if int(task_id) in self._finished_task_ids:
            self._finished_task_ids.discard(int(task_id))
            return False
        self._cancel_activity_hide()
        self._active_tasks[int(task_id)] = (int(priority), str(label), str(kind))
        self.activity_feedback.started(int(task_id), str(label), str(kind))
        self._refresh_activity_history()
        if hasattr(self, "busy_revealer"):
            self.activity_status_icon.set_visible(False)
            self.activity_status_icon.remove_css_class("activity-success")
            self.activity_status_icon.remove_css_class("activity-error")
            self.activity_dismiss.set_visible(False)
            self.busy_label.set_visible(True)
            prefix = f"{count} opérations · " if count > 1 else ""
            self.busy_label.set_label("En cours · " + prefix + str(label))
            self.busy_revealer.set_reveal_child(True)
            self.busy_spinner.set_visible(True)
            self.busy_spinner.start()
        return False

    def _task_progress(self, label: str) -> bool:
        if hasattr(self, "busy_revealer") and self._active_tasks:
            count = len(self._active_tasks)
            prefix = f"{count} opérations · " if count > 1 else ""
            self.busy_label.set_visible(True)
            self.busy_label.set_label("En cours · " + prefix + str(label))
        return False

    def _task_finished(self, task_id: int, remaining: int, label: str, success: bool, error: str | None) -> bool:
        if getattr(self, '_closed', False):return False
        current = self._active_tasks.get(int(task_id))
        kind = current[2] if current is not None else "task"
        if int(task_id) not in self._active_tasks:
            self._finished_task_ids.add(int(task_id))
            if len(self._finished_task_ids) > 100:
                self._finished_task_ids = set(sorted(self._finished_task_ids)[-50:])
        self._active_tasks.pop(int(task_id), None)
        self._last_finished_task_id = int(task_id)
        functional = self._functional_error_message(error or "") if not success else ""
        self.activity_feedback.finished(
            int(task_id),
            str(label),
            str(kind),
            success=bool(success),
            error=functional,
        )
        self._refresh_activity_history()
        if not hasattr(self, "busy_revealer"):
            return False
        if remaining and self._active_tasks:
            _priority, next_label, _kind = sorted(self._active_tasks.values(), key=lambda row: row[0])[0]
            prefix = f"{remaining} opérations · " if remaining > 1 else ""
            self.busy_label.set_visible(True)
            self.busy_label.set_label("En cours · " + prefix + next_label)
            self.activity_dismiss.set_visible(False)
            self.busy_spinner.set_visible(True)
            self.busy_spinner.start()
            return False
        if self._show_pending_activity_error():
            return False
        self.busy_spinner.stop()
        self.busy_spinner.set_visible(False)
        self.activity_status_icon.set_visible(True)
        self.activity_status_icon.set_from_icon_name("emblem-ok-symbolic")
        self.activity_status_icon.add_css_class("activity-success")
        self.activity_status_icon.remove_css_class("activity-error")
        self.busy_label.set_visible(True)
        self.busy_label.set_label(f"Terminé · {label}")
        self.activity_dismiss.set_visible(False)
        self.busy_revealer.set_reveal_child(True)
        self._schedule_activity_reduce()
        return False

    @staticmethod
    def _functional_error_message(message: str) -> str:
        text = str(message or "").strip()
        lowered = text.lower()
        if "adobe drm as not authorized/default" in lowered or "not authorized/default" in lowered:
            return "Le profil ADE sélectionné n’est pas autorisé pour cette opération"
        return text or "L’opération a échoué"

    def _busy_changed(self, count: int, label: str) -> bool:
        # Compatibility shim for older progress callbacks. New work should use
        # _task_progress so it cannot accidentally hide another active task.
        if count:
            return self._task_progress(label)
        return False

    def _on_close_request(self, *_args) -> bool:
        if self._closed:
            return False
        self._closed = True
        try:
            self.config.set("ui.width", self.get_width())
            self.config.set("ui.height", self.get_height())
            self.config.save()
        except Exception:
            pass
        try:
            if self._kobo_backup_timer_id is not None:
                GLib.source_remove(self._kobo_backup_timer_id)
                self._kobo_backup_timer_id = None
        except Exception:
            pass
        try:
            self._cancel_activity_hide()
        except Exception:
            pass
        try:
            self.runner.close()
        except Exception:
            pass
        try:
            self.history_cover_resolver.close()
        except Exception:
            pass
        try:
            self.covers.close()
        except Exception:
            pass
        try:
            self.service.close()
        except Exception:
            pass
        return False

    # ---------- provider startup ----------
    def _build_startup(self) -> Gtk.Widget:
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.set_valign(Gtk.Align.CENTER)
        outer.set_halign(Gtk.Align.FILL)
        clamp = Adw.Clamp(); clamp.set_maximum_size(620); clamp.set_tightening_threshold(460); outer.append(clamp)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14); add_css(card, "login-card"); clamp.set_child(card)
        icon = Gtk.Image.new_from_icon_name("accessories-dictionary-symbolic"); icon.set_pixel_size(52); icon.set_halign(Gtk.Align.START); card.append(icon)
        eyebrow = Gtk.Label(label="BIBLIOTHÈQUE MULTI-SOURCES", xalign=0); add_css(eyebrow, "dim-label", "caption"); card.append(eyebrow)
        title = Gtk.Label(label="cifereca", xalign=0); add_css(title, "login-brand"); card.append(title)
        subtitle = Gtk.Label(label="Connexion automatique aux providers configurés. Les identifiants sont gérés dans les préférences locales.", xalign=0)
        subtitle.set_wrap(True); add_css(subtitle, "dim-label"); card.append(subtitle)
        self.startup_status = Gtk.Label(label="Initialisation…", xalign=0); self.startup_status.set_wrap(True); card.append(self.startup_status)
        actions = AdaptiveActionBar(source=card)
        self.startup_connect_button = Gtk.Button(label="Connecter les sources"); self.startup_connect_button.add_css_class("suggested-action")
        self.startup_connect_button.connect("clicked", lambda _b: self.connect_configured_sources()); actions.append(self.startup_connect_button)
        settings = Gtk.Button(label="Configurer les sources"); settings.connect("clicked", lambda _b: self._open_prelogin_diagnostics()); actions.append(settings)
        card.append(actions)
        plugins = ", ".join(f"{d.display_name} ({'prêt' if d.state == 'ready' else 'squelette'})" for d in self.service.provider_descriptors()) or "aucun provider installé"
        info = Gtk.Label(label=f"Providers actifs : {plugins}", xalign=0); info.set_wrap(True); add_css(info, "caption", "dim-label"); card.append(info)
        version = Gtk.Label(label=f"v{__version__} · GTK 4 / libadwaita · Plugin API v1", xalign=0); add_css(version, "dim-label", "caption"); card.append(version)
        return outer

    def connect_configured_sources(self) -> bool:
        _TRACE_LOG.info("TRACE gui connect-configured-sources begin")
        if hasattr(self, "startup_status"):
            self.startup_status.set_label("Connexion aux sources configurées…")
        if hasattr(self, "dashboard_loading_status"):
            self.dashboard_loading_status.set_label("Connexion aux sources en arrière-plan…")
            self.dashboard_loading_status.set_visible(True)

        def ok(result: dict[str, Any]) -> None:
            _TRACE_LOG.info("TRACE gui connect-configured ok connected=%s errors=%s", len(result.get("connected", [])), len(result.get("errors", {}) or {}))
            if hasattr(self, "dashboard_loading_status"):
                self.dashboard_loading_status.set_visible(False)
            _TRACE_LOG.info("TRACE gui populate-accounts begin")
            self._populate_accounts()
            _TRACE_LOG.info("TRACE gui populate-accounts end")
            _TRACE_LOG.info("TRACE gui populate-loans begin count=%s", len(self.service.last_loans))
            self._populate_loans(self.service.last_loans)
            _TRACE_LOG.info("TRACE gui populate-loans end")
            connected = len(result.get("connected", []))
            errors = result.get("errors", {}) or {}
            self._activity_result(
                f"{connected} source(s) connectée(s)" + (f", {len(errors)} en erreur" if errors else ""),
                success=not bool(errors),
                persistent=bool(errors),
            )
            self._render_dashboard()
            GLib.idle_add(lambda: (self.run_opening_sync(), False)[1])

        def err(exc: BaseException) -> None:
            if hasattr(self, "startup_status"):
                self.startup_status.set_label(str(exc))
            if hasattr(self, "dashboard_loading_status"):
                self.dashboard_loading_status.set_label("Connexion aux sources indisponible. La navigation locale reste disponible.")
                self.dashboard_loading_status.set_visible(True)

        control = getattr(self, "startup_connect_button", None)
        self.runner.submit(
            "Connexion aux providers…", self.service.connect_configured, ok, err, control=control,
            priority=TaskPriority.PROVIDER_SYNC, kind="provider-connect",
        )
        return False

    def reconnect_sources(self) -> None:
        self.connect_configured_sources()

    def activate_startup_plugins(self) -> bool:
        if self.plugin_manager is None:
            return False
        _TRACE_LOG.info("TRACE gui plugin-activation begin")
        started = time.perf_counter()
        try:
            self.plugin_manager.activate()
            self.service.attach_plugin_manager(self.plugin_manager)
            self._plugins_changed()
        except Exception as exc:
            self.diagnostics.add("PLUGIN", "startup activation", "error", detail={"error": str(exc)})
            if hasattr(self, "dashboard_loading_status"):
                self.dashboard_loading_status.set_label("Certains modules n’ont pas pu être chargés. Le cœur reste disponible.")
                self.dashboard_loading_status.set_visible(True)
        finally:
            # Provider connection must not depend on successful page/header
            # reconstruction. A CoreUI exception must never strand startup in
            # local-only mode after plugins have been activated.
            _TRACE_LOG.info("TRACE gui schedule connect-configured")
            GLib.idle_add(self.connect_configured_sources)
            duration_ms = (time.perf_counter() - started) * 1000.0
            self.ui_perf.record("startup.plugins_activation", duration_ms)
            _TRACE_LOG.info("TRACE gui plugin-activation end duration_ms=%.1f", duration_ms)
        return False

    # ---------- compatibility / source configuration ----------
    def logout(self) -> None:
        # Kept as a compatibility alias: credentials are persistent provider
        # preferences now, so there is no login screen to return to.
        self.reconnect_sources()

    def _open_prelogin_diagnostics(self) -> None:
        win = Adw.Window(transient_for=self, modal=False)
        win.set_title("Diagnostic / Configuration")
        win.set_default_size(900, 650)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Adw.HeaderBar()
        header.set_title_widget(Gtk.Label(label="Configuration et diagnostic"))
        close = Gtk.Button.new_from_icon_name("window-close-symbolic")
        close.set_tooltip_text("Fermer")
        close.connect("clicked", lambda _b: win.close())
        header.pack_end(close)
        content.append(header)
        page = self._build_settings_page(standalone=True)
        page.set_vexpand(True)
        content.append(page)
        win.set_content(content)

        keys = Gtk.EventControllerKey()
        def on_key(_controller, keyval, _keycode, _state):
            if keyval == Gdk.KEY_Escape:
                win.close()
                return True
            return False
        keys.connect("key-pressed", on_key)
        win.add_controller(keys)
        # Garder une référence explicite pendant toute la vie de la fenêtre.
        self._standalone_settings_window = win
        win.connect("close-request", lambda _w: setattr(self, "_standalone_settings_window", None) or False)
        win.present()

    # ---------- shell / navigation ----------
    def _build_main_shell(self) -> Gtk.Widget:
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.shell_root = root
        from .navigation_history import NavigationHistory
        self._navigation_history = NavigationHistory()
        self._restoring_navigation = False
        self.shell_header = self._create_shell_header()
        root.append(self.shell_header)

        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        body.set_vexpand(True)
        root.append(body)
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.sidebar = sidebar
        sidebar.set_size_request(160, -1)
        add_css(sidebar, "sidebar")
        body.append(sidebar)
        brand = Gtk.Label(label=f"v{__version__}", xalign=0)
        brand.set_margin_top(14); brand.set_margin_start(16); brand.set_margin_bottom(4)
        add_css(brand, "caption", "dim-label")
        sidebar.append(brand)
        self.nav_list = Gtk.ListBox()
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.set_activate_on_single_click(True)
        self.nav_list.connect("row-selected", self._nav_selected)
        sidebar.append(self.nav_list)
        self.nav_rows: dict[str, Gtk.ListBoxRow] = {}
        self._nav_groups = {}
        self._nav_group_expanded = {}
        self.view_stack = Gtk.Stack()
        self.view_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.view_stack.set_hhomogeneous(False)
        self.view_stack.set_hexpand(True); self.view_stack.set_vexpand(True)
        body.append(self.view_stack)
        self._rebuild_plugin_pages()

        self.busy_revealer = Gtk.Revealer()
        self.busy_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        busy = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=9)
        add_css(busy, "busy-bar")
        self.busy_spinner = Gtk.Spinner(); self.busy_spinner.set_visible(False); busy.append(self.busy_spinner)
        self.activity_status_icon = Gtk.Image.new_from_icon_name("emblem-ok-symbolic")
        self.activity_status_icon.set_visible(False); busy.append(self.activity_status_icon)
        self.busy_label = Gtk.Label(label="", xalign=0)
        self.busy_label.set_hexpand(True); self.busy_label.set_wrap(True); busy.append(self.busy_label)

        self.activity_history_button = Gtk.MenuButton(label="Historique")
        self.activity_history_button.set_icon_name("view-list-symbolic")
        self.activity_history_button.set_tooltip_text("Historique des opérations")
        self.activity_history_button.set_visible(False)
        history_popover = Gtk.Popover()
        self.activity_history_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        self.activity_history_box.set_margin_top(8); self.activity_history_box.set_margin_bottom(8)
        self.activity_history_box.set_margin_start(10); self.activity_history_box.set_margin_end(10)
        history_popover.set_child(self.activity_history_box)
        self.activity_history_button.set_popover(history_popover)
        busy.append(self.activity_history_button)

        self.activity_dismiss = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.activity_dismiss.set_tooltip_text("Acquitter le résultat courant")
        self.activity_dismiss.set_visible(False)
        self.activity_dismiss.connect("clicked", lambda _button: self._dismiss_activity_feedback())
        busy.append(self.activity_dismiss)
        root.append(self.busy_revealer); self.busy_revealer.set_child(busy)

        self.navigate("dashboard")
        return root

    class _UiHost:
        def __init__(self, window: "MainWindow", action_context=None) -> None:
            self.window = window
            self._action_context = action_context
        def toast(self, message: str, timeout: int = 3) -> None:
            self.window.toast(message, timeout)
        def action_context(self):
            if self._action_context is None:
                from cifereca.plugin_api import UiActionContext
                route = self.window.view_stack.get_visible_child_name() if hasattr(self.window, "view_stack") else ""
                self._action_context = UiActionContext(target="page", route=route)
            return self._action_context
        def refresh_context(self) -> None:
            route = self.action_context().route
            if route in {"library", "recent"}:
                self.window.refresh_library(False)
                self.window.refresh_recent()
            elif route == "search":
                self.window.run_search(False)
        def current_book(self):
            return self.action_context().book
        def detail_sections(self):
            return ()
        def refresh_detail(self) -> None:
            self.refresh_context()
        def open_plugin_settings(self, plugin_id: str) -> None:
            self.window.navigate("plugins")
            page = getattr(self.window, "plugins_page", None)
            opener = getattr(page, "focus_plugin", None)
            if callable(opener):
                opener(plugin_id)
        def navigate(self, route: str) -> None:
            self.window.navigate(route)
        def build_action_bar(self, actions):
            bar = AdaptiveActionBar()
            for action in actions:
                button = action_button(action.title, action.icon_name)
                button.connect('clicked', lambda _button, contribution=action: contribution.callback(self))
                bar.append(button)
            return bar
        def build_book_master_detail(self, *, title: str, subtitle: str = "", empty_title: str = "Aucun document", empty_desc: str = "Aucun document à afficher."):
            return PublicBookMasterDetailView(
                self.window, title=title, subtitle=subtitle,
                empty_title=empty_title, empty_desc=empty_desc,
            )
        def build_standard_page(self, route: str):
            return self.window._build_standard_page(route)
        def import_acsm(self):
            self.window.choose_acsm()
        def identity_overview(self) -> list[dict[str, Any]]:
            from copy import deepcopy
            return deepcopy(self.window.service.dashboard_state())
        def open_identity_library(self, identity_id: str) -> None:
            self.window.open_identity_library(identity_id)
        def supports_identity_view_filters(self) -> bool:
            return True
        def open_identity_view(self, identity_id: str, *, route: str, providers=()) -> None:
            self.window.open_identity_view(identity_id, route=route, providers=providers)
        def synchronize(self, *, deep: bool = False, control=None) -> None:
            self.window.run_opening_sync(control, deep=deep)
        def subscribe_identity_overview(self, callback):
            listeners = getattr(self.window, "_overview_listeners", None)
            if listeners is None:
                listeners = []; self.window._overview_listeners = listeners
            listeners.append(callback)
            def cancel():
                if callback in listeners: listeners.remove(callback)
            return cancel
        def plugin_statuses(self) -> list[dict[str, Any]]:
            manager = self.window.plugin_manager
            return manager.diagnostic() if manager is not None else []

    def _plugins_changed(self) -> None:
        if self.plugin_manager is None:
            return
        try:
            self.service.attach_plugin_manager(self.plugin_manager)
        except Exception as exc:
            self.diagnostics.add("PLUGIN", "provider refresh after plugin change", "error", detail={"error": str(exc)}, level="detailed")
        old = self.shell_header
        self.shell_root.remove(old)
        self.shell_header = self._create_shell_header()
        self.shell_root.prepend(self.shell_header)
        self._ensure_pages_menu_rightmost()
        self._rebuild_plugin_pages()
        self._populate_accounts()

    def _add_nav_row(self, route: str, label: str, icon_name: str) -> None:
        if route in self.nav_rows:
            return
        row = Gtk.ListBoxRow(); row.set_name(route)
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        line.set_margin_top(7); line.set_margin_bottom(7); line.set_margin_start(8); line.set_margin_end(8)
        icon = Gtk.Image.new_from_icon_name(icon_name); icon.set_pixel_size(18); line.append(icon)
        text = Gtk.Label(label=label, xalign=0); text.set_hexpand(True); line.append(text)
        row.set_child(line); self.nav_list.append(row); self.nav_rows[route] = row

    def _build_standard_page(self, route):
        builders = {"dashboard": self._build_dashboard_page, "loans": self._build_loans_page,
                    "search": self._build_search_page, "history": self._build_history_page,
                    "bookings": self._build_bookings_page, "library": self._build_library_page,
                    "recent": self._build_recent_page, "sync": self._build_sync_page,
                    "tools": self._build_tools_page, "settings": self._build_settings_page,
                    "plugins": lambda: PluginManagerPage(self, self.plugin_manager, self._plugins_changed)
                    if self.plugin_manager else Gtk.Label(label="Gestionnaire indisponible")}
        widget = builders[route]()
        attr = {"loans": "loan", "bookings": "booking"}.get(route, route)
        setattr(self, attr + "_page", widget)
        if route == "library": self.kobo_page = widget
        return widget

    def _rebuild_plugin_pages(self) -> None:
        self._overview_listeners = []
        if not hasattr(self, "view_stack"):
            return
        query_states = {route: controls.state() for route in ("search", "library")
                        if (controls := getattr(self, route + "_controls", None)) is not None}
        previous = self.view_stack.get_visible_child_name() or "dashboard"
        for widget in self._plugin_page_widgets.values():
            self.view_stack.remove(widget)
        self._plugin_page_widgets.clear()
        clear_box(self.nav_list)
        self.nav_rows.clear()
        self._nav_groups.clear()
        from ..builtin_ui.cifereca_builtin_ui import Pages
        from dataclasses import replace
        fallback = [replace(page, priority=-10000) for page in Pages().pages()]
        try:
            contributed = self.plugin_manager.page_contributions() if self.plugin_manager else []
        except Exception:
            contributed = []
        if not contributed:
            contributed = list(fallback)
        # Management pages remain reachable to recover from broken/disabled UI.
        contributed += [p for p in fallback if p.route in {"plugins", "settings"} and all(x.route != p.route for x in contributed)]
        resolved = resolve_pages(contributed)
        self._pages = {}
        host = self._UiHost(self)
        group = None
        for page in resolved:
            try:
                widget = page.factory(host)
                if not isinstance(widget, Gtk.Widget): raise TypeError("Page factory must return Gtk.Widget")
            except Exception as exc:
                self.diagnostics.add("PLUGIN", "ui.page build", "error", detail={"route": page.route, "error": str(exc)})
                recovery = next((p for p in fallback if p.route == page.route and p.route in {"plugins", "settings"}), None)
                if recovery is None: continue
                widget = recovery.factory(host); page = recovery
            self.view_stack.add_named(widget, page.route)
            self._plugin_page_widgets[page.route] = widget
            self._pages[page.route] = page
            if page.group != group:
                group = page.group
                expanded = self._nav_group_expanded.setdefault(group, group != "configuration")
                group_label = {"usage":"Pages", "configuration":"Configuration / Technique"}.get(group, group)
                button = Gtk.ToggleButton()
                button_label = Gtk.Label(label=group_label, xalign=0)
                button_label.set_ellipsize(Pango.EllipsizeMode.END)
                button.set_child(button_label)
                button.set_active(expanded)
                button.set_tooltip_text(f"{group_label} — Déplier / replier la rubrique")
                button.connect("toggled", lambda b, g=group: self._toggle_nav_group(g, b.get_active()))
                row = Gtk.ListBoxRow(); row.set_selectable(False); row.set_activatable(False); row.set_child(button)
                self.nav_list.append(row)
            self._add_nav_row(page.route, page.title, page.icon_name)
            self._nav_groups[page.route] = group
            self.nav_rows[page.route].set_visible(self._nav_group_expanded[group])
        for route, state in query_states.items():
            controls = getattr(self, route + "_controls", None)
            if route in self._pages and controls is not None:
                controls.set_state(state)
        if self._pages:
            self.navigate(previous if previous in self._pages else next(iter(self._pages)))

    def _toggle_nav_group(self, group, expanded):
        self._nav_group_expanded[group] = expanded
        for route, row_group in self._nav_groups.items():
            if row_group == group:
                self.nav_rows[route].set_visible(expanded)

    def _install_shell_shortcuts(self):
        from .shortcuts import SHORTCUTS
        callbacks = {
            "quit": self._quit_application,
            "close": self.close,
            "pages-menu": self._open_pages_menu,
            "focus-search": self._focus_search,
            "settings": self._show_settings,
            "next-page": lambda: self._cycle_page(1),
            "previous-page": lambda: self._cycle_page(-1),
            "go-back": lambda: getattr(self, "go_back", lambda: None)(),
            "go-forward": lambda: getattr(self, "go_forward", lambda: None)(),
            "shortcuts": self._show_shortcuts,
            "reconnect-sources": self.reconnect_sources,
        }
        for name, callback in callbacks.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        for name, keys, _description in SHORTCUTS:
            self.get_application().set_accels_for_action("win." + name, list(keys))

    def _quit_application(self):
        # Close through the normal handler before application shutdown.
        application = self.get_application()
        self.close()
        if application is not None:
            application.quit()

    def _open_pages_menu(self):
        button = getattr(self, "pages_menu_button", None)
        if button is not None:
            button.popup()

    def _focus_search(self):
        if not hasattr(self, "_pages"):
            return
        route = self.view_stack.get_visible_child_name()
        if route not in {"search", "library"}:
            if "search" not in self._pages:
                return
            self.navigate("search"); route = "search"
        controls = getattr(self, route + "_controls", None)
        if controls is not None:
            controls.entry.grab_focus()

    def _show_settings(self):
        if "settings" in getattr(self, "_pages", {}):
            self.navigate("settings")
        else:
            self._open_prelogin_diagnostics()

    def _cycle_page(self, direction):
        from .shortcuts import adjacent_route
        routes = list(getattr(self, "_pages", {}))
        if routes:
            self.navigate(adjacent_route(routes, self.view_stack.get_visible_child_name(), direction))

    def _show_shortcuts(self):
        from .shortcuts import shortcuts_text
        dialog = Adw.MessageDialog.new(self, "Raccourcis clavier", shortcuts_text())
        dialog.add_response("close", "Fermer"); dialog.present()

    def _create_shell_header(self):
        host = self._UiHost(self)
        self._titlebar_service = None
        if self.plugin_manager:
            try:
                selected = self.plugin_manager.select("ui.titlebar")
                self._titlebar_service = self.plugin_manager.service(selected.descriptor.id) if selected else None
            except Exception:
                self._titlebar_service = None
        try:
            header = self._titlebar_service.build(host) if self._titlebar_service else Adw.HeaderBar()
            if not isinstance(header, Adw.HeaderBar): raise TypeError("Titlebar must return Adw.HeaderBar")
        except Exception as exc:
            self.diagnostics.add("PLUGIN", "titlebar build", "error", detail={"error": str(exc)})
            header = Adw.HeaderBar(); self._titlebar_service = None
        self.header_title = Gtk.Label(label="cifereca")
        self.header_subtitle = Gtk.Label(label="")
        title = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        title.append(self.header_title); title.append(self.header_subtitle); header.set_title_widget(title)
        navigation = Gtk.Box(spacing=2)
        self.back_button = Gtk.Button(icon_name="go-previous-symbolic")
        self.back_button.set_tooltip_text("Précédent (Alt+Gauche)")
        self.back_button.connect("clicked", lambda _b: self.go_back())
        self.forward_button = Gtk.Button(icon_name="go-next-symbolic")
        self.forward_button.set_tooltip_text("Suivant (Alt+Droite)")
        self.forward_button.connect("clicked", lambda _b: self.go_forward())
        navigation.append(self.back_button); navigation.append(self.forward_button)
        header.pack_start(navigation)
        self.header_actions = Gtk.Box(spacing=4); header.pack_start(self.header_actions)
        updater = getattr(self, "_update_navigation_buttons", None)
        if callable(updater): updater()
        def compact_dropdown(tooltip: str) -> Gtk.DropDown:
            dropdown = Gtk.DropDown()
            factory = Gtk.SignalListItemFactory()
            def setup(_factory, list_item):
                label = Gtk.Label(xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.END)
                label.set_single_line_mode(True)
                list_item.set_child(label)
            def bind(_factory, list_item):
                item = list_item.get_item()
                label = list_item.get_child()
                text = item.get_string() if isinstance(item, Gtk.StringObject) else str(item or "")
                label.set_label(text)
                label.set_tooltip_text(text)
            factory.connect("setup", setup)
            factory.connect("bind", bind)
            dropdown.set_factory(factory)

            list_factory = Gtk.SignalListItemFactory()
            def setup_list(_factory, list_item):
                label = Gtk.Label(xalign=0)
                label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
                label.set_single_line_mode(True)
                label.set_max_width_chars(44)
                list_item.set_child(label)
            list_factory.connect("setup", setup_list)
            list_factory.connect("bind", bind)
            dropdown.set_list_factory(list_factory)
            dropdown.set_tooltip_text(tooltip)
            return dropdown

        self.identity_dropdown = compact_dropdown("Identité active")
        self.identity_dropdown.connect("notify::selected", self._identity_changed)
        self.account_dropdown = compact_dropdown("Délégations de l’identité active")
        self.account_dropdown.connect("notify::selected", self._account_changed)
        self.pages_menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        self.pages_menu_button.set_tooltip_text("Menu des pages (F10)")
        self.shell_menu = Gtk.PopoverMenu.new_from_model(Gio.Menu())
        self.pages_menu_button.set_popover(self.shell_menu)
        # One ordered box makes the menu the rightmost application control.
        controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.statistics_button=Gtk.Button(label="— livres · — prêts")
        self.statistics_button.add_css_class('flat')
        self.statistics_button.set_tooltip_text("Statistiques de l’identité : inventaire local et derniers prêts connus")
        self.statistics_button.connect('clicked',lambda _b:self._show_statistics())
        controls.append(self.identity_dropdown); controls.append(self.account_dropdown)
        controls.append(self.statistics_button)
        controls.append(self.pages_menu_button)
        self._header_controls = controls
        header.pack_end(controls)
        self._ensure_pages_menu_rightmost()
        return header

    def _ensure_pages_menu_rightmost(self):
        controls = getattr(self, "_header_controls", None)
        menu = getattr(self, "pages_menu_button", None)
        if controls is None or menu is None:
            return
        children = getattr(controls, "children", None)
        if isinstance(children, list):
            if menu in children:
                children.remove(menu)
            children.append(menu)
            return
        get_last = getattr(controls, "get_last_child", None)
        if callable(get_last) and get_last() is menu:
            return
        get_parent = getattr(menu, "get_parent", None)
        if callable(get_parent) and get_parent() is controls:
            controls.remove(menu)
        controls.append(menu)

    def _refresh_header_actions(self, route):
        clear_box(self.header_actions)
        defaults = ["search.open", "library.open", "library.import-acsm", "settings.open", "plugins.open"]
        try:
            selected = self._titlebar_service.selected_actions() if self._titlebar_service else defaults
        except Exception:
            selected = defaults
        selected, obsolete = validate_action_selection(self._pages.values(), selected)
        previous_obsolete = getattr(self, "_obsolete_titlebar_actions", ())
        if obsolete and obsolete != previous_obsolete:
            self.diagnostics.add(
                "UI",
                "titlebar action configuration",
                "warning",
                detail={"obsolete_action_ids": list(obsolete)},
                level="normal",
            )
        self._obsolete_titlebar_actions = obsolete
        host = self._UiHost(self)
        for key, action in visible_actions(self._pages.values(), route, selected):
            button = Gtk.Button.new_from_icon_name(action.icon_name); button.set_tooltip_text(action.title)
            button.connect("clicked", lambda _b, a=action: self._invoke_ui_action(a, host))
            self.header_actions.append(button)
        model = Gio.Menu()
        group = Gio.SimpleActionGroup()
        count = 0
        for title, entries in menu_sections(self._pages.values(), route):
            section = Gio.Menu()
            for label, action in entries:
                name = f"item{count}"; count += 1
                command = Gio.SimpleAction.new(name, None)
                command.connect("activate", lambda _a, _p, a=action: self._invoke_ui_action(a, host))
                group.add_action(command)
                section.append(label, f"pagemenu.{name}")
            model.append_section(title, section)
        tools = Gio.Menu()
        tools.append("Reconnecter les sources", "win.reconnect-sources")
        tools.append("Raccourcis clavier", "win.shortcuts")
        tools.append("Quitter", "win.quit")
        model.append_section(None, tools)
        self.insert_action_group("pagemenu", group)
        self._page_menu_actions = group
        self.shell_menu.set_menu_model(model)
        self._ensure_pages_menu_rightmost()

    def _invoke_ui_action(self, action, host):
        self.shell_menu.popdown()
        try: action.callback(host)
        except Exception as exc: self.show_error(str(exc), "Action impossible")

    def _show_route(self, route: str) -> None:
        if route not in self._pages: return
        self.view_stack.set_visible_child_name(route)
        page = self._pages.get(route)
        self.header_subtitle.set_label(page.title if page else "")
        self._refresh_header_actions(route)
        widget = self._plugin_page_widgets.get(route)
        if widget and isinstance(widget.get_root(), Gtk.Window):
            widget.child_focus(Gtk.DirectionType.TAB_FORWARD)
        if route == "dashboard":
            self._render_dashboard()
        elif route == "library":
            self._render_library(self._library_entries_cache)
            if not self._library_entries_cache:
                self.refresh_library(False)
        elif route == "recent":
            self._render_recent(self._recent_entries_cache)
            if not self._recent_entries_cache:
                self.refresh_recent()
        elif route == "history" and not self.service.last_history:
            self.refresh_history(False)
        elif route == "bookings" and not self.service.last_bookings:
            self.refresh_bookings(False)
        elif route == "sync":
            self._refresh_sync_page()
        elif route == "tools":
            self._refresh_tools_page()
        elif route == "settings":
            self.refresh_diagnostics_text()
        elif route == "plugins" and hasattr(self.plugins_page, "refresh"):
            self.plugins_page.refresh()

    def _navigation_state(self, route: str):
        from .navigation_history import NavigationState
        identity = self.service.current_identity
        controls = getattr(self, route + "_controls", None)
        query_state = controls.state() if controls is not None else None
        return NavigationState(route, identity.identity_id if identity else None, query_state)

    def _update_navigation_buttons(self) -> None:
        history = getattr(self, "_navigation_history", None)
        if hasattr(self, "back_button"):
            self.back_button.set_sensitive(bool(history and history.can_back()))
        if hasattr(self, "forward_button"):
            self.forward_button.set_sensitive(bool(history and history.can_forward()))

    def _visit_route(self, route: str) -> None:
        if route not in self._pages:
            return
        if not getattr(self, "_restoring_navigation", False):
            self._navigation_history.visit(self._navigation_state(route))
        self._show_route(route)
        self._update_navigation_buttons()

    def _restore_navigation_state(self, state) -> None:
        if state is None or state.route not in self._pages:
            self._update_navigation_buttons(); return
        self._restoring_navigation = True
        try:
            current = self.service.current_identity
            if state.identity_id and (current is None or current.identity_id != state.identity_id):
                # History restores only still-existing identities.
                if self.service.identity_by_id(state.identity_id) is not None:
                    self._activate_identity(state.identity_id)
            controls = getattr(self, state.route + "_controls", None)
            if controls is not None and state.query_state is not None:
                controls.set_state(state.query_state)
            row = self.nav_rows.get(state.route)
            if row is not None:
                self.nav_list.select_row(row)
            self._show_route(state.route)
            if state.route == "library": self._render_library()
            elif state.route == "search": self._render_search_results()
        finally:
            self._restoring_navigation = False
            self._update_navigation_buttons()

    def can_go_back(self) -> bool:
        return bool(getattr(self, "_navigation_history", None) and self._navigation_history.can_back())

    def can_go_forward(self) -> bool:
        return bool(getattr(self, "_navigation_history", None) and self._navigation_history.can_forward())

    def go_back(self) -> None:
        if getattr(self, "_navigation_history", None):
            self._restore_navigation_state(self._navigation_history.back())

    def go_forward(self) -> None:
        if getattr(self, "_navigation_history", None):
            self._restore_navigation_state(self._navigation_history.forward())

    def _nav_selected(self, _listbox: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if row is None:
            return
        self._visit_route(row.get_name() or "library")

    def navigate(self, route: str) -> None:
        row = self.nav_rows.get(route)
        if row:
            if self.nav_list.get_selected_row() is row:
                self._visit_route(route)
            else:
                self.nav_list.select_row(row)
            return
        self._visit_route(route)

    def _replace_dropdown_labels(self, dropdown, labels):
        model = dropdown.get_model()
        if isinstance(model, Gtk.StringList):
            previous = [model.get_string(i) for i in range(model.get_n_items())]
            if previous != labels:model.splice(0, model.get_n_items(), labels)
        else:
            dropdown.set_model(Gtk.StringList.new(labels))

    def _populate_account_dropdown(self, identity_id: str | None) -> None:
        self._account_changing = True
        try:
            public = {a.provider_id for a in self.service.accounts
                      if getattr(getattr(self.service._provider_plugin_status(a.provider_id), "descriptor", None), "instantiation", "multiple") == "singleton_public"}
            self._visible_accounts = delegated_accounts(self.service.accounts, identity_id, public)
            self.account_dropdown.set_visible(bool(self._visible_accounts))
            labels = [
                f"{a.provider_label} · {a.display_name or ('Compte principal' if a.is_primary else 'Compte lié')}"
                for a in self._visible_accounts
            ]
            self._replace_dropdown_labels(self.account_dropdown, labels)
            current = self.service.current_account
            account_index = next((i for i, account in enumerate(self._visible_accounts) if account is current), Gtk.INVALID_LIST_POSITION)
            self.account_dropdown.set_selected(account_index if self._visible_accounts else Gtk.INVALID_LIST_POSITION)
        finally:
            self._account_changing = False

    def _populate_accounts(self) -> None:
        self._identity_changing = True
        try:
            identities = list(self.service.identities)
            self._replace_dropdown_labels(self.identity_dropdown, [identity.label for identity in identities])
            selected_identity = self.service.current_identity.identity_id if self.service.current_identity else None
            selected_index = next((i for i, identity in enumerate(identities) if identity.identity_id == selected_identity), 0)
            self.identity_dropdown.set_selected(selected_index if identities else Gtk.INVALID_LIST_POSITION)
        finally:
            self._identity_changing = False

        self._populate_account_dropdown(selected_identity)
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()

    def _identity_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._identity_changing:
            return
        idx = dropdown.get_selected()
        if idx < 0 or idx >= len(self.service.identities):
            return
        identity = self.service.identities[idx]
        self._activate_identity(identity.identity_id)

    def open_identity_library(self, identity_id: str) -> None:
        self._activate_identity(identity_id)
        self.navigate("library")

    def open_identity_view(self, identity_id: str, *, route: str, providers=()) -> None:
        from .query_model import QueryState
        if route not in {"search", "library"}:
            raise ValueError("Vue filtrée inconnue")
        if isinstance(providers, str) or any(not isinstance(p, str) or not p for p in providers):
            raise ValueError("Sources invalides")
        allowed = {d.id for d in self.service.provider_descriptors()} | {"local"}
        selected = tuple(dict.fromkeys(providers))
        if not set(selected) <= allowed:
            raise ValueError("Source inconnue")
        self._activate_identity(identity_id)
        controls = self.search_controls if route == "search" else self.library_controls
        controls.set_state(QueryState(scope="digital", providers=selected))
        self.navigate(route)
        if route == "search": self.run_search(False)
        else: self._render_library()

    def _activate_identity(self, identity_id: str) -> None:
        identity = self.service.identity_by_id(identity_id)
        if identity is None:
            raise ValueError("Identité inconnue")
        current = self.service.current_identity
        current_identity_id = current.identity_id if current is not None else None
        if not identity_change_required(current_identity_id, identity.identity_id):
            _TRACE_LOG.debug("Identity selection notification ignored: already current identity=%s", identity.identity_id)
            return
        self.service.select_identity(identity.identity_id)
        self._identity_context_generation += 1
        identity_generation = self._identity_context_generation
        self._populate_accounts()
        self._render_dashboard()
        self._search_generation = getattr(self, "_search_generation", 0) + 1
        self._search_local_objects = {}; self.search_objects = {}; self._remote_search_results = []
        self._search_mode = "local"; self._remote_query_key = None; self._selected_search_id = None
        if hasattr(self, "search_page"):
            self.search_page.clear_rows()
            self.search_page.detail.empty("Nouvelle identité", "Lancez une recherche dans le connu.")
        # Do not rebuild the identity dropdown from its own notify::selected callback:
        # GTK may deliver the resulting notification asynchronously after the guard
        # is released, causing an unbounded refresh/task loop.
        self._populate_account_dropdown(identity.identity_id)
        self.service.last_history = []
        self.service.last_bookings = []
        self.history_objects = {}
        self.booking_objects = {}
        self._history_all_rows = []
        self._history_rows = []
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        if hasattr(self, "history_page"):
            self.history_page.clear_rows()
        if hasattr(self, "booking_page"):
            self.booking_page.clear_rows()
        self._library_entries_cache = []
        self._recent_entries_cache = []
        if hasattr(self, "library_page"):
            self._render_library([])
            self.library_page.detail.empty("Nouvelle identité", "Chargement de la bibliothèque…")
        def identity_loaded(rows: list[Loan]) -> None:
            if not self.service.current_identity or self.service.current_identity.identity_id != identity.identity_id:
                return
            if not self._identity_request_current(identity.identity_id, identity_generation):
                return
            self._populate_loans(rows)
            self.refresh_library(False)
            self.refresh_recent()
            route = self.view_stack.get_visible_child_name() if hasattr(self, "view_stack") else None
            if route == "history":
                self.refresh_history(False)
            elif route == "bookings":
                self.refresh_bookings(False)
            self._activity_result(f"Identité chargée : {identity.label}")
        self.runner.submit(
            f"Chargement de {identity.label}…",
            lambda: self.service.refresh_identity_loans(force=False, identity_id=identity.identity_id),
            identity_loaded,
            priority=TaskPriority.USER_ACTION,
            kind="identity",
        )

    def _account_changed(self, dropdown: Gtk.DropDown, _pspec) -> None:
        if self._account_changing:
            return
        idx = dropdown.get_selected()
        accounts = getattr(self, "_visible_accounts", [])
        if idx < 0 or idx >= len(accounts):
            return
        account = accounts[idx]
        current = self.service.current_account
        if current is not None and (
            current.unique_id == account.unique_id
            and current.provider_id == account.provider_id
            and current.credential_id == account.credential_id
        ):
            _TRACE_LOG.debug("Account selection notification ignored: already current account=%s", account.unique_id)
            return
        self.service.select_account(account.unique_id, account.provider_id, account.credential_id)
        self.service.last_history = []
        self.service.last_bookings = []
        self._history_rows = []
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        if hasattr(self, "history_page"):
            self.history_page.show_rows([])
            self.history_page.detail.empty("Historique non chargé", "Ouvrez l’onglet Historique pour charger ce compte.")
        if hasattr(self, "booking_page"):
            self.booking_page.show_rows([])
            self.booking_page.detail.empty("Réservations non chargées", "Ouvrez l’onglet Réservations pour charger ce compte.")
        self.toast(f"Compte : {account.display_name or account.provider_label}")


    # ---------- 0.8.2 opening dashboard ----------
    def _build_dashboard_page(self) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        outer.set_margin_top(24); outer.set_margin_bottom(24); outer.set_margin_start(24); outer.set_margin_end(24)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); outer.append(heading)
        title = Gtk.Label(label="Bibliothèque familiale", xalign=0); title.set_hexpand(True); title.set_wrap(True); add_css(title, "page-title"); heading.append(title)
        subtitle = Gtk.Label(label="État des identités et de la synchronisation automatique", xalign=0); add_css(subtitle, "dim-label"); outer.append(subtitle)
        self.dashboard_loading_status = Gtk.Label(label="Connexion aux sources en arrière-plan…", xalign=0)
        self.dashboard_loading_status.set_wrap(True); add_css(self.dashboard_loading_status, "dim-label", "caption")
        outer.append(self.dashboard_loading_status)
        actions = AdaptiveActionBar(source=heading)
        refresh = self.action_button("Synchronisation rapide", "view-refresh-symbolic", suggested=True)
        refresh.connect("clicked", lambda _b: self.run_opening_sync(refresh, deep=False)); actions.append(refresh)
        deep = self.action_button("Vérifier toute la bibliothèque", "system-search-symbolic")
        deep.connect("clicked", lambda _b: self.run_opening_sync(deep, deep=True)); actions.append(deep)
        actions.set_halign(Gtk.Align.END); heading.append(actions)
        self.dashboard_cards = Gtk.FlowBox(); self.dashboard_cards.set_selection_mode(Gtk.SelectionMode.NONE)
        self.dashboard_cards.set_min_children_per_line(1); self.dashboard_cards.set_max_children_per_line(3)
        self.dashboard_cards.set_homogeneous(True); self.dashboard_cards.set_column_spacing(12); self.dashboard_cards.set_row_spacing(12)
        outer.append(self.dashboard_cards)
        scroll.set_child(outer)
        return scroll

    @staticmethod
    def _sync_status_label(status: str | None) -> str:
        return {"running": "En cours", "success": "Réussie", "partial": "Partielle", "failed": "Échouée"}.get(str(status or ""), "Jamais synchronisée")

    def _refresh_statistics(self):
        if not hasattr(self,'statistics_button'):return
        identity=self.service.current_identity
        if not identity:return
        stats=self.service.identity_statistics(identity.identity_id)
        loans=stats['known_loans']
        self.statistics_button.set_label(f"{stats['local_books']} livres · {loans if loans is not None else '—'} prêts")

    def _show_statistics(self):
        identity=self.service.current_identity
        if not identity:return
        stats=self.service.identity_statistics(identity.identity_id)
        loans=stats['known_loans']
        rows=[f"Livres locaux distincts : {stats['local_books']}",f"Emplacements locaux indexés présents : {stats['local_placements']}",
              f"Emprunts connus : {loans if loans is not None else 'non actualisés'}",f"Sources de prêts observées : {stats['loan_sources_observed']}",
              f"Droits d’achat distants actifs : {stats['remote_owned_rights']}"]
        for title,key in [('Formats','formats'),('Sources locales','sources'),('Couvertures EPUB','epub_covers')]:
            values=stats[key]
            rows.append(title+' : '+(', '.join(f'{name} {count}' for name,count in sorted(values.items())) or '—'))
        actions=stats.get('actions') or {}
        if actions:
            rows.append('Actions sur la période :')
            rows.append(f"  Acquisitions : {actions.get('acquisitions_succeeded',0)} réussies · {actions.get('acquisitions_failed',0)} échouées")
            rows.append(f"  Imports : {actions.get('imports_succeeded',0)} réussis · {actions.get('imports_failed',0)} échoués")
            rows.append(f"  Synchronisations : {actions.get('synchronizations_succeeded',0)} réussies · {actions.get('synchronizations_failed',0)} échouées")
            rows.append(f"  Transformations : {actions.get('transformations_succeeded',0)} réussies · {actions.get('transformations_failed',0)} échouées")
            rows.append(f"  Transferts : {actions.get('transfers_succeeded',0)} réussis · {actions.get('transfers_failed',0)} échoués")
            if actions.get('from_timestamp') and actions.get('to_timestamp'):
                rows.append('  Fenêtre : '+datetime.fromtimestamp(actions['from_timestamp']).strftime('%d/%m/%Y %H:%M')+' → '+datetime.fromtimestamp(actions['to_timestamp']).strftime('%d/%m/%Y %H:%M'))
        if stats.get('loans_observed_at'):
            rows.append("Prêts observés au plus tôt : "+datetime.fromtimestamp(stats['loans_observed_at']).strftime('%d/%m/%Y %H:%M'))
        rows.append("Inventaire issu du dernier scan ; prêts issus des dernières réponses disponibles, éventuellement partielles. Les statistiques d'actions utilisent la fenêtre indiquée.")
        dialog=Adw.MessageDialog.new(self,f"Statistiques · {identity.label}",'\n'.join(rows))
        dialog.add_response('close','Fermer');dialog.present()

    def _render_dashboard(self) -> None:
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()
        for callback in list(getattr(self, "_overview_listeners", [])):
            try: callback()
            except Exception as exc: self.diagnostics.add("UI", "identity overview callback", "error", detail={"error": type(exc).__name__})
        if not hasattr(self, "dashboard_cards"):
            return
        child = self.dashboard_cards.get_first_child()
        while child is not None:
            nxt = child.get_next_sibling(); self.dashboard_cards.remove(child); child = nxt
        for card in self.service.dashboard_state():
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            box.set_margin_top(12); box.set_margin_bottom(12); box.set_margin_start(14); box.set_margin_end(14)
            add_css(box, "cover-frame")
            head = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            name = Gtk.Button(label=str(card.get("label") or "Utilisateur")); name.set_hexpand(True)
            name.get_child().set_wrap(True)
            name.set_tooltip_text("Sélectionner cette identité et ouvrir sa bibliothèque")
            name.connect("clicked", lambda _b, iid=card["identity_id"]: self.open_identity_library(iid))
            add_css(name, "flat", "book-title"); head.append(name)
            sync = card.get("sync") or {}
            badge = Gtk.Label(label=self._sync_status_label(sync.get("status"))); add_css(badge, "badge"); head.append(badge)
            box.append(head)
            stats=card.get('statistics') or {}
            if stats:
                loans=stats.get('known_loans')
                count=Gtk.Label(label=f"{stats.get('local_books',0)} livres locaux · {loans if loans is not None else '—'} emprunts connus",xalign=0)
                count.set_wrap(True);box.append(count)
                actions=stats.get('actions') or {}
                if actions:
                    ok=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_succeeded','imports_succeeded','synchronizations_succeeded','transformations_succeeded','transfers_succeeded'))
                    failed=sum(int(actions.get(key,0) or 0) for key in ('acquisitions_failed','imports_failed','synchronizations_failed','transformations_failed','transfers_failed'))
                    action_label=Gtk.Label(label=f"Actions (30 j) : {ok} réussies · {failed} échouées",xalign=0)
                    action_label.set_wrap(True); add_css(action_label, 'caption', 'dim-label'); box.append(action_label)
            providers = ", ".join(source["label"] + ("" if source["connected"] else " (à connecter)") for source in card.get("sources", [])) or "Aucune source connectée"
            source_label = Gtk.Label(label=f"Sources : {providers}", xalign=0); source_label.set_wrap(True); box.append(source_label)
            profile = "Lié" if card.get("adept_profile_id") else "À configurer"
            box.append(Gtk.Label(label=f"Profil Adobe : {profile}", xalign=0))
            deep_last_at = card.get("deep_last_at")
            if deep_last_at:
                try:
                    deep_label = datetime.fromtimestamp(int(deep_last_at)).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    deep_label = str(deep_last_at)
                lab = Gtk.Label(label=f"Dernière vérification complète : {deep_label}", xalign=0); add_css(lab, "caption", "dim-label"); box.append(lab)
            if sync:
                sync_detail = sync.get("detail") or {}
                provisioning = sync_detail.get("adept_provisioning") or {}
                if provisioning.get("state"):
                    box.append(Gtk.Label(label=f"Provisioning : {provisioning.get('state')}", xalign=0))
                details = f"Nouvelles acquisitions : {sync.get('new_acquisitions', 0)} · Actions : {sync.get('attention_required', 0)} · Erreurs : {sync.get('errors', 0)}"
                lab = Gtk.Label(label=details, xalign=0); lab.set_wrap(True); add_css(lab, "caption", "dim-label"); box.append(lab)
                if sync.get("errors") or sync.get("attention_required"):
                    issues = Gtk.Button(label="Voir les erreurs et actions")
                    issues.connect("clicked", lambda _b, c=card: self._show_sync_issues(c)); box.append(issues)
            box.set_size_request(300, -1)
            for capability in card.get("capabilities", []):
                label = Gtk.Label(label="✓ " + capability["label"], xalign=0); add_css(label, "caption")
                box.append(label)
            self.dashboard_cards.insert(box, -1)


    def _choose_identity_directory(self, entry, root: Path) -> None:
        from ..identities.directory_selection import directory_choice
        chooser = Gtk.FileChooserNative.new("Choisir un dossier d’identité", self,
                     Gtk.FileChooserAction.SELECT_FOLDER, "Choisir", "Annuler")
        chooser.set_current_folder(Gio.File.new_for_path(str(root.expanduser())))
        self._identity_directory_chooser = chooser
        def response(dialog, selected):
            try:
                if selected == Gtk.ResponseType.ACCEPT:
                    file = dialog.get_file()
                    if file is None or not file.get_path():
                        raise ValueError("Choisissez un dossier local.")
                    entry.set_text(directory_choice(file.get_path(), root))
            except Exception as exc:
                self.show_error(str(exc), "Choix du répertoire impossible")
            finally:
                dialog.destroy()
                if getattr(self, "_identity_directory_chooser", None) is dialog:
                    self._identity_directory_chooser = None
        chooser.connect("response", response); chooser.show()

    def _choose_adept_profile(self, entry, control) -> None:
        def done(choices):
            if not choices:
                self.show_error("Autorisez un profil Adobe capable de récupérer les ACSM dans Kobo-Adept, puis réessayez.", "Aucun profil ADE prêt")
                return
            dialog = Adw.MessageDialog.new(self, "Choisir le profil ADE", "Choisissez le profil Adobe correspondant aux prêts de cette identité. Enregistrez ensuite les paramètres, puis relancez la synchronisation.")
            references = {}
            for index, choice in enumerate(choices):
                response = "profile-" + str(index)
                references[response] = choice["reference"]
                dialog.add_response(response, choice["name"] + " · " + choice["reference"])
            dialog.add_response("cancel", "Annuler")
            dialog.set_close_response("cancel")
            def selected(_dialog, response):
                if response in references:
                    entry.set_text(references[response])
            dialog.connect("response", selected)
            dialog.present()
        self.runner.submit("Profils ADE…", self.service.adept_profile_choices, done,
                           lambda exc: self.show_error(str(exc), "Profils ADE"), control=control)

    def _show_sync_issues(self, card):
        from .sync_issues import sync_issues_text
        dialog = Adw.MessageDialog.new(self, "Synchronisation · " + str(card.get("label") or ""), sync_issues_text(card.get("sync") or {}))
        dialog.add_response("settings", "Paramètres des identités")
        dialog.add_response("close", "Fermer")
        def response(_dialog, selected):
            if selected == "settings":
                self.navigate("settings")
                self._settings_tabs.set_visible_child_name("accounts")
        dialog.connect("response", response)
        dialog.present()

    def _build_sync_page(self):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + edge)(20)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Synchronisation", xalign=0); title.set_hexpand(True); add_css(title, "page-title")
        actions = AdaptiveActionBar(source=heading)
        refresh = self.action_button("Actualiser", "view-refresh-symbolic")
        all_now = self.action_button("Synchroniser les sources", "emblem-synchronizing-symbolic", suggested=True)
        self._sync_all_button = all_now
        actions.append(refresh); actions.append(all_now)
        heading.append(title); heading.append(actions); outer.append(heading)
        self._sync_sources_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); outer.append(self._sync_sources_box)
        refresh.connect("clicked", lambda _b: self._refresh_sync_page())
        def sync_all(button):
            broker = self.plugin_manager.broker("sync") if self.plugin_manager else None
            identity = self.service.current_identity
            if broker is None: return
            request = SyncRequest(identity_id=identity.identity_id if identity else None)
            self.runner.submit(
                "Synchronisation des sources…",
                lambda: broker.synchronize(request),
                lambda _r: (self._refresh_sync_page(), self.refresh_library(False)),
                control=button,
                priority=TaskPriority.USER_ACTION,
                kind="source-sync",
            )
        all_now.connect("clicked", sync_all)
        scroll.set_child(outer); return scroll

    def _refresh_sync_page(self) -> None:
        box = getattr(self, "_sync_sources_box", None)
        if box is None: return
        clear_box(box)
        broker = self.plugin_manager.broker("sync") if self.plugin_manager else None
        identity = self.service.current_identity
        all_now = getattr(self, "_sync_all_button", None)
        global_label = sync_identity_action_label(identity)
        if all_now is not None:
            all_now._cifereca_action_label = global_label
            child = all_now.get_child()
            if child is not None and callable(getattr(child, "set_label", None)):
                child.set_label(global_label)
            else:
                all_now.set_label(global_label)
            all_now.set_sensitive(False)
        if broker is None:
            if all_now is not None:
                all_now.set_tooltip_text("Synchronisation indisponible : broker absent")
            box.append(Gtk.Label(label="Broker de synchronisation indisponible", xalign=0)); return
        rows = broker.sources(identity.identity_id if identity else None)
        presentations = [sync_source_presentation(source) for source in rows]
        actionable = any(item.action_enabled for item in presentations)
        if all_now is not None:
            all_now.set_sensitive(actionable)
            all_now.set_tooltip_text(
                global_label if actionable
                else "Aucune source activée, connectée et synchronisable pour cette identité"
            )
        if not rows:
            box.append(Gtk.Label(label="Aucune source synchronisable pour cette identité", xalign=0)); return
        for source, presentation in zip(rows, presentations):
            group = Adw.PreferencesGroup(); box.append(group)
            row = Adw.ActionRow(); row.set_title(source.label or source.provider_id)
            row.set_subtitle(presentation.summary)
            row.set_subtitle_lines(0)
            toggle = Gtk.Switch(); toggle.set_active(source.enabled); toggle.set_valign(Gtk.Align.CENTER)
            toggle.set_tooltip_text("Activation : inclure cette source dans les synchronisations")
            def toggled(widget, _param, sid=source.source_id):
                try:
                    broker.set_enabled(sid, widget.get_active())
                    self._refresh_sync_page()
                except Exception as exc:
                    self.show_error(str(exc), "Synchronisation")
            toggle.connect("notify::active", toggled); row.add_suffix(toggle)
            now = self.action_button(presentation.action_label, "emblem-synchronizing-symbolic")
            now.set_sensitive(presentation.action_enabled)
            now.set_tooltip_text(
                presentation.action_label if presentation.action_enabled else presentation.disabled_reason
            )
            def one(_button, sid=source.source_id, label=source.label or source.provider_id):
                self.runner.submit(f"Synchronisation {label}…", lambda: broker.synchronize_source(sid),
                    lambda _r: (self._refresh_sync_page(), self.refresh_library(False)), control=_button,
                    priority=TaskPriority.USER_ACTION, kind="source-sync")
            now.connect("clicked", one); row.add_suffix(now); group.add(row)

    def _build_tools_page(self):
        scroll = Gtk.ScrolledWindow(); scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for edge in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + edge)(20)
        heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        title = Gtk.Label(label="Outils", xalign=0); title.set_hexpand(True); add_css(title, "page-title")
        refresh = self.action_button("Actualiser", "view-refresh-symbolic")
        heading.append(title); heading.append(refresh); outer.append(heading)
        self._tools_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8); outer.append(self._tools_box)
        refresh.connect("clicked", lambda _b: self._refresh_tools_page())
        scroll.set_child(outer); return scroll

    def _refresh_tools_page(self) -> None:
        box = getattr(self, "_tools_box", None)
        if box is None: return
        clear_box(box)
        services = self.plugin_manager.services("maintenance.tool") if self.plugin_manager else ()
        if not services:
            label = Gtk.Label(label="Aucun outil de maintenance additionnel n’est installé.", xalign=0)
            add_css(label, "dim-label"); box.append(label); return
        for resolved in services:
            tool = resolved.service
            try: info = tool.describe()
            except Exception as exc:
                box.append(Gtk.Label(label=f"{resolved.plugin_id}: description indisponible ({exc})", xalign=0)); continue
            group = Adw.PreferencesGroup(); box.append(group)
            row = Adw.ActionRow(); row.set_title(info.title); row.set_subtitle(info.description or resolved.plugin_id)
            run = self.action_button("Prévisualiser…" if info.supports_dry_run else "Exécuter…", info.icon_name)
            row.add_suffix(run); group.add(row)
            def prepare(_button, service=tool, meta=info):
                identity = self.service.current_identity
                context = MaintenanceContext(scope=meta.scope, identity_id=identity.identity_id if identity else None)
                try: plan = service.plan(context)
                except Exception as exc:
                    self.show_error(str(exc), meta.title); return
                scope_labels = {
                    "global": "Application entière",
                    "identity": "Identité active",
                    "library": "Bibliothèque",
                    "artifact": "Document ciblé",
                    "storage.target": "Cible de stockage",
                }
                scope = scope_labels.get(str(meta.scope), str(meta.scope or "Non précisée"))
                if meta.scope == "identity" and identity is not None:
                    scope += f" · {identity.label}"
                sections = [
                    "Résumé :\n" + (str(plan.summary or "").strip() or "Aucun résumé fourni."),
                    "Changements prévus :\n" + (
                        "\n".join("• " + str(value) for value in plan.changes[:20])
                        if plan.changes else "• Aucun changement prévu"
                    ),
                    "Avertissements :\n" + (
                        "\n".join("• " + str(value) for value in plan.warnings[:10])
                        if plan.warnings else "• Aucun"
                    ),
                    "Portée :\n" + scope,
                ]
                dialog = Adw.MessageDialog.new(self, meta.title, "\n\n".join(sections))
                dialog.add_response("cancel", "Annuler"); dialog.add_response("run", "Exécuter")
                if meta.danger_level == "destructive": dialog.set_response_appearance("run", Adw.ResponseAppearance.DESTRUCTIVE)
                else: dialog.set_response_appearance("run", Adw.ResponseAppearance.SUGGESTED)
                def response(_dialog, selected):
                    if selected != "run": return
                    class Control:
                        def cancelled(self): return False
                        def check_cancelled(self): return None
                        def report(self, **_kwargs): return None
                    self.runner.submit(
                        meta.title,
                        lambda: service.run(plan, Control()),
                        lambda result: self._activity_result(
                            result.summary or ("Outil terminé" if result.ok else "Outil terminé avec erreurs"),
                            success=bool(result.ok),
                            persistent=not bool(result.ok),
                        ),
                        control=_button,
                        priority=TaskPriority.MAINTENANCE,
                        kind="maintenance-tool",
                    )
                dialog.connect("response", response); dialog.present()
            run.connect("clicked", prepare)

    def run_opening_sync(self, control: Gtk.Widget | None = None, *, deep: bool = False) -> None:
        def progress(event: dict[str, Any]) -> None:
            kind = str(event.get("event") or "")
            if kind == "identity_begin":
                label = f"Synchronisation · {event.get('label') or event.get('identity_id')}…"
                GLib.idle_add(self._task_progress, label)
        def ok(summary: dict[str, Any]) -> None:
            self._render_dashboard(); self._populate_loans(self.service.last_loans); self.refresh_library(False); self.refresh_recent()
            acquisitions = sum(int(x.get("new_acquisitions") or 0) for x in summary.get("identities", []))
            attention = sum(int(x.get("attention_required") or 0) for x in summary.get("identities", []))
            errors = sum(int(x.get("errors") or 0) for x in summary.get("identities", []))
            if acquisitions or attention or errors:
                self._activity_result(
                    f"Synchronisation : {acquisitions} acquisition(s), {attention} action(s), {errors} erreur(s)",
                    success=not bool(attention or errors),
                    persistent=bool(attention or errors),
                )
        def err(exc: BaseException) -> None:
            self.diagnostics.add("SYNC", "opening sync", "error", detail={"error": type(exc).__name__, "message": str(exc)})
            self._render_dashboard()
            if control is not None: self.show_error(str(exc), "Synchronisation impossible")
        task_label = "Vérification complète de la bibliothèque…" if deep else "Synchronisation des bibliothèques…"
        self.runner.submit(
            task_label, lambda: self.service.opening_sync(progress=progress, deep=deep), ok, err, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else (TaskPriority.MAINTENANCE if deep else TaskPriority.ACTIVE_LOAN),
            kind="deep-sync" if deep else "opening-sync",
        )

    # Reader/storage targets are provided by storage.target plugins.
    # The first 0.9.2 prototype intentionally has no brand-specific reader UI.

    def _known_item_languages(self, provider_id: str, item_id: str | None, metadata: dict[str, Any] | None = None) -> set[str]:
        from cifereca.language import normalize_language_tag
        values = []
        meta = dict(metadata or {})
        raw = meta.get('languages') or meta.get('language') or ()
        values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
        if item_id:
            for row in self.service.storage.catalog_entries():
                if str(row.get('provider_id') or '') != str(provider_id or ''): continue
                if str(row.get('item_id') or '') != str(item_id): continue
                row_meta = dict(row.get('metadata') or {})
                raw = row_meta.get('languages') or row_meta.get('language') or ()
                values.extend(raw if isinstance(raw, (list, tuple, set)) else (raw,))
        return {tag for value in values if (tag := normalize_language_tag(value))}

    def _build_loans_page(self) -> MasterDetailPage:
        page = MasterDetailPage(self, "Mes emprunts", "Prêts physiques et numériques", "Aucun emprunt", "Vos prêts actifs apparaîtront ici.")
        history = Gtk.Button(label="Historique"); history.connect("clicked", lambda _b: self.navigate("history")); page.header_actions.append(history)
        bookings = Gtk.Button(label="Réservations"); bookings.connect("clicked", lambda _b: self.navigate("bookings")); page.header_actions.append(bookings)
        self.loan_filter = "digital"
        segment = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        buttons: list[Gtk.ToggleButton] = []
        self.loan_filter_buttons: dict[str, Gtk.ToggleButton] = {}
        for value, label in [("all", "Tous"), ("physical", "Physiques"), ("digital", "Numériques")]:
            b = Gtk.ToggleButton(label=label); b.set_name(value)
            if buttons: b.set_group(buttons[0])
            if value == "digital": b.set_active(True)
            b.connect("toggled", self._loan_filter_toggled)
            segment.append(b); buttons.append(b)
            self.loan_filter_buttons[value] = b
        page.toolbar.append(segment)
        self.loan_scope_count = Gtk.Label(label="", xalign=0)
        self.loan_scope_count.add_css_class("caption")
        self.loan_scope_count.add_css_class("dim-label")
        self.loan_scope_count.set_tooltip_text(
            "Nombre affiché dans le filtre actif, comparé au volume total des prêts"
        )
        page.toolbar.append(self.loan_scope_count)
        from cifereca.language import language_label
        self.loan_languages = MultiSelect('Langues', [(code, language_label(code)) for code in ('fr','en','de','es','it','pt','nl')], self._render_loans)
        page.toolbar.append(self.loan_languages)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser les emprunts")
        refresh.set_tooltip_text("Actualiser les catalogues de prêts et vérifier ADEPT, sans acquisition ni synchronisation des stores")
        refresh.connect("clicked", lambda _b: self.refresh_loans(True, refresh)); page.header_actions.append(refresh)
        page.connect_selected(self._loan_selected)
        return page

    def _loan_filter_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self.loan_filter = button.get_name() or "all"
            self._render_loans()

    def _populate_loans(self, rows: list[Loan]) -> None:
        self.service.last_loans = rows
        self.loan_objects = {x.id: x for x in rows}
        self._render_loans()
        self._render_kobo_loans()
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()

    def _render_loans(self) -> None:
        if not hasattr(self, "loan_page"):
            return
        all_loans = list(self.service.last_loans)
        counts = loan_medium_counts(all_loans)
        if hasattr(self, "loan_filter_buttons"):
            for key, button in self.loan_filter_buttons.items():
                label = LOAN_FILTER_LABELS.get(key, key)
                button.set_label(f"{label} · {counts.get(key, 0)}")
        ranked_rows: list[tuple[tuple[int, int], BookListItem]] = []
        wanted_languages = set(self.loan_languages.values()) if hasattr(self, 'loan_languages') else set()
        for loan in all_loans:
            if self.loan_filter != "all" and loan.medium != self.loan_filter:
                continue
            raw = dict(loan.raw or {})
            meta = dict(raw.get('_cifereca') or {}) if isinstance(raw.get('_cifereca'), dict) else raw
            loan_languages = self._known_item_languages(loan.source_provider, loan.record_id or loan.id, meta)
            if wanted_languages and not loan_languages.intersection(wanted_languages):
                continue
            due = loan_due_presentation(loan.due_at, is_late=bool(loan.is_late))
            subtitle = loan.library or ("Prêt numérique" if loan.medium == "digital" else "Prêt physique")
            meta = f"Retour {fr_date(loan.due_at)} · {loan.document_type or loan.provider}"
            ranked_rows.append((
                due.sort_key,
                BookListItem(
                    loan.id,
                    loan.title,
                    subtitle,
                    meta,
                    due.label,
                    loan.thumbnail_url,
                    badge_style=due.style,
                ),
            ))
        ranked_rows.sort(key=lambda item: item[0])
        rows = [row for _rank, row in ranked_rows]
        if hasattr(self, "loan_scope_count"):
            self.loan_scope_count.set_label(
                loan_context_label(
                    filter_key=self.loan_filter,
                    visible_count=len(rows),
                    medium_count=counts.get(self.loan_filter, counts["all"]),
                    total_count=counts["all"],
                    language_filtered=bool(wanted_languages),
                )
            )
        objects={key:{"source_provider":loan.source_provider,"media_format":loan.media_format,"author":loan.author,"metadata":(dict((loan.raw or {}).get('_cifereca') or {}) if isinstance((loan.raw or {}).get('_cifereca'),dict) else dict(loan.raw or {}))} for key,loan in self.loan_objects.items()}
        self._attach_row_facets(rows,objects,self.search_controls)
        self.loan_page.show_rows(rows, total_count=len(rows))

    def _loan_selected(self, row: BookListItem | None) -> None:
        if row is None: return
        loan = self.loan_objects.get(row.key)
        if loan:
            self._displayed_loan = loan
            self.loan_page.detail.show_loan(loan)
            self._add_local_book_actions(self.loan_page.detail,{'source_provider':loan.source_provider,
                'source_credential':loan.source_credential,'record_id':loan.record_id})
            self._add_facets(self.loan_page.detail, {'source_provider':loan.source_provider,
                'author':loan.author,'media_format':loan.media_format}, self.search_controls)

    def refresh_loans(self, force: bool = True, control: Gtk.Widget | None = None) -> None:
        identity = self.service.current_identity
        identity_id = identity.identity_id if identity else None
        def done(rows):
            current = self.service.current_identity
            if (current.identity_id if current else None) == identity_id:
                self._loans_refreshed(rows)
        self.runner.submit(
            "Actualisation des emprunts…",
            lambda: self.service.refresh_identity_loans(force=force, identity_id=identity_id),
            done,
            control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.ACTIVE_LOAN,
            kind="loans",
        )

    def _loans_refreshed(self, rows):
        displayed=getattr(self, '_displayed_loan', None)
        self._populate_loans(rows)
        if displayed:
            fresh=next((r for r in rows if (r.id,r.source_provider,r.source_credential,r.source_account_id)==
                        (displayed.id,displayed.source_provider,displayed.source_credential,displayed.source_account_id)),None)
            self._displayed_loan=fresh
            if fresh:self.loan_page.detail.show_loan(fresh)
            else:self.loan_page.detail.empty("Prêt absent du catalogue", "La liste des prêts a été actualisée ; les fichiers locaux sont conservés.")
        self.toast(f"{len(rows)} emprunt(s) actualisé(s)")

    def confirm_renew(self, loan: Loan, control: Gtk.Widget) -> None:
        dialog = Adw.MessageDialog.new(self, "Prolonger ce prêt ?", loan.title)
        dialog.add_response("cancel", "Annuler"); dialog.add_response("ok", "Prolonger")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.connect("response", lambda _d, response: self._renew(loan, control) if response == "ok" else None)
        dialog.present()

    def _renew(self, loan: Loan, control: Gtk.Widget) -> None:
        self.runner.submit(
            "Prolongation du prêt…", lambda: self.service.renew(loan), lambda _r: self.refresh_loans(True),
            control=control, success_toast="Prêt prolongé", priority=TaskPriority.USER_ACTION, kind="loan-renew",
        )

    def confirm_return(self, loan: Loan, control: Gtk.Widget) -> None:
        dialog = Adw.MessageDialog.new(self, "Rendre ce livre numérique ?", "Cette action libère le prêt avant son échéance si DILICOM l'autorise.")
        dialog.add_response("cancel", "Annuler"); dialog.add_response("ok", "Rendre maintenant")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.connect("response", lambda _d, response: self._return_loan(loan, control) if response == "ok" else None)
        dialog.present()

    def _return_loan(self, loan: Loan, control: Gtk.Widget) -> None:
        self.runner.submit(
            "Restitution du livre numérique…", lambda: self.service.return_digital(loan), lambda _r: self.refresh_loans(True),
            control=control, success_toast="Demande de restitution envoyée", priority=TaskPriority.USER_ACTION, kind="loan-return",
        )

    # ---------- search ----------
    def _query_controls(self, changed, activate, *, library=False):
        providers = dict(self.service.searchable_providers())
        providers["local"] = "Local"
        for row in self.service.catalog_entries():
            provider = str(row.get("provider_id") or "local")
            providers.setdefault(provider, self.service.provider_display_name(provider))
        providers = sorted(providers.items(), key=lambda pair: pair[1])
        catalog = self.plugin_manager.widget_catalog() if self.plugin_manager else None
        factory = getattr(catalog, "build_query_controls", None)
        controls = None
        if callable(factory):
            try:
                controls = factory(providers, changed, activate, library=library)
            except Exception as exc:
                self.diagnostics.add("PLUGIN", "query widget", "error", detail={"error": str(exc)})
        if controls is None:
            controls = QueryControls(providers, changed, activate, library=library)
        setter = getattr(controls, "set_provider_search_capabilities", None)
        if callable(setter) and not library:
            setter(self.service.provider_search_capabilities())
        return controls

    def _reset_search_enter(self):
        sequence = getattr(self, "_search_enter_sequence", None)
        if sequence is not None:
            sequence.reset()

    def _search_controls_changed(self):
        self._reset_search_enter()
        self._render_search_results()

    def _search_enter(self):
        from .search_intent import SearchEnterSequence
        if not hasattr(self, "_search_enter_sequence"):
            self._search_enter_sequence = SearchEnterSequence()
        state = self.search_controls.state()
        identity = self.service.current_identity.identity_id if self.service.current_identity else None
        if getattr(self, "_online_pending_key", None) == (state, identity):
            return
        online = self._search_enter_sequence.press(state)
        self.run_search(online, keyboard=True)

    def _search_key_interaction(self, _controller, keyval, _keycode, _state):
        if keyval not in {Gdk.KEY_Return, Gdk.KEY_KP_Enter}:
            self._reset_search_enter()
        return False

    def _build_search_page(self) -> MasterDetailPage:
        page = MasterDetailPage(self, "Recherche", "", "Lancez une recherche", "Recherche dans le connu, puis extension aux catalogues en ligne sur demande.")
        self.search_controls = self._query_controls(self._search_controls_changed, self._search_enter)
        page.toolbar.append(self.search_controls)
        self.search_entry = self.search_controls.entry
        if not hasattr(self, "_search_interaction_controllers"):
            keys = Gtk.EventControllerKey()
            keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            keys.connect("key-pressed", self._search_key_interaction)
            self.add_controller(keys)
            clicks = Gtk.GestureClick()
            clicks.set_button(0)
            clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            clicks.connect("pressed", lambda *_: self._reset_search_enter())
            self.add_controller(clicks)
            scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
            scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
            scroll.connect("scroll", lambda *_: (self._reset_search_enter(), False)[1])
            self.add_controller(scroll)
            self._search_interaction_controllers = (keys, clicks, scroll)
        self.search_button = Gtk.Button(label="Recherche"); self.search_button.add_css_class("suggested-action")
        self.search_button.connect("clicked", lambda _b: self.run_search(False)); self.search_controls.primary.append(self.search_button)
        self.search_global_button = Gtk.Button(label="Étendre en ligne")
        self.search_global_button.set_tooltip_text("Interroger les catalogues distants avec la requête et les filtres actuels")
        self.search_global_button.connect("clicked", lambda _b: self.run_search(True)); self.search_controls.primary.append(self.search_global_button)
        page.connect_selected(self._search_selected)
        self._search_generation = getattr(self, "_search_generation", 0) + 1
        self._remote_search_results = []
        return page

    def run_search(self, force: bool = False, *, keyboard: bool = False) -> None:
        if not keyboard:
            self._reset_search_enter()
        state = self.search_controls.state()
        if len(state.text) < 2:
            self.toast("Saisissez au moins 2 caractères"); return
        request_identity = self.service.current_identity.identity_id if self.service.current_identity else None
        online_key = (state, request_identity)
        if force and getattr(self, "_online_pending_key", None) == online_key:
            return
        self._search_generation += 1
        generation = self._search_generation
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        def current():
            identity = self.service.current_identity.identity_id if self.service.current_identity else None
            return generation == self._search_generation and identity_id == identity
        if not force:
            self._last_search_query = state.text
            self._search_mode = "local"
            def done(rows):
                if current(): self._populate_local_search(rows)
            self.runner.submit("Recherche dans le connu…",
                lambda: self.service.search_known_catalog(state.text, state.scope, field=state.field, identity_id=identity_id),
                done, control=self.search_button, priority=TaskPriority.USER_ACTION, kind="search-known")
            return
        # Retain all already-loaded provider/type results for instant reversal
        # of facet changes. A different text/field begins a new result universe.
        request_key = (state.text, state.field, state.scope)
        if request_key != getattr(self, "_remote_query_key", None):
            self._remote_search_results = []
            if state.text != self._last_search_query: self._search_local_objects = {}
        self._remote_query_key = request_key
        self._last_search_query = state.text
        pending_token = object()
        self._online_pending_key = online_key
        self._online_pending_token = pending_token
        def clear_pending():
            if getattr(self, "_online_pending_token", None) is pending_token:
                self._online_pending_key = None
        def failed(exc):
            clear_pending()
            if current(): self.show_error(str(exc), "Recherche en ligne impossible")
        def deliver_batch(batch):
            if not current(): return False
            if batch.results:
                self._remote_search_results = merge_results(self._remote_search_results, list(batch.results))
                self._populate_search(self._remote_search_results, announce=False)
            return False
        def on_batch(batch):
            GLib.idle_add(deliver_batch, batch)
        def done(summary):
            clear_pending()
            if not current(): return
            self.service.last_search_warnings = list(summary.warnings)
            # Batch delivery is progressive; the final render only reconciles
            # ranking/deduplication with the authoritative aggregate.
            self._remote_search_results = merge_results(self._remote_search_results, list(self.service.last_search))
            self._populate_search(self._remote_search_results, announce=True)
        self.runner.submit("Recherche en ligne…", lambda: progressive_online_search(self.service, state, on_batch), done, failed,
                           control=self.search_global_button, priority=TaskPriority.USER_ACTION, kind="search-online")

    def _sync_search_detail_after_results(self) -> None:
        if not hasattr(self, "search_page"):
            return
        selected = self.search_page.selection.get_selected_item()
        if isinstance(selected, BookListItem):
            return
        self._search_selected(None)

    def _populate_local_search(self, rows: list[dict[str, Any]]) -> None:
        self._search_local_objects = {str(row.get("key") or f"local:{index}"): row for index, row in enumerate(rows)}
        self._search_mode = "local"
        self.search_objects = {}
        shown = self._render_search_results()
        self._sync_search_detail_after_results()
        self.search_global_button.set_sensitive(True)

    def _populate_search(self, rows: list[SearchResult], *, announce: bool = True) -> None:
        self._search_mode = "global"
        self.service.last_search = rows
        self.search_objects = {f"{x.source_provider}:{x.id}": x for x in rows}
        shown = self._render_search_results()
        self._sync_search_detail_after_results()
        if not announce:
            return
        warnings = list(getattr(self.service, "last_search_warnings", []) or [])
        if warnings:
            self.toast(f"Résultats partiels · {warnings[0]}", 6)

    def _render_search_results(self) -> int:
        if not hasattr(self, "search_page"):
            return 0
        state = self.search_controls.state()
        if self._search_mode == "local":
            audio_formats = {"mp3", "m4a", "ogg", "opus", "flac", "wav"}
            local_rows: list[BookListItem] = []
            for item in state.filter(self._search_local_objects.values()):
                key = str(item["key"])
                media_format = str(item.get("media_format") or "").casefold()
                content_type = "audiobook" if media_format in audio_formats else "ebook"
                provider = str(item.get("provider_id") or "local")
                local_rows.append(BookListItem(
                    key, str(item.get("title") or "Sans titre"), str(item.get("author") or ""),
                    " · ".join(x for x in [self._library_state_label(item)] if x),
                    self.service.provider_display_name(provider) if provider != "local" else "Local",
                    item.get("thumbnail_url"),
                ))
            self._attach_row_facets(local_rows, self._search_local_objects, self.search_controls)
            self.search_page.show_rows(local_rows)
            shown = len(local_rows)
            self._update_search_count(shown)
            return shown
        identity = self.service.current_identity
        linked = self.service.local_placements_for_sources(identity.identity_id) if identity else {}
        remote_keys = {(item.source_provider, item.source_credential, item.record_id) for item in self._remote_search_results}
        linked_placements = {linked[key]['placement_id'] for key in remote_keys if key in linked}
        ui_rows: list[BookListItem] = []
        for known in state.filter(self._search_local_objects.values()):
            if known.get('placement_id') in linked_placements: continue
            known_key = (known.get('provider_id'), known.get('source_credential') or (known.get('metadata') or {}).get('source_credential'), str(known.get('item_id') or ''))
            if known_key in remote_keys and not known.get('local'): continue
            ui_rows.append(BookListItem(str(known["key"]), str(known.get("title") or "Sans titre"), str(known.get("author") or ""),
                "", self.service.provider_display_name(str(known.get("provider_id") or "local")), known.get("thumbnail_url")))
        filtered = {row["key"] for row in state.filter([{**result_row(x), "key": f"{x.source_provider}:{x.id}"} for x in self._remote_search_results])}
        for item in self._remote_search_results:
            key = f"{item.source_provider}:{item.id}"
            if key not in filtered: continue
            content_type = self.service.content_type_for(item)
            if item.medium == "physical":
                medium = "Physique"
            elif item.medium == "unknown" and not file_format(item.format):
                medium = "Support indéterminé"
            elif content_type == "audiobook":
                medium = "Livre audio"
            else:
                medium = "Livre numérique"
            source = self.service.provider_display_name(item.source_provider)
            extras: list[str] = []
            if item.source_provider == "kobo":
                if item.owned:
                    extras.append("Possédé sur Kobo")
                    if not self.service.can_direct_download(item):
                        extras.append("Kobo-Adept indisponible")
                elif item.price:
                    extras.append(f"{item.price} {item.currency or ''}".strip())
            meta = " · ".join(x for x in [medium, *extras, item.published_at] if x)
            placement = linked.get((item.source_provider, item.source_credential, item.record_id))
            direct = self.service.can_direct_download(item)
            callback = None
            if placement:
                extras.append('Fichier local disponible')
                meta = ' · '.join(x for x in [medium, *extras, item.published_at] if x)
                callback = lambda _button, selected=placement: self._open_placement(selected)
            elif direct:
                callback = lambda button, selected=item: self.download_direct_item(selected, button)
            ui_rows.append(BookListItem(
                key, item.title, item.author or "", meta, source, item.thumbnail_url,
                action_label="Ouvrir" if placement else (("Sauvegarder" if item.source_provider == "kobo" else "Télécharger") if direct else None),
                action_callback=callback,
                action_icon_name="document-open-symbolic" if placement else ("document-save-symbolic" if direct else None),
            ))
        objects = {**self._search_local_objects, **{f"{item.source_provider}:{item.id}": result_row(item) for item in self._remote_search_results}}
        self._attach_row_facets(ui_rows, objects, self.search_controls)
        self.search_page.show_rows(ui_rows)
        shown = len(ui_rows)
        self._update_search_count(shown)
        return shown

    def _update_search_count(self, shown: int) -> None:
        if not hasattr(self, "search_page"):
            return
        count = max(0, int(shown))
        noun = "résultat" if count == 1 else "résultats"
        scope = "Connu" if self._search_mode == "local" else "Étendu"
        self.search_page.count_label.set_label(f"{count} {noun} · {scope}")


    def _search_selected(self, row: BookListItem | None) -> None:
        if row is None:
            self._selected_search_id = None
            self.search_page.detail.empty("Aucun document sélectionné", "Sélectionnez un résultat pour afficher ses détails.")
            return
        if row.key in self._search_local_objects:
            item = self._search_local_objects.get(row.key)
            if not item:
                return
            self._selected_search_id = row.key
            provider = str(item.get("provider_id") or "local")
            availability = self._library_availability_label(item)
            if not availability and str(item.get("state") or "") == "local":
                availability = "Disponible hors ligne"
            identity = self.service.identity_by_id(str(item.get("identity_id") or ""))
            sync_label = self._kobo_backup_status_label(str(item.get("sync_status") or "")) if item.get("sync_status") else ""
            if sync_label == "À jour":
                sync_label = ""
            description = self._book_description(item)
            self.search_page.detail.show_presentation(book_detail_presentation(
                title=str(item.get("title") or "Sans titre"),
                subtitle=str(item.get("author") or ""),
                thumbnail=item.get("thumbnail_url"),
                badges=[],
                section_rows=[
                    ("Essentiel", [
                        ("Source", self.service.provider_display_name(provider)),
                        ("Format", str(item.get("media_format") or "").upper()),
                        ("Langue", detail_language(item.get("metadata"))),
                        ("Disponibilité", availability),
                    ]),
                    ("Bibliothèque", [
                        ("Identité", identity.label if identity else str(item.get("identity_id") or "")),
                        ("Fichier local", str(item.get("local_path") or "")),
                        ("État acquisition", sync_label),
                    ]),
                ],
            ), description=description)
            actions = []
            open_library = Gtk.Button(label="Voir dans Bibliothèque")
            open_library.connect("clicked", lambda _b, key=row.key: self._open_library_item(key))
            if item.get("local") or item.get("state") in {"remote_owned", "remote_loan_active", "remote_loan_realized"}:
                actions.append(open_library)
            if item.get("source") == "known_catalog":
                candidate = self._known_search_result(item)
                if candidate:
                    show = Gtk.Button(label="Disponibilités / téléchargement")
                    show.connect("clicked", lambda _b: self._load_known_detail(candidate))
                    actions.append(show)
            extend = Gtk.Button(label="En ligne")
            extend.add_css_class("suggested-action")
            extend.connect("clicked", lambda _b: self.run_search(True)); actions.append(extend)
            self.search_page.detail.add_actions(actions)
            self._add_book_notice(self.search_page.detail,item, include_description=False)
            self._add_local_book_actions(self.search_page.detail,item)
            self._add_facets(self.search_page.detail, item, self.search_controls)
            return
        item = self.search_objects.get(row.key)
        if not item: return
        self._selected_search_id = item.id
        self.search_page.detail.show_search(item, loading=True)
        self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
        self._add_local_book_actions(self.search_page.detail,result_row(item))
        selected_id = item.id
        self.runner.submit(
            "Chargement des disponibilités…",
            lambda: self.service.holdings(item, self._last_search_query, force=False),
            lambda detail: self._apply_search_detail(selected_id, item, detail),
            lambda exc: self._search_detail_error(selected_id, item, exc),
            priority=TaskPriority.METADATA, kind="holdings",
        )

    def _apply_search_detail(self, selected_id: str, item: SearchResult, detail: ItemDetail) -> None:
        if self._selected_search_id == selected_id:
            self.search_page.detail.show_item_detail(item, detail)
            self._add_facets(self.search_page.detail, result_row(item), self.search_controls)
            self._add_local_book_actions(self.search_page.detail,result_row(item))

    def _search_detail_error(self, selected_id: str, item: SearchResult, exc: BaseException) -> None:
        if self._selected_search_id == selected_id:
            self.search_page.detail.show_search(item, loading=False)
            self._add_local_book_actions(self.search_page.detail,result_row(item))
        self.toast(f"Disponibilités indisponibles : {exc}", 5)

    # ---------- history ----------
    def _build_history_page(self) -> MasterDetailPage:
        page = MasterDetailPage(self, "Historique", "Documents précédemment empruntés", "Historique non chargé", "Ouvrez cet écran ou actualisez pour récupérer l'historique.")
        page.set_total_only_count(True)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser l'historique")
        refresh.connect("clicked", lambda _b: self.refresh_history(True, refresh)); page.header_actions.append(refresh)
        from cifereca.language import language_label
        self.history_languages = MultiSelect('Langues', [(code, language_label(code)) for code in ('fr','en','de','es','it','pt','nl')], self._rerender_history)
        page.toolbar.append(self.history_languages)
        page.connect_selected(self._history_selected)
        page.scroll.get_vadjustment().connect("value-changed", self._history_scroll_changed)
        return page

    def _identity_request_current(self, identity_id: str | None, generation: int) -> bool:
        current = self.service.current_identity
        current_identity_id = current.identity_id if current is not None else None
        return (
            generation == self._identity_context_generation
            and identity_id == current_identity_id
        )

    def refresh_history(self, force: bool = False, control: Gtk.Widget | None = None) -> None:
        current = self.service.current_identity
        identity_id = current.identity_id if current is not None else None
        generation = self._identity_context_generation

        def done(rows: list[HistoryItem]) -> None:
            if self._identity_request_current(identity_id, generation):
                self._populate_history(rows)

        self.runner.submit(
            "Chargement de l'historique…", lambda: self.service.refresh_history(force=force), done, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL, kind="history",
        )

    def _history_book_row(self, x: HistoryItem) -> BookListItem:
        row = BookListItem(
            x.id, x.title, x.author or "",
            f"Emprunté le {fr_date(x.borrowed_at)} · {x.year or x.document_type or ''}",
            "Numérique" if x.medium == "digital" else "Physique",
            x.thumbnail_url,
            # 0.2.7: ne plus lancer de recherches catalogue automatiques pour
            # reconstruire les vignettes de l'historique d'un compte lié. Le site
            # officiel présente la même absence de vignettes ; on conserve donc le
            # placeholder en attendant une étude des identifiants en amont.
            cover_provider=(x.provider if (not x.thumbnail_url and self.service.current_account.is_primary) else None),
            cover_record_id=(x.record_id if (not x.thumbnail_url and self.service.current_account.is_primary) else None),
            cover_title=x.title,
            cover_author=x.author,
            cover_year=x.year,
            default_thumbnail_url=x.default_thumbnail_url,
        )
        self._attach_row_facets([row],{x.id:{'author':x.author,'source_provider':x.source_provider}},self.search_controls)
        return row

    def _rerender_history(self) -> None:
        rows = list(getattr(self, '_history_all_rows', ()))
        self._populate_history(rows, preserve_all=True)

    def _populate_history(self, rows: list[HistoryItem], preserve_all: bool = False) -> None:
        if not preserve_all:
            self._history_all_rows = list(rows)
        wanted_languages = set(self.history_languages.values()) if hasattr(self, 'history_languages') else set()
        if wanted_languages:
            rows = [x for x in rows if self._known_item_languages(x.source_provider, x.record_id or x.id).intersection(wanted_languages)]
        self.history_objects = {x.id: x for x in rows}
        self._history_rows = list(rows)
        self._history_loaded_count = 0
        self._history_append_scheduled = False
        initial = self._history_rows[: self._history_page_size]
        self._history_loaded_count = len(initial)
        self.history_page.show_rows(
            [self._history_book_row(x) for x in initial],
            total_count=len(self._history_rows),
        )
        if len(self._history_rows) > self._history_loaded_count:
            self.diagnostics.add(
                "UI", "history progressive load", "ok",
                detail={
                    "loaded": self._history_loaded_count,
                    "total": len(self._history_rows),
                    "page_size": self._history_page_size,
                },
                level="detailed",
            )

    def _history_scroll_changed(self, adjustment: Gtk.Adjustment) -> None:
        if self._history_loaded_count >= len(self._history_rows) or self._history_append_scheduled:
            return
        remaining = adjustment.get_upper() - (adjustment.get_value() + adjustment.get_page_size())
        threshold = max(420.0, adjustment.get_page_size() * 0.75)
        if remaining <= threshold:
            self._history_append_scheduled = True
            GLib.idle_add(self._append_history_chunk)

    def _append_history_chunk(self) -> bool:
        self._history_append_scheduled = False
        if self._history_loaded_count >= len(self._history_rows):
            return False
        start = self._history_loaded_count
        end = min(len(self._history_rows), start + self._history_page_size)
        chunk = self._history_rows[start:end]
        self._history_loaded_count = end
        self.history_page.append_rows(
            [self._history_book_row(x) for x in chunk],
            total_count=len(self._history_rows),
        )
        self.diagnostics.add(
            "UI", "history progressive load", "ok",
            detail={
                "loaded": self._history_loaded_count,
                "total": len(self._history_rows),
                "page_size": self._history_page_size,
            },
            level="detailed",
        )
        return False

    def _history_selected(self, row: BookListItem | None) -> None:
        if row:
            item = self.history_objects.get(row.key)
            if item:
                from dataclasses import asdict
                self.history_page.detail.show_history(item)
                self._add_facets(self.history_page.detail, asdict(item), self.search_controls)
                self._add_local_book_actions(self.history_page.detail, asdict(item))

    # ---------- bookings ----------
    def _build_bookings_page(self) -> MasterDetailPage:
        page = MasterDetailPage(self, "Réservations", "Réservations physiques et numériques", "Aucune réservation", "Vos réservations apparaîtront ici.")
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic"); refresh.set_tooltip_text("Actualiser les réservations")
        refresh.connect("clicked", lambda _b: self.refresh_bookings(True, refresh)); page.header_actions.append(refresh)
        page.connect_selected(self._booking_selected)
        return page

    def refresh_bookings(self, force: bool = False, control: Gtk.Widget | None = None) -> None:
        current = self.service.current_identity
        identity_id = current.identity_id if current is not None else None
        generation = self._identity_context_generation

        def done(rows: list[Booking]) -> None:
            if self._identity_request_current(identity_id, generation):
                self._populate_bookings(rows)

        self.runner.submit(
            "Chargement des réservations…", lambda: self.service.refresh_bookings(force=force), done, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL, kind="bookings",
        )

    def _populate_bookings(self, rows: list[Booking]) -> None:
        self.booking_objects = {x.id: x for x in rows}
        ui_rows = []
        for item in rows:
            presentation = booking_presentation(item)
            ui_rows.append(BookListItem(
                item.id,
                item.title,
                item.location or "",
                f"Réservé le {fr_date(item.booking_date)}",
                presentation.status_label,
                None,
            ))
        objects={x.id:{"source_provider":x.source_provider} for x in rows}
        self._attach_row_facets(ui_rows,objects,self.search_controls)
        self.booking_page.show_rows(ui_rows)

    def _booking_selected(self, row: BookListItem | None) -> None:
        if row:
            item = self.booking_objects.get(row.key)
            if item:
                from dataclasses import asdict
                self.booking_page.detail.show_booking(item)
                self._add_facets(self.booking_page.detail, asdict(item), self.search_controls)
                self._add_local_book_actions(self.booking_page.detail, asdict(item))

    # ---------- Bibliothèque fédérée ----------
    def _build_library_page(self) -> MasterDetailPage:
        page = MasterDetailPage(self, "Bibliothèque", "", "Bibliothèque vide", "Fichiers locaux, livres possédés et empruntés.")
        self.library_filter = "all"
        controls = self._query_controls(lambda: self._render_library(), lambda: self._render_library(), library=True)
        self.library_controls = controls; self.library_filter_entry = controls.entry
        page.toolbar.append(controls)
        search = Gtk.Button(label="Recherche"); search.connect("clicked", self._library_search_online); controls.primary.append(search)
        actions = page.header_actions
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Rescanner la bibliothèque")
        refresh.connect("clicked", lambda _b: self.refresh_library(True, refresh))
        actions.append(refresh)

        folder = Gtk.Button.new_from_icon_name("folder-open-symbolic")
        folder.set_tooltip_text("Ouvrir le dossier de la bibliothèque")
        folder.connect("clicked", lambda _b: self.open_library_folder())
        actions.append(folder)

        advanced = Gtk.MenuButton(label="Opérations…")
        advanced.set_tooltip_text("Opérations avancées et maintenance de la bibliothèque")
        advanced_popover = Gtk.Popover()
        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        advanced_box.set_margin_top(8); advanced_box.set_margin_bottom(8)
        advanced_box.set_margin_start(8); advanced_box.set_margin_end(8)

        covers = Gtk.Button(label="Réinspecter les couvertures")
        covers.set_tooltip_text("Réinspecter toutes les couvertures EPUB locales, sans téléchargement")
        covers.connect("clicked", lambda _b: self.refresh_library(True, covers, refresh_covers=True))
        advanced_box.append(covers)

        acsm = Gtk.Button(label="Importer un ACSM…")
        acsm.set_tooltip_text("Importer manuellement un fichier ACSM dans la bibliothèque")
        acsm.connect("clicked", lambda _b: self.choose_acsm())
        advanced_box.append(acsm)

        acquire = Gtk.Button(label="Synchroniser les comptes Kobo")
        acquire.set_tooltip_text(
            "Opération globale : télécharge les livres manquants de tous les comptes Kobo connectés compatibles."
        )
        acquire.connect("clicked", lambda _b: self.run_kobo_backup(True, acquire))
        advanced_box.append(acquire)

        advanced_popover.set_child(advanced_box)
        advanced.set_popover(advanced_popover)
        actions.append(advanced)
        page.connect_selected(self._library_selected)
        self._library_objects = {}
        return page

    @staticmethod

    def _kobo_backup_status_label(status: str) -> str:
        return {
            "ok": "À jour",
            "complete": "À jour",
            "pending": "À acquérir",
            "pending_acquisition": "À acquérir",
            "acquiring": "Acquisition en cours",
            "reconcile_required": "À réconcilier",
            "reconcile_conflict": "Conflit local",
            "attention_required": "Action requise",
            "queued": "Ancien job 0.4.x",
            "claimed": "Ancien job 0.4.x",
            "downloading": "Ancien job 0.4.x",
            "processing": "Ancien job 0.4.x",
            "failed": "Erreur",
            "error": "Erreur",
            "cancelled": "Annulé",
        }.get(str(status or "").lower(), str(status or "Inconnu"))

    @staticmethod
    def _library_state_label(item: dict[str, Any]) -> str:
        state = str(item.get("state") or "")
        if state == "local":
            return "Local"
        if state == "remote_owned":
            return "Possédé en ligne"
        if state == "remote_loan_active":
            return "Emprunté"
        if state == "remote_loan_realized":
            return "Emprunt réalisé"
        if state == "available_to_borrow":
            return "Empruntable"
        if state == "available_to_buy":
            return "Achetable"
        if state == "remote_catalog":
            return "Distant"
        if state == "missing":
            return "Fichier manquant"
        return state.replace("_", " ").strip().capitalize() or "Disponible"

    @classmethod
    def _library_availability_label(cls, item: dict[str, Any]) -> str:
        """Avoid repeating the local provenance as an identical availability."""
        provider = str(item.get("provider_id") or "local")
        state = str(item.get("state") or "")
        if provider == "local" and state == "local":
            return ""
        return cls._library_state_label(item)

    def _library_filter_toggled(self, button: Gtk.ToggleButton) -> None:
        if button.get_active():
            self.library_filter = button.get_name() or "all"
            self._render_library()

    def _library_search_online(self, _button: Gtk.Button) -> None:
        self.search_controls.set_state(self.library_controls.state())
        self.navigate("search")
        self.run_search(False)

    def choose_acsm(self):
        if getattr(self, "_acsm_chooser", None):
            self._acsm_chooser.present(); return
        identity = self.service.current_identity
        if identity is None:
            self.show_error("Choisissez une identité pour importer un ACSM"); return
        identity_id = identity.identity_id
        chooser = Gtk.FileChooserDialog(title=f"Importer un ACSM — {identity.label}", transient_for=self, modal=True, action=Gtk.FileChooserAction.OPEN)
        chooser.add_buttons("Annuler", Gtk.ResponseType.CANCEL, "Importer", Gtk.ResponseType.ACCEPT)
        self._acsm_chooser = chooser
        filt = Gtk.FileFilter(); filt.set_name("ACSM (*.acsm)"); filt.add_pattern("*.acsm"); filt.add_pattern("*.ACSM"); chooser.add_filter(filt)
        def chosen(dialog, response):
            file = dialog.get_file() if response == Gtk.ResponseType.ACCEPT else None
            path = file.get_path() if file else None
            dialog.destroy()
            self._acsm_chooser = None
            if not path: return
            def done(result):
                if result.get("status") == "attention_required":
                    self.show_attention("Import en attente : " + str(result.get("code") or "runtime DRM à configurer") + "\nLe staging est conservé pour une nouvelle tentative.", code=result.get("code") or "attention_required")
                else:
                    self._activity_result(
                        "ACSM importé" if result.get("status") != "unchanged" else "ACSM déjà importé"
                    )
                    self.refresh_library(False)
            self.runner.submit("Import ACSM…", lambda: self.service.import_acsm(Path(path), identity_id=identity_id), done,
                priority=TaskPriority.USER_ACTION, kind="acsm-import")
        chooser.connect("response", chosen); chooser.present()

    def _search_facet(self, field, text, controls):
        from .query_model import apply_facet
        state = controls.state()
        updated = apply_facet(state, field, text)
        # Detail facets are an explicit action: update controls silently, then
        # refresh the owning context exactly once. This also re-runs the action
        # when the selected facet already matches the current filter.
        library_controls = getattr(self, 'library_controls', None)
        target_controls = library_controls if controls is library_controls else self.search_controls
        target_controls.set_state(updated, notify=False)
        if target_controls is library_controls:
            self.navigate('library')
            self._render_library()
            return
        self.navigate("search")
        self.run_search(False)

    def _facet_values(self, item):
        from .book_facets import facets_for_book
        return [(f.field,f.label,f.value) for f in facets_for_book(item,self.service.provider_display_name)]


    def _facet_value_button(self, label, field, value, controls):
        caption = str(label or value or "").strip()
        button = Gtk.Button()
        button.add_css_class("flat")
        button.add_css_class("facet-value")
        text = Gtk.Label(label=caption, xalign=0)
        text.set_ellipsize(Pango.EllipsizeMode.END)
        text.set_max_width_chars(28)
        button.set_child(text)
        tooltip = f"Filtrer par {caption}"
        button.set_tooltip_text(tooltip)
        try:
            button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        except (AttributeError, TypeError):
            pass
        button._cifereca_facet_field = str(field)
        button._cifereca_facet_value = value
        button.connect(
            "clicked",
            lambda _button, f=field, v=value: self._search_facet(f, v, controls),
        )
        return button


    def _facet_value_group(self, choices, field, controls):
        group = Gtk.FlowBox()
        group.set_selection_mode(Gtk.SelectionMode.NONE)
        group.set_halign(Gtk.Align.START)
        group.set_valign(Gtk.Align.CENTER)
        group.set_min_children_per_line(1)
        group.set_max_children_per_line(4)
        group.set_column_spacing(4)
        group.set_row_spacing(4)
        seen = set()
        for label, value in choices:
            key = (str(label or "").strip().casefold(), str(value or ""))
            if not key[0] or key in seen:
                continue
            seen.add(key)
            group.insert(self._facet_value_button(label, field, value, controls), -1)
        return group

    def _attach_row_facets(self, rows, objects, controls):
        from .book_facets import facets_for_book, project_list_facets
        allowed = {'author': 0, 'format': 1, 'language': 2, 'collection': 3, 'source': 4}
        for row in rows:
            facets=[
                facet for facet in facets_for_book(
                    objects.get(row.key,{}),
                    self.service.provider_display_name,
                )
                if facet.field in allowed
            ]
            facets.sort(key=lambda facet:(allowed[facet.field], facet.label.casefold()))
            facets=project_list_facets(facets)
            row.tags=[
                (
                    facet,
                    None if facet.field=='_overflow' else
                    (lambda _b,f=facet.field,text=facet.value:self._search_facet(f,text,controls)),
                )
                for facet in facets
            ]
            row.author_actions=[];row.source_action=None;row.facet_actions=[]


    def _add_facets(self, detail, item, controls):
        values = self._facet_values(item)
        value_map = {}
        for field, label, value in values:
            value_map.setdefault(field, []).append((label, value))

        row_fields = {
            "Source": "source",
            "Provenance": "source",
            "Fournisseur": "source",
            "Format": "format",
            "Support": "support",
            "Auteur": "author",
            "Éditeur": "publisher",
            "Sujets": "subject",
            "Sujet": "subject",
            "Collection / série": "collection",
            "Collection": "collection",
            "Série": "series",
            "ISBN": "isbn",
            "Identifiants": "isbn",
            "Langue": "language",
            "Langues": "language",
        }
        subtitle_fallback_fields = {
            "author", "publisher", "subject", "series", "collection", "language",
        }

        def bind_rows(widget):
            if isinstance(widget, Adw.ActionRow):
                field = row_fields.get(widget.get_title())
                original_subtitle = getattr(
                    widget,
                    "_cifereca_facet_original_subtitle",
                    widget.get_subtitle(),
                )
                widget._cifereca_facet_original_subtitle = original_subtitle

                previous = getattr(widget, "_cifereca_facet_values", None)
                if previous is not None:
                    try:
                        widget.remove(previous)
                    except (AttributeError, TypeError):
                        previous.set_visible(False)
                    widget._cifereca_facet_values = None

                choices = list(value_map.get(field, ())) if field else []
                if not choices and field in subtitle_fallback_fields:
                    displayed = str(original_subtitle or "").strip()
                    if displayed and displayed != "—":
                        choices = [(displayed, displayed)]

                widget.set_activatable(False)
                if choices:
                    widget.set_subtitle("")
                    facet_values = self._facet_value_group(choices, field, controls)
                    facet_values.set_tooltip_text(
                        "Valeurs filtrables : utilisez Tab puis Entrée ou Espace"
                    )
                    widget.add_suffix(facet_values)
                    widget._cifereca_facet_values = facet_values
                else:
                    widget.set_subtitle(original_subtitle)

            child = widget.get_first_child()
            while child:
                bind_rows(child)
                child = child.get_next_sibling()

        bind_rows(detail.content)

        subtitle = getattr(detail, "_subtitle_label", None)
        authors = list(value_map.get("author", ()))
        if subtitle is not None:
            author_box = getattr(subtitle, "_cifereca_author_facets", None)
            if author_box is not None:
                parent = author_box.get_parent()
                if parent is not None:
                    parent.remove(author_box)
                subtitle._cifereca_author_facets = None
            subtitle.set_visible(True)
            if authors:
                parent = subtitle.get_parent()
                if parent is not None:
                    subtitle.set_visible(False)
                    author_box = self._facet_value_group(authors, "author", controls)
                    author_box.add_css_class("detail-subtitle")
                    parent.append(author_box)
                    subtitle._cifereca_author_facets = author_box

        identity = self.service.current_identity
        binding = self.service.adept_return_for_artifact(
            str(item.get("artifact_id") or ""),
            identity.identity_id if identity else "",
        )
        if binding:
            button = Gtk.Button(label="Restituer le prêt ADEPT")
            button.set_tooltip_text(
                "Restituer avec le profil d’origine ; conserver le fichier local. "
                "Le retour fournisseur reste une action distincte."
            )
            def return_loan(_button):
                self.runner.submit(
                    "Restitution du prêt ADEPT…",
                    lambda: self.service.return_adept_acquisition(binding["acquisition_id"]),
                    lambda _result: (
                        button.set_sensitive(False),
                        self._activity_result(
                            "Retour enregistré, jeton absent · fichier conservé"
                            + (
                                " · état local vérifié"
                                if _result.get("verification") == "confirmed"
                                else " · vérification locale non confirmée"
                            )
                        ),
                    ),
                    lambda exc: self.show_error(str(exc), "Restitution ADEPT impossible"),
                    control=button,
                    priority=TaskPriority.USER_ACTION,
                )
            button.connect("clicked", return_loan)
            detail.add_actions([button])

    def _decorate_book_detail(self, detail, item):
        library_detail = getattr(getattr(self, 'library_page', None), 'detail', None)
        controls = self.library_controls if detail is library_detail else self.search_controls
        self._add_facets(detail, item, controls)
        self._add_local_book_actions(detail, item)
        self._add_plugin_book_actions(detail, item)

    def _book_action_context(self, item) -> UiActionContext:
        metadata = dict(item.get("metadata") or {}) if isinstance(item, dict) else {}
        book = BookRef(
            book_id=str(item.get("item_id") or item.get("record_id") or item.get("key") or item.get("artifact_id") or ""),
            title=str(item.get("title") or ""),
            authors=tuple(x.strip() for x in str(item.get("author") or "").split(";") if x.strip()),
            identifiers={"provider": str(item.get("provider_id") or item.get("source_provider") or "")},
            metadata=metadata,
        )
        artifact = None
        path_value = item.get("local_path") or metadata.get("path")
        artifact_id = str(item.get("artifact_id") or "")
        if path_value and artifact_id:
            path = Path(str(path_value))
            size = 0
            try: size = path.stat().st_size
            except OSError: pass
            sha = ""
            try:
                row = next((r for r in self.service.storage.artifacts() if str(r.get("artifact_id") or "") == artifact_id), None)
                if row: sha = str(row.get("sha256") or "")
            except Exception: pass
            fmt = str(item.get("media_format") or path.suffix.lstrip(".") or "")
            artifact = ArtifactRef(artifact_id=artifact_id, book_id=book.book_id or None,
                format=ArtifactFormat("application/epub+zip" if fmt.lower() == "epub" else "application/octet-stream", fmt),
                size_bytes=size, sha256=sha, filename=path.name)
        return UiActionContext(target="book.detail", route=self.view_stack.get_visible_child_name() or "", book=book, artifact=artifact, metadata=metadata)

    def _add_plugin_book_actions(self, detail, item) -> None:
        pages = getattr(self, "_pages", {}).values()
        context = self._book_action_context(item)
        buttons = []
        for _route, action, enabled in contextual_actions(pages, "book.detail", context):
            button = self.action_button(action.title, action.icon_name)
            button._cifereca_action_id = "plugin:" + action.id
            button.set_sensitive(enabled)
            button.connect("clicked", lambda _b, a=action, c=context: self._invoke_ui_action(a, self._UiHost(self, c)))
            buttons.append(button)
        if buttons:
            detail.add_actions(buttons)

    def _known_search_result(self, row):
        meta = row.get("metadata") or {}
        raw = meta.get("search_result")
        if isinstance(raw, dict):
            try: return SearchResult(**raw)
            except TypeError: pass
        provider = row.get("provider_id")
        if provider == "local": return None
        return SearchResult(str(row.get("key")), str(row.get("item_id") or ""), str(meta.get("docbase") or ""),
            "digital", str(row.get("title") or ""), row.get("author"), None, row.get("media_format"),
            meta.get("publisher"), None, None, row.get("thumbnail_url"), None, str(meta.get("scenario") or ""),
            source_provider=str(provider), metadata=meta)

    def _load_known_detail(self, item):
        selected = self._selected_search_id
        self.runner.submit("Disponibilités…", lambda: self.service.holdings(item, self._last_search_query, force=False),
            lambda detail: self._apply_search_detail(selected, item, detail),
            priority=TaskPriority.USER_ACTION, kind="holdings")

    def refresh_library(self, scan: bool = False, control: Gtk.Widget | None = None, *, refresh_covers: bool = False) -> None:
        if self._library_refresh_pending and control is None:
            return
        self._library_refresh_pending = True
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        def work() -> list[dict[str, Any]]:
            if refresh_covers:
                self.service.catalog_scan(force_covers=True)
            return self.service.library_entries(identity_id=identity_id, scan=scan and not refresh_covers)
        def ok(entries: list[dict[str, Any]]) -> None:
            self._library_refresh_pending = False
            active = self.service.current_identity.identity_id if self.service.current_identity else None
            if active != identity_id:
                self.refresh_library(False); return
            self._show_library_entries(entries)
        def err(exc: BaseException) -> None:
            self._library_refresh_pending = False
            self.diagnostics.add("UI", "library refresh", "error", detail={"error": type(exc).__name__, "message": str(exc)})
            if control is not None:
                self.show_error(str(exc), "Actualisation de la bibliothèque impossible")
        self.runner.submit(
            "Actualisation de la bibliothèque…", work, ok, err, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
            kind="library",
        )

    def _show_library_entries(self, entries: list[dict[str, Any]]) -> None:
        self._library_entries_cache = list(entries)
        self._render_library(entries)
        if hasattr(self,'_refresh_statistics'):self._refresh_statistics()

    def _open_library_item(self, key: str) -> None:
        """Navigate to Library while retaining the current book identity."""
        self._pending_library_selection_key = str(key)
        self.navigate("library")
        self._select_pending_library_item()

    def _select_pending_library_item(self) -> bool:
        key = getattr(self, "_pending_library_selection_key", None)
        if not key or not hasattr(self, "library_page"):
            return False
        if self.library_page.select_key(key):
            self._pending_library_selection_key = None
            return True
        entries = list(getattr(self, "_library_entries_cache", ()))
        if not entries:
            # Keep the request while the initial Library refresh is pending.
            return False
        full_keys = {str(item.get("key") or "") for item in entries}
        if key in full_keys:
            self.toast("Ce livre est masqué par les filtres actifs de la Bibliothèque.", 5)
        else:
            self.toast("Ce livre n’est plus présent dans la Bibliothèque.", 5)
        self._pending_library_selection_key = None
        return False

    def _render_library(self, entries: list[dict[str, Any]] | None = None) -> None:
        if not hasattr(self, "library_page"):
            return
        _perf_started = time.perf_counter()
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        if entries is None:
            entries = list(self._library_entries_cache)
        self._library_entries_cache = list(entries)
        rows: list[BookListItem] = []
        self._library_objects = {}
        for item in self.library_controls.state().filter(entries):
            provider = str(item.get("provider_id") or "local")
            providers = list(item.get("providers") or [provider])
            provider_label = (f"{len(providers)} sources" if len(providers) > 1 else self.service.provider_display_name(provider))
            state = self._library_availability_label(item)
            key = str(item.get("key") or f"library:{len(self._library_objects)}")
            self._library_objects[key] = item
            metadata = item.get("metadata") or {}
            meta_parts = [state] if state else []
            if item.get("due_at"):
                meta_parts.append(f"retour {fr_date(str(item.get('due_at')))}")
            sync_status = item.get("sync_status")
            if sync_status and not item.get("local"):
                meta_parts.append(self._kobo_backup_status_label(str(sync_status)))
            device_statuses = item.get("device_statuses") if isinstance(item.get("device_statuses"), list) else []
            if device_statuses:
                present_count = sum(1 for status_row in device_statuses if status_row.get("observed_state") == "present")
                keep_count = sum(1 for status_row in device_statuses if status_row.get("desired_state") == "keep")
                if present_count:
                    meta_parts.append(f"Kobo {present_count}/{len(device_statuses)}")
                elif keep_count:
                    meta_parts.append("Kobo à synchroniser")
            rows.append(BookListItem(
                key, str(item.get("title") or "Sans titre"), str(item.get("author") or ""),
                " · ".join(part for part in meta_parts if part), provider_label, item.get("thumbnail_url"),
            ))
        self._attach_row_facets(rows, self._library_objects, self.library_controls)
        self.library_page.show_rows(rows, total_count=len(entries))
        restored_pending = self._select_pending_library_item()
        if not restored_pending and self.library_page.selection.get_selected_item() is None:
            self._library_selected(None)
        self.ui_perf.record("render_library", (time.perf_counter() - _perf_started) * 1000.0)

    # ---------- Récupérations récentes ----------
    def _build_recent_page(self) -> MasterDetailPage:
        page = MasterDetailPage(
            self, "Récupérations récentes",
            "Dernières matérialisations locales de l’identité courante",
            "Aucune récupération récente",
            "Les acquisitions locales apparaîtront ici.",
        )
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Actualiser les récupérations récentes")
        refresh.connect("clicked", lambda _b: self.refresh_recent(refresh)); page.header_actions.append(refresh)
        page.connect_selected(self._recent_selected)
        self._recent_objects: dict[str, dict[str, Any]] = {}
        return page

    def refresh_recent(self, control: Gtk.Widget | None = None) -> None:
        if self._recent_refresh_pending and control is None:
            return
        self._recent_refresh_pending = True
        def work() -> list[dict[str, Any]]:
            identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
            return self.service.recent_acquisitions(identity_id=identity_id)
        def ok(items: list[dict[str, Any]]) -> None:
            self._recent_refresh_pending = False
            self._show_recent_entries(items)
        def err(exc: BaseException) -> None:
            self._recent_refresh_pending = False
            self.diagnostics.add("UI", "recent refresh", "error", detail={"error": type(exc).__name__, "message": str(exc)})
            if control is not None:
                self.show_error(str(exc), "Actualisation des récupérations impossible")
        self.runner.submit(
            "Actualisation des récupérations récentes…", work, ok, err, control=control,
            priority=TaskPriority.USER_ACTION if control is not None else TaskPriority.NORMAL,
            kind="recent",
        )

    def _show_recent_entries(self, items: list[dict[str, Any]]) -> None:
        self._recent_entries_cache = list(items)
        self._render_recent(items)

    def _render_recent(self, items: list[dict[str, Any]] | None = None) -> None:
        if not hasattr(self, "recent_page"):
            return
        _perf_started = time.perf_counter()
        if items is None:
            items = list(self._recent_entries_cache)
        self._recent_entries_cache = list(items)
        self._recent_objects = {}
        rows: list[BookListItem] = []
        for item in items:
            key = str(item.get("placement_id") or f"recent:{len(rows)}")
            self._recent_objects[key] = item
            provider = str(item.get("provider_id") or "local")
            temporal = format_recent_timestamp(item.get("acquired_at"))
            rows.append(BookListItem(
                key,
                str(item.get("title") or "Sans titre"),
                str(item.get("author") or ""),
                temporal,
                "Kobo" if provider == "kobo" else provider.capitalize(),
                item.get("thumbnail_url"),
            ))
        self._attach_row_facets(rows,self._recent_objects,self.search_controls)
        self.recent_page.show_rows(rows)
        self.ui_perf.record("render_recent", (time.perf_counter() - _perf_started) * 1000.0)

    def _add_local_book_actions(self, detail, item):
        placement_id=str(item.get('placement_id') or '')
        identity_id=str(item.get('identity_id') or '')
        if not placement_id:
            provider=item.get('provider_id') or item.get('source_provider')
            credential=item.get('source_credential') or (item.get('metadata') or {}).get('source_credential')
            item_id=item.get('item_id') or item.get('record_id')
            current=getattr(self.service,'current_identity',None)
            if not provider:
                if item.get('title'): self._add_catalogue_action(detail, item)
                return
            if not credential or not item_id or not current:
                self._add_catalogue_action(detail, item); return
            placement=self.service.local_placement_for_source(provider_id=provider,credential_id=credential,
                item_id=item_id,identity_id=current.identity_id)
            if not placement:
                self._add_catalogue_action(detail, item); return
            placement_id=placement['placement_id'];identity_id=current.identity_id
        if not identity_id:
            self._add_catalogue_action(detail, item); return
        try:self.service.local_book_path(placement_id,identity_id=identity_id)
        except Exception:
            self._add_catalogue_action(detail, item); return
        opening=self.action_button('Ouvrir','document-open-symbolic')
        opening._cifereca_action_id = 'open-local'
        def open_book(_button):
            try:
                # Recheck existence and identity at activation, then launch without a shell.
                uri=self.service.local_book_path(placement_id,identity_id=identity_id).as_uri()
                Gio.AppInfo.launch_default_for_uri(uri,None)
            except Exception as exc:self.show_error(str(exc),'Ouverture du livre impossible')
        opening.connect('clicked',open_book)
        buttons=[opening]
        source_provider = str(item.get('provider_id') or item.get('source_provider') or 'local')
        source_credential = str(item.get('source_credential') or (item.get('metadata') or {}).get('source_credential') or '')
        source_item_id = str(item.get('item_id') or item.get('source_item_id') or (item.get('record_id') or ''))
        known_possessions = ()
        manager = getattr(self, 'plugin_manager', None)
        library_read = manager.broker('library.read') if manager else None
        if library_read is not None:
            try:
                logical = library_read.book(source_item_id) if source_item_id else None
                if logical is None and item.get('artifact_id'):
                    logical = library_read.for_artifact(str(item.get('artifact_id')))
                known_possessions = tuple(logical.possessions) if logical is not None else ()
            except Exception as exc:
                self.diagnostics.add(
                    'UI', 'read known possessions', 'error',
                    detail={'error': type(exc).__name__, 'message': str(exc)},
                )
        for target in self.service.identities:
            if target.identity_id==identity_id:continue
            # A current Core possession is authoritative even when there is no
            # local file yet for the target identity.
            if source_item_id and identity_has_attested_possession(
                known_possessions,
                identity_id=target.identity_id,
                provider_id=source_provider,
                source_item_id=source_item_id,
                credential_id=source_credential,
            ):
                continue
            # Keep the placement fallback for older/local-only records that are
            # not represented in the logical possession projection.
            if source_item_id:
                try:
                    already_owned = self.service.local_placement_for_source(
                        provider_id=source_provider, credential_id=source_credential,
                        item_id=source_item_id, identity_id=target.identity_id,
                    ) is not None
                except Exception:
                    already_owned = False
                if already_owned:
                    continue
            button=self.action_button('Envoyer à '+target.label,'document-send-symbolic')
            button._cifereca_action_id = 'send-local:'+target.identity_id
            def send(_button,target_id=target.identity_id):
                def work():
                    self.service.local_book_path(placement_id,identity_id=identity_id)
                    return self.service.materialize_to_identity(placement_id,target_id)
                self.runner.submit('Envoi du fichier local…',work,
                    lambda _result:(self._activity_result('Livre envoyé'),self.refresh_recent(),self.refresh_library(False)),
                    control=_button,priority=TaskPriority.USER_ACTION,kind='materialize')
            button.connect('clicked',send);buttons.append(button)
        routing = manager.broker('artifact.routing') if manager else None
        context = self._book_action_context(item) if routing is not None else None
        if routing is not None and context is not None and context.artifact is not None:
            try:
                route_request = ArtifactRouteRequest(context.artifact, identity_id=identity_id or None)
                route_targets = tuple(routing.targets(route_request))
            except Exception as exc:
                route_targets = ()
                self.diagnostics.add('UI','artifact routing targets','error',detail={'error':type(exc).__name__,'message':str(exc)})
            if route_targets:
                menu = Gtk.MenuButton(label='Envoyer vers…')
                menu._cifereca_action_id = 'artifact-routing'
                menu.set_tooltip_text('Envoyer cet artefact vers une cible de stockage compatible')
                popover = Gtk.Popover()
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
                box.set_margin_top(8); box.set_margin_bottom(8); box.set_margin_start(8); box.set_margin_end(8)
                for target in route_targets:
                    target_button = Gtk.Button(label=str(target.target.label or target.target.target_id))
                    target_button.add_css_class('flat')
                    def route(_button, target_id=target.target.target_id):
                        request = ArtifactRouteRequest(context.artifact, target_id=target_id, identity_id=identity_id or None)
                        def work():
                            plan = routing.plan(request)
                            return routing.transfer(plan)
                        self.runner.submit('Envoi vers le stockage…', work,
                            lambda _result:(self._activity_result('Livre envoyé'), self.refresh_library(False), self.refresh_recent()),
                            lambda exc:self.show_error(str(exc),'Envoi impossible'), control=_button,
                            priority=TaskPriority.USER_ACTION, kind='artifact-route')
                    target_button.connect('clicked', route); box.append(target_button)
                popover.set_child(box); menu.set_popover(popover); buttons.append(menu)
        detail.add_actions(buttons)

    def _open_placement(self, placement):
        try:
            current = self.service.current_identity
            if not current or current.identity_id != placement['identity_id']:
                raise ValueError('L’identité active a changé')
            uri = self.service.local_book_path(placement['placement_id'], identity_id=current.identity_id).as_uri()
            Gio.AppInfo.launch_default_for_uri(uri, None)
        except Exception as exc: self.show_error(str(exc), 'Ouverture du livre impossible')

    def _add_catalogue_action(self, detail, item):
        url = self.service.public_catalogue_url(item)
        if not url or url in getattr(detail, "_rendered_document_urls", set()): return
        button = self.action_button('Catalogue en ligne', 'web-browser-symbolic')
        button._cifereca_action_id = 'open-catalogue'
        button.set_tooltip_text('Ouvrir la notice connue, sinon le site de la source ou un catalogue public pour rechercher le titre')
        def open_catalogue(_button):
            try:
                # Revalidate public metadata at activation; never use a delivery URL.
                current = self.service.public_catalogue_url(item)
                if current: Gio.AppInfo.launch_default_for_uri(current, None)
            except Exception as exc: self.show_error(str(exc), 'Ouverture du catalogue impossible')
        button.connect('clicked', open_catalogue); detail.add_actions([button])


    @staticmethod
    def _book_description(item):
        metadata = item.get('metadata') or {}
        return (metadata.get('epub_metadata') or {}).get('description') or metadata.get('description')

    def _add_book_notice(self, detail, item, *, include_description=True):
        metadata=item.get('metadata') or {}
        if include_description:
            detail._show_description(self._book_description(item))
        essential_labels={'Source','Provenance','Format','Langue','Langues','Disponibilité'}
        rows=[row for row in notice_rows(metadata) if row[0] not in essential_labels]
        if rows:detail.add_group('Notice EPUB' if metadata.get('epub_metadata') else 'Notice',rows)
        catalogue=metadata.get('catalogue_metadata') or {}
        if catalogue:
            catalogue_rows=[row for row in notice_rows(catalogue) if row[0] not in essential_labels]
            if catalogue_rows:detail.add_group('Catalogue',catalogue_rows)
        if metadata.get('epub_metadata_schema'):
            cover=metadata.get('epub_cover') or {}
            labels={'extracted':'Extraite du fichier','no_usable_image':'Aucune image exploitable','metadata_unreadable':'Métadonnées illisibles'}
            status=metadata.get('epub_cover_status') or 'not_inspected'
            facts=[('Couverture',labels.get(status,'À réinspecter'))]
            if cover.get('method'):facts.append(('Détection',{'declared':'Déclaration EPUB','declared_page':'Page de couverture déclarée','guide':'Guide de couverture','first_spine':'Image unique de la première page (déduction)'}.get(cover['method'],cover['method'])))
            detail.add_group('Image du livre',facts)

    def _recent_selected(self, row: BookListItem | None) -> None:
        if not row:
            return
        item = self._recent_objects.get(row.key)
        if not item:
            return
        detail = self.recent_page.detail
        provider = str(item.get("provider_id") or "local")
        full_path = str(item.get("path") or "")
        file_name = Path(full_path).name if full_path else "—"
        acquired = format_recent_timestamp(item.get("acquired_at"), detailed=True)
        detail.show_summary(
            title=str(item.get("title") or "Sans titre"),
            subtitle=str(item.get("author") or ""),
            thumbnail=item.get("thumbnail_url"),
            badges=[provider.capitalize(), "Local"],
            group_title="Récupération",
            rows=[
                ("Fichier", file_name),
                ("Récupéré", acquired),
                ("Provenance", provider.capitalize()),
                ("Chemin", full_path or "—"),
            ],
        )
        self._add_book_notice(detail,item)
        self._add_local_book_actions(detail,item)
        self._add_facets(detail, item, self.search_controls)

    # Compatibility name used by the 0.8.0 refresh paths.
    def _render_kobo_loans(self) -> None:
        self._render_library()


    def _book_recommendation_target(self, item):
        manager = getattr(self, "plugin_manager", None)
        if manager is None:
            return None
        context = self._book_action_context(item)
        book = context.book
        if book is None:
            return None
        try:
            services = manager.services("recommendation")
        except Exception:
            return None
        for resolved in services:
            if "recommendation.book-reference" not in set(resolved.capabilities):
                continue
            service = resolved.service
            if isinstance(service, BookReferenceRecommendationService):
                return service, book
        return None

    def _add_book_recommendation_action(self, detail, item) -> None:
        if self._book_recommendation_target(item) is None:
            return
        button = self.action_button(
            "Recommander à partir de ce livre",
            "starred-symbolic",
        )
        button._cifereca_action_id = "recommend-from-book"
        button._cifereca_action_priority = 25

        def recommend(_button):
            target = self._book_recommendation_target(item)
            if target is None:
                self.toast("Le service de recommandation n’est plus disponible.")
                return
            service, book = target
            try:
                route = str(service.recommend_from_book(book) or "").strip()
            except Exception as exc:
                self.show_error(str(exc), "Recommandation impossible")
                return
            if not route:
                self.show_error(
                    "Le service n’a fourni aucune page à ouvrir.",
                    "Recommandation impossible",
                )
                return
            self.navigate(route)

        button.connect("clicked", recommend)
        detail.add_actions([button])

    def _library_selected(self, row: BookListItem | None) -> None:
        if not row:
            self.library_page.detail.empty(
                "Sélectionnez un livre",
                "Sélectionnez un livre dans la liste pour afficher ses détails.",
            )
            return
        item = self._library_objects.get(row.key)
        if not item:
            return
        if item.get("source") == "loan" and item.get("loan_id"):
            loan = self.loan_objects.get(str(item.get("loan_id")))
            if loan:
                self.library_page.detail.show_loan(loan, include_pnb_action=False)
                actions = []
                if self.service.can_download_loan(loan):
                    prepare = Gtk.Button(label="Télécharger le livre du prêt"); prepare.add_css_class("suggested-action")
                    prepare.set_tooltip_text("Récupère le prêt et traite son ACSM avec Kobo-Adept. Un fichier déjà présent est conservé ; une nouvelle copie peut être créée. Aucun nouvel emprunt.")
                    prepare.connect("clicked", lambda _b: self.prepare_pnb_for_loan(loan, False, prepare)); actions.append(prepare)
                self.library_page.detail.add_actions(actions)
                self._add_facets(self.library_page.detail, item, self.library_controls)
                self._add_local_book_actions(self.library_page.detail, item)
                self._add_book_recommendation_action(self.library_page.detail, item)
                return

        detail = self.library_page.detail
        provider = str(item.get("provider_id") or "local")
        availability = self._library_availability_label(item)
        identity = self.service.identity_by_id(str(item.get("identity_id") or ""))
        sync_label = self._kobo_backup_status_label(str(item.get("sync_status") or "")) if item.get("sync_status") else ""
        if sync_label == "À jour":
            sync_label = ""
        description = self._book_description(item)
        detail.show_presentation(book_detail_presentation(
            title=str(item.get("title") or "Sans titre"),
            subtitle=str(item.get("author") or ""),
            thumbnail=item.get("thumbnail_url"),
            badges=[],
            section_rows=[
                ("Essentiel", [
                    ("Source", self.service.provider_display_name(provider)),
                    ("Format", str(item.get("media_format") or "").upper()),
                    ("Langue", detail_language(item.get("metadata"))),
                    ("Disponibilité", availability),
                ]),
                ("Bibliothèque", [
                    ("Identité", identity.label if identity else str(item.get("identity_id") or "")),
                    ("Fichier local", str(item.get("local_path") or "")),
                    ("État acquisition", sync_label),
                ]),
            ],
            links=document_links(item),
        ), description=description)
        placements = list(item.get('placements') or [])
        possessions = list(item.get('possessions') or [])
        presence_rows = []
        for placement in placements:
            iid = str(placement.get('identity_id') or '')
            identity_ref = self.service.identity_by_id(iid) if iid else None
            owner = identity_ref.label if identity_ref else (iid or 'Bibliothèque')
            target = placement.get('storage_target_id') or placement.get('provider_id') or 'local'
            presence_rows.append((owner, f"Stockage : {target}"))
        for possession in possessions:
            iid = str(possession.get('identity_id') or '')
            identity_ref = self.service.identity_by_id(iid) if iid else None
            owner = identity_ref.label if identity_ref else (iid or 'Compte')
            provider_name = self.service.provider_display_name(str(possession.get('provider_id') or ''))
            presence_rows.append((owner, f"Droit distant : {provider_name}"))
        unique_presence = list(dict.fromkeys(presence_rows))
        if len(unique_presence) > 1:
            detail.add_group('Présence / Bibliothèques / Sources', unique_presence)
        self._add_book_notice(detail,item, include_description=False)
        self._add_local_book_actions(detail,item)
        self._add_book_recommendation_action(detail, item)
        self._add_facets(self.library_page.detail, item, self.library_controls)

    def _kobo_selected(self, row: BookListItem | None) -> None:
        self._library_selected(row)

    def _configure_kobo_backup_timer(self, *, run_now: bool = False) -> None:
        """0.8.2 compatibility shim: automation runs at application opening only.

        The historical interval setting remains readable for rollback compatibility,
        but Paris no longer installs a recurring in-process timer.
        """
        if self._kobo_backup_timer_id is not None:
            try:
                GLib.source_remove(self._kobo_backup_timer_id)
            except Exception:
                pass
            self._kobo_backup_timer_id = None
        if run_now and self.service.kobo_backup_enabled():
            GLib.idle_add(lambda: (self.run_kobo_backup(False), False)[1])

    def run_kobo_backup(self, manual: bool = True, control: Gtk.Widget | None = None) -> None:
        original_label = control.get_label() if isinstance(control, Gtk.Button) else None
        progress_state = {"last": 0}

        def progress(event: dict[str, Any]) -> None:
            if not isinstance(control, Gtk.Button):
                return
            kind = str(event.get("event") or "")
            current = int(event.get("current") or 0)
            total = int(event.get("total") or 0)
            if kind == "adept_probe":
                label = "Synchronisation Kobo · vérification Adept…"
            elif kind in {"manifest_fetch", "manifest"}:
                label = f"Synchronisation Kobo · manifest {total}" if total else "Synchronisation Kobo · manifest…"
            elif kind in {"scan", "acquiring", "downloaded", "item_error"} and total:
                if kind == "scan" and current not in {1, total} and current - progress_state["last"] < 10:
                    return
                progress_state["last"] = current
                label = f"Synchronisation Kobo · {current}/{total}"
            else:
                return
            GLib.idle_add(lambda: (control.set_label(label), False)[1])

        def work() -> dict[str, Any]:
            return self.service.sync_kobo_backup(progress=progress)

        def restore_label() -> None:
            if isinstance(control, Gtk.Button) and original_label is not None:
                control.set_label(original_label)

        def ok(summary: dict[str, Any]) -> None:
            restore_label()
            self._render_kobo_loans()
            attention = int(summary.get("attention_required", 0) or 0)
            errors = int(summary.get("errors", 0) or 0)
            if manual:
                self._activity_result(
                    f"Synchronisation Kobo : {summary.get('downloaded', 0)} téléchargé(s), "
                    f"{summary.get('pending', 0)} en attente, {summary.get('unchanged', 0)} à jour, "
                    f"{summary.get('skipped', 0)} ignoré(s), {attention} action(s), {errors} erreur(s)",
                    success=not bool(attention or errors),
                    persistent=bool(attention or errors),
                )
            elif summary.get("downloaded") or summary.get("pending") or attention or errors:
                self._activity_result(
                    f"Synchronisation Kobo automatique : {summary.get('downloaded', 0)} téléchargé(s), "
                    f"{summary.get('pending', 0)} en attente, {attention} action(s), {errors} erreur(s)",
                    success=not bool(attention or errors),
                    persistent=bool(attention or errors),
                )
        def err(exc: BaseException) -> None:
            restore_label()
            if manual:
                self.show_error(str(exc), "Synchronisation Kobo impossible")
            else:
                self.diagnostics.add("KOBO", "automatic backup task", "error", detail={"error": type(exc).__name__, "message": str(exc)})
        self.runner.submit(
            "Synchronisation locale Kobo…", work, ok, err, control=control,
            priority=TaskPriority.USER_ACTION if manual else TaskPriority.ACTIVE_LOAN, kind="kobo-acquisition",
        )

    def choose_adept_loan(self, acquisition_id, loan, control):
        self.runner.submit("Lecture des prêts ADEPT…", lambda: self.service.adept_loan_choices(acquisition_id),
            lambda result: self._present_adept_loan_choices(acquisition_id, loan, result),
            lambda exc: self.show_error(str(exc), "Lecture des prêts impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-inventory")

    def _present_adept_loan_choices(self, acquisition_id, loan, result):
        options = result["options"]
        if not options:
            self.toast("Aucun prêt ADEPT dans les profils liés" if result["complete"] else "Inventaire incomplet : aucun prêt disponible", 6)
            return
        dialog = Adw.MessageDialog.new(self, "Rattacher un prêt ADEPT", "Choisissez le prêt correspondant au fichier local. Le titre aide au choix ; il ne constitue pas une preuve automatique. Aucun livre ne sera téléchargé ni restitué.")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        title = Gtk.Label(label=loan.title, xalign=0); title.set_wrap(True); box.append(title)
        if not result["complete"]:
            warning = Gtk.Label(label="Inventaire partiel : seuls les profils lus avec succès sont proposés.", xalign=0); warning.set_wrap(True); box.append(warning)
        labels = [f"{o['title']} · {o['profile_name']} · {o['state']} · {o['loan_id']}" for o in options]
        dropdown = Gtk.DropDown.new_from_strings(labels); dropdown.set_enable_search(True)
        dropdown.set_selected(Gtk.INVALID_LIST_POSITION); dropdown.set_tooltip_text("Titre · profil · état · identifiant ADEPT")
        box.append(dropdown)
        detail = Gtk.Label(label="Sélectionnez un prêt pour voir sa validité et son chemin connu.", xalign=0); detail.set_wrap(True); box.append(detail)
        dialog.set_extra_child(box); dialog.add_response("cancel", "Annuler"); dialog.add_response("attach", "Confirmer le rattachement")
        dialog.set_response_enabled("attach", False); dialog.set_close_response("cancel")
        def selected(_dropdown, _pspec):
            index = dropdown.get_selected(); valid = index < len(options)
            dialog.set_response_enabled("attach", valid)
            if valid:
                option = options[index]; detail.set_label(f"Validité : {option.get('validity') or 'Inconnue'}\nChemin ADEPT : {option.get('local_book') or 'Non fourni'}")
        dropdown.connect("notify::selected", selected)
        def response(_dialog, choice):
            index = dropdown.get_selected()
            if choice != "attach" or index >= len(options): return
            option = options[index]
            def ok(_result):
                self.loan_page.detail.show_loan(loan); self.refresh_library(False)
                self._activity_result("Prêt ADEPT rattaché · fichier conservé")
            self.runner.submit("Rattachement du prêt ADEPT…", lambda: self.service.attach_adept_loan(acquisition_id, option["profile_id"], option["loan_id"]), ok,
                lambda exc: self.show_error(str(exc), "Rattachement impossible"), priority=TaskPriority.USER_ACTION, kind="adept-binding")
        dialog.connect("response", response); dialog.present()

    def reconcile_adept_from_detail(self, acquisition_id, loan, control):
        def ok(result):
            self.loan_page.detail.show_loan(loan)
            labels = {"resolved": "Liaison ADEPT vérifiée", "ambiguous": "Plusieurs prêts correspondent : liaison non résolue", "unresolved": "Aucun prêt ne correspond au chemin du fichier", "inventory_error": "Inventaire partiel : liaison non résolue", "profile_missing": "Associez un profil ADEPT à cette identité", "artifact_missing": "Fichier local absent", "binding_conflict": "L’inventaire contredit la liaison enregistrée"}
            self._activity_result(labels.get(result["status"], "Liaison ADEPT non résolue"))
        self.runner.submit("Vérification du prêt ADEPT…", lambda: self.service.reconcile_adept_acquisition(acquisition_id), ok,
            lambda exc: self.show_error(str(exc), "Vérification ADEPT impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-inventory")

    def return_adept_from_detail(self, acquisition_id: str, control: Gtk.Widget) -> None:
        def ok(_result):
            verification = _result.get("verification")
            suffix = " · état local vérifié" if verification == "confirmed" else (" · vérification locale non confirmée" if verification else "")
            message="Retour enregistré, jeton absent" if verification=="confirmed" else "Commande exécutée, retour non confirmé"
            result_message = message+" · fichiers conservés" + suffix
            reporter = getattr(self, "_activity_result", None)
            if callable(reporter):
                reporter(result_message)
            else:
                self.toast(result_message, 7)
            if getattr(self, "_displayed_loan", None):
                self.loan_page.detail.show_loan(self._displayed_loan)
            self.refresh_library(False)
            refresh = getattr(self, 'refresh_loans', None)
            if callable(refresh): refresh(True)
        self.runner.submit("Restitution du prêt ADEPT…", lambda: self.service.return_adept_acquisition(acquisition_id), ok,
            lambda exc: self.show_error(str(exc), "Restitution ADEPT impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="adept-return")

    def prepare_pnb_for_loan(self, loan: Loan, send: bool = False, control: Gtk.Widget | None = None) -> None:
        def work():
            if send:
                raise RuntimeError("L’envoi vers une liseuse nécessite un plugin storage.target.")
            return self.service.reacquire_digital_loan(loan)
        def ok(result):
            self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
            self.loan_page.detail.show_loan(loan)
            suffix = " · restitution ADEPT disponible" if result.get("adept_return_available") else " · liaison ADEPT indisponible : voir le détail"
            result_message = "Livre récupéré" + suffix
            reporter = getattr(self, "_activity_result", None)
            if callable(reporter):
                reporter(result_message)
            else:
                self.toast(result_message, 6)
        self.runner.submit("Acquisition du livre numérique…", work, ok,
            lambda exc: self.show_error(str(exc), "Acquisition impossible"), control=control,
            priority=TaskPriority.USER_ACTION, kind="loan-acquisition")

    def can_borrow_item(self, item: SearchResult) -> bool:
        try:
            session = self.service._session_for_source(item.source_provider, item.source_credential)
            return bool(session.connected and session.capabilities.borrow)
        except Exception:
            return False

    def _eligible_borrow_accounts(self, item: SearchResult):
        session = self.service._session_for_source(item.source_provider, item.source_credential)
        if not session.capabilities.borrow:
            return session, []
        identity_id = self.service.current_identity.identity_id if self.service.current_identity else None
        accounts = [account for account in session.accounts if account.is_primary and (not identity_id or account.identity_id == identity_id)]
        if not accounts:
            accounts = [account for account in session.accounts if account.is_primary]
        return session, accounts

    def borrow_search_item(self, item: SearchResult, control: Gtk.Widget | None = None, *, account=None) -> None:
        try:
            session, accounts = self._eligible_borrow_accounts(item)
        except Exception as exc:
            self.show_error(str(exc), "Emprunt impossible"); return
        if not session.capabilities.borrow:
            self.show_error(f"{session.descriptor.display_name} : emprunt non pris en charge", "Emprunt impossible"); return
        if not accounts:
            self.show_error(f"{session.descriptor.display_name} : aucun compte principal disponible", "Emprunt impossible"); return
        if account is None and len(accounts) > 1:
            dialog = Adw.MessageDialog.new(self, "Choisir le compte d’emprunt", "Plusieurs comptes sont éligibles. Le compte utilisé doit être choisi explicitement.")
            dialog.add_response("cancel", "Annuler")
            for index, candidate in enumerate(accounts):
                response = f"account-{index}"
                label = candidate.display_name or candidate.barcode or candidate.unique_id or f"Compte {index + 1}"
                dialog.add_response(response, label)
            def selected(_dialog, response):
                if response.startswith("account-"):
                    index = int(response.split("-", 1)[1])
                    self.borrow_search_item(item, control, account=accounts[index])
            dialog.connect("response", selected); dialog.present(); return
        account = account or accounts[0]

        def work():
            loan = session.borrow(item, account)
            loan.identity_id = loan.identity_id or account.identity_id
            if loan.source_account_id is None:
                loan.source_account_id = account.unique_id or "primary"
            loans = self.service.refresh_identity_loans(force=True, identity_id=account.identity_id)
            return loan, loans

        def ok(value) -> None:
            loan, _loans = value
            self._activity_result(f"Emprunt confirmé : {loan.title}")
            self.refresh_library(False)
            self.refresh_recent()

        self.runner.submit(f"Emprunt depuis {item.source_provider}…", work, ok, control=control,
            priority=TaskPriority.USER_ACTION, kind="borrow")

    def download_direct_item(self, item: SearchResult, control: Gtk.Widget | None = None) -> None:
        def work():
            return self.service.obtain_search_item(item)

        def ok(result) -> None:
            acquisition = result.acquisition
            if result.path is not None:
                self._activity_result(f"Disponible dans la Bibliothèque : {result.path.name}")
                self.refresh_library(False)
                self.refresh_recent()
            elif acquisition.action == "command":
                self._activity_result("Commande d’obtention terminée")
            else:
                self._activity_result("Action d’obtention terminée")

        self.runner.submit(
            f"Obtention depuis {item.source_provider}…",
            work, ok, control=control,
            priority=TaskPriority.USER_ACTION, kind="obtain",
        )

    def open_downloads_folder(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(self.service.default_downloads_dir().as_uri(), None)
        except Exception as exc:
            self.show_error(str(exc), "Impossible d'ouvrir le dossier")

    def open_library_folder(self) -> None:
        try:
            Gio.AppInfo.launch_default_for_uri(self.service.library_dir().as_uri(), None)
        except Exception as exc:
            self.show_error(str(exc), "Impossible d'ouvrir la bibliothèque locale")

    # ---------- settings / diagnostics ----------
    def _build_settings_page(self, standalone: bool = False) -> Gtk.Widget:
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        clamp = Adw.Clamp(); clamp.set_maximum_size(980); scroll.set_child(clamp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        box.set_margin_top(22); box.set_margin_bottom(28); box.set_margin_start(18); box.set_margin_end(18)
        clamp.set_child(box)
        settings_heading = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8); box.append(settings_heading)
        title = Gtk.Label(label="Configuration et diagnostic", xalign=0); title.set_hexpand(True); title.set_wrap(True); add_css(title, "page-title"); settings_heading.append(title)
        sub = Gtk.Label(label="Identités, sources, acquisitions automatiques, intégration KOBO-ADEPT et diagnostic.", xalign=0); add_css(sub, "dim-label"); box.append(sub)

        settings_root = box
        tabs = Gtk.Stack(); tabs.set_hexpand(True)
        if not standalone:
            self._settings_tabs = tabs
        switcher = Gtk.StackSwitcher(); switcher.set_stack(tabs); switcher.add_css_class("settings-tabs")
        tab_scroll = Gtk.ScrolledWindow(); tab_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.NEVER)
        tab_scroll.set_child(switcher); settings_root.append(tab_scroll); settings_root.append(tabs)
        sections = {}
        for key, label in (("accounts", "Identités et comptes"), ("library", "Bibliothèque"),
                           ("sync", "Synchronisation"), ("interface", "Interface"), ("diagnostic", "Diagnostic")):
            section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            sections[key] = section; tabs.add_titled(section, key, label)

        settings_revisions = {key: 0 for key in sections}
        saved_settings_revisions = dict(settings_revisions)
        tracked_settings_widgets: set[int] = set()
        settings_action_widgets: dict[str, Any] = {}

        def current_settings_tab() -> str:
            return str(tabs.get_visible_child_name() or "accounts")

        def update_settings_actions(*_args) -> None:
            tab = current_settings_tab()
            state = settings_action_state(
                tab,
                dirty=settings_revisions.get(tab, 0) != saved_settings_revisions.get(tab, 0),
            )
            save_widgets = tuple(settings_action_widgets.get("save", ()))
            reconnect_widgets = tuple(settings_action_widgets.get("reconnect", ()))
            for widget in save_widgets:
                widget.set_visible(state.show_save)
                widget.set_sensitive(state.save_sensitive)
            for widget in reconnect_widgets:
                widget.set_visible(state.show_reconnect)
                widget.set_sensitive(state.reconnect_sensitive)
            for widget in tuple(settings_action_widgets.get("containers", ())):
                widget.set_visible(state.show_save or state.show_reconnect)

        def mark_settings_dirty(tab: str) -> None:
            if tab == "diagnostic":
                return
            settings_revisions[tab] = settings_revisions.get(tab, 0) + 1
            update_settings_actions()

        def track_settings_tree(tab: str, widget: Gtk.Widget) -> None:
            marker = id(widget)
            if marker not in tracked_settings_widgets:
                signal = None
                if isinstance(widget, Adw.EntryRow):
                    signal = "notify::text"
                elif isinstance(widget, Gtk.Switch):
                    signal = "notify::active"
                elif isinstance(widget, Gtk.DropDown):
                    signal = "notify::selected"
                if signal is not None:
                    widget.connect(signal, lambda *_args, tab_name=tab: mark_settings_dirty(tab_name))
                    tracked_settings_widgets.add(marker)
            try:
                child = widget.get_first_child()
            except AttributeError:
                child = None
            while child is not None:
                track_settings_tree(tab, child)
                child = child.get_next_sibling()

        def track_all_settings_widgets() -> None:
            for tab, section in sections.items():
                if tab != "diagnostic":
                    track_settings_tree(tab, section)

        box = sections["accounts"]

        descriptors = {d.id: d for d in self.service.provider_descriptors()}
        public_ids = {provider_id for provider_id in descriptors
                      if getattr(getattr(self.service._provider_plugin_status(provider_id), "descriptor", None), "instantiation", "multiple") == "singleton_public"}

        warning = Gtk.Label(
            label="Les credentials fournisseurs sont actuellement stockés en clair dans le fichier de configuration local (permissions 0600). Ils ne sont jamais inclus dans les diagnostics.",
            xalign=0,
        )
        warning.set_wrap(True); add_css(warning, "caption", "dim-label"); box.append(warning)

        identities_title = Gtk.Label(label="Identités", xalign=0); add_css(identities_title, "book-title"); box.append(identities_title)
        identities_hint = Gtk.Label(
            label="Une identité regroupe ses connexions fournisseurs, ses préférences et son profil ADE. Les secrets ADE restent gérés par Kobo-Adept.",
            xalign=0,
        )
        identities_hint.set_wrap(True); add_css(identities_hint, "caption", "dim-label"); box.append(identities_hint)
        identities_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14); box.append(identities_container)
        identity_editors: list[dict[str, Any]] = []
        configured_identities = self.config.identities()

        def add_identity_editor(data: dict[str, Any] | None = None) -> None:
            data = dict(data or {})
            existing_ids = {str(editor["id"].get_text()).strip() for editor in identity_editors}
            identity_id = str(data.get("id") or "identite").strip() or "identite"
            base = identity_id
            index = 2
            while identity_id in existing_ids:
                identity_id = f"{base}-{index}"; index += 1
            prefs = dict(data.get("preferences") or {})
            group = Adw.PreferencesGroup(); group.set_title(str(data.get("label") or identity_id)); identities_container.append(group)
            id_row = Adw.EntryRow(); id_row.set_title("Identifiant stable"); id_row.set_text(identity_id)
            if data.get("id"):
                id_row.set_sensitive(False)
            group.add(id_row)
            label_row = Adw.EntryRow(); label_row.set_title("Nom affiché"); label_row.set_text(str(data.get("label") or identity_id)); group.add(label_row)
            directory_row = Adw.EntryRow(); directory_row.set_title("Nom du répertoire bibliothèque")
            directory_row.set_text(str(data.get("directory_name") or identity_id))
            directory_row.set_tooltip_text("Changer ce dossier déplace les fichiers, fusionne sans écrasement et actualise la bibliothèque. Le libellé seul ne déplace rien.")
            group.add(directory_row)
            choose_directory = Gtk.Button.new_from_icon_name("folder-open-symbolic")
            choose_directory.set_tooltip_text("Choisir un dossier existant dans la bibliothèque")
            directory_row.add_suffix(choose_directory)
            choose_directory.connect("clicked", lambda _b, entry=directory_row: self._choose_identity_directory(entry, Path(library_dir_row.get_text().strip() or "~/EBooks")))
            languages_row = Adw.EntryRow(); languages_row.set_title("Langues de lecture (ordre, séparées par des virgules)")
            languages_row.set_text(", ".join(str(x) for x in (prefs.get("reading_languages") or ["fr"]))); group.add(languages_row)
            vo_row = Adw.ActionRow(); vo_row.set_title("Préférer la langue originale")
            vo_switch = Gtk.Switch(); vo_switch.set_active(bool(prefs.get("prefer_original_language", False))); vo_switch.set_valign(Gtk.Align.CENTER); vo_row.add_suffix(vo_switch); group.add(vo_row)
            adept_row = Adw.EntryRow(); adept_row.set_title("Profil ADE lié à cette identité (optionnel)")
            adept_row.set_text(str(data.get("adept_profile") or self.service.storage.adept_profile_binding_get("identity", identity_id) or "")); group.add(adept_row)
            choose_adept = Gtk.Button(label="Choisir…"); choose_adept.set_valign(Gtk.Align.CENTER)
            choose_adept.set_tooltip_text("Lister les profils Adobe capables de récupérer les prêts ACSM")
            adept_row.add_suffix(choose_adept)
            choose_adept.connect("clicked", lambda button, entry=adept_row: self._choose_adept_profile(entry, button))
            delete_row = Adw.ActionRow(); delete_row.set_title("Supprimer cette identité de la configuration")
            delete_button = Gtk.Button(label="Supprimer"); delete_button.add_css_class("destructive-action"); delete_button.set_valign(Gtk.Align.CENTER); delete_row.add_suffix(delete_button); group.add(delete_row)
            editor = {"group": group, "id": id_row, "label": label_row, "directory": directory_row, "languages": languages_row, "prefer_vo": vo_switch, "adept_profile": adept_row, "deleted": False}
            identity_editors.append(editor)
            def delete_identity(_button, e=editor):
                e["deleted"] = True
                e["group"].set_visible(False)
                mark_settings_dirty("accounts")
            delete_button.connect("clicked", delete_identity)

        for configured_identity in configured_identities:
            add_identity_editor(configured_identity)
        add_identity = Gtk.Button(label="Ajouter une identité")
        def add_identity_clicked(_button):
            add_identity_editor({})
            mark_settings_dirty("accounts")
        add_identity.connect("clicked", add_identity_clicked); box.append(add_identity)

        public_info = Gtk.Button(label="Gérer les plugins et leurs capacités techniques…")
        public_info.set_tooltip_text("Ouvrir Plugins pour l’activation et les capacités détaillées ; Diagnostic conserve l’état technique.")
        public_info.connect("clicked", lambda _b: self.navigate("plugins")); box.append(public_info)
        accounts_title = Gtk.Label(label="Sources et comptes fournisseurs", xalign=0); add_css(accounts_title, "book-title"); box.append(accounts_title)
        accounts_hint = Gtk.Label(
            label="Ajoutez et configurez ici les comptes utilisés par vos identités. Les capacités internes des plugins restent dans Plugins et Diagnostic.",
            xalign=0,
        )
        accounts_hint.set_wrap(True); add_css(accounts_hint, "caption", "dim-label"); box.append(accounts_hint)
        accounts_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14); box.append(accounts_container)
        account_editors: list[dict[str, Any]] = []
        configured_rows = self.config.provider_accounts()

        def add_account_editor(data: dict[str, Any] | None = None, provider_id: str = "paris") -> None:
            data = dict(data or {})
            provider_id = str(data.get("provider") or provider_id).lower()
            descriptor = descriptors.get(provider_id)
            if descriptor is None or descriptor.state != "ready" or provider_id in public_ids:
                return
            existing_ids = {str(x.get("id")) for x in account_editors}
            if not data.get("id"):
                existing_ids |= {str(x.get("id")) for x in configured_rows}
            base_id = f"{provider_id}-default"
            account_id = str(data.get("id") or base_id)
            n = 2
            while account_id in existing_ids:
                account_id = f"{provider_id}-{n}"; n += 1

            group = Adw.PreferencesGroup(); group.set_title(f"{descriptor.display_name} — {account_id}"); accounts_container.append(group)
            enabled_row = Adw.ActionRow(); enabled_row.set_title("Source activée" if not descriptor.credential_fields else "Compte activé")
            enabled = Gtk.Switch(); enabled.set_active(bool(data.get("enabled", True))); enabled.set_valign(Gtk.Align.CENTER); enabled_row.add_suffix(enabled); group.add(enabled_row)
            label_row = Adw.EntryRow(); label_row.set_title("Libellé local"); label_row.set_text(str(data.get("label") or descriptor.display_name)); group.add(label_row)
            identity_row = Adw.EntryRow(); identity_row.set_title("Identité (ID)")
            default_identity_id = str((configured_identities[0].get("id") if configured_identities else "default") or "default")
            identity_row.set_text(str(data.get("identity_id") or default_identity_id)); group.add(identity_row)
            credentials = dict(data.get("credentials") or {})
            fields: dict[str, Gtk.Widget] = {}
            for field in descriptor.credential_fields:
                if field.secret:
                    entry = Adw.PasswordEntryRow(); entry.set_title(field.label); entry.set_text(str(credentials.get(field.key) or ""))
                else:
                    entry = Adw.EntryRow(); entry.set_title(field.label); entry.set_text(str(credentials.get(field.key) or ""))
                group.add(entry); fields[field.key] = entry
            options = dict(data.get("options") or {})
            base_url_row = None
            opds_url_row = None
            vault_url_row = None
            if provider_id == "paris":
                base_url_row = Adw.EntryRow(); base_url_row.set_title("URL du portail")
                base_url_row.set_text(str(options.get("base_url") or (self.config.paris_base_url() if hasattr(self.config, "paris_base_url") else self.config.get("base_url")) or "https://bibliotheques.paris.fr")); group.add(base_url_row)
            elif provider_id == "gallica":
                opds_url_row = Adw.EntryRow(); opds_url_row.set_title("URL du catalogue OPDS")
                opds_url_row.set_text(str(options.get("opds_url") or "https://gallica.bnf.fr/opds")); group.add(opds_url_row)
                public = Adw.ActionRow(); public.set_title("Accès public")
                public.set_subtitle("Aucun identifiant requis · EPUB + documents sonores publics Gallica")
                group.add(public)
            elif provider_id == "kobo":
                vault_url_row = Adw.EntryRow(); vault_url_row.set_title("URL API Kobo-Vault")
                vault_url_row.set_text(str(options.get("base_url") or "")); group.add(vault_url_row)
                info = Adw.ActionRow(); info.set_title("Connexion indirecte au compte Kobo")
                info.set_subtitle("Kobo-Vault conserve la session Kobo. cifereca utilise uniquement l'URL API et un token dédié.")
                group.add(info)
            delete_row = Adw.ActionRow(); delete_row.set_title("Supprimer cette source de la configuration")
            delete_button = Gtk.Button(label="Supprimer"); delete_button.add_css_class("destructive-action"); delete_button.set_valign(Gtk.Align.CENTER); delete_row.add_suffix(delete_button); group.add(delete_row)
            editor = {
                "id": account_id, "provider": provider_id, "group": group, "enabled": enabled,
                "label": label_row, "identity": identity_row, "fields": fields, "base_url": base_url_row, "opds_url": opds_url_row, "vault_url": vault_url_row,
                "original_options": options, "deleted": False,
            }
            account_editors.append(editor)
            def delete_account(_button, e=editor):
                e["deleted"] = True
                e["group"].set_visible(False)
                mark_settings_dirty("accounts")
            delete_button.connect("clicked", delete_account)

        for configured in configured_rows:
            add_account_editor(configured, str(configured.get("provider") or "paris"))

        add_buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        for descriptor in descriptors.values():
            if descriptor.state != "ready" or descriptor.id in public_ids:
                continue
            add_label = f"Ajouter un compte {descriptor.display_name}" if descriptor.credential_fields else f"Ajouter la source {descriptor.display_name}"
            add = Gtk.Button(label=add_label)
            def add_account_clicked(_button, pid=descriptor.id):
                add_account_editor({}, pid)
                mark_settings_dirty("accounts")
            add.connect("clicked", add_account_clicked); add_buttons.append(add)
        box.append(add_buttons)

        # ADE is bound only at Paris Identity scope. Provider sub-accounts
        # intentionally have no ADE binding UI.

        # Download policy is intentionally independent from provider accounts:
        # all credentials of one plugin share the same download override.
        box = sections["library"]
        download_group = Adw.PreferencesGroup(); download_group.set_title("Téléchargements — réglages globaux"); box.append(download_group)
        global_downloads = dict(self.config.get("downloads", {}) or {})
        global_types = dict(global_downloads.get("content_type_directories", {}) or {})
        global_dir_row = Adw.EntryRow(); global_dir_row.set_title("Répertoire global")
        global_dir_row.set_text(str(global_downloads.get("directory") or "")); download_group.add(global_dir_row)
        global_ebook_row = Adw.EntryRow(); global_ebook_row.set_title("Répertoire livres numériques")
        global_ebook_row.set_text(str(global_types.get("ebook") or "")); download_group.add(global_ebook_row)
        global_audio_row = Adw.EntryRow(); global_audio_row.set_title("Répertoire livres audio")
        global_audio_row.set_text(str(global_types.get("audiobook") or "")); download_group.add(global_audio_row)
        global_cmd_row = Adw.EntryRow(); global_cmd_row.set_title("Commande de téléchargement (optionnelle)")
        global_cmd = global_downloads.get("command", []) or []
        global_cmd_row.set_text(" ".join(shlex.quote(str(x)) for x in global_cmd)); download_group.add(global_cmd_row)
        global_post_row = Adw.EntryRow(); global_post_row.set_title("Hook post-téléchargement (optionnel)")
        global_post = global_downloads.get("post_download_command", []) or []
        global_post_row.set_text(" ".join(shlex.quote(str(x)) for x in global_post)); download_group.add(global_post_row)
        download_hint = Gtk.Label(
            label="Priorité des répertoires : type+plugin → plugin → type global → global. Placeholders commandes : {url} {file} {directory} {title} {record_id} {provider} {content_type} {format}.",
            xalign=0,
        )
        download_hint.set_wrap(True); add_css(download_hint, "caption", "dim-label"); box.append(download_hint)

        provider_download_editors: dict[str, dict[str, Adw.EntryRow]] = {}
        for descriptor in descriptors.values():
            if descriptor.state != "ready":
                continue
            settings = self.config.provider_settings(descriptor.id)
            dcfg = dict(settings.get("downloads", {}) or {})
            tcfg = dict(dcfg.get("content_type_directories", {}) or {})
            group = Adw.PreferencesGroup(); group.set_title(f"Téléchargements — {descriptor.display_name}"); box.append(group)
            drow = Adw.EntryRow(); drow.set_title("Répertoire du plugin"); drow.set_text(str(dcfg.get("directory") or "")); group.add(drow)
            erow = Adw.EntryRow(); erow.set_title("Répertoire livres numériques"); erow.set_text(str(tcfg.get("ebook") or "")); group.add(erow)
            arow = Adw.EntryRow(); arow.set_title("Répertoire livres audio"); arow.set_text(str(tcfg.get("audiobook") or "")); group.add(arow)
            crow = Adw.EntryRow(); crow.set_title("Commande de téléchargement (surcharge)")
            crow.set_text(" ".join(shlex.quote(str(x)) for x in (dcfg.get("command", []) or []))); group.add(crow)
            prow = Adw.EntryRow(); prow.set_title("Hook post-téléchargement du plugin")
            prow.set_text(" ".join(shlex.quote(str(x)) for x in (dcfg.get("post_download_command", []) or []))); group.add(prow)
            provider_download_editors[descriptor.id] = {"directory": drow, "ebook": erow, "audiobook": arow, "command": crow, "post": prow}

        box = sections["diagnostic"]
        config_group = Adw.PreferencesGroup(); config_group.set_title("Application"); box.append(config_group)
        diag_row = Adw.ActionRow(); diag_row.set_title("Logs de diagnostic"); diag_row.set_subtitle("Conserver un journal technique expurgé des secrets")
        diag_switch = Gtk.Switch(); diag_switch.set_active(bool(self.config.get("diagnostics.enabled", True))); diag_switch.set_valign(Gtk.Align.CENTER); diag_row.add_suffix(diag_switch); config_group.add(diag_row)
        exp_row = Adw.ActionRow(); exp_row.set_title("Actions expérimentales"); exp_row.set_subtitle("Prolongation et retour anticipé numérique")
        exp_switch = Gtk.Switch(); exp_switch.set_active(bool(self.config.get("experimental_actions", False))); exp_switch.set_valign(Gtk.Align.CENTER); exp_row.add_suffix(exp_switch); interface_group = Adw.PreferencesGroup(); interface_group.set_title("Interface et actions"); interface_group.add(exp_row); sections["interface"].append(interface_group)
        interface_button = Gtk.Button(label="Configurer la barre de titre et les actions…")
        interface_button.connect("clicked", lambda _b: self.navigate("plugins")); sections["interface"].append(interface_button)
        level_row = Adw.ActionRow(); level_row.set_title("Niveau de diagnostic")
        level_row.set_subtitle("Appliqué et enregistré dès modification")
        level_drop = Gtk.DropDown.new_from_strings(["errors", "normal", "verbose", "trace"])
        levels = ["errors", "normal", "verbose", "trace"]
        try: level_drop.set_selected(levels.index({"detailed":"verbose"}.get(str(self.config.get("diagnostics.level", "normal")), str(self.config.get("diagnostics.level", "normal")))))
        except ValueError: level_drop.set_selected(1)
        level_row.add_suffix(level_drop); config_group.add(level_row)

        box = sections["sync"]
        backup_group = Adw.PreferencesGroup(); backup_group.set_title("Acquisition automatique / activité Kobo"); box.append(backup_group)
        backup_auto_row = Adw.ActionRow(); backup_auto_row.set_title("Synchroniser automatiquement les ebooks Kobo")
        backup_auto_row.set_subtitle("Paris résout l’acquisition via Kobo-Vault v2.3+ puis exécute localement via Kobo-Adept contract v8")
        backup_switch = Gtk.Switch(); backup_switch.set_active(bool(self.config.get("kobo_backup.enabled", False))); backup_switch.set_valign(Gtk.Align.CENTER); backup_auto_row.add_suffix(backup_switch); backup_group.add(backup_auto_row)
        unlinked_row = Adw.ActionRow(); unlinked_row.set_title("Autoriser les acquisitions Kobo malgré des livres locaux sans liaison")
        unlinked_row.set_subtitle("Désactivé : suspend les acquisitions du lot en présence d’EPUB importés non rapprochés. Activé : des doublons peuvent être téléchargés.")
        unlinked_switch = Gtk.Switch(); unlinked_switch.set_active(bool(self.config.get("kobo_backup.allow_unlinked_acquisition", False))); unlinked_switch.set_valign(Gtk.Align.CENTER); unlinked_row.add_suffix(unlinked_switch); backup_group.add(unlinked_row)
        library_dir_row = Adw.EntryRow(); library_dir_row.set_title("Bibliothèque locale possédée par cifereca")
        library_dir_row.set_text(str(self.config.get("library.directory", "~/EBooks") or "~/EBooks")); download_group.add(library_dir_row)
        backup_dir_row = Adw.EntryRow(); backup_dir_row.set_title("Sous-répertoire/override Kobo (vide = bibliothèque locale)")
        backup_dir_row.set_text(str(self.config.get("kobo_backup.directory", "") or "")); backup_group.add(backup_dir_row)
        backup_interval_row = Adw.EntryRow(); backup_interval_row.set_title("Intervalle automatique (minutes, minimum 15)")
        backup_interval_row.set_text(str(self.config.get("kobo_backup.interval_minutes", 60) or 60)); backup_group.add(backup_interval_row)
        vault_profiles = ["Android", "Desktop"]
        current_vault_profile = str(self.config.get("kobo_backup.display_profile", "Android") or "Android")
        if current_vault_profile not in vault_profiles:
            current_vault_profile = "Android"
        vault_display_row = Adw.ActionRow(); vault_display_row.set_title("DisplayProfile Kobo-Vault")
        vault_display_row.set_subtitle("v2.3 : ACSM est prioritaire quand disponible ; le DisplayProfile pilote ensuite SignedNoDrm/KDRM")
        vault_profile_drop = Gtk.DropDown.new_from_strings(vault_profiles)
        vault_profile_drop.set_selected(vault_profiles.index(current_vault_profile))
        vault_profile_drop.set_valign(Gtk.Align.CENTER); vault_display_row.add_suffix(vault_profile_drop); backup_group.add(vault_display_row)

        settings_actions = AdaptiveActionBar(source=settings_heading); settings_actions.set_halign(Gtk.Align.END); settings_heading.append(settings_actions)
        save = action_button(
            "Enregistrer", "document-save-symbolic", suggested=True,
            tooltip="Enregistrer les modifications de configuration en attente",
        ); settings_actions.append(save)
        reconnect = action_button(
            "Reconnecter", "network-transmit-receive-symbolic",
            tooltip="Enregistrer les modifications en attente puis reconnecter les sources",
        ); settings_actions.append(reconnect)
        settings_footer = AdaptiveActionBar(source=settings_root); settings_footer.set_halign(Gtk.Align.END)
        save_bottom = action_button(
            "Enregistrer", "document-save-symbolic", suggested=True,
            tooltip="Enregistrer les modifications de configuration en attente",
        ); settings_footer.append(save_bottom)
        reconnect_bottom = action_button(
            "Reconnecter", "network-transmit-receive-symbolic",
            tooltip="Enregistrer les modifications en attente puis reconnecter les sources",
        ); settings_footer.append(reconnect_bottom)
        settings_root.append(settings_footer)
        settings_action_widgets.update({
            "save": (save, save_bottom),
            "reconnect": (reconnect, reconnect_bottom),
            "containers": (settings_actions, settings_footer),
        })
        from .action_widgets import mirror_sensitivity
        mirror_sensitivity(save, save_bottom); mirror_sensitivity(reconnect, reconnect_bottom)
        tabs.connect("notify::visible-child-name", update_settings_actions)
        track_all_settings_widgets()
        update_settings_actions()

        box = sections["diagnostic"]
        diag_group = Adw.PreferencesGroup(); diag_group.set_title("Diagnostic copiable"); box.append(diag_group)
        diag_text = Gtk.TextView(); diag_text.set_editable(False); diag_text.set_monospace(True); diag_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        diag_text.set_top_margin(10); diag_text.set_bottom_margin(10); diag_text.set_left_margin(10); diag_text.set_right_margin(10)
        diag_scroll = Gtk.ScrolledWindow(); diag_scroll.set_min_content_height(330); diag_scroll.set_child(diag_text); diag_group.add(diag_scroll)
        buttons = AdaptiveActionBar(source=box); box.append(buttons)
        refresh = self.action_button("Actualiser", "view-refresh-symbolic")
        copy = self.action_button("Copier dans le presse-papiers", "edit-copy-symbolic", suggested=True)
        clear = self.action_button("Effacer", "edit-clear-symbolic")
        buttons.append(refresh); buttons.append(copy); buttons.append(clear)

        def refresh_text() -> None:
            content = self.diagnostics.text(__version__, {
                "transport": "httpx local direct",
                "base_url": (self.config.paris_base_url() if hasattr(self.config, "paris_base_url") else self.config.get("base_url")),
                "ui": "GTK4/libadwaita",
                "cover_pipeline": self.covers.stats(),
                "history_cover_search": self.history_cover_resolver.stats(),
                "ui_performance": self.ui_perf.snapshot(),
                "providers": self.service.provider_diagnostic_state(),
            })
            diag_text.get_buffer().set_text(content)

        def save_settings(_button=None, *, reconnect_after=False) -> bool:
            from pathlib import Path
            from copy import deepcopy
            draft = deepcopy(self.config)
            saved_identities: list[dict[str, Any]] = []
            identity_ids: set[str] = set()
            directory_names: set[str] = set()
            for editor in identity_editors:
                if editor.get("deleted"):
                    continue
                identity_id = editor["id"].get_text().strip()
                if not identity_id or not re.fullmatch(r"[A-Za-z0-9._-]+", identity_id):
                    self.show_error("Identité invalide : utilisez uniquement lettres, chiffres, point, tiret ou underscore.")
                    return False
                if identity_id in identity_ids:
                    self.show_error(f"Identité dupliquée : {identity_id}")
                    return False
                identity_ids.add(identity_id)
                from ..identities.directory_selection import directory_choice
                try:
                    directory_name = directory_choice(editor["directory"].get_text(), Path(library_dir_row.get_text().strip() or "~/EBooks"))
                except ValueError as exc:
                    self.show_error(str(exc)); return False
                except RuntimeError as exc:
                    self.show_error(str(exc)); return False
                if directory_name in directory_names:
                    self.show_error(f"Répertoire d’identité dupliqué : {directory_name}")
                    return False
                directory_names.add(directory_name)
                languages = [part.strip().lower() for part in editor["languages"].get_text().split(",") if part.strip()]
                if not languages:
                    languages = ["fr"]
                saved_identities.append({
                    "id": identity_id,
                    "label": editor["label"].get_text().strip() or identity_id,
                    "directory_name": directory_name,
                    "adept_profile": editor["adept_profile"].get_text().strip(),
                    "preferences": {
                        "prefer_original_language": editor["prefer_vo"].get_active(),
                        "reading_languages": languages,
                    },
                })
            if not saved_identities:
                self.show_error("cifereca doit conserver au moins une identité.")
                return False
            draft.set("identities", saved_identities)
            preserved = []
            for row in draft.provider_accounts():
                descriptor = descriptors.get(str(row.get("provider") or "").lower())
                if descriptor is None or descriptor.state != "ready" or descriptor.id in public_ids:
                    preserved.append(row)
            saved_accounts = list(preserved)
            kobo_identity_accounts: dict[str, str] = {}
            for row in preserved:
                if str(row.get("provider") or "").lower() == "kobo":
                    iid = str(row.get("identity_id") or "").strip()
                    if iid:
                        kobo_identity_accounts[iid] = str(row.get("id") or "kobo")
            for editor in account_editors:
                if editor.get("deleted"):
                    continue
                descriptor = descriptors[editor["provider"]]
                credentials: dict[str, str] = {}
                for field in descriptor.credential_fields:
                    credentials[field.key] = editor["fields"][field.key].get_text()
                options: dict[str, Any] = dict(editor.get("original_options") or {})
                if editor.get("base_url") is not None:
                    options["base_url"] = editor["base_url"].get_text().strip() or "https://bibliotheques.paris.fr"
                if editor.get("opds_url") is not None:
                    options["opds_url"] = editor["opds_url"].get_text().strip() or "https://gallica.bnf.fr/opds"
                if editor.get("vault_url") is not None:
                    options["base_url"] = editor["vault_url"].get_text().strip()
                account_identity_id = editor["identity"].get_text().strip() or saved_identities[0]["id"]
                if account_identity_id not in identity_ids:
                    self.show_error(f"Identité inconnue pour {editor['id']} : {account_identity_id}")
                    return False
                if editor["provider"] == "kobo":
                    previous = kobo_identity_accounts.get(account_identity_id)
                    if previous and previous != editor["id"]:
                        self.show_error(
                            f"Une identité ne peut avoir qu'un compte Kobo Vault : {account_identity_id} "
                            f"est déjà lié à {previous}."
                        )
                        return False
                    kobo_identity_accounts[account_identity_id] = editor["id"]
                saved_accounts.append({
                    "id": editor["id"],
                    "provider": editor["provider"],
                    "identity_id": account_identity_id,
                    "label": editor["label"].get_text().strip() or descriptor.display_name,
                    "enabled": editor["enabled"].get_active(),
                    "anonymous": not bool(descriptor.credential_fields),
                    "credentials": credentials,
                    "options": options,
                })
            draft.set("providers.accounts", saved_accounts)
            try:
                global_command = shlex.split(global_cmd_row.get_text()) if global_cmd_row.get_text().strip() else []
                global_post_command = shlex.split(global_post_row.get_text()) if global_post_row.get_text().strip() else []
                provider_commands: dict[str, tuple[list[str], list[str]]] = {}
                for provider_id, editor in provider_download_editors.items():
                    provider_commands[provider_id] = (
                        shlex.split(editor["command"].get_text()) if editor["command"].get_text().strip() else [],
                        shlex.split(editor["post"].get_text()) if editor["post"].get_text().strip() else [],
                    )
            except ValueError as exc:
                self.show_error(f"Commande de téléchargement invalide : {exc}")
                return False
            draft.set("downloads", {
                "directory": global_dir_row.get_text().strip(),
                "content_type_directories": {
                    "ebook": global_ebook_row.get_text().strip(),
                    "audiobook": global_audio_row.get_text().strip(),
                },
                "command": global_command,
                "post_download_command": global_post_command,
            })
            for provider_id, editor in provider_download_editors.items():
                settings = draft.provider_settings(provider_id)
                command, post_command = provider_commands[provider_id]
                settings["downloads"] = {
                    "directory": editor["directory"].get_text().strip(),
                    "content_type_directories": {
                        "ebook": editor["ebook"].get_text().strip(),
                        "audiobook": editor["audiobook"].get_text().strip(),
                    },
                    "command": command,
                    "post_download_command": post_command,
                }
                draft.set_provider_settings(provider_id, settings)
            draft.set("diagnostics.enabled", diag_switch.get_active())
            draft.set("diagnostics.level", levels[level_drop.get_selected()])
            draft.set("experimental_actions", exp_switch.get_active())
            try:
                backup_interval = max(15, int(backup_interval_row.get_text().strip() or "60"))
            except ValueError as exc:
                self.show_error(f"Configuration Kobo invalide : {exc}")
                return False
            draft.set("library.directory", library_dir_row.get_text().strip() or "~/EBooks")
            draft.set("kobo_backup.enabled", backup_switch.get_active())
            draft.set("kobo_backup.allow_unlinked_acquisition", unlinked_switch.get_active())
            draft.set("kobo_backup.directory", backup_dir_row.get_text().strip())
            draft.set("kobo_backup.interval_minutes", backup_interval)
            selected_profile = int(vault_profile_drop.get_selected())
            draft.set("kobo_backup.display_profile", vault_profiles[selected_profile] if 0 <= selected_profile < len(vault_profiles) else "Android")
            draft.set("kobo_backup.format", "epub")
            current_dirs = {i.identity_id: i.directory_name for i in self.service.identities}
            directory_change = any(row["id"] in current_dirs and row["directory_name"] != current_dirs[row["id"]] for row in saved_identities)
            if directory_change and getattr(self, "_active_tasks", {}):
                self.show_error("Attendez la fin des opérations en cours avant de changer un répertoire d’identité.")
                return False
            staged = deepcopy(draft.data)
            staged_settings_revisions = dict(settings_revisions)
            def ok(result):
                self.diagnostics.enabled = bool(staged["diagnostics"]["enabled"])
                self.diagnostics.level = staged["diagnostics"]["level"]
                from ..logging_utils import set_diagnostics_level
                set_diagnostics_level(self.diagnostics.level)
                message = "Configuration enregistrée"
                if result.get("migrated"):
                    message = f"Répertoire déplacé · {result['collisions']} collision(s) préservée(s) · bibliothèque actualisée"
                    self.refresh_library(False); self.refresh_recent(); self._render_dashboard()
                if hasattr(self, "identity_dropdown"):
                    self._populate_accounts()
                    self._render_dashboard()
                reporter = getattr(self, "_activity_result", None)
                if callable(reporter):
                    reporter(message)
                else:
                    self.toast(message, 5)
                self._configure_kobo_backup_timer(run_now=False)
                refresh_text()
                for tab, revision in staged_settings_revisions.items():
                    saved_settings_revisions[tab] = revision
                track_all_settings_widgets()
                update_settings_actions()
                if reconnect_after:
                    self.reconnect_sources()
            self.runner.submit("Enregistrement et migration des répertoires…" if directory_change else "Enregistrement de la configuration…",
                lambda: self.service.save_configuration(staged), ok,
                control=_button or save, priority=TaskPriority.USER_ACTION, kind="settings")
            return True

        def save_and_reconnect(_button=None) -> None:
            save_settings(_button or reconnect, reconnect_after=True)

        def copy_text(_button=None) -> None:
            """Copie le diagnostic via un Gdk.ContentProvider GTK4 natif.

            Les bindings PyGObject de certaines distributions n'exposent pas
            ``Gdk.Clipboard.set_text``. ``set_content`` avec un ContentProvider
            fonctionne sur GTK4/Wayland/X11 et remplace correctement un ancien
            contenu du presse-papiers.
            """
            refresh_text()
            buffer = diag_text.get_buffer()
            start, end = buffer.get_bounds()
            content = buffer.get_text(start, end, True)
            display = Gdk.Display.get_default()
            if not display:
                self.show_error("Aucun presse-papiers graphique disponible", "Copie impossible")
                return
            clipboard = display.get_clipboard()
            try:
                provider = Gdk.ContentProvider.new_for_value(content)
                # Garder une référence côté fenêtre est volontaire : le provider
                # doit rester vivant tant que notre application possède le clipboard.
                self._clipboard_provider = provider
                if not clipboard.set_content(provider):
                    raise RuntimeError("GDK a refusé la prise de possession du presse-papiers")
            except Exception as exc:
                self.diagnostics.add(
                    "UI", "clipboard write", "error",
                    detail={"error": type(exc).__name__, "message": str(exc)[:180]},
                    level="detailed",
                )
                self.show_error(f"Impossible d'écrire dans le presse-papiers : {exc}", "Copie impossible")
                return

            def verified(source, result, *_args) -> None:
                try:
                    copied = source.read_text_finish(result)
                except Exception as exc:
                    self.diagnostics.add(
                        "UI", "clipboard verify", "error",
                        detail={"error": type(exc).__name__, "message": str(exc)[:180]},
                        level="detailed",
                    )
                    # L'écriture GDK a réussi ; seule la relecture n'est pas disponible.
                    self.toast("Diagnostic copié dans le presse-papiers")
                    return
                if copied == content:
                    self.diagnostics.add("UI", "clipboard copy", "ok", detail={"verified": True}, level="detailed")
                    self.toast("Diagnostic copié dans le presse-papiers")
                else:
                    self.diagnostics.add(
                        "UI", "clipboard verify", "error",
                        detail={"message": "clipboard content mismatch"},
                        level="detailed",
                    )
                    self.toast("Le presse-papiers n'a pas pu être vérifié", 5)

            try:
                clipboard.read_text_async(None, verified)
            except Exception as exc:
                self.diagnostics.add(
                    "UI", "clipboard verify", "error",
                    detail={"error": type(exc).__name__, "message": str(exc)[:180]},
                    level="detailed",
                )
                self.toast("Diagnostic copié dans le presse-papiers")

        save.connect("clicked", save_settings)
        reconnect.connect("clicked", save_and_reconnect)
        save_bottom.connect("clicked", save_settings)
        reconnect_bottom.connect("clicked", save_and_reconnect)
        refresh.connect("clicked", lambda _b: refresh_text())
        copy.connect("clicked", copy_text)
        def persist_diagnostics(*_args):
            selected = int(level_drop.get_selected())
            if not 0 <= selected < len(levels):
                return
            try:
                self.service.save_diagnostic_preferences(enabled=diag_switch.get_active(), level=levels[selected])
                refresh_text()
            except Exception as exc:
                self.show_error("Impossible d’enregistrer le niveau de diagnostic : " + str(exc))
        level_drop.connect("notify::selected", persist_diagnostics)
        diag_switch.connect("notify::active", persist_diagnostics)
        clear.connect("clicked", lambda _b: (self.diagnostics.clear(), refresh_text()))
        refresh_text()
        if not standalone:
            self._diag_refresh = refresh_text
        return scroll

    def refresh_diagnostics_text(self) -> None:
        cb = getattr(self, "_diag_refresh", None)
        if cb:
            cb()
