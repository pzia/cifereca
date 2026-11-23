"""#5540: declarative lender settings never represent personal ceilings as quotas.

All fixtures are synthetic. Rendering never calls a supplier or changes config.
"""
from types import SimpleNamespace as NS

import pytest

from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.models import Account
from cifereca.providers.base import (
    AccountOptionField, BorrowerScopedOptionField, CredentialField,
    ProviderCapabilities, ProviderDescriptor,
)
from tests.test_core_ui_1127_provider_account_editor import Widget


def _render(provider_id):
    descriptor = ProviderDescriptor(
        id=provider_id, display_name="Fournisseur de test",
        description="Fournisseur synthétique", state="ready",
        capabilities=ProviderCapabilities(loans=True),
        credential_fields=(CredentialField("username", "Identifiant"),),
        account_options=(
            AccountOptionField(
                "ceiling", "Prêts numériques", default="4",
                value_type="unsigned_integer", min_value=0, max_value=99,
                advisory_loan_medium="digital",
            ),
            AccountOptionField("display_mode", "Affichage", default="compact"),
        ),
        borrower_scoped_options=(BorrowerScopedOptionField(
            key="linked_ceiling", label="Lecteur lié",
            branches=(("digital", "Numérique"), ("physical", "Physique")),
            default="4", min_value=0, max_value=99,
            advisory_loan_limit=True,
        ),),
    )
    saved = {
        "provider": provider_id, "id": "conn-a", "identity_id": "alice",
        "enabled": True, "credentials": {"username": "fake"},
        "options": {
            "ceiling": "0", "display_mode": "compact",
            "linked_ceiling": {"linked-a": {"digital": "99", "physical": "3"}},
        },
    }
    observed = (Account(
        unique_id="linked-a", is_primary=False, identity_id="alice",
        provider_id=provider_id, credential_id="conn-a",
        display_name="Lecteur fictif",
    ),)
    config = NS(get=lambda key, default=None: (_ for _ in ()).throw(
        AssertionError("unexpected configuration read")))
    gtk = NS(Switch=Widget, Button=lambda label=None: Widget(),
             Align=NS(CENTER="center"))
    adw = NS(PreferencesGroup=Widget, ActionRow=Widget, EntryRow=Widget,
             PasswordEntryRow=Widget)
    ed, _ = build_provider_account_editor(
        data=saved, provider_id=provider_id, account_id="conn-a",
        descriptor=descriptor, configured_identities=[{"id": "alice"}],
        accounts_container=Widget(), config=config, Gtk=gtk, Adw=adw,
        observed_borrowers=observed, observed_loans=(),
    )
    return ed, saved


@pytest.mark.parametrize("provider_id", ["test-lender-a", "test-lender-b"])
def test_advisory_settings_are_uniform_across_provider_ids(provider_id):
    editor, saved = _render(provider_id)
    rows = editor["group"].children
    titles = [row.title for row in rows]
    assert "Plafond renseigné par vous · Prêts numériques" in titles
    assert "Plafond renseigné par vous · Lecteur lié · Compte lié 1 · Lecteur fictif · Numérique" in titles
    assert "Plafond renseigné par vous · Lecteur lié · Compte lié 1 · Lecteur fictif · Physique" in titles
    assert "Affichage" in titles
    assert "Plafond renseigné par vous · Affichage" not in titles
    notes = [row.subtitle for row in rows if type(row.subtitle) is str]
    assert sum("ni quota officiel ni autorisation" in note for note in notes) == 2
    assert editor["option_fields"]["ceiling"].text == "0"
    assert editor["scoped_option_fields"]["linked_ceiling"]["linked-a"]["digital"].text == "99"
    assert editor["scoped_option_fields"]["linked_ceiling"]["linked-a"]["physical"].text == "3"
    assert saved["options"]["ceiling"] == "0"
    assert saved["options"]["linked_ceiling"]["linked-a"] == {"digital": "99", "physical": "3"}
    assert editor["invalid_saved_snapshot"] is False


@pytest.mark.parametrize("private_label", (
    "access_token:abc", "refresh_token=abc", "client_secret:abc",
    "sessionid=abc", "Set-Cookie: session=abc", "X-API-Key: abc",
    "jwt=abc",
))
def test_settings_connection_title_never_echoes_credential_markers(private_label):
    from cifereca.gui.settings_account_editor import _public_connection_title
    descriptor = NS(display_name="Fournisseur de test")
    title = _public_connection_title(descriptor, {"label": private_label})
    assert title == "Fournisseur de test — Connexion"
    descriptor = NS(display_name=private_label)
    assert _public_connection_title(descriptor, {"label": "Compte public"}) == (
        "Fournisseur — Compte public"
    )
