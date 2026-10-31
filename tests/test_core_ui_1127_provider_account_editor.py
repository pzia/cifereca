"""P0 #1127: GTK account editor widget contracts without importing GTK."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast
from pathlib import Path
from types import SimpleNamespace

from cifereca.gui.settings_account_editor import build_provider_account_editor


class Widget:
    def __init__(self):
        self.title = None
        self.text = None
        self.subtitle = None
        self.active = None
        self.sensitive = True
        self.classes = []
        self.children = []
        self.suffixes = []
        self.visible = True
    def set_title(self, value): self.title = value
    def set_subtitle(self, value): self.subtitle = value
    def set_text(self, value): self.text = value
    def set_active(self, value): self.active = value
    def set_sensitive(self, value): self.sensitive = value
    def set_visible(self, value): self.visible = value
    def set_valign(self, _value): pass
    def add_css_class(self, value): self.classes.append(value)
    def add(self, value): self.children.append(value)
    def append(self, value): self.children.append(value)
    def add_suffix(self, value): self.suffixes.append(value)


def _render(provider_id="paris", *, data=None, credentials=None, url=None,
            descriptor_fields=None, descriptor_options=None, descriptor_note=None,
            legacy_values=None, provider_values=None, configured=None):
    secret = SimpleNamespace(key="password", label="Mot de passe", secret=True)
    plain = SimpleNamespace(key="card", label="Numéro de carte", secret=False)
    fields = descriptor_fields if descriptor_fields is not None else [plain, secret]
    descriptor = SimpleNamespace(display_name=provider_id.upper(), credential_fields=fields,
                                 account_options=descriptor_options or (), account_note=descriptor_note)
    gtk = SimpleNamespace(
        Switch=Widget, Button=lambda label=None: Widget(),
        Align=SimpleNamespace(CENTER="center"),
    )
    adw = SimpleNamespace(
        PreferencesGroup=Widget, ActionRow=Widget,
        EntryRow=Widget, PasswordEntryRow=type("PasswordEntryRow", (Widget,), {}),
    )
    container = Widget()
    settings = data if data is not None else {}
    config = SimpleNamespace(
        paris_base_url=lambda: url,
        get=lambda key, default=None: (legacy_values or {}).get(key, default),
        provider_option=lambda pid, key, default=None: (provider_values or {}).get((pid, key), default),
    )
    editor, delete = build_provider_account_editor(
        data=settings, provider_id=provider_id,
        account_id=settings.get("id") or provider_id + "-default",
        descriptor=descriptor, configured_identities=configured or [],
        accounts_container=container, config=config, Gtk=gtk, Adw=adw,
    )
    return editor, delete, container, adw


def _url_option(key, title, default='', legacy_config_key=None):
    return SimpleNamespace(key=key, label=title, default=default,
                           legacy_config_key=legacy_config_key, secret=False)


def test_paris_rows_keep_password_obscured_and_same_identity_defaults():
    data = {
        "id": "paris-a", "label": "Bibliothèque", "enabled": False,
        "identity_id": "alice", "credentials": {
            "card": "CARD-77", "password": "private-password",
        },
        "options": {"base_url": "https://custom.paris.example"},
    }
    editor, delete, container, adw = _render(
        "paris", data=data, descriptor_options=[_url_option('base_url', 'URL du portail')],
    )
    assert len(container.children) == 1
    group = container.children[0]
    assert group.title == "PARIS — paris-a"
    assert [row.title for row in group.children][:3] == [
        "Compte activé", "Libellé local", "Identité (ID)"
    ]
    assert editor["enabled"].active is False
    assert editor["label"].text == "Bibliothèque"
    assert editor["identity"].text == "alice"
    assert editor["fields"]["card"].text == "CARD-77"
    assert isinstance(editor["fields"]["password"], adw.PasswordEntryRow)
    assert editor["fields"]["password"].text == "private-password"
    assert editor["option_fields"]["base_url"].text == "https://custom.paris.example"
    assert editor["original_options"] == data["options"]
    assert editor["deleted"] is False
    assert delete in group.children[-1].suffixes
    assert "destructive-action" in delete.classes


def test_paris_url_and_identity_fallbacks_are_unchanged():
    option = _url_option('base_url', 'URL du portail',
                         'https://bibliotheques.paris.fr', 'base_url')
    editor, _, _, _ = _render('paris', configured=[{'id': 'bob'}],
                              descriptor_options=[option],
                              legacy_values={'base_url': 'https://env.paris.example'})
    assert editor['identity'].text == 'bob'
    assert editor['option_fields']['base_url'].text == 'https://env.paris.example'
    editor, _, _, _ = _render('paris', descriptor_options=[option])
    assert editor['identity'].text == 'default'
    assert editor['option_fields']['base_url'].text == 'https://bibliotheques.paris.fr'


def test_gallica_public_and_kobo_indirect_access_rows_are_preserved():
    gallica, _, gc, _ = _render(
        'gallica', descriptor_fields=[],
        descriptor_options=[_url_option('opds_url', 'URL du catalogue OPDS')],
        descriptor_note=('Accès public', 'Source sans authentification'),
        data={'options': {'opds_url': 'https://test.example/opds'}},
    )
    assert gc.children[0].children[0].title == "Source activée"
    assert gallica["option_fields"]["opds_url"].text == "https://test.example/opds"
    assert any(row.title == "Accès public" for row in gc.children[0].children)
    kobo, _, kc, _ = _render(
        'kobo', data={'options': {'base_url': 'http://local:9000'}},
        descriptor_options=[_url_option('base_url', 'URL API Kobo-Vault')],
        descriptor_note=('Connexion indirecte au compte Kobo', 'Kobo-Vault est intermédiaire'),
    )
    assert kobo['option_fields']['base_url'].text == 'http://local:9000'
    assert any(row.title == "Connexion indirecte au compte Kobo" for row in kc.children[0].children)


def test_builder_does_not_install_signals_or_mutate_configured_data():
    data = {
        "provider": "paris", "id": "paris-1",
        "credentials": {"card": "abc", "password": "secret"},
        "options": {"base_url": "https://paris.example"},
    }
    before = {key: dict(value) if isinstance(value, dict) else value for key,value in data.items()}
    editor, button, _, _ = _render("paris", data=data)
    assert data == before
    assert editor["id"] == "paris-1"
    assert button is not None


def test_window_keeps_guard_id_allocation_delete_dirty_and_async_save():
    root = Path(__file__).resolve().parents[1]
    render = ast.unparse(ast.parse(settings_source_text()))
    assert "def add_account_editor" in render
    assert "descriptor.state != 'ready'" in render
    assert "provider_id in public_ids" in render
    assert "build_provider_account_editor(" in render
    assert "mark_settings_dirty('accounts')" in render
    assert "settings_committer.submit(" in render


def test_unknown_provider_option_schema_builds_generic_rows_and_preserves_values():
    option = SimpleNamespace(key='endpoint', label='Adresse du service',
                             default='https://default.example', secret=False,
                             legacy_config_key=None)
    secret = SimpleNamespace(key='auth_hint', label='Clé de compte',
                             default='', secret=True, legacy_config_key=None)
    original = {'options': {'endpoint': 'https://saved.example', 'unmanaged': 'keep'}}
    editor, _, container, adw = _render(
        'synthetic-provider', data=original, descriptor_options=[option, secret],
    )
    assert editor['option_fields']['endpoint'].text == 'https://saved.example'
    assert isinstance(editor['option_fields']['auth_hint'], adw.PasswordEntryRow)
    assert editor['option_fields']['auth_hint'].text == ''
    assert editor['original_options']['unmanaged'] == 'keep'
    assert 'base_url' not in editor and 'vault_url' not in editor
    assert not any(row.title in {'Connexion indirecte au compte Kobo', 'Accès public'}
                   for row in container.children[0].children)
    assert original == {'options': {'endpoint': 'https://saved.example', 'unmanaged': 'keep'}}


def test_declared_option_prefers_saved_value_then_legacy_config_then_descriptor_default():
    field = SimpleNamespace(key='base_url', label='URL du portail',
                            default='https://default.example', secret=False,
                            legacy_config_key='base_url')
    note = ('Accès indirect', 'Service local intermédiaire')
    editor, _, container, _ = _render(
        'arbitrary-provider', descriptor_options=[field], descriptor_note=note,
        legacy_values={'base_url': 'https://legacy.example'},
    )
    assert editor['option_fields']['base_url'].text == 'https://legacy.example'
    assert any(row.title == 'Accès indirect' and row.subtitle == 'Service local intermédiaire'
               for row in container.children[0].children)
    saved, _, _, _ = _render(
        'arbitrary-provider', data={'options': {'base_url': 'https://saved.example'}},
        descriptor_options=[field], legacy_values={'base_url': 'https://legacy.example'},
    )
    assert saved['option_fields']['base_url'].text == 'https://saved.example'
    default, _, _, _ = _render('arbitrary-provider', descriptor_options=[field])
    assert default['option_fields']['base_url'].text == 'https://default.example'


def test_legacy_provider_option_precedes_global_default_without_overwriting_saved_value():
    option = _url_option('base_url', 'URL du portail',
                         'https://default.example', 'base_url')
    kwargs = {
        'descriptor_options': [option],
        'legacy_values': {'base_url': 'https://global.example'},
        'provider_values': {('arbitrary', 'base_url'): 'https://provider.example'},
    }
    editor, _, _, _ = _render('arbitrary', **kwargs)
    assert editor['option_fields']['base_url'].text == 'https://provider.example'
    saved, _, _, _ = _render('arbitrary', data={'options': {'base_url': 'https://saved.example'}}, **kwargs)
    assert saved['option_fields']['base_url'].text == 'https://saved.example'


def test_new_unknown_provider_account_defaults_disabled_without_changing_existing(editor=None):
    """#3731: adding an account must not enable it implicitly."""
    created, _, _, _ = _render('synthetic-provider', data={})
    assert created['enabled'].active is False
    configured, _, _, _ = _render(
        'synthetic-provider', data={'id': 'legacy', 'credentials': {}},
    )
    assert configured['enabled'].active is True  # legacy missing flag
    disabled, _, _, _ = _render(
        'synthetic-provider', data={'id': 'existing', 'enabled': False},
    )
    assert disabled['enabled'].active is False



def test_account_editor_has_no_implicit_paris_fallback_for_unknown_plugin():
    """#3731: missing/retired provider IDs must never become a Paris account."""
    source = ast.unparse(ast.parse(settings_source_text()))
    assert "def add_account_editor" in source
    assert "provider_id: str=''" in source
    assert "configured.get('provider') or ''" in source
    assert "configured.get('provider') or 'paris'" not in source
    assert "provider_id: str='paris'" not in source
    assert "descriptor = descriptors.get(provider_id)" in source
    assert "descriptor is None or descriptor.state != 'ready'" in source
