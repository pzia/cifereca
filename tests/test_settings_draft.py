"""Exercise neutral inputs, validation precedence and independent revisions."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from cifereca.config import Config
from cifereca.plugin_api import AccountOptionField, ProviderDescriptor, ProviderCapabilities
from cifereca.gui.settings_draft import build_settings_draft, SettingsValidationError
from cifereca.gui.settings_model import SettingsRevisionState


@pytest.fixture
def editor(tmp_path):
    config = Config(tmp_path / 'config.json')
    config.set('identities', [{'id': 'alice', 'directory_name': 'alice'}])
    config.set('providers.accounts', [])
    values = {
        'identities': [dict(id='alice', label='Alice', directory='alice',
                                  adept_profile='', languages=' FR, en ', prefer_vo=True)],
        'accounts': [], 'provider_downloads': {},
        'levels': ['errors', 'normal', 'verbose', 'trace'], 'diagnostics_level_index': 1,
        'vault_profiles': ['Android', 'Desktop'], 'vault_profile_index': 0,
        'diagnostics_enabled': True, 'experimental_actions': False, 'backup_enabled': False,
        'allow_unlinked_acquisition': False, 'backup_interval': '60',
        'library_directory': str(tmp_path / 'books'), 'backup_directory': '',
        'download_command': '', 'post_download_command': '', 'download_directory': '',
        'ebook_directory': '', 'audiobook_directory': '', 'ai': {},
    }
    return config, values


def validate(editor, **kwargs):
    config, values = editor
    options = dict(descriptors={}, public_ids=set(), current_dirs={'alice': 'alice'})
    options.update(kwargs)
    return build_settings_draft(config, values, **options)


def account(provider, identity='alice', account_id='source'):
    return dict(id=account_id, provider=provider, label='', identity=identity,
                enabled=True, fields={'username': 'synthetic-user'}, original_options={})


def descriptor(provider, state='ready'):
    return NS(id=provider, state=state, display_name=provider,
              credential_fields=[NS(key='username')])


def test_draft_neither_mutates_config_nor_writes_and_preserves_missing_adept_profile(editor):
    config, values = editor
    before = deepcopy(config.data)
    values['identities'].append(dict(id='bob', label='', directory='bob',
        adept_profile='missing-profile', languages='', prefer_vo=False))
    draft = validate(editor)
    assert config.data == before and not config.path.exists()
    assert draft.data['identities'][0]['preferences']['reading_languages'] == ['fr', 'en']
    assert draft.data['identities'][0]['adept_profile'] == ''
    assert draft.data['identities'][1]['adept_profile'] == 'missing-profile'
    assert draft.data['identities'][1]['label'] == 'bob'
    assert draft.data['identities'][1]['preferences']['reading_languages'] == ['fr']
    draft.data['identities'][0]['label'] = 'Changed'
    assert values['identities'][0]['label'] == 'Alice' and config.data == before


@pytest.mark.parametrize('change,message', [
    ('empty', 'au moins une identité'), ('bad_id', 'Identité invalide'),
    ('duplicate_id', 'Identité dupliquée'), ('duplicate_dir', 'Répertoire d’identité dupliqué'),
    ('outside_dir', 'sous-répertoire direct'), ('unknown_account', 'Identité inconnue'),
    ('bad_command', 'Commande de téléchargement invalide'),
    ('bad_provider_command', 'Commande de téléchargement invalide'),
    ('bad_interval', 'Configuration Kobo invalide'),
])
def test_invalid_values_raise_without_changing_config(editor, change, message):
    config, values = editor
    before = deepcopy(config.data)
    descriptors = {'paris': descriptor('paris')}
    if change == 'empty': values['identities'] = []
    if change == 'bad_id': values['identities'][0]['id'] = 'bad/id'
    if change.startswith('duplicate'):
        row = deepcopy(values['identities'][0])
        row['id'] = 'alice' if change == 'duplicate_id' else 'bob'
        values['identities'].append(row)
    if change == 'outside_dir': values['identities'][0]['directory'] = '/outside/alice'
    if change == 'unknown_account': values['accounts'] = [account('paris', 'missing')]
    if change == 'bad_command': values['download_command'] = 'unclosed "'
    if change == 'bad_provider_command':
        values['provider_downloads'] = {'paris': dict(command='unclosed "', post='')}
    if change == 'bad_interval': values['backup_interval'] = 'invalid'
    with pytest.raises(SettingsValidationError, match=message):
        validate(editor, descriptors=descriptors)
    assert config.data == before and not config.path.exists()


def test_unavailable_public_and_unknown_accounts_are_preserved(editor):
    config, values = editor
    rows = [dict(id=p, provider=p, identity_id='alice', options={'kept': True})
            for p in ('unknown', 'broken', 'public', 'paris')]
    config.set('providers.accounts', rows)
    descriptors = {p: descriptor(p, 'error' if p == 'broken' else 'ready')
                   for p in ('broken', 'public', 'paris')}
    replacement = account('paris')
    replacement['original_options'] = {'kept': 'option'}
    replacement['option_fields'] = {'base_url': ' '}
    descriptors['paris'].account_options = (
        AccountOptionField('base_url', 'URL du portail', default='https://bibliotheques.paris.fr'),
    )
    values['accounts'] = [replacement]
    draft = validate(editor, descriptors=descriptors, public_ids={'public'})
    accounts = draft.data['providers']['accounts']
    assert accounts[:3] == config.provider_accounts()[:3]
    assert len(accounts) == 4
    assert accounts[3]['credentials'] == {'username': 'synthetic-user'}
    assert accounts[3]['options'] == {'kept': 'option', 'base_url': 'https://bibliotheques.paris.fr'}
    assert accounts[3]['identity_id'] == 'alice'


def test_two_kobo_accounts_cannot_share_one_identity(editor):
    values = editor[1]
    values['accounts'] = [account('kobo', account_id='one'), account('kobo', account_id='two')]
    kobo = descriptor('kobo')
    kobo.max_accounts_per_identity = 1
    with pytest.raises(SettingsValidationError, match='au plus 1 compte'):
        validate(editor, descriptors={'kobo': kobo})
    values['identities'].append(dict(id='bob', label='Bob', directory='bob',
        adept_profile='', languages='fr', prefer_vo=False))
    values['accounts'][1]['identity'] = 'bob'
    assert len(validate(editor, descriptors={'kobo': kobo}).data['providers']['accounts']) == 2


def test_commands_remain_argument_lists_and_kobo_defaults_are_unchanged(editor):
    values = editor[1]
    values['download_command'] = 'downloader "{file}" --title "two words"'
    values['backup_interval'] = '1'
    values['vault_profile_index'] = 50
    values['provider_downloads'] = {'paris': dict(directory='p', ebook='e',
        audiobook='a', command='tool "two words"', post='')}
    draft = validate(editor)
    assert draft.data['downloads']['command'] == ['downloader', '{file}', '--title', 'two words']
    assert draft.data['kobo_backup']['interval_minutes'] == 15
    assert draft.data['kobo_backup']['display_profile'] == 'Android'
    assert draft.data['providers']['settings']['paris']['downloads']['command'] == ['tool', 'two words']


def test_active_tasks_only_block_actual_directory_changes(editor):
    assert not validate(editor, has_active_tasks=True).directory_change
    editor[1]['identities'][0]['directory'] = 'renamed'
    with pytest.raises(SettingsValidationError, match='Attendez la fin'):
        validate(editor, has_active_tasks=True)
    assert validate(editor).directory_change


def test_only_dirty_ai_is_normalized_and_errors_keep_safe_title(editor):
    config, values = editor
    before = deepcopy(config.data)
    values['ai'] = {'endpoint': 'https://user:synthetic-secret@example.test/private?token=synthetic'}
    assert validate(editor).data['llm'] == config.get('llm')
    with pytest.raises(SettingsValidationError) as caught:
        validate(editor, ai_dirty=True)
    assert caught.value.title == 'Configuration IA invalide'
    assert 'synthetic-secret' not in str(caught.value) and '/private' not in str(caught.value)
    assert config.data == before
    values['ai'] = dict(enabled=True, endpoint='http://127.0.0.1:11434/v1/chat/completions',
                        model='synthetic', api_key_env='SYNTHETIC_KEY_NAME')
    draft = validate(editor, ai_dirty=True)
    assert draft.ai_restart_required and draft.data['llm']['api_key_env'] == 'SYNTHETIC_KEY_NAME'


def test_error_precedence_is_identity_then_command_then_ai_then_kobo(editor):
    values = editor[1]
    values['identities'][0]['id'] = '/'
    values['download_command'] = '"'
    values['ai'] = {'enabled': True}
    values['backup_interval'] = 'invalid'
    with pytest.raises(SettingsValidationError, match='Identité invalide'): validate(editor, ai_dirty=True)
    values['identities'][0]['id'] = 'alice'
    with pytest.raises(SettingsValidationError, match='Commande de téléchargement'): validate(editor, ai_dirty=True)
    values['download_command'] = ''
    with pytest.raises(SettingsValidationError, match='Indiquer l’adresse'): validate(editor, ai_dirty=True)
    with pytest.raises(SettingsValidationError, match='Configuration Kobo'): validate(editor)


def test_revision_snapshot_is_independent_and_later_edits_remain_dirty():
    state = SettingsRevisionState(['accounts', 'ai', 'diagnostic'])
    assert state.mark_dirty('accounts')
    assert not state.mark_dirty('diagnostic') and not state.dirty('diagnostic')
    saved = state.snapshot()
    state.mark_dirty('accounts'); state.mark_dirty('ai')
    state.commit(saved)
    assert state.dirty('accounts') and state.dirty('ai')
    state.reset('ai')
    assert not state.dirty('ai') and state.dirty('accounts')
    current = state.snapshot(); current['accounts'] = 999
    assert state.snapshot()['accounts'] == 2
    state.commit(state.snapshot())
    assert not state.dirty('accounts')


def test_unknown_provider_descriptor_options_are_defaults_not_core_provider_rules(editor):
    config, values = editor
    values['accounts'] = [account('synthetic-provider')]
    values['accounts'][0]['original_options'] = {'untouched': 'kept', 'endpoint': 'old'}
    values['accounts'][0]['option_fields'] = {'endpoint': '   '}
    spec = AccountOptionField('endpoint', 'Endpoint', default='https://source.example/api')
    provider = ProviderDescriptor(
        id='synthetic-provider', display_name='Synthetic', description='', state='ready',
        capabilities=ProviderCapabilities(), account_options=(spec,),
        max_accounts_per_identity=1,
    )
    draft = validate(editor, descriptors={'synthetic-provider': provider})
    assert draft.data['providers']['accounts'][0]['options'] == {
        'untouched': 'kept', 'endpoint': 'https://source.example/api',
    }
    assert config.provider_accounts() == []  # draft is non-mutating


def test_descriptor_limit_is_enforced_for_arbitrary_providers(editor):
    values = editor[1]
    values['accounts'] = [account('synthetic', account_id='first'),
                          account('synthetic', account_id='second')]
    spec = descriptor('synthetic')
    spec.max_accounts_per_identity = 1
    with pytest.raises(SettingsValidationError, match='au plus 1 compte'):
        validate(editor, descriptors={'synthetic': spec})
    values['identities'].append(dict(id='bob', label='Bob', directory='bob',
        adept_profile='', languages='fr', prefer_vo=False))
    values['accounts'][1]['identity'] = 'bob'
    assert len(validate(editor, descriptors={'synthetic': spec}).data['providers']['accounts']) == 2


def test_descriptor_required_option_refuses_blank_without_dropping_existing_config(editor):
    config, values = editor
    values['accounts'] = [account('synthetic')]
    values['accounts'][0]['option_fields'] = {'endpoint': '  '}
    spec = descriptor('synthetic')
    spec.account_options = (AccountOptionField('endpoint', 'Endpoint', required=True),)
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='Option obligatoire : Endpoint'):
        validate(editor, descriptors={'synthetic': spec})
    assert config.data == before
    values['accounts'][0]['option_fields']['endpoint'] = ' https://source.example '
    result = validate(editor, descriptors={'synthetic': spec})
    assert result.data['providers']['accounts'][0]['options']['endpoint'] == 'https://source.example'


def test_retired_global_unlinked_bypass_is_preserved_not_rewritten(editor):
    """#3196: save a current settings draft without consuming legacy override."""
    config, values = editor
    config.set("kobo_backup.allow_unlinked_acquisition", True)
    values.pop("allow_unlinked_acquisition")
    original = deepcopy(config.data)
    draft = validate(editor)
    assert draft.data["kobo_backup"]["allow_unlinked_acquisition"] is True
    assert config.data == original
    config.set("kobo_backup.allow_unlinked_acquisition", False)
    assert validate(editor).data["kobo_backup"]["allow_unlinked_acquisition"] is False


