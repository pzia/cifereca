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
            descriptor_fields=None, descriptor_options=None, configured=None):
    secret = SimpleNamespace(key="password", label="Mot de passe", secret=True)
    plain = SimpleNamespace(key="card", label="Numéro de carte", secret=False)
    fields = descriptor_fields if descriptor_fields is not None else [plain, secret]
    descriptor = SimpleNamespace(display_name=provider_id.upper(), credential_fields=fields,
                                 account_options=descriptor_options or ())
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
        get=lambda key, default=None: default,
    )
    editor, delete = build_provider_account_editor(
        data=settings, provider_id=provider_id,
        account_id=settings.get("id") or provider_id + "-default",
        descriptor=descriptor, configured_identities=configured or [],
        accounts_container=container, config=config, Gtk=gtk, Adw=adw,
    )
    return editor, delete, container, adw


def test_paris_rows_keep_password_obscured_and_same_identity_defaults():
    data = {
        "id": "paris-a", "label": "Bibliothèque", "enabled": False,
        "identity_id": "alice", "credentials": {
            "card": "CARD-77", "password": "private-password",
        },
        "options": {"base_url": "https://custom.paris.example"},
    }
    editor, delete, container, adw = _render("paris", data=data)
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
    assert editor["base_url"].text == "https://custom.paris.example"
    assert editor["opds_url"] is None and editor["vault_url"] is None
    assert editor["original_options"] == data["options"]
    assert editor["deleted"] is False
    assert delete in group.children[-1].suffixes
    assert "destructive-action" in delete.classes


def test_paris_url_and_identity_fallbacks_are_unchanged():
    editor, _, _, _ = _render("paris", configured=[{"id": "bob"}],
                              url="https://env.paris.example")
    assert editor["identity"].text == "bob"
    assert editor["base_url"].text == "https://env.paris.example"
    editor, _, _, _ = _render("paris")
    assert editor["identity"].text == "default"
    assert editor["base_url"].text == "https://bibliotheques.paris.fr"


def test_gallica_public_and_kobo_indirect_access_rows_are_preserved():
    gallica, _, gc, _ = _render("gallica", descriptor_fields=[],
                               data={"options": {"opds_url": "https://test.example/opds"}})
    assert gc.children[0].children[0].title == "Source activée"
    assert gallica["opds_url"].text == "https://test.example/opds"
    assert gallica["base_url"] is None and gallica["vault_url"] is None
    assert any(row.title == "Accès public" for row in gc.children[0].children)
    kobo, _, kc, _ = _render("kobo", data={"options": {"base_url": "http://local:9000"}})
    assert kobo["vault_url"].text == "http://local:9000"
    assert kobo["opds_url"] is None and kobo["base_url"] is None
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
                             default='https://default.example', secret=False)
    secret = SimpleNamespace(key='auth_hint', label='Clé de compte',
                             default='', secret=True)
    original = {'options': {'endpoint': 'https://saved.example', 'unmanaged': 'keep'}}
    editor, _, container, adw = _render(
        'synthetic-provider', data=original, descriptor_options=[option, secret],
    )
    assert editor['option_fields']['endpoint'].text == 'https://saved.example'
    assert isinstance(editor['option_fields']['auth_hint'], adw.PasswordEntryRow)
    assert editor['option_fields']['auth_hint'].text == ''
    assert editor['original_options']['unmanaged'] == 'keep'
    assert editor['base_url'] is None and editor['vault_url'] is None
    assert not any(row.title in {'Connexion indirecte au compte Kobo', 'Accès public'}
                   for row in container.children[0].children)
    assert original == {'options': {'endpoint': 'https://saved.example', 'unmanaged': 'keep'}}
