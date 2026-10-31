"""Compatibility adapter for obsolete global Kobo preferences.

Only these legacy keys are written here. No acquisition consent is inferred
from a Settings save, and the old enabled/unlinked flags are never modified.
The provider-neutral draft delegates this translation so new providers need
no Kobo-specific code.
"""
from __future__ import annotations


_LEGACY_WIDGET_KEYS = (
    'backup_interval', 'vault_profile_index', 'backup_directory',
    'vault_profiles',
)


def apply_legacy_kobo_settings(draft, values, *, validation_error):
    """Copy legacy widgets only when the complete old editor is still present.

    A provider-neutral Settings editor may omit all Kobo-only widgets. Its
    unrelated save must preserve the persisted legacy config byte-for-byte.
    A partial widget snapshot is stale/corrupt, not authorization to reset the
    missing fields to defaults.
    """
    presence = tuple(key in values for key in _LEGACY_WIDGET_KEYS)
    if not any(presence):
        return
    if not all(presence):
        raise validation_error('Configuration Kobo incomplète : recharger les paramètres.')

    raw_interval = values['backup_interval']
    raw_directory = values['backup_directory']
    if type(raw_interval) is not str or type(raw_directory) is not str:
        raise validation_error('Configuration Kobo invalide : champs non textuels.')
    try:
        # Legacy GTK text is a *decimal minute count*, not a Python integer
        # literal. int() alone accepts +60, -1, 1_000 and non-ASCII digits;
        # malformed old widget snapshots must never silently alter settings.
        decimal = raw_interval.strip() or '60'
        if not decimal.isascii() or not decimal.isdecimal():
            raise ValueError('decimal minutes expected')
        interval = max(15, int(decimal))
    except ValueError as exc:
        raise validation_error(f'Configuration Kobo invalide : {exc}') from exc

    index = values['vault_profile_index']
    profiles = values['vault_profiles']
    if type(index) is not int or type(profiles) not in (list, tuple):
        raise validation_error('Profil de sauvegarde historique invalide')
    selected = profiles[index] if 0 <= index < len(profiles) else 'Android'
    if type(selected) is not str:
        raise validation_error('Profil de sauvegarde historique invalide')

    # Never grant downloads or touch legacy enabled/allow-unlinked fields.
    draft.set('kobo_backup.directory', raw_directory.strip())
    draft.set('kobo_backup.interval_minutes', interval)
    draft.set('kobo_backup.display_profile', selected)
