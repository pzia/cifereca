"""P0 #1127: GTK account editor widget contracts without importing GTK."""
from __future__ import annotations
from tests.settings_source import settings_source_text

import ast

import pytest
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
    def set_text(self, value):
        self.text = value
        for callback in getattr(self, "_signal_callbacks", {}).get("changed", ()):
            callback(self)
    def set_active(self, value):
        self.active = value
        for callback in getattr(self, "_signal_callbacks", {}).get("notify::active", ()):
            callback(self, None)
    def get_active(self): return self.active
    def connect(self, signal, callback):
        if not hasattr(self, "_signal_callbacks"):
            self._signal_callbacks = {}
        self._signal_callbacks.setdefault(signal, []).append(callback)
    def set_sensitive(self, value): self.sensitive = value
    def set_visible(self, value): self.visible = value
    def set_valign(self, _value): pass
    def add_css_class(self, value): self.classes.append(value)
    def add(self, value): self.children.append(value)
    def append(self, value): self.children.append(value)
    def add_suffix(self, value): self.suffixes.append(value)


def _render(provider_id="paris", *, data=None, credentials=None, url=None,
            descriptor_fields=None, descriptor_options=None, descriptor_note=None,
            legacy_values=None, provider_values=None, configured=None, interactive=False):
    secret = SimpleNamespace(key="password", label="Mot de passe", secret=True)
    plain = SimpleNamespace(key="card", label="Numéro de carte", secret=False)
    fields = descriptor_fields if descriptor_fields is not None else [plain, secret]
    descriptor = SimpleNamespace(display_name=provider_id.upper(), credential_fields=fields,
                                 account_options=descriptor_options or (), account_note=descriptor_note,
                                 interactive_authentication=interactive)
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
        provider_option=lambda pid, key, default=None, *, credential_id=None:
            (provider_values or {}).get((pid, key, credential_id), default),
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
    assert group.title == "PARIS — Bibliothèque"
    assert [row.title for row in group.children][:3] == [
        "Compte activé", "État de la connexion", "Libellé local"
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



def test_settings_header_never_discloses_opaque_credential_id_or_private_label():
    private_credential = "https://private.example/?token=secret-credential"
    editor, _, container, _ = _render("paris", data={
        "id": private_credential, "label": "Paris personnel",
        "identity_id": "alice", "credentials": {}, "options": {},
    })
    assert container.children[0].title == "PARIS — Paris personnel"
    # Changing the visual label must not rebind any persisted account.
    assert editor["id"] == private_credential
    editor, _, container, _ = _render("paris", data={
        "id": private_credential,
        "label": "https://private.example/?token=leaked",
        "identity_id": "alice", "credentials": {}, "options": {},
    })
    assert container.children[0].title == "PARIS — Connexion"
    assert "private.example" not in container.children[0].title
    assert editor["id"] == private_credential


def test_settings_header_without_saved_label_stays_generic_and_private():
    _, _, container, _ = _render("paris", data={
        "id": "authorization: Bearer SECRET", "identity_id": "alice",
    })
    assert container.children[0].title == "PARIS — Connexion"


def test_paris_url_and_identity_fallbacks_are_unchanged():
    option = _url_option('base_url', 'URL du portail',
                         'https://bibliotheques.paris.fr', 'base_url')
    editor, _, _, _ = _render('paris', configured=[{'id': 'bob'}],
                              descriptor_options=[option],
                              legacy_values={'base_url': 'https://env.paris.example'})
    assert editor['identity'].text == 'bob'
    assert editor['option_fields']['base_url'].text == 'https://bibliotheques.paris.fr'
    historical, _, _, _ = _render(
        'paris', data={'id': 'paris-existing', 'options': {}},
        descriptor_options=[option],
        legacy_values={
            'base_url': 'https://env.paris.example',
            'providers.accounts': [{'id': 'paris-existing', 'provider': 'paris'}],
        },
    )
    assert historical['option_fields']['base_url'].text == 'https://env.paris.example'
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
    assert editor['option_fields']['base_url'].text == 'https://default.example'
    assert any(row.title == 'Accès indirect' and row.subtitle == 'Service local intermédiaire'
               for row in container.children[0].children)
    saved, _, _, _ = _render(
        'arbitrary-provider', data={'options': {'base_url': 'https://saved.example'}},
        descriptor_options=[field], legacy_values={'base_url': 'https://legacy.example'},
    )
    assert saved['option_fields']['base_url'].text == 'https://saved.example'
    default, _, _, _ = _render('arbitrary-provider', descriptor_options=[field])
    assert default['option_fields']['base_url'].text == 'https://default.example'


def test_legacy_provider_option_is_scoped_to_exact_account_before_global_default():
    option = _url_option('base_url', 'URL du portail',
                         'https://default.example', 'base_url')
    kwargs = {
        'descriptor_options': [option],
        'legacy_values': {
            'base_url': 'https://global.example',
            'providers.accounts': [
                {'id': 'arbitrary-a', 'provider': 'arbitrary',
                 'options': {'base_url': 'https://provider-a.example'}},
                {'id': 'arbitrary-b', 'provider': 'arbitrary',
                 'options': {}},
            ],
        },
    }
    owner, _, _, _ = _render(
        'arbitrary', data={'id': 'arbitrary-a', 'options': {}}, **kwargs,
    )
    assert owner['option_fields']['base_url'].text == 'https://provider-a.example'
    other, _, _, _ = _render(
        'arbitrary', data={'id': 'arbitrary-b', 'options': {}}, **kwargs,
    )
    assert other['option_fields']['base_url'].text == 'https://default.example'
    new, _, _, _ = _render('arbitrary', **kwargs)
    assert new['option_fields']['base_url'].text == 'https://default.example'
    saved, _, _, _ = _render(
        'arbitrary',
        data={'id': 'arbitrary-b', 'options': {'base_url': 'https://saved.example'}},
        **kwargs,
    )
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



def test_optional_unknown_provider_option_without_default_is_empty_not_none():
    """#3731: absent optional text may not become literal 'None'."""
    option = SimpleNamespace(
        key='endpoint', label='Optional endpoint', default=None,
        secret=False, legacy_config_key=None,
    )
    editor, _, _, _ = _render('future-provider', descriptor_options=[option])
    assert editor['option_fields']['endpoint'].text == ''
    invalid, _, _, _ = _render(
        'future-provider', data={'options': {'endpoint': None}},
        descriptor_options=[option],
    )
    assert invalid['option_fields']['endpoint'].text == ''


def test_unknown_provider_option_declared_empty_default_stays_empty():
    option = SimpleNamespace(
        key='endpoint', label='Optional endpoint', default='',
        secret=False, legacy_config_key=None,
    )
    editor, _, _, _ = _render('future-provider', descriptor_options=[option])
    assert editor['option_fields']['endpoint'].text == ''



def test_unknown_provider_malformed_credential_values_are_not_stringified():
    """#3731: stored untyped credentials are never invented as secret strings."""
    for invalid in (None, False, True, 17, ["secret"], {"secret": "x"}):
        original = {
            'id': 'source-existing', 'credentials': {
                'card': invalid, 'password': invalid,
            },
        }
        editor, _, _, _ = _render('synthetic-provider', data=original)
        assert editor['fields']['card'].text == ''
        assert editor['fields']['password'].text == ''
        assert original['credentials']['card'] is invalid
        assert original['credentials']['password'] is invalid


def test_unknown_provider_valid_text_credentials_remain_unchanged():
    original = {
        'id': 'source-existing', 'credentials': {
            'card': 'abc-0123', 'password': 'private-value',
        },
    }
    editor, _, _, _ = _render('synthetic-provider', data=original)
    assert editor['fields']['card'].text == 'abc-0123'
    assert editor['fields']['password'].text == 'private-value'


def test_unknown_provider_corrupt_enabled_value_never_activates_source():
    """#3731: truthy legacy snapshots cannot turn into an enabled account."""
    for invalid in ("true", "false", 1, 0, None, [], {}, ["enabled"]):
        editor, _, _, _ = _render(
            "future-provider", data={"id": "existing", "enabled": invalid},
        )
        assert editor["enabled"].active is False
    assert _render("future-provider", data={"id": "existing", "enabled": True})[0]["enabled"].active is True
    assert _render("future-provider", data={"id": "existing", "enabled": False})[0]["enabled"].active is False
    # Existing accounts without the historical key remain compatible;
    # new accounts without any saved state stay disabled.
    assert _render("future-provider", data={"id": "existing"})[0]["enabled"].active is True
    assert _render("future-provider", data={})[0]["enabled"].active is False


def test_corrupt_provider_account_containers_render_safely_but_block_save():
    """#3731: never crash GTK or quietly replace corrupted persisted data."""
    for corrupt in ("bad", ["bad"], True, 7, None):
        for field in ("credentials", "options"):
            data = {"id": "existing", field: corrupt}
            editor, _, _, _ = _render("future-provider", data=data)
            assert editor["invalid_saved_snapshot"] is True
            assert data[field] is corrupt
    valid, _, _, _ = _render(
        "future-provider", data={"id": "existing", "credentials": {}, "options": {}},
    )
    assert valid["invalid_saved_snapshot"] is False


def test_unknown_provider_editor_preserves_undeclared_retired_credentials():
    """#3731: an unknown provider version must not discard stored secrets."""
    data = {
        'id': 'future-account',
        'credentials': {'card': 'active', 'retired_token': 'opaque-old-secret'},
    }
    editor, _, _, _ = _render('future-provider', data=data)
    assert editor['fields']['card'].text == 'active'
    assert 'retired_token' not in editor['fields']
    assert editor['original_credentials']['retired_token'] == 'opaque-old-secret'
    assert data['credentials']['retired_token'] == 'opaque-old-secret'


def test_unknown_provider_editor_keeps_opaque_account_attributes():
    data = {
        'id': 'future-account', 'provider': 'future-provider',
        'enabled': False, 'credentials': {'card': 'current'},
        'provider_meta': {'opaque': ['untouched']},
        'sync_receipt': 'receipt-reference',
    }
    editor, _, _, _ = _render('future-provider', data=data)
    assert editor['original_account']['provider_meta'] == {'opaque': ['untouched']}
    assert editor['original_account']['sync_receipt'] == 'receipt-reference'
    assert editor['enabled'].active is False
    assert data['provider_meta'] == {'opaque': ['untouched']}



@pytest.mark.parametrize("saved_owner", [None, False, 17, {}, [], "", "  "])
def test_p1_existing_account_with_invalid_identity_is_not_preassigned(saved_owner):
    """#3731: do not display the first identity as owner of an old account."""
    original = {"id": "existing", "identity_id": saved_owner, "enabled": False}
    editor, _delete, _container, _adw = _render(
        "provider.future", data=original, configured=[
            {"id": "alice"}, {"id": "bob"},
        ],
    )
    assert editor["identity"].text == ""
    assert editor["invalid_saved_snapshot"] is (type(saved_owner) is not str)
    assert editor["original_account"]["identity_id"] is saved_owner
    assert original["identity_id"] is saved_owner


def test_p1_missing_persisted_owner_is_not_preassigned_to_first_identity():
    saved = {"id": "existing", "enabled": True}
    editor, _delete, _container, _adw = _render(
        "provider.future", data=saved, configured=[
            {"id": "alice"}, {"id": "bob"},
        ],
    )
    assert editor["identity"].text == ""
    assert editor["original_account"] == saved
    assert "identity_id" not in saved


def test_p1_new_provider_account_still_preselects_first_identity():
    editor, _delete, _container, _adw = _render(
        "provider.future", data={}, configured=[
            {"id": "alice"}, {"id": "bob"},
        ],
    )
    assert editor["identity"].text == "alice"
    assert editor["enabled"].active is False


def test_p1_valid_existing_provider_owner_is_preserved_without_normalization():
    editor, _delete, _container, _adw = _render(
        "provider.future",
        data={"id": "existing", "identity_id": "bob"},
        configured=[{"id": "alice"}, {"id": "bob"}],
    )
    assert editor["identity"].text == "bob"



@pytest.mark.parametrize(("kind", "invalid"), [
    ("credential", None), ("credential", False), ("credential", 42),
    ("credential", ["value"]), ("option", None), ("option", True),
    ("option", 123), ("option", {"private": "value"}),
])
def test_p1_declared_malformed_saved_text_blocks_unrelated_save(kind, invalid):
    """#3731: malformed persisted text must not be rewritten from a blank UI."""
    option = _url_option("endpoint", "Provider endpoint", "https://default.invalid")
    saved = {
        "id": "existing", "enabled": False, "identity_id": "alice",
        "credentials": {"card": invalid if kind == "credential" else "card"},
        "options": {"endpoint": invalid if kind == "option" else "https://saved.invalid"},
    }
    editor, _, _, _ = _render(
        "provider.future", data=saved, descriptor_options=[option],
    )
    assert editor["invalid_saved_snapshot"] is True
    assert editor["enabled"].active is False
    assert saved["credentials"]["card"] == (
        invalid if kind == "credential" else "card"
    )
    assert saved["options"]["endpoint"] == (
        invalid if kind == "option" else "https://saved.invalid"
    )


def test_p1_retired_non_text_extensions_do_not_block_editable_fields():
    """Provider extensions outside the declared descriptor remain opaque."""
    option = _url_option("endpoint", "Provider endpoint", "")
    saved = {
        "id": "existing", "enabled": False,
        "credentials": {"card": "current", "retired_token": {"opaque": 1}},
        "options": {"endpoint": "", "retired_setting": [1, 2, 3]},
    }
    editor, _, _, _ = _render(
        "provider.future", data=saved, descriptor_options=[option],
    )
    assert editor["invalid_saved_snapshot"] is False
    assert editor["original_options"]["retired_setting"] == [1, 2, 3]
    assert editor["original_credentials"]["retired_token"] == {"opaque": 1}


def test_p1_bnf_manual_loan_limit_default_and_hint_are_descriptor_driven():
    """#4065: option is an indicative local value, not an authorization."""
    option = SimpleNamespace(
        key="manual_loan_limit", label="Maximum d’emprunts (valeur personnelle)",
        default="4", secret=False, legacy_config_key=None,
        value_type="unsigned_integer", min_value=0, max_value=99,
        help_text="Valeur personnelle ; ne change pas les droits serveur.",
    )
    account_editor, _, container, _ = _render(
        "bnf-pret", descriptor_options=[option],
        data={"id": "bnf-reader", "identity_id": "alice", "options": {}},
    )
    assert account_editor["option_fields"]["manual_loan_limit"].text == "4"
    rows = container.children[0].children
    assert any(
        row.title == option.label and row.subtitle == option.help_text
        for row in rows if hasattr(row, "subtitle")
    )
    assert account_editor["original_options"] == {}

    saved, _, _, _ = _render(
        "bnf-pret", descriptor_options=[option],
        data={"id": "bnf-reader", "identity_id": "alice",
              "options": {"manual_loan_limit": "9"}},
    )
    assert saved["option_fields"]["manual_loan_limit"].text == "9"


def test_interactive_provider_has_explicit_probe_button_only():
    editor, delete, container, _adw = _render(
        "synthetic-interactive",
        interactive=True,
        data={"id": "saved", "identity_id": "alice",
              "enabled": True, "credentials": {"card": "x"}},
    )
    assert editor["explicit_probe_button"] is not None
    assert editor["explicit_probe_status"].title == "Vérifier la connexion au compte"
    assert delete in container.children[0].children[-1].suffixes

    normal, _, _, _ = _render("ordinary-provider")
    assert normal["explicit_probe_button"] is None
    assert normal["explicit_probe_status"] is None


def test_cleaned_account_editor_shows_only_current_option_but_keeps_legacy_config():
    """#3691: removed technical options are opaque, not silently deleted."""
    descriptor_options = [_url_option(
        'authenticated_sync', 'Synchronisation des prêts (on/off)', default='off',
    )]
    saved = {
        "id": "la-rochelle-1", "enabled": True, "identity_id": "identity-a",
        "label": "Lecteur", "credentials": {"card": "SYNTHETIC-USER",
                                             "password": "SYNTHETIC-SECRET"},
        "options": {"experimental_http_probe": "auto-post",
                    "ajax_dc": "PRIVATE-SYNTHETIC-DYNAMIC"},
    }
    editor, _delete, _container, _adw = _render(
        "biblioaccess.la-rochelle", data=saved,
        descriptor_options=descriptor_options, interactive=True,
    )
    assert set(editor["option_fields"]) == {"authenticated_sync"}
    assert editor["option_fields"]["authenticated_sync"].text == "off"
    assert editor["original_options"] == saved["options"]
    initial = editor["probe_rendered_baseline"]
    assert initial["enabled"] is True
    assert initial["identity"] == "identity-a"
    assert initial["fields"]["password"] == "SYNTHETIC-SECRET"
    assert initial["option_fields"] == {"authenticated_sync": "off"}
    # The initial snapshot is internal; it must never appear in page labels.
    assert all("PRIVATE-SYNTHETIC-DYNAMIC" not in str(row.title)
               for row in editor["group"].children)


def test_fieldless_sso_is_an_account_not_an_anonymous_source():
    sso, _, container, _ = _render(
        "synthetic-sso", descriptor_fields=[], interactive=True,
        data={"identity_id": "alice", "id": "sso-a", "enabled": True,
              "credentials": {}, "options": {}},
    )
    assert container.children[0].children[0].title == "Compte activé"
    assert sso["enabled"].active is True
    public, _, container, _ = _render(
        "synthetic-catalogue", descriptor_fields=[],
    )
    assert container.children[0].children[0].title == "Source activée"
    assert public["enabled"].active is False


def test_account_lifecycle_distinguishes_saved_activation_from_live_session():
    """Activation is a saved preference, not evidence of provider connection."""
    for enabled, expected in (
        (True, "Activée dans les paramètres · session non vérifiée"),
        (False, "Désactivée dans les paramètres · session non vérifiée"),
    ):
        _, _, container, _ = _render(data={
            "id": "connection-a", "identity_id": "alice", "enabled": enabled,
            "label": "Compte local", "credentials": {}, "options": {},
        })
        rows = container.children[0].children
        lifecycle, = (row for row in rows if row.title == "État de la connexion")
        assert lifecycle.subtitle == expected
        # Switch changes the same row immediately, without saving or probing.
        editor, _, container2, _ = _render(data={
            "id": "connection-b", "identity_id": "alice", "enabled": enabled,
            "label": "Compte local", "credentials": {}, "options": {},
        })
        editor["enabled"].set_active(not enabled)
        row2, = (row for row in container2.children[0].children
                 if row.title == "État de la connexion")
        assert "modification non enregistrée" in row2.subtitle
        assert ("Désactivée" if enabled else "Activée") in row2.subtitle
        editor["enabled"].set_active(enabled)
        assert "modification non enregistrée" not in row2.subtitle
        assert row2.subtitle == expected


def test_p1_5540_two_accounts_two_identities_keep_exact_options_and_credentials():
    """Installed-style GTK contract: one descriptor, two saved connections."""
    option = _url_option("endpoint", "Catalogue personnel",
                         default="https://default.example",
                         legacy_config_key="legacy.endpoint")
    accounts = [
        {"id": "connection-a", "provider": "synthetic",
         "identity_id": "alice", "enabled": True, "label": "Bibliothèque A",
         "credentials": {"card": "CARD-A", "password": "SECRET-A"},
         "options": {"endpoint": "https://a.example"}},
        {"id": "connection-b", "provider": "synthetic",
         "identity_id": "bob", "enabled": False, "label": "Bibliothèque B",
         "credentials": {"card": "CARD-B", "password": "SECRET-B"},
         "options": {"endpoint": "https://b.example"}},
    ]
    observed = []
    for account in accounts:
        editor, _, container, adw = _render(
            "synthetic", data=account, descriptor_options=[option],
            configured=[{"id": "alice"}, {"id": "bob"}],
            legacy_values={"providers.accounts": accounts,
                           "legacy.endpoint": "https://unscoped.example"},
        )
        observed.append((editor, container, adw))
        assert editor["identity"].text == account["identity_id"]
        assert editor["fields"]["card"].text == account["credentials"]["card"]
        assert isinstance(editor["fields"]["password"], adw.PasswordEntryRow)
        assert editor["fields"]["password"].text == account["credentials"]["password"]
        assert editor["option_fields"]["endpoint"].text == account["options"]["endpoint"]
        assert editor["invalid_saved_snapshot"] is False
        assert account["credentials"]["password"] not in str(container.children[0].title)
    first, second = observed[0][0], observed[1][0]
    first["option_fields"]["endpoint"].set_text("https://changed.example")
    assert second["option_fields"]["endpoint"].text == "https://b.example"
    assert second["enabled"].get_active() is False
    assert accounts[0]["options"]["endpoint"] == "https://a.example"
    assert accounts[1]["options"]["endpoint"] == "https://b.example"


def test_p1_5540_unscoped_legacy_option_never_migrates_to_two_accounts():
    option = _url_option("endpoint", "Catalogue personnel",
                         default="https://default.example",
                         legacy_config_key="legacy.endpoint")
    accounts = [
        {"id": "a", "provider": "synthetic", "identity_id": "alice"},
        {"id": "b", "provider": "synthetic", "identity_id": "bob"},
    ]
    for account in accounts:
        editor, _, _, _ = _render(
            "synthetic", data=account, descriptor_options=[option],
            legacy_values={"providers.accounts": accounts,
                           "legacy.endpoint": "https://private.example"},
        )
        assert editor["option_fields"]["endpoint"].text == "https://default.example"
    # A sole historical connection may still recover its legacy preference.
    editor, _, _, _ = _render(
        "synthetic", data=accounts[0], descriptor_options=[option],
        legacy_values={"providers.accounts": [accounts[0]],
                       "legacy.endpoint": "https://historical.example"},
    )
    assert editor["option_fields"]["endpoint"].text == "https://historical.example"


def test_p1_5540_linked_readers_keep_exact_credential_and_branch_limits():
    """Paris-like linked-reader limits are local preferences, never quotas."""
    from cifereca.models import Account
    from cifereca.plugin_api import BorrowerScopedOptionField
    from cifereca.gui.borrower_scoped_options import linked_borrower_choices

    spec = BorrowerScopedOptionField(
        key="linked_limits", label="Maximum local",
        branches=(("digital", "Numérique"), ("physical", "Physique")),
        default="4", advisory_loan_limit=True,
    )
    saved = {"reader-a": {"digital": "3", "physical": "8"}}
    observations = [
        Account(display_name="Lecteur A", unique_id="reader-a", is_primary=False,
                provider_id="paris", credential_id="credential-a", identity_id="alice"),
        Account(display_name="Lecteur B", unique_id="reader-b", is_primary=False,
                provider_id="paris", credential_id="credential-a", identity_id="alice"),
        Account(display_name="Lecteur tiers", unique_id="reader-c", is_primary=False,
                provider_id="paris", credential_id="credential-b", identity_id="alice"),
        Account(display_name="Autre profil", unique_id="reader-d", is_primary=False,
                provider_id="paris", credential_id="credential-a", identity_id="bob"),
        Account(display_name="Compte primaire", unique_id="primary", is_primary=True,
                provider_id="paris", credential_id="credential-a", identity_id="alice"),
    ]
    readers = linked_borrower_choices(
        saved, spec, observations, provider_id="paris",
        credential_id="credential-a", identity_id="alice",
    )
    assert [borrower for borrower, _ in readers] == ["reader-a", "reader-b"]
    assert all("reader-c" not in label and "reader-d" not in label
               for _, label in readers)
    # The same persisted branch settings are editable offline, without
    # inventing additional observed borrowers or requiring provider I/O.
    offline = linked_borrower_choices(
        saved, spec, (), provider_id="paris",
        credential_id="credential-a", identity_id="alice",
    )
    assert [borrower for borrower, _ in offline] == ["reader-a"]
    assert saved == {"reader-a": {"digital": "3", "physical": "8"}}


def test_p1_5540_empty_saved_option_never_resurrects_global_endpoint():
    option = _url_option("endpoint", "Catalogue personnel",
                         default="https://default.example",
                         legacy_config_key="legacy.endpoint")
    saved = {"id": "a", "provider": "synthetic", "identity_id": "alice",
             "options": {"endpoint": ""}}
    draft = dict(saved, options={})
    editor, _, _, _ = _render(
        "synthetic", data=draft, descriptor_options=[option],
        legacy_values={"providers.accounts": [saved],
                       "legacy.endpoint": "https://retired.example"},
    )
    assert editor["option_fields"]["endpoint"].text == ""


def test_p1_5540_probe_result_invalidated_even_after_draft_revert():
    editor, _, _, _ = _render(interactive=True, data={
        "id": "a", "identity_id": "alice", "enabled": True,
        "credentials": {"password": "original"},
    })
    lifecycle = editor["connection_lifecycle_status"]
    status = editor["explicit_probe_status"]
    lifecycle.set_subtitle("Test de connexion réussi")
    status.set_subtitle("Connexion vérifiée")
    assert editor["_probe_draft_revision"] == 0
    editor["fields"]["password"].set_text("changed")
    assert "non vérifiée" in lifecycle.subtitle
    assert "Enregistrer" in status.subtitle
    editor["fields"]["password"].set_text("original")
    assert editor["_probe_draft_revision"] == 2
    assert "non vérifiée" in lifecycle.subtitle


def test_p1_5540_owner_edit_invalidates_probe():
    editor, _, _, _ = _render(interactive=True, data={
        "id": "a", "identity_id": "alice", "enabled": True,
    })
    lifecycle = editor["connection_lifecycle_status"]
    lifecycle.set_subtitle("Test de connexion réussi")
    editor["identity"].set_text("bob")
    assert editor["_probe_draft_revision"] == 1
    assert "non vérifiée" in lifecycle.subtitle
