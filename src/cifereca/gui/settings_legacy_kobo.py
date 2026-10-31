"""Compatibility adapter for obsolete global Kobo preferences.

Only these legacy keys are written here. No acquisition consent is inferred
from a Settings save, and the old enabled/unlinked flags are never modified.
The provider-neutral draft delegates this translation so new providers need
no Kobo-specific code.
"""
from __future__ import annotations


def apply_legacy_kobo_settings(draft, values, *, validation_error):
    """Validate and copy editable legacy settings without granting downloads."""
    try:
        interval = max(15, int(values['backup_interval'].strip() or '60'))
    except ValueError as exc:
        raise validation_error(f'Configuration Kobo invalide : {exc}') from exc
    try:
        index = values['vault_profile_index']
        if type(index) is not int:
            raise ValueError('profile index must be an integer')
    except (KeyError, ValueError, TypeError) as exc:
        raise validation_error('Profil de sauvegarde historique invalide') from exc

    # No writes to kobo_backup.enabled, allow_unlinked_acquisition or format.
    draft.set('kobo_backup.directory', values['backup_directory'].strip())
    draft.set('kobo_backup.interval_minutes', interval)
    draft.set(
        'kobo_backup.display_profile',
        values['vault_profiles'][index]
        if 0 <= index < len(values['vault_profiles']) else 'Android',
    )
