"""Headless entry point for refreshing Settings Diagnostics widgets (#1127).

The Settings page continues owning GTK widgets, lifetime and plugin navigation.
No provider calls beyond the previous system-status snapshot, no subscriptions
and no implicit plugin activation. GTK and projection are injected by the host.
"""
from __future__ import annotations

import json

from ..brokers import DiagnosticsBrokerImpl
from ..core.diagnostic_journal import journal_payload
from .connection_failure_summary import connection_failure_summary
from .diagnostics_pseudonyms import DISPLAY_PSEUDONYMS


def _canonical_settings_report(service, version: str, extra: dict) -> str:
    """Short local compatibility preview of the SAME persistent journal.

    This Settings section is not another event collector or independent
    export source. It applies the same GTK-only session pseudonyms as the
    Diagnostics plugin to explicit login/password fields, nothing else.
    """
    lines = [
        f"cifereca v{version}",
        "Source: journal canonique local (100 dernières observations structurées)",
        "Pour tous les logs, leurs rotations et l'export brut : ouvrir Diagnostics.",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: " + DISPLAY_PSEUDONYMS.text(json.dumps(
            journal_payload(value), ensure_ascii=False, allow_nan=False,
        )))
    lines.append("")
    try:
        events = DiagnosticsBrokerImpl(service).snapshot()
    except Exception as exc:
        lines.append("Lecture du journal indisponible : " + type(exc).__name__)
        return "\n".join(lines) + "\n"
    if not events:
        lines.append("Aucune observation structurée dans la rétention accessible.")
    for event in events[-100:]:
        lines.append(DISPLAY_PSEUDONYMS.text(json.dumps(
            journal_payload(event), ensure_ascii=False, allow_nan=False,
        )))
    return "\n".join(lines) + "\n"


def refresh_settings_diagnostics(
    window, *, page_lifetime, diag_summary, diag_filter, diag_issues_box,
    diag_text, open_diagnostic_plugin, Gtk, Adw, clear_box,
    diagnostic_plugin_rows, system_status, version,
) -> None:
    if not page_lifetime.alive or getattr(window, "_closed", False):
        return
    data = system_status(window.plugin_manager)
    attention = int(data.get("attention") or 0)
    # Passive Core snapshot: no reconnect, remote query or plugin serializer.
    provider_state = window.service.provider_diagnostic_state()
    failure_text = connection_failure_summary(
        provider_state.get("connection_failures")
        if type(provider_state) is dict else None
    )
    diag_summary.set_label(
        f"{data.get('ready', 0)} prêt(s) · {attention} à examiner. "
        + (
            "Les anomalies sont affichées en premier."
            if attention else (
                "Des connexions nécessitent une vérification."
                if failure_text else "Aucune anomalie bloquante détectée."
            )
        )
        + (f"\n{failure_text}" if failure_text else "")
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

    # Never read the obsolete Diagnostics._items memory cache. The recent
    # structured journal is bounded here; the full canonical log is browsed
    # via the dedicated Diagnostics page, not dumped synchronously in Settings.
    diagnostics_text = _canonical_settings_report(window.service, version, {
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
        "providers": provider_state,
    })
    diag_text.get_buffer().set_text(diagnostics_text)