@pytest.mark.parametrize(
    ("persisted", "spoofed"),
    [(False, True), (True, False), ("true", True), (1, False), (None, True)],
)
def test_generic_settings_save_preserves_readonly_legacy_kobo_enable_flag(
    editor, persisted, spoofed,
):
    config, values = editor
    config.set("kobo_backup.enabled", persisted)
    config.set("kobo_backup.allow_unlinked_acquisition", persisted)
    before = deepcopy(config.data)
    values["backup_enabled"] = spoofed
    # Another Settings section may be edited and saved simultaneously.
    values["identities"][0]["label"] = "Updated without global Kobo opt-in"
    draft = validate(editor)
    assert draft.data["kobo_backup"]["enabled"] == persisted
    assert draft.data["kobo_backup"]["allow_unlinked_acquisition"] == persisted
    assert draft.data["identities"][0]["label"] == "Updated without global Kobo opt-in"
    assert config.data == before
    assert not config.path.exists()


def test_kobo_legacy_enabled_absent_stays_absent_after_unrelated_settings_save(editor):
    config, values = editor
    config.data.setdefault("kobo_backup", {}).pop("enabled", None)
    original = deepcopy(config.data)
    values["backup_enabled"] = True
    values["backup_interval"] = "90"
    values["backup_directory"] = "KoboBooks"
    draft = validate(editor)
    assert "enabled" not in draft.data["kobo_backup"]
    assert draft.data["kobo_backup"]["interval_minutes"] == 90
    assert draft.data["kobo_backup"]["directory"] == "KoboBooks"
    assert config.data == original


