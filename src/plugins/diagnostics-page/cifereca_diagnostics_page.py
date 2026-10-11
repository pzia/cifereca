from __future__ import annotations

from collections.abc import Mapping
import json
import math
import re
from typing import Any

from cifereca.plugin_api import UiPageContribution

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
    text = value[:_MAX_DETAIL_TEXT]
    text = _DIAGNOSTIC_URL.sub("<URL expurgée>", text)
    text = _DIAGNOSTIC_MAIL.sub("<courriel expurgé>", text)
    text = _DIAGNOSTIC_PATH.sub("<chemin expurgé>", text)
    text = _DIAGNOSTIC_SECRET_VALUE.sub(r"\1=<expurgé>", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,;]+", "Bearer <expurgé>", text)
    return text + ("… [tronqué]" if len(value) > _MAX_DETAIL_TEXT else "")


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

        diag_title = Gtk.Label(label="Diagnostics", xalign=0)
        diag_title.add_css_class("heading")
        outer.append(diag_title)
        diag_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(diag_box)

        check_title = Gtk.Label(label="Vérification active", xalign=0)
        check_title.add_css_class("heading")
        outer.append(check_title)
        check_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.append(check_box)
        check_hint = Gtk.Label(label="Lancez la vérification pour contrôler les connexions connues et runtimes sans synchroniser ni télécharger.", xalign=0)
        check_hint.set_wrap(True)
        check_box.append(check_hint)

        export_title = Gtk.Label(label="Rapport support", xalign=0)
        export_title.add_css_class("heading")
        outer.append(export_title)
        export_view = Gtk.TextView()
        export_view.set_editable(False)
        export_view.set_cursor_visible(False)
        export_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        export_view.set_monospace(True)
        export_view.set_vexpand(True)
        export_view.set_size_request(-1, 220)
        outer.append(export_view)

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

        refresh.connect("clicked", render)
        check.connect("clicked", run_checks)
        copy.connect("clicked", copy_report)
        _run_once_on_first_map(scroll, render)
        return scroll

    def deactivate(self):
        if self._check_handle is not None:
            try:
                self._check_handle.cancel()
            except Exception:
                pass
        self._check_handle = None
        self.tasks = None
        self.diagnostics = None
        self.context = None


PLUGIN = DiagnosticsPagePlugin()
