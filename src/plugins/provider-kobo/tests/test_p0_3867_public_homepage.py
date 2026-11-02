"""#3867: public Kobo catalogue is a provider descriptor, not Core default."""
from cifereca_kobo import DESCRIPTOR


def test_kobo_public_homepage_is_owned_by_provider_not_vault_api():
    assert DESCRIPTOR.id == "kobo"
    assert DESCRIPTOR.homepage == "https://www.kobo.com"
    assert any(option.key == "base_url" for option in DESCRIPTOR.account_options)
    # The customer's configured private Vault API must not masquerade as
    # the reader-facing public Kobo catalogue page.
    assert "kobo-vault" not in DESCRIPTOR.homepage