@pytest.mark.parametrize("persisted", ["epub", "kepub", "pdf", "", None, 42])
def test_settings_save_preserves_legacy_kobo_format_without_ui_control(
    editor, persisted,
):
    config, values = editor
    config.set("kobo_backup.format", persisted)
    before = deepcopy(config.data)
    values["backup_interval"] = "90"
    values["identities"][0]["label"] = "Updated without changing Kobo format"
    draft = validate(editor)
    assert draft.data["kobo_backup"]["format"] == persisted
    assert draft.data["kobo_backup"]["interval_minutes"] == 90
    assert draft.data["identities"][0]["label"] == (
        "Updated without changing Kobo format"
    )
    assert config.data == before
    assert not config.path.exists()


def test_settings_save_does_not_create_missing_obsolete_kobo_format(editor):
    config, values = editor
    config.data.setdefault("kobo_backup", {}).pop("format", None)
    before = deepcopy(config.data)
    values["backup_interval"] = "90"
    draft = validate(editor)
    assert "format" not in draft.data["kobo_backup"]
    assert draft.data["kobo_backup"]["interval_minutes"] == 90
    assert config.data == before


@pytest.mark.parametrize("untyped", [
    None, False, 42, ["PRIVATE-VALUE"], {"endpoint": "PRIVATE-VALUE"},
])
def test_generic_provider_option_rejects_untyped_editor_snapshot_without_mutation(
    editor, untyped,
):
    config, values = editor
    values["accounts"] = [account("synthetic")]
    values["accounts"][0]["option_fields"] = {"endpoint": untyped}
    provider = descriptor("synthetic")
    provider.account_options = (
        AccountOptionField("endpoint", "Endpoint", required=True),
    )
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Valeur d’option invalide : Endpoint"):
        validate(editor, descriptors={"synthetic": provider})
    assert config.data == before
    assert not config.path.exists()


