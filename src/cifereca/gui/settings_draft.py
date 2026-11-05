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
from .borrower_scoped_options import (
    MAX_BORROWERS, _valid_borrower_id, decimal_limit,
    validate_saved_borrower_limits,
)


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
    # A Settings snapshot is not trusted to retain the GTK editor's types.
    # Guard global strings and toggles before any Path/shlex operation or
    # draft.set(), so an unrelated save cannot coerce a stale widget value.
    text_fields = (
        'library_directory', 'download_command', 'post_download_command',
        'download_directory', 'ebook_directory', 'audiobook_directory',
    )
    if any(type(values.get(key)) is not str for key in text_fields):
        raise SettingsValidationError(
            'Paramètres de répertoire ou de téléchargement invalides : recharger les paramètres.'
        )
    levels = values.get('levels')
    level_index = values.get('diagnostics_level_index')
    if (type(levels) not in (list, tuple)
            or not levels or len(levels) > 32
            or any(type(value) is not str for value in levels)
            or type(level_index) is not int
            or not 0 <= level_index < len(levels)):
        raise SettingsValidationError(
            'Niveau de diagnostic invalide : recharger les paramètres.'
        )
    provider_downloads = values.get('provider_downloads')
    if type(provider_downloads) is not dict:
        raise SettingsValidationError(
            'Réglages de téléchargement fournisseur invalides : recharger les paramètres.'
        )
    for provider_id, fields in provider_downloads.items():
        if (type(provider_id) is not str or not provider_id
                or type(fields) is not dict
                or any(type(fields.get(key)) is not str for key in (
                    'command', 'post',
                ))):
            raise SettingsValidationError(
                'Réglages de téléchargement fournisseur invalides : recharger les paramètres.'
            )
    draft = deepcopy(config)
    # A malformed snapshot such as accounts={} is iterable but empty:
    # rebuilding from it could silently delete every editable provider account.
    # Require actual GTK editor sequences before constructing any draft rows.
    if type(values.get('identities')) not in (list, tuple):
        raise SettingsValidationError(
            'Liste d’identités invalide : recharger les paramètres.'
        )
    if type(values.get('accounts')) not in (list, tuple):
        raise SettingsValidationError(
            'Liste de comptes fournisseurs invalide : recharger les paramètres.'
        )
    saved_identities: list[dict[str, Any]] = []
    identity_ids: set[str] = set()
    directory_names: set[str] = set()
    for editor in values['identities']:
        if type(editor) is not dict:
            raise SettingsValidationError('Éditeur d’identité invalide : recharger les paramètres.')
        delete_identity = editor.get('deleted', False)
        if type(delete_identity) is not bool:
            raise SettingsValidationError('Suppression d’identité invalide : recharger les paramètres.')
        if delete_identity:
            continue
        if type(editor.get('id')) is not str:
            raise SettingsValidationError('Identité invalide : utilisez uniquement lettres, chiffres, point, tiret ou underscore.')
        identity_id = editor['id'].strip()
        if not identity_id or not re.fullmatch('[A-Za-z0-9._-]+', identity_id):
            raise SettingsValidationError('Identité invalide : utilisez uniquement lettres, chiffres, point, tiret ou underscore.')
        if identity_id in identity_ids:
            raise SettingsValidationError(f'Identité dupliquée : {identity_id}')
        identity_ids.add(identity_id)
        if any(type(editor.get(key)) is not str for key in (
            'directory', 'label', 'adept_profile', 'languages'
        )):
            raise SettingsValidationError('Champs d’identité invalides : recharger les paramètres.')
        if type(editor.get('prefer_vo')) is not bool:
            raise SettingsValidationError('Préférence de langue invalide : recharger les paramètres.')
        try:
            directory_name = directory_choice(editor['directory'], Path(values['library_directory'].strip() or '~/EBooks'))
        except ValueError as exc:
            raise SettingsValidationError(str(exc))
        except RuntimeError as exc:
            raise SettingsValidationError(str(exc))
        if directory_name in directory_names:
            raise SettingsValidationError(f'Répertoire d’identité dupliqué : {directory_name}')
        directory_names.add(directory_name)
        # A missing widget key only occurs in old/headless callers. Use
        # the exact existing binding without coercing malformed saved IDs.
        prior_identity = config.identity(identity_id) or {}
        raw_saved_primary = prior_identity.get('primary_paris_connection_id', '')
        if type(raw_saved_primary) is not str:
            raise SettingsValidationError(
                'Connexion Paris principale historique invalide : recharger les paramètres.'
            )
        selected = editor.get(
            'primary_paris_connection_id',
            prior_identity.get('primary_paris_connection_id', ''),
        )
        if type(selected) is not str or len(selected) > 256 or (
                selected and (selected != selected.strip()
                              or not selected.isprintable())):
            raise SettingsValidationError(
                'Identifiant de connexion Paris principale invalide : recharger les paramètres.'
            )
        selected = selected.strip()
        languages = [part.strip().lower() for part in editor['languages'].split(',') if part.strip()]
        if not languages:
            languages = ['fr']
        saved_identities.append({
            **({'primary_paris_connection_id': selected} if selected else {}),
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
        if type(editor) is not dict:
            raise SettingsValidationError('Compte fournisseur invalide : recharger les paramètres.')
        deletion_flag = editor.get('deleted', False)
        if type(deletion_flag) is not bool:
            raise SettingsValidationError(
                'Suppression de compte fournisseur invalide : recharger les paramètres.'
            )
        if deletion_flag:
            continue
        # A stale or malformed GTK snapshot must never stringify a numeric ID,
        # coerce a provider selector or raise on an unhashable provider value.
        account_id = editor.get('id')
        if (type(account_id) is not str or not account_id.strip()
                or account_id in account_ids):
            raise SettingsValidationError('Identifiant de compte fournisseur vide ou dupliqué.')
        provider_id = editor.get('provider')
        if type(provider_id) is not str or not provider_id:
            raise SettingsValidationError('Fournisseur indisponible : recharger les paramètres avant de sauvegarder.')
        label_text = editor.get('label')
        if type(label_text) is not str:
            raise SettingsValidationError('Libellé de compte fournisseur invalide : recharger les paramètres.')
        account_ids.add(account_id)
        descriptor = descriptors.get(provider_id)
        if descriptor is None or descriptor.state != 'ready':
            raise SettingsValidationError('Fournisseur indisponible : recharger les paramètres avant de sauvegarder.')
        if editor.get('invalid_saved_snapshot') is True:
            raise SettingsValidationError(
                'Données du compte fournisseur invalides : recharger les paramètres.'
            )
        # An existing untyped ON/OFF value is not a deliberate user toggle.
        # Refuse a stale GTK draft instead of rewriting a malformed persisted
        # flag as False during an unrelated Settings save. Legacy *missing*
        # flags retain their historical compatibility.
        original_account = editor.get('original_account')
        # Never copy opaque provider metadata from a *different* connection
        # if a stale GTK editor was rebound or duplicated. A new row has no
        # historical snapshot; an existing one must match exact account ID
        # and provider. Preserve legacy provider casing only.
        if (type(original_account) is dict and (
                ('id' in original_account
                 and original_account['id'] != account_id)
                or ('provider' in original_account
                    and (type(original_account['provider']) is not str
                         or original_account['provider'].casefold()
                         != provider_id.casefold())))):
            raise SettingsValidationError(
                'Origine du compte fournisseur incohérente : recharger les paramètres.'
            )
        if (type(original_account) is dict
                and original_account.get('id') == account_id
                and 'enabled' in original_account
                and type(original_account['enabled']) is not bool):
            raise SettingsValidationError(
                'Activation historique invalide : recharger les paramètres.'
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
            # A synthetic/stale snapshot cannot bypass the GUI invalid flag.
            # Do not silently replace a persisted non-text value with the
            # blank displayed by a text-only editor on an unrelated Save.
            if (field.key in original_credentials
                    and type(original_credentials[field.key]) is not str):
                raise SettingsValidationError(
                    'Identifiants historiques invalides : recharger les paramètres.'
                )
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
                # Legacy editors predate newer descriptor options. Preserve
                # existing values, but do not activate an account whose new
                # required option has never been configured.
                prior = original_options.get(field.key)
                if (field.required is True and editor['enabled'] is True
                        and (type(prior) is not str or not prior.strip())):
                    raise SettingsValidationError(
                        f'Schéma des options modifié : renseignez {field.label}.'
                    )
                continue
            if (field.key in original_options
                    and type(original_options[field.key]) is not str):
                raise SettingsValidationError(
                    'Options du fournisseur invalides : recharger les paramètres.'
                )
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
            value_type = getattr(field, 'value_type', 'text')
            if value_type == 'unsigned_integer':
                # A local user preference is not a provider right. Refuse
                # empty, signed, padded, float, boolean and out-of-range values
                # before any configuration write; do not silently default an
                # explicitly cleared field or normalize a stale widget.
                if not re.fullmatch(r'(0|[1-9][0-9]{0,2})', raw_value):
                    raise SettingsValidationError(f'Entier décimal invalide : {field.label}')
                number = int(raw_value)
                lower = getattr(field, 'min_value', None)
                upper = getattr(field, 'max_value', None)
                if (type(lower) is not int or type(upper) is not int
                        or lower < 0 or upper < lower or not lower <= number <= upper):
                    raise SettingsValidationError(f'Valeur hors limites : {field.label}')
                value = raw_value
            elif value_type == 'text':
                # An option explicitly stored on an existing account can be
                # intentionally empty. The GTK editor preserves that blank;
                # do not resurrect a retired global or descriptor default on
                # an unrelated save (or when an optional field is cleared).
                # New accounts still receive the declared default when blank.
                original_account = editor.get('original_account')
                existing_option = (
                    type(original_account) is dict
                    and original_account.get('id') == editor['id']
                    and field.key in original_options
                )
                value = raw_value.strip()
                if not value and not existing_option:
                    value = fallback
            else:
                raise SettingsValidationError(f'Type d’option non pris en charge : {field.label}')
            if field.required is True and editor['enabled'] is True and not value:
                original_account = editor.get('original_account', {})
                old_value = original_options.get(field.key)
                # An unrelated Settings save must not silently erase an old
                # incomplete configuration or force an authentication reset.
                # A newly enabled account or an explicit clearing is rejected.
                legacy_unchanged = (
                    type(original_account) is dict
                    and original_account.get('id') == editor['id']
                    and original_account.get('enabled', True) is True
                    and type(old_value) is str
                    and old_value == raw_value
                )
                if not legacy_unchanged:
                    raise SettingsValidationError(f'Option obligatoire : {field.label}')
            options[field.key] = value
        # Nested, borrower-keyed numeric preferences remain strictly
        # advisory. Never flatten linked accounts into a connection-wide
        # quota or convert a bad saved snapshot to a plausible default.
        scoped_fields = editor.get('scoped_option_fields', {})
        if type(scoped_fields) is not dict:
            raise SettingsValidationError(
                'Préférences par emprunteur invalides : recharger les paramètres.'
            )
        for spec in getattr(descriptor, 'borrower_scoped_options', ()):
            try:
                previous = validate_saved_borrower_limits(
                    original_options.get(spec.key, {}), spec
                )
            except ValueError:
                raise SettingsValidationError(
                    'Préférences d’emprunteur historiques invalides : recharger les paramètres.'
                ) from None
            submitted = scoped_fields.get(spec.key, {})
            if type(submitted) is not dict or len(submitted) > MAX_BORROWERS:
                raise SettingsValidationError(
                    'Préférences par emprunteur invalides : recharger les paramètres.'
                )
            allowed = {medium for medium, _label in spec.branches}
            merged = dict(previous)
            for borrower_id, branch_values in submitted.items():
                if (not _valid_borrower_id(borrower_id)
                        or type(branch_values) is not dict
                        or set(branch_values) != allowed):
                    raise SettingsValidationError(
                        'Périmètre de préférence emprunteur invalide.'
                    )
                normalized = {}
                for medium, value in branch_values.items():
                    if not decimal_limit(value, spec):
                        raise SettingsValidationError(
                            'Plafond personnel invalide : entier décimal de 0 à 99 requis.'
                        )
                    normalized[medium] = value
                merged[borrower_id] = {
                    **previous.get(borrower_id, {}), **normalized,
                }
            if len(merged) > MAX_BORROWERS:
                raise SettingsValidationError('Nombre de lecteurs liés invalide.')
            if submitted or spec.key in original_options:
                options[spec.key] = merged
        identity_text = editor.get('identity')
        if type(identity_text) is not str:
            raise SettingsValidationError('Identité de compte fournisseur invalide : recharger les paramètres.')
        selected_identity = identity_text.strip()
        # A blank identity on an existing account must never silently move
        # its credentials to the first identity when Settings is saved.
        # New accounts retain the historical first-identity default.
        original_for_identity = editor.get('original_account', {})
        if (not selected_identity and type(original_for_identity) is dict
                and original_for_identity.get('id') == editor['id']):
            raise SettingsValidationError(
                'Identité du compte fournisseur vide : sélectionnez explicitement une identité.'
            )
        if (type(original_for_identity) is dict
                and original_for_identity.get('id') == editor['id']
                and type(original_for_identity.get('identity_id')) is str
                and original_for_identity['identity_id'] != selected_identity
                and any(
                    editor.get('scoped_option_fields', {}).get(spec.key)
                    or original_options.get(spec.key)
                    for spec in getattr(descriptor, 'borrower_scoped_options', ())
                )):
            raise SettingsValidationError(
                'Déplacer un compte avec des plafonds de lecteurs liés '
                'nécessite une nouvelle connexion explicite.'
            )
        # A changed public login can represent another remote borrower while
        # reusing the local credential row ID. Never carry previously scoped
        # linked-reader preferences across that ownership boundary. Secret
        # password rotation alone does not change the borrower identity.
        if (type(original_for_identity) is dict
                and original_for_identity.get('id') == editor['id']
                and any(
                    original_options.get(spec.key)
                    for spec in getattr(descriptor, 'borrower_scoped_options', ())
                )
                and any(
                    field.secret is not True
                    and field.key in original_credentials
                    and original_credentials.get(field.key) != credentials.get(field.key)
                    for field in descriptor.credential_fields
                )):
            raise SettingsValidationError(
                'Changer l’identifiant de connexion avec des plafonds de '
                'lecteurs liés nécessite un nouveau compte fournisseur.'
            )
        account_identity_id = selected_identity or saved_identities[0]['id']
        if account_identity_id not in identity_ids:
            raise SettingsValidationError(f"Identité inconnue pour {editor['id']} : {account_identity_id}")
        provider_id = provider_id.lower()
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
            'label': label_text.strip() or descriptor.display_name,
            'enabled': editor['enabled'],
            'anonymous': not bool(descriptor.credential_fields),
            'credentials': credentials, 'options': options,
        })
        saved_accounts.append(saved_account)
    # The normal binding refers to a configured, enabled Paris connection
    # of this exact identity. A save cannot silently redirect it to another
    # login (including after an account was removed or reassigned).
    for identity in saved_identities:
        selected = identity.get('primary_paris_connection_id')
        if not selected:
            continue
        matched = [row for row in saved_accounts if (
            type(row.get('id')) is str
            and row['id'] == selected
            and type(row.get('provider')) is str
            and row['provider'].casefold() == 'paris'
            and row.get('identity_id') == identity['id']
            and row.get('enabled') is True
        )]
        if len(matched) != 1:
            raise SettingsValidationError(
                'Connexion Paris principale absente, désactivée ou ambiguë : '
                'corrigez son ID ou effacez le choix explicite.'
            )
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
    # Preserve the existing priority of the detailed shlex error over an
    # incomplete legacy provider row, then reject missing directory widgets
    # before any new download settings can be constructed.
    for editor in provider_downloads.values():
        if any(type(editor.get(key)) is not str for key in (
            'directory', 'ebook', 'audiobook',
        )):
            raise SettingsValidationError(
                'Réglages de téléchargement fournisseur invalides : recharger les paramètres.'
            )
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
    ai_restart_required = False
    if ai_dirty:
        try:
            new_ai_settings = normalize_llm_settings(values['ai'])
        except LLMSettingsError as exc:
            raise SettingsValidationError(str(exc), 'Configuration IA invalide')
        ai_restart_required = new_ai_settings != config.get('llm', {})
        draft.set('llm', new_ai_settings)
    draft.set('library.directory', values['library_directory'].strip() or '~/EBooks')
    # Generic Settings never translates retired supplier-specific widgets.
    # Historical configuration is already preserved by the config deep copy.
    # Compatibility-only translations live in settings_legacy_kobo, outside
    # the active Settings draft and without acquisition consent side effects.
    directory_change = any(
        row['id'] in current_dirs and row['directory_name'] != current_dirs[row['id']]
        for row in saved_identities
    )
    if directory_change and has_active_tasks:
        raise SettingsValidationError('Attendez la fin des opérations en cours avant de changer un répertoire d’identité.')
    return SettingsDraft(draft.data, directory_change, ai_restart_required)
