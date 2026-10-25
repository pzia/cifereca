"""Presentational GTK Settings Diagnostics section (#1127).

MainWindow retains status collection, redaction, copy/export, plugin navigation
and persistence. No PyGObject import, signal, network or disk side effects.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class DiagnosticSettingsWidgets:
    summary: Any
    filter: Any
    issues_box: Any
    details_toggle: Any
    text: Any
    scroll: Any
    refresh: Any
    copy: Any
    export: Any
    clear: Any
    performance_capture: Any
    performance_export: Any
    performance_status: Any
    open_page: Any
    entry: Any
    fallback_widgets: tuple[Any, ...]


def build_settings_diagnostics_section(
    *, section: Any, Gtk: Any, Adw: Any,
    adaptive_action_bar: Callable[..., Any],
    action_button: Callable[..., Any],
) -> DiagnosticSettingsWidgets:
    """Build exactly the original view, without binding callbacks."""
    entry = Adw.PreferencesGroup()
    entry.set_title("Journaux et captures")
    section.append(entry)
    diag_group = Adw.PreferencesGroup()
    diag_group.set_title("Diagnostic système")
    section.append(diag_group)
    diag_summary = Gtk.Label(xalign=0)
    diag_summary.set_wrap(True)
    diag_group.add(diag_summary)

    diag_filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    diag_filter_label = Gtk.Label(label="Afficher", xalign=0)
    diag_filter_label.set_hexpand(True)
    diag_filter_row.append(diag_filter_label)
    diag_filter = Gtk.DropDown.new_from_strings(["À examiner", "Tous"])
    diag_filter.set_tooltip_text("Afficher uniquement les anomalies ou l’inventaire complet")
    diag_filter_row.append(diag_filter)
    diag_group.add(diag_filter_row)
    diag_issues_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    diag_group.add(diag_issues_box)

    details_toggle = Gtk.ToggleButton(label="Afficher les détails techniques")
    details_toggle.set_halign(Gtk.Align.START)
    diag_group.add(details_toggle)
    diag_text = Gtk.TextView()
    diag_text.set_editable(False)
    diag_text.set_monospace(True)
    diag_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
    diag_text.set_top_margin(10)
    diag_text.set_bottom_margin(10)
    diag_text.set_left_margin(10)
    diag_text.set_right_margin(10)
    diag_scroll = Gtk.ScrolledWindow()
    diag_scroll.set_min_content_height(330)
    diag_scroll.set_child(diag_text)
    diag_scroll.set_visible(False)
    diag_group.add(diag_scroll)

    # Transitional entry point: Settings retains a Core fallback when the
    # optional Diagnostics page is not installed or is disabled.
    open_page = action_button("Ouvrir Diagnostics", "go-next-symbolic")
    open_page.set_tooltip_text(
        "Accéder aux journaux et rapports dans la page Diagnostics"
    )
    entry.add(open_page)

    # A separate, explicitly opted-in UI performance observer. The monitor
    # already exists on MainWindow; opening Settings never starts capture.
    perf_group = Adw.PreferencesGroup()
    perf_group.set_title("Performances · capture volontaire")
    perf_note = Gtk.Label(
        label=(
            "Les mesures passives restent actives. La chronologie détaillée "
            "ne démarre qu’après votre action, reste locale et peut être "
            "arrêtée à tout moment. Fermer cet onglet n’arrête pas une capture "
            "déjà démarrée ; fermer l’application l’arrête."
        ), xalign=0,
    )
    perf_note.set_wrap(True)
    perf_group.add(perf_note)
    performance_status = Gtk.Label(label="Capture détaillée désactivée.", xalign=0)
    performance_status.set_wrap(True)
    perf_group.add(performance_status)
    perf_actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
    performance_capture = action_button("Démarrer la capture détaillée", "media-record-symbolic")
    performance_export = action_button("Exporter les mesures JSON", "document-save-symbolic")
    performance_export.set_tooltip_text(
        "Enregistrer localement les mesures bornées et expurgées ; aucun envoi automatique"
    )
    perf_actions.append(performance_capture)
    perf_actions.append(performance_export)
    perf_group.add(perf_actions)
    section.append(perf_group)

    buttons = adaptive_action_bar(source=section)
    section.append(buttons)
    refresh = action_button("Actualiser", "view-refresh-symbolic")
    copy = action_button("Copier dans le presse-papiers", "edit-copy-symbolic", suggested=True)
    export = action_button("Exporter l’affichage", "document-save-symbolic")
    export.set_tooltip_text("Sauvegarde locale de la vue avec login/mot de passe pseudonymisés ; autres valeurs visibles, fichier 0600")
    clear = action_button("Effacer l’affichage", "edit-clear-symbolic")
    buttons.append(refresh)
    buttons.append(copy)
    buttons.append(export)
    clear.set_tooltip_text("Efface uniquement le texte affiché ; le journal conservé ne sera pas supprimé")
    buttons.append(clear)
    return DiagnosticSettingsWidgets(
        summary=diag_summary, filter=diag_filter, issues_box=diag_issues_box,
        details_toggle=details_toggle, text=diag_text, scroll=diag_scroll,
        refresh=refresh, copy=copy, export=export, clear=clear,
        performance_capture=performance_capture,
        performance_export=performance_export,
        performance_status=performance_status,
        open_page=open_page, entry=entry,
        fallback_widgets=(diag_group, perf_group, buttons),
    )