def test_generic_provider_required_option_none_default_is_not_the_string_none(editor):
    config, values = editor
    values["accounts"] = [account("synthetic")]
    values["accounts"][0]["option_fields"] = {"endpoint": "  "}
    provider = descriptor("synthetic")
    provider.account_options = (
        AccountOptionField("endpoint", "Endpoint", default=None, required=True),
    )
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Option obligatoire : Endpoint"):
        validate(editor, descriptors={"synthetic": provider})
    assert config.data == before
    values["accounts"][0]["option_fields"]["endpoint"] = " https://source.example "
    result = validate(editor, descriptors={"synthetic": provider})
    assert result.data["providers"]["accounts"][0]["options"]["endpoint"] == (
        "https://source.example"
    )


@pytest.mark.parametrize("bad_default", [123, False, {"endpoint": "PRIVATE"}])
def test_generic_provider_descriptor_cannot_install_nonstr_option_default(
    editor, bad_default,
):
    config, values = editor
    values["accounts"] = [account("synthetic")]
    values["accounts"][0]["option_fields"] = {"endpoint": ""}
    provider = descriptor("synthetic")
    provider.account_options = (
        AccountOptionField("endpoint", "Endpoint", default=bad_default),
    )
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Défaut d’option invalide : Endpoint"):
        validate(editor, descriptors={"synthetic": provider})
    assert config.data == before


