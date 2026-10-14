"""Headless entry point for refreshing Settings Diagnostics widgets (#1127).

The Settings page continues owning GTK widgets, lifetime and plugin navigation.
No provider calls beyond the previous system-status snapshot, no subscriptions
and no implicit plugin activation. GTK and projection are injected by the host.
"""
from __future__ import annotations


def refresh_settings_diagnostics(
    window, *, page_lifetime, diag_summary, diag_filter, diag_issues_box,
    diag_text, open_diagnostic_plugin, Gtk, Adw, clear_box,
    diagnostic_plugin_rows, system_status, version,
) -> None:
    if not page_lifetime.alive or getattr(window, "_closed", False):
        return
    data = system_status(window.plugin_manager)
    attention = int(data.get("attention") or 0)
    diag_summary.set_label(
        f"{data.get('ready', 0)} prêt(s) · {attention} à examiner. "
        + (
            "Les anomalies sont affichées en premier."
            if attention else "Aucune anomalie bloquante détectée."
        )
    )
    clear_box(diag_issues_box)
    attention_only = int(diag_filter.get_selected()) == 0
    rows = diagnostic_plugin_rows(data, attention_only=attention_only)
    if not rows:
        empty = Gtk.Label(label="Aucune anomalie à examiner.", xalign=0)
        empty.add_css_class("success")
        diag_issues_box.append(empty)
    for plugin in rows:
        item = Adw.ExpanderRow()
        item.set_title(f"{plugin['display_name']} · {plugin['state_label']}")
        item.set_subtitle(plugin["summary"])
        if plugin["attention"]:
            item.add_css_class("warning")
        action = Adw.ActionRow()
        action.set_title("Action")
        action.set_subtitle("Ouvrir le gestionnaire sur ce plugin")
        open_button = Gtk.Button(label="Ouvrir Plugins")
        open_button.set_valign(Gtk.Align.CENTER)
        open_button.connect(
            "clicked",
            lambda button, pid=plugin["plugin_id"]:
                open_diagnostic_plugin(button, pid),
        )
        action.add_suffix(open_button)
        item.add_row(action)
        detail = Adw.ActionRow()
        detail.set_title("Détails techniques")
        detail.set_subtitle(plugin["technical"] or "Aucun détail technique")
        detail.set_subtitle_lines(0)
        item.add_row(detail)
        diag_issues_box.append(item)

    # Preserve the former diagnostic snapshot with its bounded presentation
    # and without re-triggering any provider discovery or background job.
    diagnostics_text = window.diagnostics.text(version, {
        "transport": "httpx local direct",
        "base_url": (
            window.config.paris_base_url()
            if hasattr(window.config, "paris_base_url")
            else window.config.get("base_url")
        ),
        "ui": "GTK4/libadwaita",
        "cover_pipeline": window.covers.stats(),
        "history_cover_search": window.history_cover_resolver.stats(),
        "ui_performance": window.ui_perf.snapshot(),
        "providers": window.service.provider_diagnostic_state(),
    })
    diag_text.get_buffer().set_text(diagnostics_text)
