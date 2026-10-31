"""One explicit source acquisition choice, backed by SourceSyncConsentBroker."""
from __future__ import annotations

from typing import Any
from gi.repository import Gtk, Adw

from .sync_action_layout import add_sync_action, sync_action_row
from .sync_action_scope import sync_action_scope_current


def append_source_sync_consent_controls(window: Any, broker: Any, source: Any, scope: Any, group: Any) -> None:
    if (not sync_action_scope_current(window, scope)
            or getattr(source, 'identity_id', None) != scope.identity_id):
        return
    source_id = source.source_id
    row = Adw.ActionRow()
    row.set_title('Téléchargement automatique de ce compte')
    row.set_subtitle_lines(0)
    group.add(sync_action_row(row))
    read = getattr(broker, 'source_sync_consent', None)
    write = getattr(broker, 'set_source_sync_consent', None)
    try:
        state = read(source_id)
    except Exception:
        import logging
        logging.getLogger('cifereca').warning('Source consent UI read failed (details withheld)')
        row.set_subtitle('Réglage indisponible. Actualisez avant de modifier les téléchargements.')
        return
    if not sync_action_scope_current(window, scope):
        return
    if (type(state) is not dict
            or set(state) != {'supported', 'configured', 'enabled', 'revision', 'valid'}
            or any(type(state[field]) is not bool for field in ('supported', 'configured', 'enabled', 'valid'))
            or (state['configured'] and (type(state['revision']) is not str or not 1 <= len(state['revision']) <= 128))
            or (not state['configured'] and (
                state['revision'] is not None or state['enabled'] or not state['valid']))
            or (not state['valid'] and state['enabled'])
            or not callable(write)):
        row.set_subtitle('Réglage indisponible. Aucun changement possible depuis cette page.')
        return
    supported, configured, enabled = state['supported'], state['configured'], state['enabled']
    revision = state['revision']

    def change(wanted):
        if not sync_action_scope_current(window, scope):
            return
        if wanted and not (supported and source.enabled):
            return
        try:
            write(source_id, enabled=wanted, expected_revision=revision)
        except Exception:
            import logging
            logging.getLogger('cifereca').warning('Source consent UI update failed (details withheld)')
            if sync_action_scope_current(window, scope):
                window.show_error('Le réglage a changé ou n’a pas pu être enregistré. Actualisez puis réessayez.',
                                  'Synchronisation')
        if sync_action_scope_current(window, scope):
            window._refresh_sync_page()

    if configured and state['valid']:
        row.set_subtitle(
            ('Activé : la synchronisation peut recevoir les fichiers disponibles de ce compte. '
             if enabled else 'Désactivé : aucune nouvelle livraison automatique pour ce compte. ')
            + 'Les fichiers déjà reçus sont conservés.'
        )
        toggle = Gtk.Switch()
        toggle.set_active(enabled)
        toggle.set_sensitive(bool(enabled or (supported and source.enabled)))
        toggle.set_valign(Gtk.Align.CENTER)
        toggle.set_tooltip_text('Autoriser ou arrêter les téléchargements de tous les achats et prêts de ce compte')
        toggle.connect('notify::active', lambda widget, _param: change(widget.get_active()))
        row.add_suffix(toggle)
        return

    row.set_subtitle(
        ('Réglage à vérifier. Choisissez explicitement une autorisation pour ce compte. '
         if not state['valid'] else
         'Choisissez le téléchargement automatique pour tous les achats et prêts de ce compte. '
         'Les autorisations déjà enregistrées sont conservées jusqu’à votre choix. ')
        + ('' if supported else 'Cette source ne permet pas l’activation depuis cette page. ')
        + 'Cette action ne lance aucun téléchargement.'
    )
    for wanted, label in ((True, 'Activer'), (False, 'Désactiver')):
        button = window.action_button(label, 'emblem-synchronizing-symbolic' if wanted else 'media-playback-stop-symbolic')
        button.set_sensitive(bool(not wanted or (supported and source.enabled)))
        button.set_valign(Gtk.Align.CENTER)
        button.connect('clicked', lambda _button, value=wanted: change(value))
        add_sync_action(row, button)