def test_generic_provider_optional_empty_uses_declared_text_default(editor):
    config, values = editor
    values["accounts"] = [account("synthetic")]
    values["accounts"][0]["option_fields"] = {"endpoint": "   "}
    provider = descriptor("synthetic")
    provider.account_options = (
        AccountOptionField(
            "endpoint", "Endpoint", default=" https://default.example/api ",
        ),
    )
    result = validate(editor, descriptors={"synthetic": provider})
    assert result.data["providers"]["accounts"][0]["options"]["endpoint"] == (
        "https://default.example/api"
    )
    assert config.provider_accounts() == []


def test_unknown_provider_account_is_preserved_verbatim_without_implicit_activation(editor):
    """#3731: absent descriptor must not delete or enable a stored account."""
    config, values = editor
    unknown = dict(
        id="fixture-1", provider="provider.future", identity_id="alice",
        enabled=False, credential_id="credential-opaque",
        credentials={"token": "unchanged-test-secret"},
        options={"future_switch": False, "nested": {"keep": 7}},
    )
    config.set("providers.accounts", [deepcopy(unknown)])
    saved = validate(editor)
    assert saved.data["providers"]["accounts"] == [unknown]
    assert config.provider_accounts() == [unknown]
    assert saved.data["providers"]["accounts"][0]["enabled"] is False


