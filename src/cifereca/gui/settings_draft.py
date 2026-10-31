"""Neutral settings draft validation; no widgets, scheduling or persistence."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
import re
import shlex
from typing import Any

from ..identities.directory_selection import directory_choice
from .llm_settings import LLMSettingsError, normalize_llm_settings


class SettingsValidationError(ValueError):
    def __init__(self, message, title=None):
        super().__init__(message)
        self.title = title


@dataclass(frozen=True)
class SettingsDraft:
    data: dict[str, Any]
    directory_change: bool
    ai_restart_required: bool


def build_settings_draft(config, values, *, descriptors, public_ids,
                         current_dirs, has_active_tasks=False, ai_dirty=False):
    """Validate a GTK-thread snapshot without changing the current config.

    The service still owns migration and rollback. Provider descriptors contain
    credential and account-option schemas; values contain no widgets.
    """
    draft = deepcopy(config)
    saved_identities: list[dict[str, Any]] = []
    identity_ids: set[str] = set()
    directory_names: set[str] = set()
    for editor in values['identities']:
        if editor.get('deleted'):
            continue
        identity_id = editor['id'].strip()
        if not identity_id or not re.fullmatch('[A-Za-z0-9._-]+', identity_id):
            raise SettingsValidationError('Identité invalide : utilisez uniquement lettres, chiffres, point, tiret ou underscore.')
        if identity_id in identity_ids:
            raise SettingsValidationError(f'Identité dupliquée : {identity_id}')
        identity_ids.add(identity_id)
        try:
            directory_name = directory_choice(editor['directory'], Path(values['library_directory'].strip() or '~/EBooks'))
        except ValueError as exc:
            raise SettingsValidationError(str(exc))
        except RuntimeError as exc:
            raise SettingsValidationError(str(exc))
        if directory_name in directory_names:
            raise SettingsValidationError(f'Répertoire d’identité dupliqué : {directory_name}')
        directory_names.add(directory_name)
        languages = [part.strip().lower() for part in editor['languages'].split(',') if part.strip()]
        if not languages:
            languages = ['fr']
        saved_identities.append({
            'id': identity_id,
            'label': editor['label'].strip() or identity_id,
            'directory_name': directory_name,
            'adept_profile': editor['adept_profile'].strip(),
            'preferences': {
                'prefer_original_language': editor['prefer_vo'],
                'reading_languages': languages,
            },
        })
    if not saved_identities:
        raise SettingsValidationError('cifereca doit conserver au moins une identité.')
    draft.set('identities', saved_identities)
    preserved = []
    for row in draft.provider_accounts():
        descriptor = descriptors.get(str(row.get('provider') or '').lower())
        if descriptor is None or descriptor.state != 'ready' or descriptor.id in public_ids:
            preserved.append(row)
    saved_accounts = list(preserved)
    # A declared cardinality is a provider contract, never CoreUI provider policy.
    scoped_accounts: dict[tuple[str, str], list[str]] = {}
    for row in preserved:
        provider_id = str(row.get('provider') or '').lower()
        iid = str(row.get('identity_id') or '').strip()
        if iid:
            scoped_accounts.setdefault((provider_id, iid), []).append(str(row.get('id') or ''))
    for editor in values['accounts']:
        if editor.get('deleted'):
            continue
        descriptor = descriptors[editor['provider']]
        credentials: dict[str, str] = {}
        for field in descriptor.credential_fields:
            credentials[field.key] = editor['fields'][field.key]
        options: dict[str, Any] = dict(editor.get('original_options') or {})
        for field in getattr(descriptor, 'account_options', ()):
            if field.key not in editor.get('option_fields', {}):
                continue  # A legacy editor does not silently reset saved options.
            value = str(editor['option_fields'][field.key]).strip() or str(field.default)
            if field.required and not value:
                raise SettingsValidationError(f'Option obligatoire : {field.label}')
            options[field.key] = value
        account_identity_id = editor['identity'].strip() or saved_identities[0]['id']
        if account_identity_id not in identity_ids:
            raise SettingsValidationError(f"Identité inconnue pour {editor['id']} : {account_identity_id}")
        provider_id = str(editor['provider']).lower()
        limit = getattr(descriptor, 'max_accounts_per_identity', None)
        if limit is not None:
            if type(limit) is not int or limit < 1:
                raise SettingsValidationError(f'Limite de comptes invalide pour {descriptor.display_name}.')
            key = (provider_id, account_identity_id)
            linked = scoped_accounts.setdefault(key, [])
            if editor['id'] not in linked and len(linked) >= limit:
                raise SettingsValidationError(
                    f'{descriptor.display_name} : au plus {limit} compte(s) par identité ({account_identity_id}).'
                )
            if editor['id'] not in linked:
                linked.append(editor['id'])
        saved_accounts.append({
            'id': editor['id'], 'provider': editor['provider'],
            'identity_id': account_identity_id,
            'label': editor['label'].strip() or descriptor.display_name,
            'enabled': editor['enabled'],
            'anonymous': not bool(descriptor.credential_fields),
            'credentials': credentials, 'options': options,
        })
    draft.set('providers.accounts', saved_accounts)
    try:
        global_command = shlex.split(values['download_command']) if values['download_command'].strip() else []
        global_post_command = shlex.split(values['post_download_command']) if values['post_download_command'].strip() else []
        provider_commands: dict[str, tuple[list[str], list[str]]] = {}
        for provider_id, editor in values['provider_downloads'].items():
            provider_commands[provider_id] = (
                shlex.split(editor['command']) if editor['command'].strip() else [],
                shlex.split(editor['post']) if editor['post'].strip() else [],
            )
    except ValueError as exc:
        raise SettingsValidationError(f'Commande de téléchargement invalide : {exc}')
    draft.set('downloads', {
        'directory': values['download_directory'].strip(),
        'content_type_directories': {
            'ebook': values['ebook_directory'].strip(),
            'audiobook': values['audiobook_directory'].strip(),
        },
        'command': global_command, 'post_download_command': global_post_command,
    })
    for provider_id, editor in values['provider_downloads'].items():
        settings = draft.provider_settings(provider_id)
        command, post_command = provider_commands[provider_id]
        settings['downloads'] = {
            'directory': editor['directory'].strip(),
            'content_type_directories': {
                'ebook': editor['ebook'].strip(),
                'audiobook': editor['audiobook'].strip(),
            },
            'command': command, 'post_download_command': post_command,
        }
        draft.set_provider_settings(provider_id, settings)
    draft.set('diagnostics.enabled', True)
    draft.set('diagnostics.level', values['levels'][values['diagnostics_level_index']])
    draft.set('experimental_actions', values['experimental_actions'])
    ai_restart_required = False
    if ai_dirty:
        try:
            new_ai_settings = normalize_llm_settings(values['ai'])
        except LLMSettingsError as exc:
            raise SettingsValidationError(str(exc), 'Configuration IA invalide')
        ai_restart_required = new_ai_settings != config.get('llm', {})
        draft.set('llm', new_ai_settings)
    try:
        backup_interval = max(15, int(values['backup_interval'].strip() or '60'))
    except ValueError as exc:
        raise SettingsValidationError(f'Configuration Kobo invalide : {exc}')
    draft.set('library.directory', values['library_directory'].strip() or '~/EBooks')
    # This historical global switch is rendered read-only. A Settings save
    # must not turn its (possibly stale) widget snapshot into acquisition
    # permission. Preserve its persisted value verbatim for migration and
    # rollback; only source-scoped Sync consent grants new authorization.
    # The retired global bypass is likewise not a user setting.
    draft.set('kobo_backup.directory', values['backup_directory'].strip())
    draft.set('kobo_backup.interval_minutes', backup_interval)
    selected_profile = int(values['vault_profile_index'])
    draft.set('kobo_backup.display_profile',
              values['vault_profiles'][selected_profile]
              if 0 <= selected_profile < len(values['vault_profiles']) else 'Android')
    # Preserve the configured Kobo format: this Settings form has no
    # editable format control. Defaulting is the executor's responsibility;
    # saving another section must not silently turn kepub into epub.
    directory_change = any(
        row['id'] in current_dirs and row['directory_name'] != current_dirs[row['id']]
        for row in saved_identities
    )
    if directory_change and has_active_tasks:
        raise SettingsValidationError('Attendez la fin des opérations en cours avant de changer un répertoire d’identité.')
    return SettingsDraft(draft.data, directory_change, ai_restart_required)
