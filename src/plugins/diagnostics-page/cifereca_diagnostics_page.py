from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any

from cifereca.plugin_api import UiPageContribution
from cifereca.plugin_api.version_inventory import version_inventory
from diagnostics_ai import llm_destination, prepare_analysis, analyze_with_consent, export_analysis_json
from diagnostics_explorer import (
    SOURCES, SEVERITIES, PERIODS, PAGE_SIZE, fetch_explorer_page,
    sanitized_rows, module_groups, linked_operation_groups, source_summaries, explorer_header,
    diagnostic_channel_caption,
    next_page_offset, selectable_event_ids, export_selected_events,
    empty_observation, local_timestamp_label, DOMAIN_LABELS,
    normalize_diagnostic_domain, connection_provenance_label,
)
from diagnostics_selection import collect_filtered_selection, page_selection_ids
from diagnostics_performance import PerformanceCapture, performance_bytes
from diagnostics_support import (
    SupportReportExport, diagnostic_json_bytes, preserve_report_selection,
)

# Only the GTK presentation pseudonymizes login and password fields.
# The journal, console, broker and raw exports retain their original values.
from diagnostics_pseudonyms import DISPLAY_PSEUDONYMS

def _readable_raw_export_folder() -> str | None:
    """Prefer a real, browsable local home directory over GTK's stale Recents.

    No journal is written here. Only the user-selected SAVE destination is
    ever opened; no directory name appears in a toast, log or broker call.
    """
    import os
    from pathlib import Path

    try:
        home = Path.home()
        if not home.is_absolute() or not home.is_dir():
            return None
        with os.scandir(home) as entries:
            next(entries, None)
        return str(home)
    except (OSError, ValueError):
        return None


_MAX_DETAIL_BYTES = 24_000
_MAX_DETAIL_NODES = 300
_MAX_DETAIL_DEPTH = 7
_MAX_DETAIL_CHILDREN = 50
_MAX_DETAIL_TEXT = 800


def _redacted_text(value: str, *, max_chars: int = _MAX_DETAIL_TEXT,
                   max_input_chars: int = _MAX_DETAIL_BYTES) -> str:
    """Render only explicit login/password values as stable session pseudonyms."""
    if type(value) is not str:
        raise TypeError("invalid diagnostics text")
    text = DISPLAY_PSEUDONYMS.text(value)
    if len(text) > min(max_chars, max_input_chars):
        return text[:min(max_chars, max_input_chars)] + "\n… [tronqué]"
    return text


def redacted_ai_response(value: str) -> str:
    """Preserve a bounded multi-paragraph LLM answer without relaxing UI caps.

    Redact the *entire* response before cutting it, so long tokens or signed
    URLs cannot leak a surviving prefix. Invalid Unicode is never exported.
    """
    if type(value) is not str:
        raise ValueError("invalid_llm_response")
    try:
        value.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("invalid_llm_response_unicode") from exc
    return _redacted_text(value, max_chars=3960)


def redacted_detail(
    value: Any, *, max_text: int = _MAX_DETAIL_TEXT,
) -> Any:
    if type(max_text) is not int or not 1 <= max_text <= 4000:
        raise ValueError("invalid_diagnostics_text_budget")
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
            return _redacted_text(item, max_chars=max_text)
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
                    # Preserve the whole key until AFTER redaction. Cutting
                    # before masking can expose partial emails or signed URLs.
                    name = str(key)
                    pairs.append((name, child))
                pairs.sort(key=lambda pair: pair[0].casefold())
                result = {}
                for name, child in pairs[:_MAX_DETAIL_CHILDREN]:
                    safe_name = _redacted_text(name)[:160]
                    # Redaction/truncation may collapse distinct original keys.
                    # Preserve every entry without revealing original names.
                    if safe_name in result:
                        base_name = safe_name
                        suffix = 2
                        while safe_name in result:
                            marker = f" [{suffix}]"
                            safe_name = base_name[:160 - len(marker)] + marker
                            suffix += 1
                    # Explicit login/password fields are pseudonymized
                    # only here. Paths, URLs, tokens, titles, authors, IDs and
                    # other data are displayed as emitted.
                    visible = DISPLAY_PSEUDONYMS.value(name, child)
                    result[safe_name] = walk(visible, depth + 1)
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
    try:
        encoded = text.encode("utf-8")
    except UnicodeError:
        notice = "Contenu Unicode invalide : affichage masqué"
        return (json.dumps({"notice": notice, "truncated": True},
                           ensure_ascii=False) if mode == "json" else notice)
    if len(encoded) > _MAX_DETAIL_BYTES:
        excerpt = encoded[:(
            _MAX_DETAIL_BYTES // 2 if mode == "json" else _MAX_DETAIL_BYTES - 100
        )].decode("utf-8", errors="ignore")
        if mode == "json":
            def preview_json(value: str) -> str:
                return json.dumps({
                    "notice": "Aperçu JSON limité à 24 Kio",
                    "truncated": True, "preview": value,
                }, ensure_ascii=False, indent=2)
            result = preview_json(excerpt)
            while len(result.encode("utf-8")) > _MAX_DETAIL_BYTES:
                excerpt = excerpt[:len(excerpt) // 2]
                result = preview_json(excerpt)
            return result
        return excerpt + "\n… [affichage limité]"
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


def _install_report_escape(Gtk, Gdk, container, scroller, resize):
    """Reduce only this report; preserve its buffer, selection and focus."""
    controller = Gtk.EventControllerKey()
    # Catch Escape before a focused TextView consumes it. The controller is
    # scoped to the report, so other Diagnostics controls keep their bindings.
    controller.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def key_pressed(_controller, keyval, _keycode, state):
        modified = state & (
            Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK
            | Gdk.ModifierType.SUPER_MASK | Gdk.ModifierType.META_MASK
        )
        if keyval != Gdk.KEY_Escape or modified:
            return False
        if resize.get_label() != "Réduire":
            return False
        scroller.set_size_request(-1, 320)
        resize.set_label("Agrandir")
        return True

    controller.connect("key-pressed", key_pressed)
    container.add_controller(controller)


def _scroll_report_adjustment(adjustment, delta: float, unit, Gdk) -> bool:
    """Move only an inner GTK adjustment, without scrolling its parent.

    Return False at an edge so GTK can chain the wheel to the page normally.
    Match GTK4's wheel/page-size and surface scroll factors.
    """
    if not math.isfinite(delta) or not delta:
        return False
    page = adjustment.get_page_size()
    lower = adjustment.get_lower()
    upper = max(lower, adjustment.get_upper() - page)
    value = adjustment.get_value()
    factor = max(0.0, page) ** (2.0 / 3.0) if unit == Gdk.ScrollUnit.WHEEL else 2.5
    new_value = min(upper, max(lower, value + delta * factor))
    if abs(new_value - value) < 0.0001:
        return False
    adjustment.set_value(new_value)
    return True


def _install_report_wheel(Gtk, Gdk, GLib, root, outer_scroll, report_scroll, report_expander):
    """Keep physical wheel events inside the hovered report viewport.

    A scroller-local GTK motion controller knows whether its subtree is under
    the pointer, without guessing X11/surface coordinates from scroll events.
    The root captures scroll before the GTK ancestor scroller can consume it.
    At the report edge, propagation to the page remains normal.
    """
    tracking = Gtk.EventControllerMotion()
    tracking.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    report_scroll.add_controller(tracking)

    # GTK4 may move the ancestor adjustment even after a nested wheel
    # handler returns TRUE. Keep the parent fixed during an actual inner
    # wheel gesture, without disabling normal scroll or keyboard navigation.
    page_adjustment = outer_scroll.get_vadjustment()
    guard = {"anchor": None, "restoring": False, "serial": 0}

    def release_guard():
        guard["serial"] += 1
        guard["anchor"] = None

    def hold_parent():
        if guard["anchor"] is None:
            guard["anchor"] = page_adjustment.get_value()
        guard["serial"] += 1
        token = guard["serial"]

        def release_later():
            if guard["serial"] == token:
                guard["anchor"] = None
            return False

        GLib.timeout_add(350, release_later)

    def keep_page_fixed(adjustment):
        if guard["anchor"] is None or guard["restoring"]:
            return
        limit = max(adjustment.get_lower(),
                    adjustment.get_upper() - adjustment.get_page_size())
        anchor = max(adjustment.get_lower(), min(limit, guard["anchor"]))
        if abs(adjustment.get_value() - anchor) <= 0.001:
            return
        guard["restoring"] = True
        try:
            adjustment.set_value(anchor)
        finally:
            guard["restoring"] = False

    page_adjustment.connect("value-changed", keep_page_fixed)

    wheel = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
    wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)

    def over_report():
        # GTK tracks pointer crossing for the report and *all its children*.
        # Unlike a cached root position, this also survives scrolling,
        # resizing, text selection and a relocated XTEST pointer.
        return tracking.contains_pointer()

    def on_wheel(controller, dx, dy):
        if not report_expander.get_expanded() or not report_scroll.get_mapped():
            release_guard()
            return False
        if not over_report():
            release_guard()
            return False
        if controller.get_current_event_state() & Gdk.ModifierType.SHIFT_MASK:
            dx, dy = dy, dx
        unit = controller.get_unit()
        # Arm before the inner adjustment changes, in case Gtk tries to
        # propagate the same physical wheel event to the outer viewport.
        hold_parent()
        moved = False
        if dy:
            moved = _scroll_report_adjustment(
                report_scroll.get_vadjustment(), dy, unit, Gdk,
            )
        if dx:
            moved = _scroll_report_adjustment(
                report_scroll.get_hadjustment(), dx, unit, Gdk,
            ) or moved
        if not moved:
            # GTK4's nested ScrolledWindow may consume a physical wheel even
            # when our root capture controller returns False. An unhandled
            # scroll event is therefore NOT proof that it reaches the parent.
            # Release the protection before routing the same gesture to the
            # outer adjustment. Return True if the parent moved, preventing
            # GTK from processing the event a second time.
            release_guard()
            if dy:
                moved = _scroll_report_adjustment(
                    page_adjustment, dy, unit, Gdk,
                )
            if dx:
                moved = _scroll_report_adjustment(
                    outer_scroll.get_hadjustment(), dx, unit, Gdk,
                ) or moved
        return moved

    wheel.connect("scroll", on_wheel)
    root.add_controller(wheel)