def test_provider_declared_account_limit_is_not_hardcoded_to_known_supplier(editor):
    """Unknown future providers get the same declarative cardinality contract."""
    values = editor[1]
    values["accounts"] = [
        account("provider.future", account_id="one"),
        account("provider.future", account_id="two"),
    ]
    plugin = descriptor("provider.future")
    plugin.max_accounts_per_identity = 1
    with pytest.raises(SettingsValidationError, match="au plus 1 compte"):
        validate(editor, descriptors={"provider.future": plugin})
    plugin.max_accounts_per_identity = 2
    saved = validate(editor, descriptors={"provider.future": plugin})
    assert len(saved.data["providers"]["accounts"]) == 2


@pytest.mark.parametrize("invalid", ["1", 1.0, True, None, {}, -0.5])
def test_legacy_backup_profile_index_rejects_untyped_widget_values(editor, invalid):
    """#3731: a stale widget must not silently change legacy provider settings."""
    config, values = editor
    before = deepcopy(config.data)
    values["vault_profile_index"] = invalid
    with pytest.raises(SettingsValidationError, match="Profil de sauvegarde historique invalide"):
        validate(editor)
    assert config.data == before


def test_legacy_backup_profile_index_out_of_range_preserves_default(editor):
    values = editor[1]
    values["vault_profile_index"] = 99
    assert validate(editor).data["kobo_backup"]["display_profile"] == "Android"


def test_generic_provider_duplicate_account_ids_rejected_before_save(editor):
    config, values = editor
    values['accounts'] = [
        account('future-provider', account_id='same'),
        account('future-provider', account_id='same'),
    ]
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='dupliqué'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert config.data == before


def test_generic_provider_empty_account_id_rejected(editor):
    values = editor[1]
    values['accounts'] = [account('future-provider', account_id='')]
    with pytest.raises(SettingsValidationError, match='vide ou dupliqué'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})


def test_provider_descriptor_disappearing_mid_edit_is_safe_error(editor):
    config, values = editor
    values['accounts'] = [account('future-provider', account_id='future-1')]
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='Fournisseur indisponible'):
        validate(editor, descriptors={})
    assert config.data == before


def test_preserved_unknown_provider_account_id_cannot_be_reused(editor):
    config, values = editor
    old = dict(id='existing', provider='retired-provider', identity_id='alice',
               label='Legacy', enabled=False, credentials={}, options={})
    config.set('providers.accounts', [old])
    values['accounts'] = [account('future-provider', account_id='existing')]
    with pytest.raises(SettingsValidationError, match='dupliqué'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert config.provider_accounts() == [old]


@pytest.mark.parametrize("invalid", ["true", "false", 1, 0, None, [], {}])
def test_generic_provider_rejects_nonboolean_activation_without_mutation(editor, invalid):
    config, values = editor
    values['accounts'] = [account('future-provider')]
    values['accounts'][0]['enabled'] = invalid
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='Activation de compte invalide'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert config.data == before


@pytest.mark.parametrize("invalid", [None, False, 17, [], {}, {"username": None}, {"username": True}])
def test_generic_provider_rejects_stale_credential_fields_without_mutation(editor, invalid):
    config, values = editor
    values['accounts'] = [account('future-provider')]
    values['accounts'][0]['fields'] = invalid
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='Identifiants de compte indisponibles|Schéma des identifiants modifié'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert config.data == before


def test_generic_provider_descriptor_new_required_credential_fails_closed(editor):
    config, values = editor
    values['accounts'] = [account('future-provider')]
    before = deepcopy(config.data)
    future = descriptor('future-provider')
    future.credential_fields.append(NS(key='second-credential'))
    with pytest.raises(SettingsValidationError, match='Schéma des identifiants modifié'):
        validate(editor, descriptors={'future-provider': future})
    assert config.data == before



@pytest.mark.parametrize("invalid", [None, False, 0, "endpoint", ["endpoint"], [("endpoint", "injected")]])
def test_generic_provider_rejects_untyped_saved_option_snapshots(editor, invalid):
    """#3731: malformed GUI snapshots cannot coerce provider settings."""
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["original_options"] = invalid
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Options du fournisseur invalides"):
        validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    assert config.data == before


def test_generic_provider_preserves_valid_empty_and_unknown_saved_options(editor):
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["original_options"] = {"legacy": "unchanged"}
    before = deepcopy(config.data)
    result = validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    assert result.data["providers"]["accounts"][0]["options"] == {"legacy": "unchanged"}
    assert config.data == before


@pytest.mark.parametrize("invalid", [None, False, 0, "endpoint", ["endpoint"], [("endpoint", "malicious")]])
def test_generic_provider_rejects_stale_option_fields_without_mutation(editor, invalid):
    """#3731: a stale Settings widget must not coerce or crash on option fields."""
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["option_fields"] = invalid
    future = descriptor("future-provider")
    future.account_options = (AccountOptionField("endpoint", "Endpoint"),)
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Schéma des options modifié"):
        validate(editor, descriptors={"future-provider": future})
    assert config.data == before


def test_generic_provider_missing_option_fields_preserves_legacy_saved_options(editor):
    """Existing editors without new descriptor options must not erase saved data."""
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["original_options"] = {"endpoint": "saved", "unknown": "kept"}
    future = descriptor("future-provider")
    future.account_options = (AccountOptionField("endpoint", "Endpoint"),)
    saved = validate(editor, descriptors={"future-provider": future})
    assert saved.data["providers"]["accounts"][0]["options"] == {
        "endpoint": "saved", "unknown": "kept",
    }


def test_invalid_stored_provider_containers_cannot_be_silently_repaired_on_save(editor):
    config, values = editor
    values["accounts"] = [account("future-provider")]
    values["accounts"][0]["invalid_saved_snapshot"] = True
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match="Données du compte fournisseur invalides"):
        validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    assert config.data == before


