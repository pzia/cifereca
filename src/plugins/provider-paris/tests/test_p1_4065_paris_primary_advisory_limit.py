"""#4065: Paris primary-branch loan preferences are advisory and scoped.

Only synthetic objects. No login, supplier GET/POST, ADEPT, or real credentials.
Linked borrowers must never inherit a primary credential's manual value.
"""
from __future__ import annotations

from cifereca.plugin_api import Account, Loan
from cifereca_paris import DESCRIPTOR, ParisProviderSession


def fields():
    return {field.key: field for field in DESCRIPTOR.account_options}


def test_primary_limit_options_are_two_independent_typed_decimal_fields():
    declared = fields()
    assert declared["base_url"].default == "https://bibliotheques.paris.fr"
    for key, branch in (
        ("manual_loan_limit_primary_digital", "DILICOM"),
        ("manual_loan_limit_primary_physical", "SYRACUSE"),
    ):
        option = declared[key]
        assert option.value_type == "unsigned_integer"
        assert (option.default, option.min_value, option.max_value) == ("4", 0, 99)
        assert "principal" in option.label.casefold()
        assert branch in option.help_text
        assert "liés" in option.help_text
        assert "droits" in option.help_text or "quota" in option.help_text
    assert declared["manual_loan_limit_primary_digital"].key != (
        declared["manual_loan_limit_primary_physical"].key
    )


def test_provider_capabilities_never_make_local_preferences_a_permission():
    assert DESCRIPTOR.capabilities.loans
    assert DESCRIPTOR.capabilities.renew_loan
    assert DESCRIPTOR.capabilities.return_loan is False
    assert not hasattr(DESCRIPTOR.capabilities, "manual_loan_limit")
    # These options only live in descriptor configuration: neither an
    # observed provider quota nor a supplier capability is being published.


def test_primary_preferences_have_no_effect_on_linked_loan_inventory():
    session = ParisProviderSession(
        credential_id="synthetic-credential", label="Paris",
        credentials={"username": "fixture", "password": "fixture"},
        options={"manual_loan_limit_primary_digital": "0",
                 "manual_loan_limit_primary_physical": "99"},
        config={}, diagnostics=None,
    )
    calls = []

    class Client:
        def loans(self, upstream, borrower):
            calls.append((upstream, borrower))
            return [Loan(
                id=f"{upstream}-{borrower or 'primary'}",
                provider=upstream,
                medium="digital" if upstream == "DILICOM" else "physical",
                record_id="fictitious", title="Fixture",
            )]
        def close(self):
            return None

    session.client.close()
    session.client = Client()
    session._enrich_digital_loan_metadata = lambda loan: None
    session.connected = True
    primary = Account(unique_id="", is_primary=True, provider_id="paris",
                      credential_id="synthetic-credential", identity_id="alice")
    linked = Account(unique_id="linked-B", is_primary=False, provider_id="paris",
                     credential_id="synthetic-credential", identity_id="alice")
    session._accounts = [primary, linked]
    try:
        first = session.loan_inventory_snapshot(primary)
        second = session.loan_inventory_snapshot(linked)
        assert len(first.loans) == 2
        assert len(second.loans) == 2
        assert all(loan.source_account_id == "primary" for loan in first.loans)
        assert all(loan.source_account_id == "linked-B" for loan in second.loans)
        assert calls == [("DILICOM", ""), ("SYRACUSE", ""),
                         ("DILICOM", "linked-B"), ("SYRACUSE", "linked-B")]
        assert all(b.completeness == "unknown" for b in first.branches + second.branches)
        assert session.options["manual_loan_limit_primary_digital"] == "0"
        assert session.options["manual_loan_limit_primary_physical"] == "99"
    finally:
        session.close()


def test_missing_values_keep_declarative_default_but_never_write_config():
    session = ParisProviderSession(
        credential_id="synthetic-credential", label="Paris",
        credentials={}, options={"opaque_plugin_option": "keep"},
        config={}, diagnostics=None,
    )
    try:
        assert "manual_loan_limit_primary_digital" not in session.options
        assert "manual_loan_limit_primary_physical" not in session.options
        assert session.options["opaque_plugin_option"] == "keep"
        assert all(fields()[name].default == "4" for name in (
            "manual_loan_limit_primary_digital",
            "manual_loan_limit_primary_physical",
        ))
    finally:
        session.close()


def test_paris_linked_options_are_declarative_and_disjoint_from_primary():
    (spec,) = DESCRIPTOR.borrower_scoped_options
    assert spec.key == "manual_loan_limits_linked"
    assert spec.default == "4"
    assert (spec.min_value, spec.max_value) == (0, 99)
    assert spec.branches == (("digital", "Numérique · DILICOM"),
                             ("physical", "Physique · SYRACUSE"))
    assert "droits" in spec.help_text and "synchronisation" in spec.help_text
    assert all(option.key != spec.key for option in DESCRIPTOR.account_options)