class DiagnosticsPagePlugin:
    def __init__(self) -> None:
        self.context = None
        self.diagnostics = None
        self.tasks = None
        self._check_handle = None
        self._event_handle = None
        self._ai_handle = None
        self._selection_handle = None
        self.llm = None

    def activate(self, context):
        self.context = context
        self.diagnostics = context.broker("diagnostics")
        self.tasks = context.broker("tasks")
        service = getattr(context, "service", None)
        self.llm = service("llm") if callable(service) else None
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
        from gi.repository import Gdk, Gtk, Pango, Gio, GLib

        broker = self.diagnostics
        if broker is None:
            raise RuntimeError("plugin Diagnostics non activé")
        journal_available = callable(getattr(broker, "export_raw_journal", None))

        # Rebuilding a page revokes file actions belonging to its old widgets.
        # The shared Core capture itself deliberately remains untouched.
        # Native dialogs outlive the GTK page unless explicitly dismissed.
        previous_raw_chooser = getattr(self, "_raw_export_chooser", None)
        if previous_raw_chooser is not None:
            previous_raw_chooser.destroy()
        self._raw_export_chooser = None
        previous_daily_export = getattr(self, "_daily_journal_export", None)
        if previous_daily_export is not None:
            previous_daily_export.close()
        self._daily_journal_export = None
        previous_http_export = getattr(self, "_http_trace_export", None)
        if previous_http_export is not None:
            previous_http_export.close()
        self._http_trace_export = None
        for exporter in getattr(self, "_support_exports", ()):
            exporter.close()
        self._support_exports = []
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for margin in ("top", "bottom", "start", "end"):
            getattr(outer, "set_margin_" + margin)(18)
        scroll.set_child(outer)
        # A parent capture controller must run before GTK4's outer scroller
        # when a pointer wheel is inside the long support report.
        page_root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._page_root = page_root
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        page_root.append(scroll)

        sections = Gtk.Stack()
        sections.set_name("diagnostics-sections")
        sections.set_hhomogeneous(False)
        sections.set_vhomogeneous(False)
        journals_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        capture_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        system_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        reports_tab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for key, title, content in (
            ("journals", "Journaux", journals_tab),
            ("capture", "Capture", capture_tab),
            ("system", "État système", system_tab),
            ("reports", "Rapports et vérifications", reports_tab),
        ):
            sections.add_titled(content, key, title)
        sections.set_visible_child_name("journals")
        switcher = Gtk.StackSwitcher()
        switcher.set_stack(sections)
        page_root.prepend(switcher)

        intro = Gtk.Label(
            label="État technique expurgé fourni par le Core. Les plugins ne lisent ni fichiers internes, ni cookies, ni secrets."
        )
        intro.set_wrap(True)
        intro.set_xalign(0)
        system_tab.append(intro)

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
        save_report = Gtk.Button(label="Enregistrer le rapport support…")
        save_report.set_tooltip_text("Exporter le rapport support expurgé vers un fichier choisi")
        action_row.append(save_report)
        outer.append(action_row)
        outer.append(sections)

        status_title = Gtk.Label(label="État système", xalign=0)
        status_title.add_css_class("heading")
        system_tab.append(status_title)
        versions_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        versions_core = Gtk.Label(label="Core Cifereca · version inconnue", xalign=0)
        versions_core.set_hexpand(True)
        versions_core.set_wrap(True)
        versions_core.add_css_class("heading")
        versions_copy = Gtk.Button(label="Copier les versions")
        versions_copy.set_tooltip_text("Copier uniquement les versions et états des composants affichés")
        versions_copy.set_sensitive(False)
        versions_header.append(versions_core)
        versions_header.append(versions_copy)
        system_tab.append(versions_header)
        versions_count = Gtk.Label(label="Inventaire local des plugins", xalign=0)
        versions_count.set_wrap(True)
        system_tab.append(versions_count)
        versions_scroll = Gtk.ScrolledWindow()
        versions_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        versions_scroll.set_size_request(-1, 230)
        versions_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        versions_scroll.set_child(versions_list)
        system_tab.append(versions_scroll)
        status_grid = Gtk.FlowBox()
        status_grid.set_selection_mode(Gtk.SelectionMode.NONE)
        status_grid.set_homogeneous(True)
        status_grid.set_min_children_per_line(1)
        status_grid.set_max_children_per_line(3)
        status_grid.set_column_spacing(10)
        status_grid.set_row_spacing(8)
        system_tab.append(status_grid)


        # Journals remain inside the existing Diagnostics page. Every refresh
        # is an asynchronous call to the read-only public Diagnostics broker.
        journals_title = Gtk.Label(label=("Journal des événements" if journal_available
                                               else "Journaux par composant"), xalign=0)
        journals_title.add_css_class("heading")
        journals_tab.append(journals_title)
        journals_hint = Gtk.Label(
            label="Recherche sur tout le journal local conservé, rotations comprises. "
                  "Les données brutes peuvent contenir des informations sensibles. "
                  "Seuls login et mot de passe sont pseudonymisés à l’écran.",
            xalign=0,
        )
        journals_hint.set_wrap(True)
        journals_tab.append(journals_hint)

        search_filter = Gtk.Entry()
        search_filter.set_placeholder_text(
            "Rechercher dans tous les événements" if journal_available
            else "Rechercher dans cette page"
        )
        search_filter.set_hexpand(True)
        search_filter.set_tooltip_text(
            "Recherche côté Core sur toutes les rotations avant pagination ; Entrée pour actualiser"
        )
        if journal_available:
            journals_tab.append(search_filter)

        advanced_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        filters_top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        source_labels = ["Toutes les sources (ancien)", "Événements Core", "Journal applicatif"]
        if journal_available:
            source_labels.append("Journal unifié")
        source_selector = Gtk.DropDown.new_from_strings(source_labels)
        source_selector.set_selected(3 if journal_available else 0)
        source_selector.set_tooltip_text(
            "Journal canonique ; anciennes sources accessibles en mode avancé"
            if journal_available else "Source des événements"
        )
        severity_selector = Gtk.DropDown.new_from_strings(
            ["Tous niveaux", "Debug", "Info", "Avertissements", "Erreurs", "Critiques"]
        )
        severity_selector.set_tooltip_text("Niveau de gravité")
        period_selector = Gtk.DropDown.new_from_strings(
            ["Toutes périodes", "Dernière heure", "24 heures", "7 jours", "30 jours"]
        )
        period_selector.set_selected(0 if journal_available else 2)
        period_selector.set_tooltip_text("Période de la consultation")
        if not journal_available:
            advanced_box.append(source_selector)
        filters_top.append(severity_selector)
        filters_top.append(period_selector)
        journals_tab.append(filters_top)

        filters_bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        module_filter = Gtk.Entry()
        module_filter.set_placeholder_text("Préfixe module (Entrée pour appliquer)")
        module_filter.set_hexpand(True)
        module_filter.set_tooltip_text("Filtre serveur sur le préfixe de module")
        sort_latest = Gtk.CheckButton(label="Plus récents d’abord")
        sort_latest.set_active(True)
        filters_bottom.append(module_filter)
        if not journal_available:
            # Preserve the existing GTK keyboard focus order for legacy brokers.
            filters_bottom.append(search_filter)
        filters_bottom.append(sort_latest)
        advanced_box.append(filters_bottom)

        # Alias UI -> domaine Core attesté, sans recherche dans les journaux
        # privés ou les messages libres de plugins.
        domain_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        domain_caption = Gtk.Label(label="Domaine métier :", xalign=0)
        domain_filter = Gtk.Entry()
        domain_filter.set_width_chars(18)
        domain_filter.set_max_width_chars(26)
        domain_filter.set_hexpand(True)
        domain_filter.set_placeholder_text("Browser / Réconciliation / Download")
        domain_filter.set_tooltip_text(
            "Filtre global : Browser, recorder, Réconciliation, Reconciliation, "
            "Réconcilitation, Download ou Téléchargements de prêts. "
            "Vide : tous domaines ; seules les observations Core attestées sont retenues."
        )
        domain_row.append(domain_caption)
        domain_row.append(domain_filter)
        if not journal_available:
            advanced_box.append(domain_row)

        # Separate from the module prefix and from the event-source channel.
        # The Core filter is exact and runs before pagination.
        provider_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        provider_caption = Gtk.Label(label="Fournisseur :", xalign=0)
        provider_filter = Gtk.Entry()
        provider_filter.set_width_chars(16)
        provider_filter.set_max_width_chars(28)
        provider_filter.set_hexpand(True)
        provider_filter.set_placeholder_text("Identifiant canonique (ex. paris) · Entrée")
        provider_filter.set_tooltip_text(
            "Filtre Core exact sur le provider attesté ; ne pas utiliser un compte ou un email."
        )
        provider_row.append(provider_caption)
        provider_row.append(provider_filter)
        advanced_box.append(provider_row)

        # This is a SERVER-side exact Core reason-code filter, unlike the
        # search field above which only scans the 50 loaded observations.
        # A separate row keeps controls usable on 760/1024px desktops.
        reason_row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        reason_caption = Gtk.Label(label="Limite de réconciliation :", xalign=0)
        reason_code_filter = Gtk.Entry()
        # The GTK Entry's long placeholder must not increase the minimum
        # top-level width. Keep a narrow natural request and expand into the
        # available space; the layout is stacked at 760px and larger.
        reason_code_filter.set_width_chars(18)
        reason_code_filter.set_max_width_chars(32)
        reason_code_filter.set_placeholder_text(
            "Code exact · toutes les pages (Entrée pour appliquer)"
        )
        reason_code_filter.set_tooltip_text(
            "Filtre Core sur les causes de réconciliation, avant pagination. "
            "Un Core ancien peut ne pas prendre en charge ce filtre."
        )
        reason_code_filter.set_hexpand(True)
        reason_row.append(reason_caption)
        reason_row.append(reason_code_filter)
        if not journal_available:
            advanced_box.append(reason_row)

        operation_grouping = Gtk.CheckButton(label="Regrouper par tâche liée (cette page)")
        operation_grouping.set_tooltip_text(
            "Uniquement les références Core publiées ; étapes parfois absentes "
            "avec les filtres, la pagination ou la rotation des journaux. "
            "Les résultats métier et worker restent distincts."
        )
        advanced_box.append(operation_grouping)
        advanced_filters = Gtk.Expander(label="Filtres avancés")
        advanced_filters.set_expanded(not journal_available)
        advanced_filters.set_child(advanced_box)
        journals_tab.append(advanced_filters)

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
        journals_tab.append(journals_actions)
        journals_today_export = Gtk.Button(
            label="Exporter le journal brut du jour (ZIP)…"
        )
        journals_tab.append(journals_today_export)
        from diagnostics_today_export import DailyJournalExport
        self._daily_journal_export = DailyJournalExport(
            journals_today_export, broker=broker, tasks=self.tasks, host=host,
            current=lambda: self._page_root is page_root and self.diagnostics is broker,
            folder=_readable_raw_export_folder,
        )
        # The historical all-retention JSON export remains available as an
        # explicitly named advanced action, never confused with today's ZIP.
        journals_raw_export = Gtk.Button(label="Exporter tout l’historique (JSON)…")
        journals_raw_export.set_tooltip_text(
            "Historique complet de la rétention, sans filtre ni masquage. "
            "Peut contenir mots de passe, cookies, tokens et URLs privées."
        )
        advanced_box.append(journals_raw_export)
        journals_http_export = Gtk.Button(label="Exporter les échanges HTTP…")
        journals_tab.append(journals_http_export)
        from diagnostics_http_export import HTTPTraceExport
        self._http_trace_export = HTTPTraceExport(
            journals_http_export, broker=broker, tasks=self.tasks, host=host,
            current=lambda: self._page_root is page_root and self.diagnostics is broker,
            folder=_readable_raw_export_folder,
        )

        # FlowBox wraps four actions even in narrow 760px windows.
        journals_select_actions = Gtk.FlowBox()
        journals_select_actions.set_selection_mode(Gtk.SelectionMode.NONE)
        journals_select_actions.set_min_children_per_line(1)
        journals_select_actions.set_max_children_per_line(4)
        journals_select_actions.set_column_spacing(6)
        journals_select_actions.set_row_spacing(6)
        journals_select_errors = Gtk.Button(label="Erreurs + critiques (filtres)")
        journals_select_all = Gtk.Button(label="Tout sélectionner (filtres)")
        journals_invert = Gtk.Button(label="Inverser cette page")
        journals_clear = Gtk.Button(label="Effacer la sélection")
        for button in (journals_select_errors, journals_select_all,
                       journals_invert, journals_clear):
            journals_select_actions.insert(button, -1)
        journals_select_errors.set_tooltip_text(
            "Erreurs/critiques sur toutes les pages : source, module, période "
            "et recherche texte ; remplace uniquement le filtre de niveau"
        )
        journals_select_all.set_tooltip_text(
            "Toutes les pages du périmètre filtré, dans les limites sûres d'export"
        )
        journals_tab.append(journals_select_actions)
        journals_selection = Gtk.Label(label="0 sélectionné · portée : cette page", xalign=0)
        journals_selection.set_wrap(True)
        journals_selection.add_css_class("dim-label")
        journals_tab.append(journals_selection)
        journals_preview = Gtk.Expander(label="Aperçu des événements expurgés sélectionnés")
        journals_preview_scroll = Gtk.ScrolledWindow()
        journals_preview_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        journals_preview_scroll.set_size_request(-1, 145)
        journals_preview_text = Gtk.TextView()
        journals_preview_text.set_editable(False)
        journals_preview_text.set_monospace(True)
        journals_preview_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        journals_preview_scroll.set_child(journals_preview_text)
        journals_preview.set_child(journals_preview_scroll)
        journals_tab.append(journals_preview)
        journals_limits = Gtk.Label(
            label="Page : 50 événements / 24 Ko. Sélection filtrée multi-page : "
                  "200 événements / 96 Ko maximum, 8 pages par niveau. "
                  "Consultation non atomique, sources parfois partielles ; "
                  "champs expurgés et résumés à 500 caractères.", xalign=0,
        )
        journals_limits.set_wrap(True)
        journals_limits.add_css_class("dim-label")
        journals_tab.append(journals_limits)

        journals_sources = Gtk.Label(label="Canaux de diagnostic : actualisation requise", xalign=0)
        journals_sources.set_wrap(True)
        journals_sources.add_css_class("dim-label")
        journals_tab.append(journals_sources)
        journals_summary = Gtk.Label(label="Actualisation requise", xalign=0)
        journals_summary.set_wrap(True)
        journals_tab.append(journals_summary)
        journals_scroll = Gtk.ScrolledWindow()
        journals_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        journals_scroll.set_min_content_height(200)
        journals_scroll.set_size_request(-1, 350)
        journals_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        journals_scroll.set_child(journals_box)
        journals_tab.append(journals_scroll)

        ai_title = Gtk.Label(label="Analyse IA (facultative)", xalign=0)
        ai_title.add_css_class("heading")
        reports_tab.append(ai_title)
        ai_notice = Gtk.Label(
            label="Aucune donnée transmise par défaut. Prévisualiser les événements expurgés, "
                  "vérifier la destination, puis autoriser chaque analyse explicitement.",
            xalign=0,
        )
        ai_notice.set_wrap(True)
        reports_tab.append(ai_notice)
        ai_snapshot = Gtk.CheckButton(
            label="Inclure les diagnostics structurés Core (lecture seule)"
        )
        ai_snapshot.set_active(False)
        reports_tab.append(ai_snapshot)
        ai_support_report = Gtk.CheckButton(
            label="Inclure le rapport support Core (lecture seule)"
        )
        ai_support_report.set_active(False)
        reports_tab.append(ai_support_report)
        manual_log_path = None  # Only from explicit Gtk.FileChooserNative acceptance.
        manual_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        manual_choose = Gtk.Button(label="Choisir un journal local…")
        manual_clear = Gtk.Button(label="Retirer le journal")
        manual_clear.set_sensitive(False)
        manual_state = Gtk.Label(
            label="Aucun journal sélectionné · aucune lecture automatique", xalign=0,
        )
        manual_state.set_wrap(True)
        manual_row.append(manual_choose)
        manual_row.append(manual_clear)
        reports_tab.append(manual_row)
        reports_tab.append(manual_state)
        # Do not impose five button minima on the journal-selection page.
        ai_controls = Gtk.FlowBox()
        ai_controls.set_selection_mode(Gtk.SelectionMode.NONE)
        ai_controls.set_min_children_per_line(1)
        ai_controls.set_max_children_per_line(5)
        ai_controls.set_column_spacing(8)
        ai_controls.set_row_spacing(8)
        ai_prepare = Gtk.Button(label="Prévisualiser pour l’IA")
        ai_send = Gtk.Button(label="Autoriser et analyser")
        ai_send.set_sensitive(False)
        ai_cancel = Gtk.Button(label="Annuler")
        ai_export = Gtk.Button(label="Copier Markdown IA")
        ai_export.set_sensitive(False)
        ai_export_json = Gtk.Button(label="Copier JSON IA")
        ai_export_json.set_sensitive(False)
        ai_controls.insert(ai_prepare, -1)
        ai_controls.insert(ai_send, -1)
        ai_controls.insert(ai_cancel, -1)
        ai_controls.insert(ai_export, -1)
        ai_controls.insert(ai_export_json, -1)
        reports_tab.append(ai_controls)
        ai_status = Gtk.Label(label="IA non configurée ou non consultée", xalign=0)
        ai_status.set_wrap(True)
        reports_tab.append(ai_status)
        ai_expander = Gtk.Expander(label="Données expurgées proposées à l’IA")
        ai_expander.set_expanded(False)
        ai_scroller = Gtk.ScrolledWindow()
        ai_scroller.set_min_content_height(150)
        ai_scroller.set_size_request(-1, 220)
        ai_input = Gtk.TextView()
        ai_input.set_editable(False)
        ai_input.set_monospace(True)
        ai_input.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        ai_scroller.set_child(ai_input)
        ai_expander.set_child(ai_scroller)
        reports_tab.append(ai_expander)
        ai_report = Gtk.TextView()
        ai_report.set_editable(False)
        ai_report.set_monospace(True)
        ai_report.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        ai_report_scroll = Gtk.ScrolledWindow()
        ai_report_scroll.set_min_content_height(160)
        ai_report_scroll.set_size_request(-1, 280)
        ai_report_scroll.set_child(ai_report)
        reports_tab.append(ai_report_scroll)

        diag_title = Gtk.Label(label="Diagnostics", xalign=0)
        diag_title.add_css_class("heading")
        reports_tab.append(diag_title)
        diag_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        diag_expander = Gtk.Expander(label="Afficher les détails techniques")
        diag_expander.set_expanded(False)
        diag_expander.set_child(diag_box)
        reports_tab.append(diag_expander)

        check_title = Gtk.Label(label="Vérification active", xalign=0)
        check_title.add_css_class("heading")
        reports_tab.append(check_title)
        check_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        reports_tab.append(check_box)
        check_hint = Gtk.Label(label="Lancez la vérification pour contrôler les connexions connues et runtimes sans synchroniser ni télécharger.", xalign=0)
        check_hint.set_wrap(True)
        check_box.append(check_hint)

        export_title = Gtk.Label(label="Rapport support (copie expurgée)", xalign=0)
        export_title.add_css_class("heading")
        reports_tab.append(export_title)
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
        reports_tab.append(report_expander)

        capture = PerformanceCapture(host)
        capture_note = Gtk.Label(
            label="Capture locale volontaire. Elle continue pendant la navigation et "
                  "s’arrête à la fermeture de l’application. Aucun envoi automatique.",
            xalign=0,
        )
        capture_note.set_wrap(True)
        capture_tab.append(capture_note)
        capture_status = Gtk.Label(label="Capture non consultée", xalign=0)
        capture_status.set_wrap(True)
        capture_tab.append(capture_status)
        capture_actions = Gtk.FlowBox()
        capture_actions.set_selection_mode(Gtk.SelectionMode.NONE)
        capture_actions.set_min_children_per_line(1)
        capture_actions.set_max_children_per_line(4)
        capture_start = Gtk.Button(label="Démarrer la capture")
        capture_stop = Gtk.Button(label="Arrêter la capture")
        capture_refresh = Gtk.Button(label="Actualiser les mesures")
        capture_save = Gtk.Button(label="Enregistrer les mesures JSON…")
        for button in (capture_start, capture_stop, capture_refresh, capture_save):
            capture_actions.insert(button, -1)
        capture_tab.append(capture_actions)
        capture_view = Gtk.TextView()
        capture_view.set_editable(False)
        capture_view.set_monospace(True)
        capture_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        capture_scroll = Gtk.ScrolledWindow()
        capture_scroll.set_min_content_height(240)
        capture_scroll.set_child(capture_view)
        capture_tab.append(capture_scroll)

        def render_capture(report=None):
            try:
                report = capture.read() if report is None else report
                data = performance_bytes(report)
            except Exception:
                capture_status.set_text("Capture indisponible avec ce Core ou cette page périmée")
                capture_view.get_buffer().set_text("")
                for button in (capture_start, capture_stop, capture_save):
                    button.set_sensitive(False)
                return
            enabled = report["detailed_capture_enabled"]
            capture_status.set_text("Capture en cours · locale" if enabled
                                    else "Capture arrêtée · mesures locales")
            capture_start.set_sensitive(not enabled)
            capture_stop.set_sensitive(enabled)
            capture_save.set_sensitive(True)
            capture_view.get_buffer().set_text(data.decode("utf-8"))

        def change_capture(enabled):
            try:
                report = capture.set_enabled(enabled)
            except Exception:
                host.toast("Modification de capture indisponible")
                render_capture()
            else:
                render_capture(report)

        capture_export = SupportReportExport(
            Gtk, Gio, capture_save, scroll.get_root, capture.read, host.toast,
            filename="cifereca-performance.json", title="Enregistrer les mesures locales",
            success_message="Mesures JSON enregistrées", prepare_bytes=performance_bytes,
        )
        if not hasattr(self, "_support_exports"):
            self._support_exports = []
        self._support_exports.append(capture_export)
        capture_save.connect("clicked", capture_export.choose)
        capture_start.connect("clicked", lambda *_: change_capture(True))
        capture_stop.connect("clicked", lambda *_: change_capture(False))
        capture_refresh.connect("clicked", lambda *_: render_capture())

        def section_changed(*_args):
            scroll.get_vadjustment().set_value(0)
            if sections.get_visible_child_name() == "capture":
                render_capture()

        sections.connect("notify::visible-child-name", section_changed)

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
        _install_report_escape(
            Gtk, Gdk, report_container, report_scroll, report_resize,
        )
        _install_report_wheel(
            Gtk, Gdk, GLib, page_root, scroll, report_scroll, report_expander,
        )
        preserve_report_selection(GLib, report_expander, export_view.get_buffer())

        def report_text():
            buffer = export_view.get_buffer()
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        report_export = SupportReportExport(
            Gtk, Gio, save_report, scroll.get_root, report_text, host.toast,
        )
        if not hasattr(self, "_support_exports"):
            self._support_exports = []
        self._support_exports.append(report_export)
        save_report.connect("clicked", report_export.choose)

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
            save_json = Gtk.Button(label="Enregistrer JSON…")
            save_json.set_tooltip_text("Exporter ce détail en JSON indenté expurgé vers un fichier local")
            controls.append(label)
            controls.append(selector)
            controls.append(copy_detail)
            controls.append(save_json)
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

            # Export always uses the same frozen and already-expurgated
            # snapshot as the three detail views, even when the selector
            # currently shows Lisible or Brut. No new Core broker call.
            json_export = SupportReportExport(
                Gtk, Gio, save_json, scroll.get_root, lambda: safe, host.toast,
                filename="cifereca-diagnostic.json",
                title="Enregistrer le détail JSON expurgé",
                success_message="Détail JSON expurgé enregistré",
                prepare_bytes=diagnostic_json_bytes,
            )
            self._support_exports.append(json_export)

            def on_detail_unmap(*_args):
                # A tab change unmaps a live detail too. Check attachment on
                # idle, after GTK finishes an actual pagination/removal, so
                # returning to a tab does not permanently disable its export.
                def release_if_detached():
                    if (getattr(self, "_page_root", None) is page_root
                            and expander.is_ancestor(page_root)):
                        return False
                    json_export.close()
                    if json_export in self._support_exports:
                        self._support_exports.remove(json_export)
                    return False
                GLib.idle_add(release_if_detached)

            expander.connect("unmap", on_detail_unmap)
            selector.connect("notify::selected", change_view)
            copy_detail.connect("clicked", copy_view)
            save_json.connect("clicked", json_export.choose)
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
        selection_json: str | None = None
        selection_note = ""
        selection_generation = 0
        selection_busy = False

        def update_selected_status() -> None:
            if selection_busy:
                journals_selection.set_text("Sélection filtrée multi-page en cours…")
            elif selection_json is not None:
                journals_selection.set_text(
                    f"{len(selected_event_ids)} sélectionné(s) · portée : "
                    f"ensemble filtré consultable (multi-page) · {selection_note}"
                )
            else:
                journals_selection.set_text(
                    f"{len(selected_event_ids)} sélectionné(s) · portée : page courante"
                    + (f" · {selection_note}" if selection_note else "")
                )
            journals_copy.set_sensitive(bool(selected_event_ids) and not selection_busy)
            journals_select_errors.set_sensitive(not selection_busy)
            journals_select_all.set_sensitive(not selection_busy)
            journals_invert.set_sensitive(not selection_busy)
            # Page-only selections also get a live, already-redacted preview.
            # This remains local to the loaded page (no broker I/O).
            preview = selection_json or ""
            if selection_json is None and selected_event_ids:
                page_rows = sanitized_rows(
                    current_page, search=search_filter.get_text().strip(),
                    newest_first=sort_latest.get_active(),
                )
                visible_ids = selectable_event_ids(page_rows)
                try:
                    preview = export_selected_events(
                        page_rows, selected_event_ids.intersection(visible_ids),
                    )
                except ValueError:
                    preview = "Export trop volumineux : réduire la sélection"
            journals_preview_text.get_buffer().set_text(preview)

        def invalidate_selection(message="Sélection effacée après changement de filtres") -> None:
            nonlocal selection_json, selection_note, selection_generation, selection_busy
            selection_generation += 1
            if self._selection_handle is not None and not self._selection_handle.done():
                self._selection_handle.cancel()
            selection_busy = False
            selected_event_ids.clear()
            selection_json = None
            selection_note = message
            update_selected_status()

        def display_events() -> None:
            clear_container(journals_box)
            journal_mode = int(source_selector.get_selected()) == 3
            rows = sanitized_rows(
                current_page, search="" if journal_mode else search_filter.get_text().strip(),
                newest_first=sort_latest.get_active(),
                include_detail=True,
            )
            valid_ids = selectable_event_ids(rows)
            if selection_json is None:
                selected_event_ids.intersection_update(valid_ids)
            journals_sources.set_text(diagnostic_channel_caption(current_page))
            domain_label = "Domaine non reconnu"
            try:
                domain_label = DOMAIN_LABELS[
                    normalize_diagnostic_domain(domain_filter.get_text())
                ]
            except ValueError:
                pass
            journals_summary.set_text(explorer_header(
                current_page, rows, search="" if journal_mode else search_filter.get_text().strip()
            ) + " · Domaine : " + domain_label)
            groups = (linked_operation_groups(rows) if operation_grouping.get_active()
                      else module_groups(rows))
            for group in groups:
                title = (f"{group['label']} · {group['count']} observation(s) · page partielle"
                         if "label" in group else (
                    f"{group['module']} · {group['source']} · "
                    f"{group['count']} événement(s) · {group['severity']}"
                ))
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
                    select.set_sensitive(event_id in valid_ids)
                    select.set_active(event_id in valid_ids and event_id in selected_event_ids)

                    def on_select(button, event_ref=event_id):
                        nonlocal selection_json, selection_note
                        if event_ref not in valid_ids or selection_busy:
                            return
                        replace_batch = selection_json is not None
                        if replace_batch:
                            # One page edit replaces, but cannot mutate, the
                            # frozen multi-page selection.
                            selected_event_ids.clear()
                            selection_json = None
                            selection_note = "Sélection multi-page remplacée par la page courante"
                        if button.get_active():
                            selected_event_ids.add(event_ref)
                        else:
                            selected_event_ids.discard(event_ref)
                        update_selected_status()
                        if replace_batch:
                            # Only a batch-to-page switch requires refreshing
                            # other checkboxes. Ordinary toggles must not
                            # unparent the GTK row receiving the click.
                            display_events()

                    select.connect("toggled", on_select)
                    first_line.append(select)
                    summary = _safe_text(
                        f"{local_timestamp_label(row['timestamp'])} · {row['severity']} · {row['message']}"
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
                            + (f"· Fournisseur : {row['provider_id']} "
                               if row.get("provider_id") else "")
                            + f"· Référence : {event_id[:16]}"
                            + connection_provenance_label(row)
                        ),
                        xalign=0,
                    )
                    provenance.add_css_class("dim-label")
                    provenance.set_wrap(True)
                    provenance.set_selectable(True)
                    item.append(provenance)
                    # A closed Core terminal event gets a readable local
                    # coverage/phase summary. Never draw raw provider or
                    # private audit fields in this expedited explanation.
                    reconciliation = row.get("reconciliation")
                    if type(reconciliation) is dict:
                        content = (
                            f"{reconciliation['state']} · Code : {reconciliation['code']}\n"
                            f"{reconciliation['coverage']}\n"
                            f"{reconciliation['timing']}\n"
                            f"{reconciliation['limits']}\n"
                            f"{reconciliation['note']}"
                        )
                        extra = Gtk.Label(label=content, xalign=0)
                        extra.set_wrap(True)
                        extra.set_selectable(True)
                        extra.add_css_class("dim-label")
                        item.append(extra)
                    add_detail_view(item, row)
                    group_box.append(item)
                expander.set_child(group_box)
                journals_box.append(expander)
            if not rows:
                observation = empty_observation(
                    current_page, period=PERIODS[int(period_selector.get_selected())],
                    severity=SEVERITIES[int(severity_selector.get_selected())],
                    module=module_filter.get_text().strip(),
                    search=search_filter.get_text().strip(),
                    domain=domain_filter.get_text().strip(),
                )
                empty = Gtk.Label(
                    label=observation + "\nAucun événement sur cette page. "
                          "Vérifier la période, les filtres ou la disponibilité des sources.",
                    xalign=0,
                )
                empty.set_wrap(True)
                journals_box.append(empty)
            journals_previous.set_sensitive(
                page_offset >= PAGE_SIZE
            )
            journals_next.set_sensitive(
                next_page_offset(current_page, current_offset=page_offset) is not None
            )
            update_selected_status()

        def load_events(*_args, keep_bulk=False) -> None:
            nonlocal page_generation, current_page
            page_generation += 1
            generation = page_generation
            if not (keep_bulk and selection_json is not None):
                invalidate_selection("Sélection effacée après actualisation ou navigation")
            if self._event_handle is not None and not self._event_handle.done():
                self._event_handle.cancel()
            # Invalidate the previous snapshot immediately, including when
            # a local search change happens while the new query is pending.
            current_page = {
                "events": [], "sources": [], "total": 0,
                "offset": page_offset, "next_offset": None,
            }
            display_events()
            journals_summary.set_text("Actualisation en cours…")
            journals_previous.set_sensitive(False)
            journals_next.set_sensitive(False)

            # GTK widgets may only be accessed on the UI thread.
            query = {
                "source": SOURCES[int(source_selector.get_selected())],
                "module": module_filter.get_text(),
                "domain": domain_filter.get_text().strip(),
                "provider_id": provider_filter.get_text().strip(),
                "severity": SEVERITIES[int(severity_selector.get_selected())],
                "period": PERIODS[int(period_selector.get_selected())],
                "offset": page_offset,
                "reason_code": ("" if int(source_selector.get_selected()) == 3
                                else reason_code_filter.get_text().strip()),
                "search": (search_filter.get_text().strip()
                           if int(source_selector.get_selected()) == 3 else ""),
            }
            if int(source_selector.get_selected()) == 3:
                query["domain"] = ""
                # A journal page is already globally ordered by Core. The
                # 50-row GTK display sort alone would otherwise reverse the
                # OLDEST page and falsely label it "Plus récents d’abord".
                query["sort_order"] = "desc" if sort_latest.get_active() else "asc"

            def read_snapshot(_control):
                return fetch_explorer_page(broker, **query)

            try:
                handle = self.tasks.submit(
                    read_snapshot, label="Consultation des journaux",
                    kind="diagnostics",
                )
            except Exception as exc:
                # A failed refresh must not leave an earlier page visible as
                # though it were the result of the current filters.
                current_page = {
                    "events": [], "sources": [], "total": 0,
                    "offset": page_offset, "next_offset": None,
                }
                display_events()
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
            load_events(keep_bulk=True)

        def next_events(_button) -> None:
            nonlocal page_offset
            next_offset = next_page_offset(current_page, current_offset=page_offset)
            if next_offset is None:
                return
            page_offset = next_offset
            load_events(keep_bulk=True)

        def copy_selected(_button) -> None:
            if selection_busy:
                return
            if selection_json is not None:
                display = Gdk.Display.get_default()
                if display is None:
                    host.toast("Presse-papiers indisponible")
                    return
                display.get_clipboard().set(selection_json)
                host.toast("Sélection multi-page expurgée copiée (non atomique)")
                return
            rows = sanitized_rows(
                current_page, search=("" if int(source_selector.get_selected()) == 3
                                      else search_filter.get_text().strip()),
                newest_first=sort_latest.get_active(),
            )
            visible_ids = selectable_event_ids(rows)
            selected = selected_event_ids.intersection(visible_ids)
            if not selected:
                host.toast("Sélectionner un événement visible")
                return
            display = Gdk.Display.get_default()
            if display is None:
                host.toast("Presse-papiers indisponible")
                return
            try:
                exported = export_selected_events(rows, selected)
            except ValueError:
                host.toast("Export trop volumineux : réduire la sélection")
                return
            display.get_clipboard().set(exported)
            host.toast("Sélection expurgée copiée")

        def select_on_page(action):
            nonlocal selection_json, selection_note
            if selection_busy:
                return
            rows = sanitized_rows(
                current_page, search=search_filter.get_text().strip(),
                newest_first=sort_latest.get_active(),
            )
            previous = selected_event_ids if selection_json is None else set()
            next_ids = page_selection_ids(rows, set(previous), action)
            selected_event_ids.clear()
            selected_event_ids.update(next_ids)
            selection_json = None
            selection_note = "Inversion limitée à cette page"
            display_events()

        def select_filtered(mode):
            nonlocal selection_json, selection_note, selection_generation, selection_busy
            if selection_busy:
                return
            invalidate_selection("")
            selection_generation += 1
            token = selection_generation
            start_generation = page_generation
            query = {
                "source": SOURCES[int(source_selector.get_selected())],
                "module": module_filter.get_text(),
                "domain": domain_filter.get_text().strip(),
                "provider_id": provider_filter.get_text().strip(),
                "severity": SEVERITIES[int(severity_selector.get_selected())],
                "period": PERIODS[int(period_selector.get_selected())],
                "search": search_filter.get_text().strip(),
                "reason_code": reason_code_filter.get_text().strip(),
                "mode": mode,
            }
            selection_busy = True
            update_selected_status()

            def scan(control):
                return collect_filtered_selection(
                    broker, cancelled=control.cancelled, **query,
                )

            try:
                handle = self.tasks.submit(
                    scan, label="Sélection expurgée des journaux", kind="diagnostics",
                )
            except Exception:
                selection_busy = False
                selection_note = "Sélection indisponible (traitement non démarré)"
                update_selected_status()
                return
            self._selection_handle = handle

            def finish(token_result, page_token, selected, failure):
                nonlocal selection_json, selection_note, selection_busy
                if token_result != selection_generation or page_token != page_generation:
                    return False
                selection_busy = False
                selected_event_ids.clear()
                if failure is not None:
                    selection_json = None
                    selection_note = failure
                else:
                    selection_json = selected.export_json
                    selected_event_ids.update(selected.event_ids)
                    selection_note = (
                        f"{selected.inspected} examinés · premier lot revérifié · "
                        "pas d’instantané Core atomique"
                        + (" · sources incomplètes possibles" if selected.partial_sources else "")
                    )
                display_events()
                if selection_json is not None:
                    journals_preview.set_expanded(True)
                return False

            def done(done_handle):
                from gi.repository import GLib
                try:
                    selected = done_handle.result()
                except Exception as exc:
                    fixed = str(exc) if type(exc) is ValueError else ""
                    safe = {
                        "diagnostic selection changed during pagination",
                        "diagnostic selection contains ambiguous event IDs",
                        "diagnostic selection contains duplicate event IDs",
                        "diagnostic selection is incomplete",
                        "diagnostic selection has missing rows",
                        "diagnostic selection exceeds the safe page limit",
                        "diagnostic selection exceeds 200 events",
                        "diagnostic selection exceeds 96 KB",
                        "diagnostic selection cancelled",
                        "diagnostic selection contains invalid Unicode",
                        "diagnostic selection contains out-of-scope events",
                        "diagnostic selection contains out-of-period events",
                    }
                    reason = fixed if fixed in safe else "Sélection indisponible : source ou export non qualifié"
                    GLib.idle_add(finish, token, start_generation, None, reason)
                else:
                    GLib.idle_add(finish, token, start_generation, selected, None)

            handle.add_done_callback(done)

        raw_export_armed = False

        def export_raw_journal_clicked(_button):
            nonlocal raw_export_armed
            if not raw_export_armed:
                raw_export_armed = True
                journals_raw_export.set_label("Confirmer l’export brut non masqué")
                host.toast(
                    "Attention : le journal brut contient potentiellement logins, "
                    "mots de passe, cookies, tokens, URL et autres données sensibles. "
                    "Cet export JSON inclut toute la rétention, pas seulement aujourd'hui. "
                    "Cliquez encore pour choisir un fichier local."
                )
                return
            raw_export_armed = False
            journals_raw_export.set_label("Exporter tout l’historique (JSON)…")
            if not callable(getattr(broker, "export_raw_journal", None)):
                host.toast("Core incompatible : export brut non disponible")
                return
            # _UiHost is an adapter, not a Gtk.Window. The initiating button
            # supplies the real toplevel; detached test pages have no parent.
            root = _button.get_root()
            parent = root if isinstance(root, Gtk.Window) else None
            if self._raw_export_chooser is not None:
                host.toast("Un sélecteur d’export est déjà ouvert")
                return
            chooser = Gtk.FileChooserNative.new(
                "Exporter les événements bruts", parent,
                Gtk.FileChooserAction.SAVE, "Exporter", "Annuler",
            )
            chooser.set_modal(parent is not None)
            folder = _readable_raw_export_folder()
            if folder is not None:
                try:
                    chooser.set_current_folder(Gio.File.new_for_path(folder))
                except (GLib.Error, OSError, ValueError):
                    # The native/portal backend may reject a local folder.
                    # Keep the chooser available; never choose a path to write.
                    pass
            chooser.set_current_name("cifereca-journal-brut.json")
            self._raw_export_chooser = chooser

            def chose_file(dialog, response):
                try:
                    if response != Gtk.ResponseType.ACCEPT:
                        return
                    output = dialog.get_file()
                    destination = output.get_path() if output is not None else None
                    if not destination:
                        host.toast("Destination locale requise")
                        return

                    def write_raw(control):
                        import os
                        if control.cancelled():
                            return False
                        content = broker.export_raw_journal()
                        if type(content) is not bytes or len(content) > 12_000_000:
                            raise ValueError("journal_export_limit")
                        if control.cancelled():
                            return False
                        flags = (os.O_WRONLY | os.O_CREAT | os.O_TRUNC
                                 | getattr(os, "O_NOFOLLOW", 0))
                        fd = os.open(destination, flags, 0o600)
                        with os.fdopen(fd, "wb") as handle:
                            os.fchmod(handle.fileno(), 0o600)
                            handle.write(content)
                        return True

                    try:
                        handle = self.tasks.submit(
                            write_raw, label="Export local du journal brut",
                            kind="diagnostics",
                        )
                    except Exception:
                        host.toast("Export brut indisponible")
                        return

                    def completed(done_handle):
                        try:
                            okay = done_handle.result()
                        except Exception:
                            okay = False

                        def status():
                            if self._page_root is page_root:
                                host.toast(
                                    "Journal brut exporté localement" if okay
                                    else "Export brut impossible ou annulé"
                                )
                            return False

                        GLib.idle_add(status)

                    handle.add_done_callback(completed)
                finally:
                    if self._raw_export_chooser is dialog:
                        self._raw_export_chooser = None
                    dialog.destroy()

            chooser.connect("response", chose_file)
            chooser.show()

        journals_raw_export.connect("clicked", export_raw_journal_clicked)

        # An explicit refresh changes the underlying evidence. Any old AI
        # preview or consent must be revoked *before* starting broker I/O.
        def refresh_journals(_button):
            invalidate_ai()
            reset_events()

        journals_refresh.connect("clicked", refresh_journals)
        journals_previous.connect("clicked", previous_events)
        journals_next.connect("clicked", next_events)
        journals_copy.connect("clicked", copy_selected)
        journals_select_errors.connect("clicked", lambda *_: select_filtered("errors"))
        journals_select_all.connect("clicked", lambda *_: select_filtered("all"))
        journals_invert.connect("clicked", lambda *_: select_on_page("invert"))
        journals_clear.connect("clicked", lambda *_: (invalidate_selection("Sélection effacée"), display_events()))
        source_selector.connect("notify::selected", reset_events)
        severity_selector.connect("notify::selected", reset_events)
        period_selector.connect("notify::selected", reset_events)
        module_filter.connect("activate", reset_events)
        domain_filter.connect("activate", reset_events)
        domain_filter.connect("changed", lambda *_: invalidate_selection())
        provider_filter.connect("activate", reset_events)
        provider_filter.connect("changed", lambda *_: invalidate_selection())
        reason_code_filter.connect("activate", reset_events)
        reason_code_filter.connect("changed", lambda *_: invalidate_selection())
        search_timeout = 0

        def on_search_change(*_args):
            # Debounce typing: never scan 10,000 events on every keystroke.
            # The actual query uses the task broker; stale replies are ignored
            # through page_generation, and the prior task is cancelled.
            from gi.repository import GLib
            nonlocal search_timeout
            invalidate_selection()
            if not journal_available:
                display_events()  # Backward-compatible Core, local page search.
                return
            if search_timeout:
                GLib.source_remove(search_timeout)

            def apply_search():
                nonlocal search_timeout
                search_timeout = 0
                if int(source_selector.get_selected()) == 3:
                    reset_events()
                else:
                    display_events()
                return False

            search_timeout = GLib.timeout_add(220, apply_search)

        search_filter.connect("changed", on_search_change)
        sort_latest.connect(
            "toggled",
            lambda *_: reset_events() if int(source_selector.get_selected()) == 3
            else display_events(),
        )
        operation_grouping.connect("toggled", lambda *_: display_events())
        journals_copy.set_sensitive(False)
        journals_previous.set_sensitive(False)
        journals_next.set_sensitive(False)

        ai_preview = None
        ai_result = ""
        ai_json = ""
        ai_generation = 0

        def invalidate_ai(*_args):
            nonlocal ai_preview, ai_result, ai_json, ai_generation
            ai_generation += 1
            ai_preview = None
            ai_result = ""
            ai_json = ""
            if self._ai_handle is not None and not self._ai_handle.done():
                self._ai_handle.cancel()
            ai_prepare.set_sensitive(True)
            ai_send.set_sensitive(False)
            ai_export.set_sensitive(False)
            ai_export_json.set_sensitive(False)
            ai_input.get_buffer().set_text("")
            ai_report.get_buffer().set_text("")
            ai_status.set_text("Aperçu à actualiser avant toute nouvelle analyse")

        def choose_manual_log(_button):
            # A file portal grants access only to the explicitly chosen file.
            # Never reveal its name or local path in preview, logs or exports.
            chooser = Gtk.FileChooserNative.new(
                "Choisir un journal (max. 64 Kio)", None,
                Gtk.FileChooserAction.OPEN, "Choisir", "Annuler",
            )
            chooser.set_modal(True)

            def selected(dialog, response):
                nonlocal manual_log_path
                try:
                    if response != Gtk.ResponseType.ACCEPT:
                        return
                    choice = dialog.get_file()
                    chosen = choice.get_path() if choice is not None else None
                    invalidate_ai()
                    manual_log_path = chosen if type(chosen) is str and chosen else None
                    manual_clear.set_sensitive(manual_log_path is not None)
                    manual_state.set_text(
                        "Un journal sélectionné · lecture bornée à la prévisualisation"
                        if manual_log_path else
                        "Sélection non accessible localement · aucun journal chargé"
                    )
                finally:
                    dialog.destroy()

            chooser.connect("response", selected)
            chooser.show()

        def clear_manual_log(_button):
            nonlocal manual_log_path
            invalidate_ai()
            manual_log_path = None
            manual_clear.set_sensitive(False)
            manual_state.set_text("Aucun journal sélectionné · aucune lecture automatique")

        def prepare_ai(_button):
            nonlocal ai_generation, ai_preview, ai_result, ai_json
            if self.llm is None:
                ai_status.set_text("Service IA désactivé : aucun envoi.")
                return
            try:
                destination = llm_destination(self.llm)
                selected_source = SOURCES[int(source_selector.get_selected())]
                selected_period = PERIODS[int(period_selector.get_selected())]
                selected_module = module_filter.get_text().strip()
                selected_provider = provider_filter.get_text().strip()
                selected_severity = SEVERITIES[int(severity_selector.get_selected())]
                selected_snapshot = ai_snapshot.get_active()
                selected_support_report = ai_support_report.get_active()
                selected_manual_log = manual_log_path
            except (ValueError, TypeError, IndexError) as exc:
                ai_status.set_text("Destination IA indisponible : " + type(exc).__name__)
                return
            ai_generation += 1
            token = ai_generation
            ai_preview = None
            ai_result = ""
            ai_json = ""
            ai_input.get_buffer().set_text("")
            ai_report.get_buffer().set_text("")
            ai_send.set_sensitive(False)
            ai_export.set_sensitive(False)
            ai_export_json.set_sensitive(False)
            ai_status.set_text("Préparation locale de l’aperçu expurgé…")

            def worker(_control):
                from diagnostics_explorer import explorer_since
                from diagnostics_ai import read_user_selected_log
                # Read only after the explicit selection AND preview click,
                # on the background task. File contents are not kept in UI.
                manual = (
                    read_user_selected_log(selected_manual_log)
                    if selected_manual_log is not None else None
                )
                return prepare_analysis(
                    self.llm, broker, source=selected_source,
                    module=selected_module, severity=selected_severity,
                    provider_id=selected_provider,
                    since=explorer_since(selected_period),
                    include_snapshot=selected_snapshot,
                    include_support_report=selected_support_report,
                    manual_log=manual,
                )

            try:
                handle = self.tasks.submit(
                    worker, label="Aperçu local Diagnostics IA", kind="diagnostics",
                )
            except Exception:
                ai_status.set_text("Aperçu IA indisponible : tâche non démarrée")
                ai_send.set_sensitive(False)
                ai_export.set_sensitive(False)
                ai_export_json.set_sensitive(False)
                return
            self._ai_handle = handle

            def completed(done_handle):
                from gi.repository import GLib
                try:
                    result = done_handle.result()
                except Exception as exc:
                    GLib.idle_add(finish_preview, token, None, type(exc).__name__)
                else:
                    GLib.idle_add(finish_preview, token, result, None)

            handle.add_done_callback(completed)

        def finish_preview(token, preview, error):
            nonlocal ai_preview
            if token != ai_generation:
                return False
            if error is not None or not isinstance(preview, Mapping):
                ai_status.set_text("Aperçu IA indisponible : aucune donnée transmise")
                ai_send.set_sensitive(False)
                return False
            dest = preview["destination"]
            scope = preview["scope"]
            locality = (
                "DISTANT — aucun envoi avant autorisation"
                if dest["remote"] else "LOCAL — aucun appel avant autorisation"
            )
            source_states = preview.get("source_states") or ()
            source_warnings = [
                str(row.get("source", "inconnue")) + " : " +
                str(row.get("status", "unavailable")) +
                (" (" + str(row["reason"]) + ")" if row.get("reason") else "")
                for row in source_states if row.get("status") in (
                    "partial", "disabled", "unavailable",
                )
            ]
            coverage_warning = (
                "Attention : échantillon incomplet (budget/limite des sources). "
                if preview.get("sample_truncated") else ""
            )
            if source_warnings:
                coverage_warning += "Sources limitées : " + "; ".join(source_warnings) + ". "
            ai_status.set_text(
                f"{locality} · {dest['provider']} · modèle {dest['model']} · "
                f"hôte {dest['host']} · {preview['count']} événements, "
                f"{preview['structured_count']} diagnostics structurés, "
                f"{preview['support_fields']} rubriques support · "
                f"{preview['bytes']} octets UTF-8. "
                f"Journal sélectionné : "
                f"{preview['manual_lines']} lignes de journal sélectionné (signaux seulement). "
                f"Périmètre demandé : {scope['source']} · "
                f"{scope['module'] or 'tous modules'} · "
                f"{scope['severity'] or 'tous niveaux'} · "
                f"depuis {scope['since'] or 'tout historique'} · "
                f"diagnostics structurés : {'oui' if scope['include_snapshot'] else 'non'} · "
                f"rapport support : {'oui' if scope['include_support_report'] else 'non'} · "
                f"journal manuel : {'oui' if scope.get('user_log_selected') else 'non'}. "
                + coverage_warning
                + "Le modèle produit des hypothèses, jamais une preuve."
            )
            ai_input.get_buffer().set_text(preview["prompt"])
            ai_expander.set_expanded(True)
            ai_preview = preview
            ai_send.set_sensitive(True)
            return False

        def send_ai(_button):
            nonlocal ai_preview, ai_generation
            if ai_preview is None:
                return
            selected = ai_preview
            ai_preview = None  # single-use consent per request
            ai_send.set_sensitive(False)
            ai_prepare.set_sensitive(False)
            ai_status.set_text("Analyse IA en cours (requête autorisée)…")
            token = ai_generation
            try:
                handle = self.tasks.submit(
                    lambda control: analyze_with_consent(
                        self.llm, selected, approval_id=selected["approval_id"],
                        control=control,
                    ),
                    label="Analyse Diagnostics IA explicite", kind="diagnostics",
                )
            except Exception:
                ai_prepare.set_sensitive(True)
                ai_send.set_sensitive(False)
                ai_export.set_sensitive(False)
                ai_status.set_text("Analyse IA indisponible : planification impossible")
                ai_export_json.set_sensitive(False)
                return
            self._ai_handle = handle

            def completed(done_handle):
                from gi.repository import GLib
                try:
                    result = done_handle.result()
                except Exception as exc:
                    GLib.idle_add(finish_ai, token, None, type(exc).__name__)
                else:
                    GLib.idle_add(finish_ai, token, result, None)

            self._ai_handle.add_done_callback(completed)

        def finish_ai(token, result, error):
            nonlocal ai_result, ai_json
            if token != ai_generation:
                return False
            ai_prepare.set_sensitive(True)
            if error or not isinstance(result, Mapping) or not result.get("ok"):
                ai_status.set_text("Analyse IA indisponible ou annulée ; aucun diagnostic confirmé")
                return False
            cited = result.get("references_matched") or ()
            unknown_refs = result.get("references_unknown") or ()
            ambiguous_refs = result.get("references_ambiguous") or ()
            citations = (
                "\n".join(
                    f"- {row['event_id']} · {row['timestamp']} · {row['source']}"
                    for row in cited
                )
                if cited else "Aucun événement de l’extrait n’a été cité par identifiant."
            )
            unsupported = (
                "\nRéférences non retrouvées : " + ", ".join(unknown_refs)
                if unknown_refs else ""
            )
            ambiguity = (
                "\nRéférences ambiguës (ID dupliqué, source non attribuable) : "
                + ", ".join(ambiguous_refs)
                if ambiguous_refs else ""
            )
            ai_result = (
                "# Analyse générée par IA — hypothèses NON vérifiées\n"
                + f"Destination annoncée : {result['provider']} / {result['model']}\n"
                + f"Provenance : {result['evidence_count']} événements Core et "
                  f"{result['structured_count']} diagnostics structurés et "
                  f"{result['support_fields']} rubriques support expurgées et "
                  f"{result['manual_lines']} lignes de journal sélectionné (signaux)\n"
                + f"Limites : {result['limitations']}\n\n"
                + "## Analyse du modèle (inférée)\n"
                + result["text"]
                + "\n\n## Références d’événements présentes dans l’extrait\n"
                + citations + unsupported + ambiguity
                + "\nLa correspondance d’un identifiant ne prouve PAS "
                  "la véracité de l’affirmation associée.\n"
            )
            ai_report.get_buffer().set_text(ai_result)
            ai_export.set_sensitive(True)
            try:
                ai_json = export_analysis_json(result)
            except (ValueError, TypeError):
                ai_json = ""
            ai_export_json.set_sensitive(bool(ai_json))
            ai_status.set_text("Analyse IA générée ; vérifier toutes les références avant décision.")
            return False

        def cancel_ai(_button):
            invalidate_ai()
            ai_prepare.set_sensitive(True)
            handle = self._ai_handle
            if handle is not None and not handle.done():
                handle.cancel()
            ai_status.set_text("Analyse annulée côté interface (requête distante éventuellement déjà envoyée)")

        def copy_ai(_button):
            if not ai_result:
                return
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(ai_result)
                host.toast("Analyse IA expurgée copiée (hypothèses non vérifiées)")

        def copy_ai_json(_button):
            if not ai_json:
                return
            display = Gdk.Display.get_default()
            if display is not None:
                display.get_clipboard().set(ai_json)
                host.toast("Rapport IA JSON expurgé copié (inférences non vérifiées)")

        manual_choose.connect("clicked", choose_manual_log)
        manual_clear.connect("clicked", clear_manual_log)
        ai_prepare.connect("clicked", prepare_ai)
        ai_send.connect("clicked", send_ai)
        ai_cancel.connect("clicked", cancel_ai)
        ai_export.connect("clicked", copy_ai)
        ai_export_json.connect("clicked", copy_ai_json)
        source_selector.connect("notify::selected", invalidate_ai)
        severity_selector.connect("notify::selected", invalidate_ai)
        period_selector.connect("notify::selected", invalidate_ai)
        module_filter.connect("changed", invalidate_ai)
        domain_filter.connect("changed", invalidate_ai)
        provider_filter.connect("changed", invalidate_ai)
        ai_snapshot.connect("toggled", invalidate_ai)
        ai_support_report.connect("toggled", invalidate_ai)

        versions_snapshot = [None]

        def copy_versions(_button):
            snapshot = versions_snapshot[0]
            if snapshot is None:
                host.toast("Versions indisponibles : actualiser l’inventaire")
                return
            display = Gdk.Display.get_default()
            if display is None:
                host.toast("Presse-papiers indisponible")
                return
            try:
                provider = Gdk.ContentProvider.new_for_value(snapshot.text)
                self._versions_clipboard_provider = provider
                if display.get_clipboard().set_content(provider):
                    host.toast("Versions copiées")
                else:
                    host.toast("Copie des versions impossible")
            except Exception:
                host.toast("Copie des versions impossible")

        def render(*_args) -> None:
            versions_snapshot[0] = None
            versions_copy.set_sensitive(False)
            clear_container(versions_list)
            clear_container(status_grid)
            clear_container(diag_box)
            try:
                status = broker.system_status()
                inventory = public_inventory(broker)
                versions = version_inventory(status, inventory)
                versions_snapshot[0] = versions
                versions_copy.set_sensitive(True)
                versions_core.set_text(f"Core Cifereca · v{versions.core_version}")
                versions_count.set_text(
                    f"{len(versions.plugins)} plugins installés / enregistrés"
                    + (" · inventaire limité à 300" if versions.limited else "")
                )
                for component in versions.plugins:
                    item_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
                    item_name = Gtk.Label(
                        label=f"{component.name} [{component.plugin_id}]", xalign=0)
                    item_name.set_wrap(True)
                    item_box.append(item_name)
                    item_detail = Gtk.Label(label=component.details, xalign=0)
                    item_detail.set_wrap(True)
                    item_detail.add_css_class("dim-label")
                    item_box.append(item_detail)
                    versions_list.append(item_box)
                if isinstance(status, Mapping):
                    for key in sorted(status, key=lambda item: str(item).casefold()):
                        add_status(str(key), status[key])
                else:
                    add_status("État", status)
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
            sections.set_visible_child_name("reports")
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

        def refresh_diagnostics(_button):
            # Fail closed even when the next broker read fails or is partial.
            # Generation revocation also ignores any old async AI response.
            invalidate_ai()
            render()
            reset_events()

        refresh.connect("clicked", refresh_diagnostics)
        versions_copy.connect("clicked", copy_versions)
        check.connect("clicked", run_checks)
        copy.connect("clicked", copy_report)
        def initial_load() -> None:
            render()
            load_events()

        _run_once_on_first_map(scroll, initial_load)
        return page_root

    def deactivate(self):
        # Native dialogs outlive the GTK page unless explicitly dismissed.
        previous_raw_chooser = getattr(self, "_raw_export_chooser", None)
        if previous_raw_chooser is not None:
            previous_raw_chooser.destroy()
        self._raw_export_chooser = None
        previous_daily_export = getattr(self, "_daily_journal_export", None)
        if previous_daily_export is not None:
            previous_daily_export.close()
        self._daily_journal_export = None
        previous_http_export = getattr(self, "_http_trace_export", None)
        if previous_http_export is not None:
            previous_http_export.close()
        self._http_trace_export = None
        for exporter in getattr(self, "_support_exports", ()):
            exporter.close()
        self._support_exports = []
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
        if getattr(self, "_selection_handle", None) is not None:
            try:
                self._selection_handle.cancel()
            except Exception:
                pass
        self._selection_handle = None
        if getattr(self, "_ai_handle", None) is not None:
            try:
                self._ai_handle.cancel()
            except Exception:
                pass
        self._ai_handle = None
        self.llm = None
        self.tasks = None
        self.diagnostics = None
        self.context = None


PLUGIN = DiagnosticsPagePlugin()
