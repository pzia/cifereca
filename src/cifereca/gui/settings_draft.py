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
from .settings_legacy_kobo import apply_legacy_kobo_settings


class SettingsValidationError(ValueError):
    def __init__(self, message, title=None):
        super().__init__(message)
        self.title = title


@dataclass(frozen=True)
class SettingsDraft:
    data: dict[str, Any]
    directory_change: bool
    ai_restart_required: bool


def _merge_download_config(previous, *, directory, ebook, audiobook, command, post):
    """Update only the fields owned by Settings; retain provider extensions.

    Download settings may contain keys declared by a future adapter, including
    extra content-type directories. A save in another section must not erase
    opaque provider configuration or silently reset a future capability.
    """
    merged = dict(previous) if type(previous) is dict else {}
    previous_types = merged.get('content_type_directories')
    type_dirs = dict(previous_types) if type(previous_types) is dict else {}
    type_dirs.update({'ebook': ebook, 'audiobook': audiobook})
    merged.update({
        'directory': directory,
        'content_type_directories': type_dirs,
        'command': command,
        'post_download_command': post,
    })
    return merged


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
    account_ids = {str(row.get('id') or '') for row in preserved}
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
        account_id = str(editor.get('id') or '')
        if not account_id or account_id in account_ids:
            raise SettingsValidationError('Identifiant de compte fournisseur vide ou dupliqué.')
        account_ids.add(account_id)
        descriptor = descriptors.get(editor['provider'])
        if descriptor is None or descriptor.state != 'ready':
            raise SettingsValidationError('Fournisseur indisponible : recharger les paramètres avant de sauvegarder.')
        if editor.get('invalid_saved_snapshot') is True:
            raise SettingsValidationError(
                'Données du compte fournisseur invalides : recharger les paramètres.'
            )
        # A plugin may change its descriptor after Settings was opened.
        # Refuse stale or untyped snapshots rather than dropping a credential,
        # coercing an activation flag, or raising an unhandled KeyError.
        if type(editor.get('enabled')) is not bool:
            raise SettingsValidationError('Activation de compte invalide : recharger les paramètres.')
        submitted_fields = editor.get('fields')
        if type(submitted_fields) is not dict:
            raise SettingsValidationError('Identifiants de compte indisponibles : recharger les paramètres.')
        original_credentials = editor.get('original_credentials', {})
        if type(original_credentials) is not dict:
            raise SettingsValidationError(
                'Identifiants historiques invalides : recharger les paramètres.'
            )
        # A descriptor may stop exposing an old credential field. Preserve
        # its stored value, even if the current editor does not render it.
        # Current descriptor fields still receive the explicitly edited text.
        credentials: dict[str, Any] = dict(original_credentials)
        for field in descriptor.credential_fields:
            if field.key not in submitted_fields or type(submitted_fields[field.key]) is not str:
                raise SettingsValidationError(
                    'Schéma des identifiants modifié : recharger les paramètres.'
                )
            raw_credential = submitted_fields[field.key]
            # Only active accounts require credentials; a disabled account can
            # be staged for later configuration without enabling any source.
            # Preserve an unchanged malformed legacy value on unrelated saves
            # instead of silently wiping it or forcing credential re-entry.
            if (getattr(field, 'required', False) is True
                    and editor['enabled'] is True and not raw_credential.strip()):
                original_account = editor.get('original_account', {})
                old_value = original_credentials.get(field.key)
                legacy_unchanged = (
                    type(original_account) is dict
                    and original_account.get('id') == editor['id']
                    and original_account.get('enabled', True) is True
                    and type(old_value) is str
                    and old_value == raw_credential
                )
                if not legacy_unchanged:
                    raise SettingsValidationError(
                        f'Identifiant obligatoire : {field.label}'
                    )
            credentials[field.key] = raw_credential
        # Snapshot values originate from a GTK editor, not the provider's
        # parser. Refuse malformed legacy containers rather than coercing
        # pair lists or strings into account configuration.
        original_options = editor.get('original_options', {})
        if type(original_options) is not dict:
            raise SettingsValidationError(
                'Options du fournisseur invalides : recharger les paramètres.'
            )
        options: dict[str, Any] = dict(original_options)
        option_fields = editor.get('option_fields', {})
        if type(option_fields) is not dict:
            raise SettingsValidationError(
                'Schéma des options modifié : recharger les paramètres.'
            )
        for field in getattr(descriptor, 'account_options', ()):
            if field.key not in option_fields:
                continue  # A legacy editor does not silently reset saved options.
            # AccountOptionField is a declarative TEXT schema. Never turn
            # None/bool/dicts from a stale widget snapshot into a plausible
            # provider option by applying str() (e.g. endpoint='None').
            raw_value = option_fields[field.key]
            if type(raw_value) is not str:
                raise SettingsValidationError(f'Valeur d’option invalide : {field.label}')
            if field.default is None:
                fallback = ''  # tolerate historical descriptors with no default
            elif type(field.default) is str:
                fallback = field.default.strip()
            else:
                raise SettingsValidationError(f'Défaut d’option invalide : {field.label}')
            value = raw_value.strip() or fallback
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
        original_account = editor.get('original_account', {})
        if type(original_account) is not dict:
            raise SettingsValidationError(
                'Compte fournisseur historique invalide : recharger les paramètres.'
            )
        # Retain opaque provider-specific extensions. CoreUI only owns the
        # generic fields below; rebuilding a fixed dict discards future
        # adapter metadata during unrelated Settings saves.
        saved_account = dict(original_account)
        saved_account.update({
            'id': editor['id'], 'provider': editor['provider'],
            'identity_id': account_identity_id,
            'label': editor['label'].strip() or descriptor.display_name,
            'enabled': editor['enabled'],
            'anonymous': not bool(descriptor.credential_fields),
            'credentials': credentials, 'options': options,
        })
        saved_accounts.append(saved_account)
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
    draft.set('downloads', _merge_download_config(
        draft.get('downloads', {}),
        directory=values['download_directory'].strip(),
        ebook=values['ebook_directory'].strip(),
        audiobook=values['audiobook_directory'].strip(),
        command=global_command, post=global_post_command,
    ))
    for provider_id, editor in values['provider_downloads'].items():
        settings = draft.provider_settings(provider_id)
        command, post_command = provider_commands[provider_id]
        settings['downloads'] = _merge_download_config(
            settings.get('downloads'),
            directory=editor['directory'].strip(),
            ebook=editor['ebook'].strip(),
            audiobook=editor['audiobook'].strip(),
            command=command, post=post_command,
        )
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
    draft.set('library.directory', values['library_directory'].strip() or '~/EBooks')
    apply_legacy_kobo_settings(draft, values, validation_error=SettingsValidationError)
    directory_change = any(
        row['id'] in current_dirs and row['directory_name'] != current_dirs[row['id']]
        for row in saved_identities
    )
    if directory_change and has_active_tasks:
        raise SettingsValidationError('Attendez la fin des opérations en cours avant de changer un répertoire d’identité.')
    return SettingsDraft(draft.data, directory_change, ai_restart_required)
