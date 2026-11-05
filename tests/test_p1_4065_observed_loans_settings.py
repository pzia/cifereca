"""P1 #4065 — cached positives are not an inventory or provider quota.

No provider session, credentials, database, HTTP or mutation. Identity,
connection, upstream borrower and loan medium remain exact. Settings rendering
only adds informational ActionRows; it never changes the saved preferences.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from cifereca.models import Account, Loan
from cifereca.gui.settings_loan_observation import loan_cache_observations
from cifereca.gui.settings_account_editor import build_provider_account_editor
from cifereca.providers.base import (
    AccountOptionField, BorrowerScopedOptionField, CredentialField,
    ProviderCapabilities, ProviderDescriptor,
)
from tests.test_core_ui_1127_provider_account_editor import Widget


def account(borrower="", *, identity="alice", credential="cred-1",
            provider="future-provider", primary=True):
    return Account(
        unique_id=borrower, identity_id=identity, credential_id=credential,
        provider_id=provider, is_primary=primary, display_name="PRIVATE_NAME",
    )


def loan(identifier, *, borrower="", identity="alice", credential="cred-1",
         provider="future-provider", medium="digital"):
    return Loan(
        id=identifier, provider="DILICOM", medium=medium,
        record_id="PRIVATE_RECORD", title="PRIVATE_BOOK_TITLE",
        identity_id=identity, source_provider=provider,
        source_credential=credential, source_account_id=borrower,
    )


def projection(accounts, rows, *, provider="future-provider",
               credential="cred-1", identity="alice"):
    return loan_cache_observations(
        provider_id=provider, credential_id=credential, identity_id=identity,
        observed_borrowers=accounts, cached_loans=rows,
    )


def test_primary_and_linked_are_scoped_to_credential_identity_and_medium():
    owners = (
        account(), account("reader-1", primary=False),
        account("other-identity", identity="bob", primary=False),
        account("other-credential", credential="cred-2", primary=False),
        account("other-provider", provider="other", primary=False),
    )
    cached = (
        loan("a"), loan("a"),  # duplicate is one observed positive
        loan("b", medium="digital"), loan("c", medium="physical"),
        loan("linked-a", borrower="reader-1"),
        loan("linked-b", borrower="reader-1", medium="physical"),
        loan("linked-extra", borrower="reader-1", medium="physical"),
        loan("foreign-identity", identity="bob"),
        loan("foreign-credential", credential="cred-2"),
        loan("foreign-provider", provider="other"),
        loan("foreign-borrower", borrower="unknown"),
        loan("unattributed", borrower="primary"),  # never primary wildcard
    )
    out = projection(owners, cached)
    assert len(out) == 2
    assert out[0][0] == "Prêts observés · compte principal"
    assert out[1][0] == "Prêts observés · compte lié 1"
    assert "Numériques : 2 prêt(s) observé(s)" in out[0][1]
    assert "Physiques : 1 prêt(s) observé(s)" in out[0][1]
    assert "Numériques : 1 prêt(s) observé(s)" in out[1][1]
    assert "Physiques : 2 prêt(s) observé(s)" in out[1][1]
    assert all("Quota fournisseur : inconnu" in text for _, text in out)
    assert all("Aucun emprunt restant calculé" in text for _, text in out)
    assert not any(private in repr(out) for private in (
        "PRIVATE", "foreign-", "reader-1", "cred-1", "alice",
    ))


def test_paris_primary_legacy_alias_uses_same_scope_as_core_inventory():
    # Paris's typed loan inventory accepts the literal 'primary' only for
    # the real primary reader (unique_id=''); this is no linked-account grant.
    rows = (
        loan("main-actual", provider="paris", borrower=""),
        loan("main-legacy", provider="paris", borrower="primary"),
        loan("other-credential", provider="paris", borrower="primary",
             credential="elsewhere"),
    )
    out = projection((account(provider="paris"),), rows, provider="paris")
    assert len(out) == 1
    assert "Numériques : 2 prêt(s) observé(s)" in out[0][1]
    assert "Quota fournisseur : inconnu" in out[0][1]


def test_paris_legacy_primary_alias_collision_cannot_count_for_two_borrowers():
    # A linked reader literally named 'primary' would make the old alias
    # ambiguous; no cached positive may be attributed to either owner.
    owners = (account(provider="paris"),
              account("primary", provider="paris", primary=False))
    rows = (loan("ambiguous", provider="paris", borrower="primary"),
            loan("real-empty", provider="paris", borrower=""))
    out = projection(owners, rows, provider="paris")
    assert len(out) == 2
    assert "Numériques : 1 prêt(s) observé(s)" in out[0][1]
    assert "Numériques : non vérifiés" in out[1][1]
    assert "ambiguous" not in str(out)


def test_zero_cached_rows_never_mean_zero_live_loans_or_remaining_allowance():
    out = projection((account(),), ())
    assert len(out) == 1
    assert "Numériques : non vérifiés" in out[0][1]
    assert "Physiques : non vérifiés" in out[0][1]
    assert "0 prêt" not in str(out)
    assert "Inventaire exhaustif : non attesté" in out[0][1]
    assert "Quota fournisseur : inconnu" in out[0][1]


def test_bnf_synthetic_unique_primary_id_is_not_treated_as_paris_empty_id():
    owners = (account("bnf-account", provider="bnf-pret"),)
    rows = (
        loan("bnf-a", borrower="bnf-account", provider="bnf-pret"),
        loan("paris-a", borrower="", provider="paris"),
    )
    out = projection(owners, rows, provider="bnf-pret")
    assert len(out) == 1
    assert "Numériques : 1 prêt(s) observé(s)" in out[0][1]
    assert "Physiques : non vérifiés" in out[0][1]
    assert "Quota fournisseur : inconnu" in out[0][1]


@pytest.mark.parametrize("borrowers,rows", [
    (object(), ()),
    ((account(),), object()),
    ((account(),), (loan("a"),) * 10_001),
    ((account(),) * 33, ()),
    ((account(), account()), ()),
    ((account("control\nchar"),), ()),
])
def test_malformed_or_ambiguous_cache_fails_closed(borrowers, rows):
    out = projection(borrowers, rows)
    assert len(out) == 1
    assert "quota" not in out[0][0].lower()
    assert "indisponible" in out[0][1] or "Aucun compte" in out[0][1]
    assert "Quota fournisseur : inconnu" in out[0][1]


def test_opaque_or_malformed_cache_items_cannot_be_counted_or_rendered():
    out = projection(
        (account(),),
        (object(), {"source_account_id": "", "title": "PRIVATE"},
         loan("PRIVATE", identity="bob"),
         loan("bad id\n", borrower=""),
         loan("unassigned", borrower="elsewhere")),
    )
    assert "Numériques : non vérifiés" in out[0][1]
    assert "PRIVATE" not in str(out)
    assert "unassigned" not in str(out)


def test_unavailable_connection_or_no_live_borrower_is_unknown_not_zero():
    out = projection((), (loan("a"),))
    assert "Aucun compte emprunteur confirmé" in out[0][1]
    assert "non attesté" in out[0][1]
    assert "0 prêt" not in str(out)


SPEC = BorrowerScopedOptionField(
    key="synthetic_borrower_limits", label="Limite personnelle",
    branches=(("digital", "Numérique"), ("physical", "Physique")),
    default="4", min_value=0, max_value=99,
)
DESCRIPTOR = ProviderDescriptor(
    id="future-provider", display_name="Fournisseur futur",
    description="Fixture sans réseau", state="ready",
    capabilities=ProviderCapabilities(loans=True),
    credential_fields=(CredentialField("username", "Utilisateur"),),
    account_options=(
        AccountOptionField("synthetic_local_max", "Maximum personnel",
                           default="4", value_type="unsigned_integer",
                           min_value=0, max_value=99),
    ),
    borrower_scoped_options=(SPEC,),
)


def test_generic_settings_editor_displays_observations_without_changing_options():
    data = {
        "id": "cred-1", "provider": "future-provider",
        "identity_id": "alice", "enabled": True,
        "credentials": {"username": "PRIVATE_USER"},
        "options": {
            "synthetic_local_max": "7",
            "synthetic_borrower_limits": {"reader-1": {"digital": "3"}},
            "opaque": {"never": "overwrite"},
        },
    }
    before = deepcopy(data)
    container = Widget()
    gtk = NS(Switch=Widget, Button=lambda label=None: Widget(),
             Align=NS(CENTER="center"))
    adw = NS(PreferencesGroup=Widget, ActionRow=Widget,
             EntryRow=Widget, PasswordEntryRow=Widget)
    config = NS(provider_option=lambda *args: None, get=lambda *args: None)
    editor, delete = build_provider_account_editor(
        data=data, provider_id="future-provider", account_id="cred-1",
        descriptor=DESCRIPTOR, configured_identities=[{"id": "alice"}],
        accounts_container=container, config=config, Gtk=gtk, Adw=adw,
        observed_borrowers=(account(), account("reader-1", primary=False),
                            account("foreign", identity="bob", primary=False)),
        observed_loans=(loan("main"), loan("linked", borrower="reader-1"),
                        loan("private-other", identity="bob")),
    )
    group = container.children[0]
    notes = [row for row in group.children
             if type(row.title) is str and row.title.startswith("Prêts observés")]
    assert len(notes) == 2
    assert "Numériques : 1 prêt(s) observé(s)" in notes[0].subtitle
    assert "Numériques : 1 prêt(s) observé(s)" in notes[1].subtitle
    assert all("Quota fournisseur : inconnu" in row.subtitle for row in notes)
    assert all("PRIVATE" not in str(row.subtitle) for row in notes)
    assert editor["option_fields"]["synthetic_local_max"].text == "7"
    assert editor["scoped_option_fields"][SPEC.key]["reader-1"]["digital"].text == "3"
    assert editor["original_options"]["opaque"] == {"never": "overwrite"}
    assert data == before
    assert group.children[-1].suffixes == [delete]
    assert not hasattr(delete, "handlers")


def test_settings_callsite_reads_core_cache_only_without_inventory_or_provider_io():
    source = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/settings_editor_actions.py").read_text(encoding="utf-8")
    assert 'observed_borrowers=getattr(self.service, "accounts", ())' in source
    assert 'observed_loans=getattr(self.service, "last_loans", ())' in source
    helper = (Path(__file__).resolve().parents[1] /
              "src/cifereca/gui/settings_loan_observation.py").read_text(encoding="utf-8")
    assert "httpx" not in helper.lower()
    assert "requests.get(" not in helper.lower()
    assert "synchronize" not in helper.lower()
