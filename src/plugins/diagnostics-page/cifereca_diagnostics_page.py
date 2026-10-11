from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any

from cifereca.plugin_api import UiPageContribution
from diagnostics_explorer import (
    SOURCES, SEVERITIES, PERIODS, PAGE_SIZE, fetch_explorer_page,
    sanitized_rows, module_groups, source_summaries, explorer_header,
    export_selected_events,
)

# The broker provides already-redacted data. This is a second, stricter
# presentation boundary: neither UI modes nor clipboard may expose raw secrets.
_DIAGNOSTIC_SECRET_KEY = re.compile(
    r"pass(?:word)?|cookie|token|secret|authorization|bearer|barcode|readerid|"
    r"credential[_-]?id|account[_-]?id|identity[_-]?id|profile[_-]?id|"
    r"acsm|licen[sc]e|private[_-]?key|(^|_)path$|filename|payload|headers?", re.I
)
_DIAGNOSTIC_SECRET_VALUE = re.compile(
    r"(?i)\b(password|cookie|token|secret|authorization|barcode|readerid)"
    r"\s*[:=]\s*[^\s,;]+"
)
_DIAGNOSTIC_URL = re.compile(r"(?i)https?://[^\s<>]+")
_DIAGNOSTIC_MAIL = re.compile(r"(?i)\b[a-z0-9_.+%-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
_DIAGNOSTIC_PATH = re.compile(
    r"(?:/home/|/Users/|/run/user/)[^\s,;]+|[A-Za-z]:\\Users\\[^\s,;]+"
)
_MAX_DETAIL_BYTES = 24_000
_MAX_DETAIL_NODES = 300
_MAX_DETAIL_DEPTH = 7
_MAX_DETAIL_CHILDREN = 50
_MAX_DETAIL_TEXT = 800


def _redacted_text(value: str) -> str:
    # A partial email, URL or token can straddle the preview cut. Redact
    # complete values BEFORE truncating anything shown or copied to the UI.
    # Fail closed on oversized untrusted text to bound regex scanning.
    if len(value) > _MAX_DETAIL_BYTES:
        return "<texte trop long : contenu masqué>"
    text = _DIAGNOSTIC_URL.sub("<URL expurgée>", value)
    text = _DIAGNOSTIC_MAIL.sub("<courriel expurgé>", text)
    text = _DIAGNOSTIC_PATH.sub("<chemin expurgé>", text)
    text = _DIAGNOSTIC_SECRET_VALUE.sub(r"\1=<expurgé>", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <expurgé>", text)
    return text[:_MAX_DETAIL_TEXT] + (
        "… [tronqué]" if len(text) > _MAX_DETAIL_TEXT else ""
    )


def redacted_detail(value: Any) -> Any:
    """Snapshot JSON-compatible values with depth, count and length limits.

    Unknown objects are never introspected or stringified; cycles are stopped.
    Stable key ordering is applied only to supported scalar dictionary keys.
    """
    budget = [_MAX_DETAIL_NODES]
    stack: set[int] = set()

    def walk(item: Any, depth: int) -> Any:
        if budget[0] <= 0 or depth > _MAX_DETAIL_DEPTH:
            return "<limite atteinte>"
        budget[0] -= 1
        if item is None or isinstance(item, bool) or isinstance(item, int):
            return item
        if isinstance(item, float):
            return item if math.isfinite(item) else "<nombre non fini>"
        if isinstance(item, str):
            return _redacted_text(item)
        if not isinstance(item, (Mapping, list, tuple)):
            return "<type non pris en charge>"
        if id(item) in stack:
            return "<référence circulaire>"
        stack.add(id(item))
        try:
            if isinstance(item, Mapping):
                pairs = []
                for key, child in item.items():
                    if type(key) not in (str, int, float, bool):
                        continue
                    name = str(key)[:160]
                    pairs.append((name, child))
                pairs.sort(key=lambda pair: pair[0].casefold())
                result = {}
                for name, child in pairs[:_MAX_DETAIL_CHILDREN]:
                    safe_name = _redacted_text(name)
                    result[safe_name] = (
                        "<expurgé>" if _DIAGNOSTIC_SECRET_KEY.search(name)
                        else walk(child, depth + 1)
                    )
                if len(pairs) > _MAX_DETAIL_CHILDREN:
                    result["[suite]"] = "<éléments supplémentaires masqués>"
                return result
            children = [walk(child, depth + 1) for child in item[:_MAX_DETAIL_CHILDREN]]
            if len(item) > _MAX_DETAIL_CHILDREN:
                children.append("<éléments supplémentaires masqués>")
            return children
        finally:
            stack.remove(id(item))

    return walk(value, 0)


def _readable_lines(value: Any, indent: int = 0) -> list[str]:
    pad = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [pad + "{}"]
        rows = []
        for key, child in value.items():
            if isinstance(child, (dict, list)):
                rows.append(pad + str(key) + ":")
                rows.extend(_readable_lines(child, indent + 1))
            else:
                rows.append(pad + str(key) + ": " + json.dumps(child, ensure_ascii=False))
        return rows
    if isinstance(value, list):
        if not value:
            return [pad + "[]"]
        rows = []
        for child in value:
            if isinstance(child, (dict, list)):
                rows.append(pad + "–")
                rows.extend(_readable_lines(child, indent + 1))
            else:
                rows.append(pad + "– " + json.dumps(child, ensure_ascii=False))
        return rows
    return [pad + (value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))]


def format_diagnostic_detail(value: Any, mode: str = "readable") -> str:
    """Three selectable representations of the SAME redacted snapshot."""
    if mode not in {"readable", "json", "raw"}:
        raise ValueError("unknown diagnostic display mode")
    safe = redacted_detail(value)
    if mode == "readable":
        text = "\n".join(_readable_lines(safe))
    elif mode == "json":
        text = json.dumps(safe, ensure_ascii=False, indent=2, allow_nan=False)
    elif isinstance(safe, str):
        text = safe
    else:
        text = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    if len(text) > _MAX_DETAIL_BYTES:
        if mode == "json":
            return json.dumps({
                "notice": "Aperçu JSON limité à 24 Kio",
                "truncated": True,
                "preview": text[:_MAX_DETAIL_BYTES // 2],
            }, ensure_ascii=False, indent=2)
        return text[:_MAX_DETAIL_BYTES] + "\n… [affichage limité]"
    return text


def _safe_text(value: Any) -> str:
    return format_diagnostic_detail(value, "readable")


def public_inventory(diagnostics_broker) -> list[dict[str, Any]]:
    """Return the Core-owned plugin/version inventory as plain mappings."""
    rows: list[dict[str, Any]] = []
    for item in tuple(diagnostics_broker.inventory()):
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def relevant_runtime_diagnostics(diagnostics_broker, inventory_rows) -> list[dict[str, Any]]:
    """Read only bounded runtime diagnostics for runtimes present in inventory."""
    rows: list[dict[str, Any]] = []
    runtime_ids = {
        str(row.get("plugin_id") or "")
        for row in inventory_rows
        if str(row.get("type") or "") == "runtime.adapter"
    }
    for plugin_id in sorted(value for value in runtime_ids if value):
        result = diagnostics_broker.runtime_diagnostic(plugin_id)
        if isinstance(result, Mapping) and result:
            rows.append({"plugin_id": plugin_id, **dict(result)})
    return rows


def active_checks(diagnostics_broker) -> list[dict[str, Any]]:
    """Normalize Core-owned health checks for this presentation-only page."""
    rows: list[dict[str, Any]] = []
    for item in tuple(diagnostics_broker.active_checks()):
        if isinstance(item, Mapping):
            rows.append(dict(item))
            continue
        rows.append({
            "check_id": str(getattr(item, "check_id", "") or ""),
            "component": str(getattr(item, "component", "") or ""),
            "message": str(getattr(item, "message", "") or "Vérification"),
            "status": str(getattr(item, "status", "unknown") or "unknown"),
            "detail": dict(getattr(item, "detail", {}) or {}),
            "scope": str(getattr(item, "scope", "") or ""),
        })
    return rows


def _run_once_on_first_map(widget, callback):
    """Run the initial page work once, only after the page is effectively mapped."""
    fired = False

    def on_map(_widget):
        nonlocal fired
        if fired:
            return
        fired = True
        callback()

    widget.connect("map", on_map)
    get_mapped = getattr(widget, "get_mapped", None)
    if callable(get_mapped) and get_mapped():
        on_map(widget)


class DiagnosticsPagePlugin:
    def __init__(self) -> None:
        self.context = None
        self.diagnostics = None
        self.tasks = None
        self._check_handle = None

    def activate(self, context):
        self.context = context
        self.diagnostics = context.broker("diagnostics")
        self.tasks = context.broker("tasks")
        if self.diagnostics is None or self.tasks is None:
            raise RuntimeError("brokers diagnostics/tasks indisponibles")
        return self

    def pages(self):
        return [
            UiPageContribution(
                route="diagnostics",
                title="Diagnostics",
                icon_name="utilities-system-monitor-symbolic",
                order=95,
                factory=self._build,
                group="configuration",
            )
        ]

    def _build(self, host):
        from gi.repository import Gdk, Gtk, Pango

        broker = self.diagnostics
        if broker is None:
            raise RuntimeError("plugin Diagnostics non activé")

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for margin in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + margin)(18)
        scroll.set_child(outer)

        intro = Gtk.Label(
            label="État technique expurgé fourni par le Core. Les plugins ne lisent ni fichiers internes, ni cookies, ni secrets."
        )
        intro.set_wrap(True)
        intro.set_xalign(0)
        outer.append(intro)

        action_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh.set_tooltip_text("Actualiser les diagnostics")
        copy = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
        copy.set_tooltip_text("Copier le diagnostic support expurgé")
        check = Gtk.Button.new_from_icon_name("system-run-symbolic")
        check.set_tooltip_text("Vérifier les services configurés et runtimes")
        action_row.append(refresh)
        action_row.append(check)
        action_row.append(copy)
        outer.append(action_row)

        status_title = Gtk.Label(label="État système", xalign=0)
        status_title.add_css_class("heading")
        outer.append(status_title)
        status_grid = Gtk.FlowBox()
        status_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        status_grid.set_homogeneous(True)
        status_grid.set_min_children_per_line(1)
        status_grid.set_max_children_per_line(3)
        status_grid.set_column_spacing(10)
        status_grid.set_row_spacing(8)
        outer.append(status_grid)


        # Journals remain inside the existing Diagnostics page. Every refresh
        # is an asynchronous call to the read-only public Diagnostics broker.
        journals_title = Gtk.Label(label="Journaux par composant", xalign=0)
        journals_title.add_css_class("heading")
        outer.append(journals_title)
        journals_hint = Gtk.Label(
            label="Flux expurgé et borné du Core ; aucune collecte réseau. "
                  "La recherche texte et le tri agissent sur la page chargée.",
            xalign=0,
        )
        journals_hint.set_wrap(True)
        outer.append(journals_hint)

        filters_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_selector = Gtk.DropDown.new_from_strings(
            ["Toutes les sources", "Événements Core", "Journal applicatif"]
        )
        source_selector.set_tooltip_text("Source des événements")
        severity_selector = Gtk.DropDown.new_from_strings(
            ["Tous niveaux", "Debug", "Info", "Avertissements", "Erreurs", "Critiques"]
        )
        severity_selector.set_tooltip_text("Niveau de gravité")
        period_selector = Gtk.DropDown.new_from_strings(
            ["Toutes périodes", "Dernière heure", "24 heures", "7 jours", "30 jours"]
        )
        period_selector.set_selected(2)
        period_selector.set_tooltip_text("Période de la consultation")
        filters_top.append(source_selector)
        filters_top.append(severity_selector)
        filters_top.append(period_selector)
        outer.append(filters_top)

        filters_bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        module_filter = Gtk.Entry()
        module_filter.set_placeholder_text("Préfixe module (Entrée pour appliquer)")
        module_filter.set_hexpand(True)
        module_filter.set_tooltip_text("Filtre serveur sur le préfixe de module")
        search_filter = Gtk.Entry()
        search_filter.set_placeholder_text("Rechercher dans cette page")
        search_filter.set_hexpand(True)
        search_filter.set_tooltip_text("Recherche locale dans les événements chargés")
        sort_latest = Gtk.CheckButton(label="Plus récents d’abord")
        sort_latest.set_active(True)
        filters_bottom.append(module_filter)
        filters_bottom.append(search_filter)
        filters_bottom.append(sort_latest)
        outer.append(filters_bottom)

        journals_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        journals_refresh = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        journals_refresh.set_tooltip_text("Actualiser les journaux sans synchronisation")
        journals_previous = Gtk.Button(label="Précédent")
        journals_next = Gtk.Button(label="Suivant")
        journals_copy = Gtk.Button(label="Copier la sélection")
        journals_actions.append(journals_refresh)
        journals_actions.append(journals_previous)
        journals_actions.append(journals_next)
        journals_actions.append(journals_copy)
        outer.append(journals_actions)

        journals_sources = Gtk.Label(label="Sources non alimentées", xalign=0)
        journals_sources.set_wrap(True)
        journals_sources.add_css_class("dim-label")
        outer.append(journals_sources)
        journals_summary = Gtk.Label(label="Actualisation requise", xalign=0)
        journals_summary.set_wrap(True)
        outer.append(journals_summary)
        journals_scroll = Gtk.ScrolledWindow()
        journals_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        journals_scroll.set_min_content_height(200)
        journals_scroll.set_size_request(-1, 350)
        journals_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        journals_scroll.set_child(journals_box)
        outer.append(journals_scroll)

        diag_title = Gtk.Label(label="Diagnostics", xalign=0)
        diag_title.add_css_class("heading")
        outer.append(diag_title)
        diag_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        diag_expander = Gtk.Expander(label="Afficher les détails techniques")
        diag_expander.set_expanded(False)
        diag_expander.set_child(diag_box)
        outer.append(diag_expander)

        check_title = Gtk.Label(label="Vérification active", xalign=0)
        check_title.add_css_class("heading")
        outer.append(check_title)
        check_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(check_box)
        check_hint = Gtk.Label(label="Lancez la vérification pour contrôler les connexions connues et runtimes sans synchroniser ni télécharger.", xalign=0)
        check_hint.set_wrap(True)
        check_box.append(check_hint)

        export_title = Gtk.Label(label="Rapport support (copie expurgée)", xalign=0)
        export_title.add_css_class("heading")
        outer.append(export_title)
        report_expander = Gtk.Expander(label="Ouvrir le rapport support")
        report_expander.set_expanded(False)
        report_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        report_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        report_resize = Gtk.Button(label="Agrandir")
        report_wrap = Gtk.CheckButton(label="Retour à la ligne")
        report_wrap.set_active(True)
        report_actions.append(report_resize)
        report_actions.append(report_wrap)
        report_container.append(report_actions)
        report_scroll = Gtk.ScrolledWindow()
        report_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        report_scroll.set_min_content_height(220)
        report_scroll.set_size_request(-1, 320)
        export_view = Gtk.TextView()
        export_view.set_editable(False)
        export_view.set_cursor_visible(False)
        export_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        export_view.set_monospace(True)
        report_scroll.set_child(export_view)
        report_container.append(report_scroll)
        report_expander.set_child(report_container)
        outer.append(report_expander)

        def toggle_report_size(_button):
            is_big = report_resize.get_label() == "Agrandir"
            report_scroll.set_size_request(-1, 560 if is_big else 320)
            report_resize.set_label("Réduire" if is_big else "Agrandir")

        def toggle_report_wrap(button):
            export_view.set_wrap_mode(
                Gtk.WrapMode.WORD_CHAR if button.get_active() else Gtk.WrapMode.NONE
            )

        report_resize.connect("clicked", toggle_report_size)
        report_wrap.connect("toggled", toggle_report_wrap)

        def clear_container(container) -> None:
            child = container.get_first_child()
            while child is not None:
                nxt = child.get_next_sibling()
                container.remove(child)
                child = nxt

        def add_detail_view(container, value: Any) -> None:
            # A single redacted snapshot drives every mode and the clipboard.
            safe = redacted_detail(value)
            expander = Gtk.Expander(label="Détails expurgés")
            expander.set_expanded(False)
            wrapper = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            controls = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            label = Gtk.Label(label="Vue :", xalign=0)
            selector = Gtk.DropDown.new_from_strings(
                ["Lisible", "JSON indenté", "Brut expurgé"]
            )
            selector.set_tooltip_text("Représentation des mêmes données expurgées")
            copy_detail = Gtk.Button.new_from_icon_name("edit-copy-symbolic")
            copy_detail.set_tooltip_text("Copier le détail expurgé actuellement affiché")
            controls.append(label)
            controls.append(selector)
            controls.append(copy_detail)
            wrapper.append(controls)

            detail_scroll = Gtk.ScrolledWindow()
            detail_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            detail_scroll.set_min_content_height(160)
            detail_scroll.set_size_request(-1, 250)
            detail_view = Gtk.TextView()
            detail_view.set_editable(False)
            detail_view.set_cursor_visible(False)
            detail_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
            detail_view.set_monospace(True)
            detail_scroll.set_child(detail_view)
            wrapper.append(detail_scroll)

            def visible_text() -> str:
                modes = ("readable", "json", "raw")
                selected = min(int(selector.get_selected()), len(modes) - 1)
                return format_diagnostic_detail(safe, modes[selected])

            def change_view(*_args) -> None:
                detail_view.get_buffer().set_text(visible_text())

            def copy_view(_button) -> None:
                display = Gdk.Display.get_default()
                if display is None:
                    host.toast("Presse-papiers indisponible")
                    return
                display.get_clipboard().set(visible_text())
                host.toast("Détail expurgé copié")

            selector.connect("notify::selected", change_view)
            copy_detail.connect("clicked", copy_view)
            change_view()
            expander.set_child(wrapper)
            container.append(expander)

        def add_status(key: str, value: Any) -> None:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.add_css_class("card")
            card.set_hexpand(True)
            for margin in ("top", "bottom", "start", "end"):
                getattr(card, "set_margin_" + margin)(8)
            caption = Gtk.Label(label=str(key), xalign=0)
            caption.add_css_class("caption")
            caption.set_wrap(True)
            card.append(caption)
            if isinstance(value, (Mapping, list, tuple)):
                summary = Gtk.Label(label="Données structurées", xalign=0)
                summary.add_css_class("dim-label")
                card.append(summary)
                add_detail_view(card, value)
            else:
                value_label = Gtk.Label(label=_safe_text(value), xalign=0)
                value_label.set_wrap(True)
                value_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                value_label.set_selectable(True)
                card.append(value_label)
            status_grid.insert(card, -1)

        def add_diag(row: Mapping[str, Any]) -> None:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            card.add_css_class("card")
            for margin in ("top", "bottom", "start", "end"):
                getattr(card, "set_margin_" + margin)(8)
            title = _safe_text(row.get("message") or row.get("operation") or row.get("id") or "Diagnostic")
            heading = Gtk.Label(label=title, xalign=0)
            heading.set_wrap(True)
            heading.add_css_class("heading")
            card.append(heading)
            meta_parts = []
            for key in ("timestamp", "component", "status", "severity", "level", "plugin_id", "source", "operation"):
                if key not in row:
                    continue
                value = row[key]
                if value not in (None, ""):
                    meta_parts.append(f"{key}: {_safe_text(value)}")
            if meta_parts:
                meta = Gtk.Label(label=" · ".join(meta_parts), xalign=0)
                meta.add_css_class("dim-label")
                meta.set_wrap(True)
                card.append(meta)
            if "detail" in row:
                add_detail_view(card, row["detail"])
            diag_box.append(card)


        page_offset = 0
        page_generation = 0
        current_page: dict[str, Any] = {
            "events": [], "sources": [], "total": 0, "offset": 0,
            "next_offset": None,
        }
        selected_event_ids: set[str] = set()

        def display_events() -> None:
            clear_container(journals_box)
            rows = sanitized_rows(
                current_page, search=search_filter.get_text().strip(),
                newest_first=sort_latest.get_active(),
            )
            states = source_summaries(current_page)
            if states:
                journals_sources.set_text("  ·  ".join(
                    f"{state['source']} : {state['label']}" for state in states
                ))
            else:
                journals_sources.set_text("Sources non alimentées ou indisponibles")
            journals_summary.set_text(explorer_header(
                current_page, rows, search=search_filter.get_text().strip()
            ))
            for group in module_groups(rows):
                title = (
                    f"{group['module']} · {group['source']} · "
                    f"{group['count']} événement(s) · {group['severity']}"
                )
                expander = Gtk.Expander(label=title)
                expander.set_expanded(group["severity"] in {"critical", "error"})
                group_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
                for row in group["events"]:
                    item = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                    item.add_css_class("card")
                    first_line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    select = Gtk.CheckButton()
                    select.set_tooltip_text("Sélectionner cet événement pour copie expurgée")
                    event_id = row["event_id"]
                    select.set_active(event_id in selected_event_ids)

                    def on_select(button, event_ref=event_id):
                        if button.get_active():
                            selected_event_ids.add(event_ref)
                        else:
                            selected_event_ids.discard(event_ref)
                        journals_copy.set_sensitive(bool(selected_event_ids))

                    select.connect("toggled", on_select)
                    first_line.append(select)
                    summary = _safe_text(
                        f"{row['timestamp']} · {row['severity']} · {row['message']}"
                    )
                    event_label = Gtk.Label(label=summary, xalign=0)
                    event_label.set_wrap(True)
                    event_label.set_selectable(True)
                    event_label.set_hexpand(True)
                    first_line.append(event_label)
                    item.append(first_line)
                    provenance = Gtk.Label(
                        label=_safe_text(
                            f"Source : {row['source']} · Module : {row['module']} "
                            f"· Référence : {event_id[:16]}"
                        ),
                        xalign=0,
                    )
                    provenance.add_css_class("dim-label")
                    provenance.set_wrap(True)
                    provenance.set_selectable(True)
                    item.append(provenance)
                    add_detail_view(item, row)
                    group_box.append(item)
                expander.set_child(group_box)
                journals_box.append(expander)
            if not rows:
                empty = Gtk.Label(
                    label="Aucun événement sur cette page. "
                          "Vérifier la période, les filtres ou la disponibilité des sources.",
                    xalign=0,
                )
                empty.set_wrap(True)
                journals_box.append(empty)
            journals_previous.set_sensitive(
                page_offset >= PAGE_SIZE
            )
            journals_next.set_sensitive(
                current_page.get("next_offset") is not None
                and int(current_page["next_offset"]) <= 10000
            )
            journals_copy.set_sensitive(bool(selected_event_ids))

        def load_events(*_args) -> None:
            nonlocal page_generation
            page_generation += 1
            generation = page_generation
            selected_event_ids.clear()
            journals_summary.set_text("Actualisation en cours…")
            journals_previous.set_sensitive(False)
            journals_next.set_sensitive(False)

            # GTK widgets may only be accessed on the UI thread.
            query = {
                "source": SOURCES[int(source_selector.get_selected())],
                "module": module_filter.get_text(),
                "severity": SEVERITIES[int(severity_selector.get_selected())],
                "period": PERIODS[int(period_selector.get_selected())],
                "offset": page_offset,
            }

            def read_snapshot(_control):
                return fetch_explorer_page(broker, **query)

            try:
                handle = self.tasks.submit(
                    read_snapshot, label="Consultation des journaux",
                    kind="diagnostics",
                )
            except Exception as exc:
                journals_summary.set_text(
                    f"Source indisponible : {type(exc).__name__}"
                )
                return
            self._event_handle = handle

            def apply_result(token, data, error) -> bool:
                nonlocal current_page
                if token != page_generation:
                    return False  # stale asynchronous result
                if error is not None or not isinstance(data, Mapping):
                    current_page = {
                        "events": [], "sources": [], "total": 0,
                        "offset": page_offset, "next_offset": None,
                    }
                    display_events()
                    journals_summary.set_text(
                        "Données indisponibles — vérification du broker requise"
                    )
                    return False
                current_page = dict(data)
                display_events()
                return False

            def done(done_handle) -> None:
                from gi.repository import GLib
                try:
                    snapshot = done_handle.result()
                except Exception as exc:
                    GLib.idle_add(apply_result, generation, None, type(exc).__name__)
                else:
                    GLib.idle_add(apply_result, generation, snapshot, None)

            handle.add_done_callback(done)

        def reset_events(*_args) -> None:
            nonlocal page_offset
            page_offset = 0
            load_events()

        def previous_events(_button) -> None:
            nonlocal page_offset
            page_offset = max(0, page_offset - PAGE_SIZE)
            load_events()

        def next_events(_button) -> None:
            nonlocal page_offset
            next_offset = current_page.get("next_offset")
            if next_offset is None or int(next_offset) > 10000:
                return
            page_offset = int(next_offset)
            load_events()

        def copy_selected(_button) -> None:
            rows = sanitized_rows(
                current_page, search=search_filter.get_text().strip(),
                newest_first=sort_latest.get_active(),
            )
            visible_ids = {row["event_id"] for row in rows}
            selected = selected_event_ids.intersection(visible_ids)
            if not selected:
                host.toast("Sélectionner un événement visible")
                return
            display = Gdk.Display.get_default()
            if display is None:
                host.toast("Presse-papiers indisponible")
                return
            display.get_clipboard().set(export_selected_events(rows, selected))
            host.toast("Sélection expurgée copiée")

        journals_refresh.connect("clicked", reset_events)
        journals_previous.connect("clicked", previous_events)
        journals_next.connect("clicked", next_events)
        journals_copy.connect("clicked", copy_selected)
        source_selector.connect("notify::selected", reset_events)
        severity_selector.connect("notify::selected", reset_events)
        period_selector.connect("notify::selected", reset_events)
        module_filter.connect("activate", reset_events)
        search_filter.connect("changed", lambda *_: display_events())
        sort_latest.connect("toggled", lambda *_: display_events())
        journals_copy.set_sensitive(False)
        journals_previous.set_sensitive(False)
        journals_next.set_sensitive(False)

        def render(*_args) -> None:
            clear_container(status_grid)
            clear_container(diag_box)
            try:
                status = broker.system_status()
                if isinstance(status, Mapping):
                    for key in sorted(status, key=lambda item: str(item).casefold()):
                        add_status(str(key), status[key])
                else:
                    add_status("État", status)
                inventory = public_inventory(broker)
                for row in inventory:
                    plugin_id = str(row.get("plugin_id") or "Plugin")
                    version = str(row.get("version") or "—")
                    package_version = str(row.get("package_version") or "")
                    state = str(row.get("state") or "")
                    value = f"{version}"
                    if package_version and package_version != version:
                        value += f" · package {package_version}"
                    if state:
                        value += f" · {state}"
                    add_status(plugin_id, value)

                for row in relevant_runtime_diagnostics(broker, inventory):
                    add_diag({
                        "message": f"Runtime {row.get('plugin_id')}",
                        "status": "ok",
                        "plugin_id": row.get("plugin_id"),
                        "detail": {key: value for key, value in row.items() if key != "plugin_id"},
                    })

                snapshot = broker.snapshot()
                for row in snapshot:
                    if isinstance(row, Mapping):
                        add_diag(row)
                exporter = getattr(broker, "export_support_text", None)
                report = str((exporter() if callable(exporter) else broker.export_text()) or "")
                export_view.get_buffer().set_text(report)
            except Exception as exc:
                host.toast(f"Diagnostics indisponibles : {type(exc).__name__}")
                if self.context is not None:
                    self.context.report_diagnostic(
                        "Diagnostics page refresh failed",
                        "error",
                        detail={"error_type": type(exc).__name__},
                        level="normal",
                    )

        def render_checks(rows, error=None) -> bool:
            clear_container(check_box)
            check.set_sensitive(True)
            self._check_handle = None
            if error is not None:
                host.toast(f"Vérification impossible : {error}")
                return False
            for row in rows:
                card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
                card.add_css_class("card")
                for margin in ("top", "bottom", "start", "end"):
                    getattr(card, "set_margin_" + margin)(8)
                status = str(row.get("status") or "unknown")
                title = Gtk.Label(label=str(row.get("message") or "Vérification"), xalign=0)
                title.add_css_class("heading")
                card.append(title)
                result_state = Gtk.Label(label=_safe_text(status), xalign=0)
                result_state.set_selectable(True)
                card.append(result_state)
                if "detail" in row:
                    add_detail_view(card, row["detail"])
                check_box.append(card)
            host.toast("Vérification terminée")
            return False

        def run_checks(_button) -> None:
            if self._check_handle is not None and not self._check_handle.done():
                return
            clear_container(check_box)
            running = Gtk.Label(label="Vérification en cours…", xalign=0)
            running.set_wrap(True)
            check_box.append(running)
            check.set_sensitive(False)

            handle = self.tasks.submit(
                lambda _control: active_checks(broker),
                label="Vérifications diagnostics",
                kind="diagnostics",
            )
            self._check_handle = handle

            def completed(done_handle) -> None:
                from gi.repository import GLib
                if done_handle.control.cancelled():
                    GLib.idle_add(render_checks, (), "annulée")
                    return
                try:
                    rows = done_handle.result()
                except Exception as exc:
                    GLib.idle_add(render_checks, (), type(exc).__name__)
                else:
                    GLib.idle_add(render_checks, rows, None)

            handle.add_done_callback(completed)

        def copy_report(_button) -> None:
            try:
                exporter = getattr(broker, "export_support_text", None)
                text = str((exporter() if callable(exporter) else broker.export_text()) or "")
                display = Gdk.Display.get_default()
                if display is None:
                    raise RuntimeError("display indisponible")
                display.get_clipboard().set(text)
                host.toast("Diagnostic support copié")
            except Exception as exc:
                host.toast(f"Copie impossible : {type(exc).__name__}")

        refresh.connect("clicked", lambda *_: (render(), reset_events()))
        check.connect("clicked", run_checks)
        copy.connect("clicked", copy_report)
        def initial_load() -> None:
            render()
            load_events()

        _run_once_on_first_map(scroll, initial_load)
        return scroll

    def deactivate(self):
        if self._check_handle is not None:
            try:
                self._check_handle.cancel()
            except Exception:
                pass
        self._check_handle = None
        if getattr(self, "_event_handle", None) is not None:
            try:
                self._event_handle.cancel()
            except Exception:
                pass
        self._event_handle = None
        self.tasks = None
        self.diagnostics = None
        self.context = None


PLUGIN = DiagnosticsPagePlugin()
