"""#4065: BnF setting is local metadata, not a lending authorization."""
from cifereca_bnf_pret import DESCRIPTOR, BnfProviderSession


def test_bnf_advisory_limit_is_a_bounded_per_credential_option():
    (field,) = DESCRIPTOR.account_options
    assert field.key == "manual_loan_limit"
    assert field.default == "4"
    assert field.value_type == "unsigned_integer"
    assert field.min_value == 0 and field.max_value == 99
    assert "droits" in field.help_text
    assert DESCRIPTOR.capabilities.borrow is True


def test_session_never_uses_advisory_preference_for_provider_capability():
    session = BnfProviderSession(
        credential_id="credential-a", label="Synthetic",
        credentials={}, options={"manual_loan_limit": "0"},
    )
    try:
        assert session.options["manual_loan_limit"] == "0"
        assert session.capabilities.borrow is True
        assert not session.connected
    finally:
        session.close()
