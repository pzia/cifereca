"""P0 #1013/#1011 GTK structure and responsiveness contract (no GTK runtime)."""
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / "cifereca_diagnostics_page.py"


def test_journals_explorer_is_inside_existing_diagnostics_page():
    source = PAGE.read_text(encoding="utf-8")
    assert 'route="diagnostics"' in source
    assert 'Gtk.Label(label="Journaux par composant"' in source
    assert '["Toutes les sources", "Événements Core", "Journal applicatif"]' in source
    assert '["Tous niveaux", "Debug", "Info", "Avertissements", "Erreurs", "Critiques"]' in source
    assert '["Toutes périodes", "Dernière heure", "24 heures", "7 jours", "30 jours"]' in source
    assert "module_filter.connect(\"activate\", reset_events)" in source
    assert 'search_filter.connect("changed", lambda *_: (invalidate_selection(), display_events()))' in source
    assert "sort_latest.connect(\"toggled\", lambda *_: display_events())" in source


def test_query_runs_through_tasks_broker_and_result_renders_on_gtk_idle():
    source = PAGE.read_text(encoding="utf-8")
    assert "self.tasks.submit(" in source
    assert "fetch_explorer_page(broker, **query)" in source
    assert "GLib.idle_add(apply_result, generation, snapshot, None)" in source
    assert "if token != page_generation:" in source
    assert "query = {" in source
    assert "module_filter.get_text()" in source
    assert 'self._event_handle.cancel()' in source
    assert "open(" not in source
    assert "cifereca.paths" not in source
    assert "cifereca.core" not in source
    assert "subprocess" not in source


def test_module_groups_event_ids_and_states_are_explicit_not_claimed_correlations():
    src = PAGE.read_text(encoding="utf-8")
    assert "else module_groups(rows))" in src
    assert "for row in group[\"events\"]:" in src
    assert "event_id = row[\"event_id\"]" in src
    assert "Aucun événement sur cette page" in src
    assert "Sources non alimentées ou indisponibles" in src
    assert "Actualisation en cours" in src
    assert "Données indisponibles" in src
    assert 'journals_copy.set_sensitive(bool(selected_event_ids) and not selection_busy)' in src


def test_pagination_search_and_copy_never_request_raw_logs():
    src = PAGE.read_text(encoding="utf-8")
    assert 'journals_previous.connect("clicked", previous_events)' in src
    assert 'journals_next.connect("clicked", next_events)' in src
    assert 'journals_copy.connect("clicked", copy_selected)' in src
    # Preserve the export contract even when the UI now catches oversized
    # selections: only sanitized visible rows may reach the clipboard.
    assert 'selected = selected_event_ids.intersection(visible_ids)' in src
    assert 'exported = export_selected_events(rows, selected)' in src
    assert 'except ValueError:' in src
    assert 'host.toast("Export trop volumineux : réduire la sélection")' in src
    assert 'display.get_clipboard().set(exported)' in src
    # Pagination must use the validated monotonic/bounded broker cursor.
    assert 'next_offset = next_page_offset(current_page, current_offset=page_offset)' in src
    assert 'if next_offset is None:' in src
    assert 'page_offset = next_offset' in src
    assert 'search=search_filter.get_text().strip()' in src


def test_large_support_report_is_collapsible_resizeable_scrollable_and_wrappable():
    src = PAGE.read_text(encoding="utf-8")
    assert 'Gtk.Expander(label="Ouvrir le rapport support")' in src
    assert 'report_expander.set_expanded(False)' in src
    assert 'report_resize = Gtk.Button(label="Agrandir")' in src
    assert 'report_wrap = Gtk.CheckButton(label="Retour à la ligne")' in src
    assert 'report_scroll.set_size_request(-1, 320)' in src
    assert '560 if is_big else 320' in src
    assert 'report_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)' in src
    assert 'export_view.set_editable(False)' in src
    assert 'export_view.set_monospace(True)' in src
    assert 'Gtk.Expander(label="Afficher les détails techniques")' in src
    assert "self.context.report_diagnostic(" in src
