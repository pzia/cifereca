"""#3252: selected-right API contract, synthetic Vault only."""
import pytest
from cifereca.plugin_api import ProviderCapabilityError
from tests.test_p1_3252_kobo_selected_delivery_offer import setup

def select(**changes):
    value = dict(edition_id="book-1", right_id="book-1",
                 kind="purchase", branch="personal_manifest",
                 explicit_user_action=True)
    value.update(changes)
    return value

def test_selected_kobo_one_right_one_preflight():
    session, vault, broker, account, posts = setup()
    try:
        result = session.rights_selected_delivery_offer_snapshot(
            account, **select(),
        )
        assert len(result.rights) == len(posts) == 1
        assert result.rights[0].delivery == "available"
        assert result.status == result.coverage == "partial"
        assert not result.authorizes_mutation
    finally:
        session.close()

@pytest.mark.parametrize("changes", [
    {"explicit_user_action": False},
    {"kind": "loan"},
    {"branch": "other"},
    {"edition_id": "different"},
])
def test_selected_kobo_wrong_key_has_no_network(changes):
    session, vault, broker, account, posts = setup()
    try:
        with pytest.raises(ProviderCapabilityError):
            session.rights_selected_delivery_offer_snapshot(
                account, **select(**changes),
            )
        assert vault.calls == 0 and not posts
    finally:
        session.close()
