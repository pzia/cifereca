from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from cifereca.plugin_api import UiPageContribution


def _safe_text(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if isinstance(value, (str, int, float)):
        return str(value)
    return str(value)


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

        def add_status(key: str, value: Any) -> None:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            card.add_css_class("card")
            card.set_hexpand(True)
            for margin in ("top", "bottom", "start", "end"):
                getattr(card, "set_margin_" + margin)(8)
            caption = Gtk.Label(label=str(key), xalign=0)
            caption.add_css_class("caption")
            caption.set_wrap(True)
            value_label = Gtk.Label(label=_safe_text(value), xalign=0)
            value_label.set_wrap(True)
            value_label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            value_label.set_selectable(True)
            card.append(caption)
            card.append(value_label)
            status_grid.insert(card, -1)

        def add_diag(row: Mapping[str, Any]) -> None:
            card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
            card.add_css_class("card")
            for margin in ("top", "bottom", "start", "end"):
                getattr(card, "set_margin_" + margin)(8)
            title = str(row.get("message") or row.get("operation") or row.get("id") or "Diagnostic")
            heading = Gtk.Label(label=title, xalign=0)
            heading.set_wrap(True)
            heading.add_css_class("heading")
            card.append(heading)
            meta_parts = []
            for key in ("status", "level", "plugin_id", "source"):
                value = row.get(key)
                if value not in (None, ""):
                    meta_parts.append(f"{key}: {value}")
            if meta_parts:
                meta = Gtk.Label(label=" · ".join(meta_parts), xalign=0)
                meta.add_css_class("dim-label")
                meta.set_wrap(True)
                card.append(meta)
            detail = row.get("detail")
            if detail:
                detail_label = Gtk.Label(label=_safe_text(detail), xalign=0)
                detail_label.set_wrap(True)
                detail_label.set_selectable(True)
                card.append(detail_label)
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
                detail = Gtk.Label(label=f"{status} · {_safe_text(row.get('detail'))}", xalign=0)
                detail.set_wrap(True)
                detail.set_selectable(True)
                card.append(detail)
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
        render()
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