def test_valid_provider_snapshot_without_guard_remains_compatible(editor):
    values = editor[1]
    values["accounts"] = [account("future-provider")]
    result = validate(editor, descriptors={"future-provider": descriptor("future-provider")})
    assert result.data["providers"]["accounts"][0]["enabled"] is True


def test_provider_descriptor_removed_credential_field_is_preserved_on_save(editor):
    """#3731: schema evolution must not delete hidden historic credentials."""
    config, values = editor
    values['accounts'] = [account('future-provider')]
    values['accounts'][0]['original_credentials'] = {
        'username': 'old-user', 'retired_token': 'secret-to-keep',
    }
    values['accounts'][0]['fields']['username'] = 'new-user'
    previous = deepcopy(config.data)
    draft = validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert draft.data['providers']['accounts'][0]['credentials'] == {
        'username': 'new-user', 'retired_token': 'secret-to-keep',
    }
    assert config.data == previous


def test_provider_descriptor_with_no_credentials_retains_retired_values(editor):
    values = editor[1]
    values['accounts'] = [account('future-provider')]
    values['accounts'][0]['fields'] = {}
    values['accounts'][0]['original_credentials'] = {'retired_token': 'untouched'}
    provider = descriptor('future-provider')
    provider.credential_fields = []
    result = validate(editor, descriptors={'future-provider': provider})
    assert result.data['providers']['accounts'][0]['credentials'] == {
        'retired_token': 'untouched',
    }


@pytest.mark.parametrize('invalid', [None, False, 1, 'secret', ['secret']])
def test_provider_rejects_malformed_historical_credential_snapshot(editor, invalid):
    config, values = editor
    values['accounts'] = [account('future-provider')]
    values['accounts'][0]['original_credentials'] = invalid
    before = deepcopy(config.data)
    with pytest.raises(SettingsValidationError, match='Identifiants historiques invalides'):
        validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert config.data == before


def test_provider_account_without_original_credential_snapshot_remains_compatible(editor):
    values = editor[1]
    values['accounts'] = [account('future-provider')]
    result = validate(editor, descriptors={'future-provider': descriptor('future-provider')})
    assert result.data['providers']['accounts'][0]['credentials'] == {
        'username': 'synthetic-user',
    }
